#!/usr/bin/env python3
"""
analyze_blackbox.py — Betaflight blackbox log analyzer.

Parses Betaflight blackbox logs (.bbl/.bfl/.txt). By default it parses ALL
headers of every log session and prints a readable summary. On demand it can
fully decode the binary frame stream (I/P/S/G/H/E frames) using the field
encodings and predictors — the real flight data — and emit per-field
statistics, a CSV export, or a JSON document.

Decoding is a pure-Python port of the official blackbox-log-viewer; the
heavy lifting lives in blackbox_decoder.py. numpy/pandas are used here only
for statistics and CSV export.

Usage:
    python analyze_blackbox.py <log>                 # headers + summary
    python analyze_blackbox.py <log> --decode        # also decode frames
    python analyze_blackbox.py <log> --stats         # per-field statistics
    python analyze_blackbox.py <log> --csv out.csv   # export main frames to CSV
    python analyze_blackbox.py <log> --csv -         # CSV to stdout
    python analyze_blackbox.py <log> --json          # everything as JSON
    python analyze_blackbox.py <log> --session 2     # pick a log session

Flags --stats and --csv imply --decode. For full FFT / noise analysis still
prefer https://blackbox.betaflight.com.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import blackbox_decoder as bb  # noqa: E402
import blackbox_presenter as pr  # noqa: E402

# Header config keys worth surfacing in the human summary (when present).
# Values prefer the decoded (enum-name) form when available.
_SUMMARY_KEYS = [
    ("Firmware revision", "Firmware"),
    ("Board information", "Board"),
    ("Craft name", "Craft"),
    ("Log start datetime", "Logged at"),
    ("looptime", "Looptime (us)"),
    ("gyro_sync_denom", "Gyro sync denom"),
    ("pid_process_denom", "PID process denom"),
    ("fast_pwm_protocol", "Motor protocol"),
    ("dshot_bidir", "Bidir DSHOT"),
    ("rates_type", "Rates type"),
    ("serialrx_provider", "Serial RX"),
    ("debug_mode", "Debug mode"),
]


def _tune_warnings(sys_config: dict) -> list[str]:
    """Surface-level red flags derived from the embedded header config."""
    warnings = []
    bidir = sys_config.get("dshot_bidir")
    if bidir in (0, None, "0"):
        warnings.append(
            "Bidirectional DSHOT not enabled - RPM filter inactive, dynamic notch quality reduced"
        )
    motor_limit = sys_config.get("motor_output_limit")
    if isinstance(motor_limit, (int, float)) and motor_limit < 100:
        warnings.append(f"motor_output_limit = {motor_limit} (<100): motors are being capped")
    dyn_count = sys_config.get("dyn_notch_count")
    if isinstance(dyn_count, (int, float)) and dyn_count == 0:
        warnings.append("dyn_notch_count = 0: dynamic notch filtering disabled")
    return warnings


def analyze(path: Path, *, decode: bool, want_stats: bool, session_sel) -> dict:
    raw = path.read_bytes()
    ranges = bb.split_sessions(raw)

    result = {
        "file": str(path),
        "size_bytes": len(raw),
        "session_count": len(ranges),
        "sessions": [],
    }

    if not ranges:
        result["error"] = (
            "No blackbox log section markers found — file may be corrupt or not a Betaflight log"
        )
        return result

    for idx, (start, end) in enumerate(ranges, start=1):
        if session_sel is not None and idx != session_sel:
            continue
        info = _analyze_session(raw, start, end, idx, decode=decode, want_stats=want_stats)
        result["sessions"].append(info)

    return result


def _analyze_session(raw, start, end, idx, *, decode, want_stats) -> dict:
    parser = bb.FlightLogParser(raw, start, end)
    info: dict = {"index": idx, "warnings": []}
    try:
        parser.parse_header()
    except ValueError as exc:
        info["error"] = str(exc)
        return info

    sc = parser.sys_config
    info["sys_config"] = sc
    info["frame_defs"] = {
        marker: {"count": fd["count"], "fields": fd["name"]}
        for marker, fd in parser.frame_defs.items()
    }
    info["decoded_settings"] = pr.present_headers(sc)
    info["rates"] = pr.compute_rates(sc)
    info["warnings"] = _tune_warnings(sc)

    if decode or want_stats:
        parser.parse_data()
        info["decoded"] = True
        info["frame_stats"] = parser.stats["frame"]
        info["corrupt_frames"] = parser.stats["total_corrupt_frames"]
        info["main_frame_count"] = len(parser.main_frames)
        info["gps_frame_count"] = len(parser.gps_frames)
        info["event_count"] = len(parser.events)
        info["duration_s"] = _duration_seconds(parser)
        info["_parser"] = parser  # kept for stats/CSV; stripped before JSON
        if want_stats:
            info["field_stats"] = _field_statistics(parser)
    return info


def _duration_seconds(parser) -> float:
    frames = parser.main_frames
    if len(frames) < 2:
        return 0.0
    t0 = frames[0][bb.FIELD_INDEX_TIME]
    t1 = frames[-1][bb.FIELD_INDEX_TIME]
    return round((t1 - t0) / 1_000_000, 3)


def _field_statistics(parser) -> dict:
    """Per-field min/max/mean/std in human-readable units.

    Stats are computed on raw decoded values, then mapped to physical units
    via the field presenter. Because every presented field is an affine
    (scale + offset) transform of the raw value, we derive (scale, offset)
    from two in-range sample points and apply it to the raw statistics.
    """
    import numpy as np

    if not parser.main_frames:
        return {}
    arr = np.array(parser.main_frames, dtype=np.float64)
    sc = parser.sys_config
    names = parser.main_field_names
    n_cells = pr.num_cells_estimate(sc, names)
    n_motors = pr.num_motors(names)
    hr = 10 if sc.get("blackbox_high_resolution") else 1

    stats = {}
    x_lo, x_hi = 1000.0, 2000.0
    for i, name in enumerate(names):
        col = arr[:, i]
        s_lo, unit = pr.present_main_field(sc, name, x_lo, num_cells=n_cells,
                                           n_motors=n_motors, high_res_scale=hr)
        s_hi, _ = pr.present_main_field(sc, name, x_hi, num_cells=n_cells,
                                        n_motors=n_motors, high_res_scale=hr)
        if isinstance(s_lo, (int, float)) and isinstance(s_hi, (int, float)):
            scale = (s_hi - s_lo) / (x_hi - x_lo)
            offset = s_lo - x_lo * scale
        else:
            scale, offset, unit = 1.0, 0.0, ""
        stats[name] = {
            "min": round(float(col.min()) * scale + offset, 3),
            "max": round(float(col.max()) * scale + offset, 3),
            "mean": round(float(col.mean()) * scale + offset, 3),
            "std": round(float(col.std()) * abs(scale), 3),
            "unit": unit,
        }
    return stats


def _write_csv(parser, dest: str) -> None:
    import pandas as pd

    df = pd.DataFrame(parser.main_frames, columns=parser.main_field_names)
    if dest == "-":
        df.to_csv(sys.stdout, index=False)
    else:
        df.to_csv(dest, index=False)


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------
def _print_human(result: dict) -> None:
    print("=" * 64)
    print(f"Blackbox Log Analysis: {Path(result['file']).name}")
    print("=" * 64)
    print(f"File size:     {result['size_bytes']:,} bytes")
    print(f"Log sessions:  {result['session_count']}")

    if result.get("error"):
        print(f"\n⚠ {result['error']}")
        return

    for s in result["sessions"]:
        print(f"\n-- Session {s['index']} " + "-" * 48)
        if s.get("error"):
            print(f"  ! Header error: {s['error']}")
            continue
        sc = s["sys_config"]
        decoded = s.get("decoded_settings", {})
        for key, label in _SUMMARY_KEYS:
            value = decoded.get(key, sc.get(key))
            if value not in (None, ""):
                print(f"  {label + ':':<22}{value}")

        fd = s["frame_defs"]
        fd_summary = ", ".join(f"{m}={d['count']}" for m, d in fd.items())
        print(f"  {'Frame field counts:':<22}{fd_summary}")

        _print_rates(s.get("rates"))

        if s.get("decoded"):
            print(f"  {'Duration:':<22}{s['duration_s']} s")
            print(f"  {'Main frames:':<22}{s['main_frame_count']:,}")
            if s["gps_frame_count"]:
                print(f"  {'GPS frames:':<22}{s['gps_frame_count']:,}")
            if s["event_count"]:
                print(f"  {'Events:':<22}{s['event_count']}")
            fs = s["frame_stats"]
            counts = ", ".join(
                f"{m}:{v['valid']}" for m, v in fs.items() if v["valid"]
            )
            print(f"  {'Valid frames:':<22}{counts}")
            if s["corrupt_frames"]:
                print(f"  {'Corrupt frames:':<22}{s['corrupt_frames']}")

        if s.get("field_stats"):
            print("\n  Per-field statistics (human-readable units):")
            print(f"    {'field':<16}{'min':>13}{'max':>13}{'mean':>13}{'std':>13}  unit")
            for name, st in s["field_stats"].items():
                print(
                    f"    {name:<16}{st['min']:>13.2f}{st['max']:>13.2f}"
                    f"{st['mean']:>13.2f}{st['std']:>13.2f}  {st.get('unit', '')}"
                )

        if s["warnings"]:
            print(f"\n  Warnings ({len(s['warnings'])}):")
            for w in s["warnings"]:
                print(f"    ! {w}")

    print(
        "\nNote: filter/PID red flags above are header-level heuristics. "
        "For gyro/D-term noise spectra run: python -m scripts.spectral_analysis <log.bbl> --plot"
    )


def _print_rates(rates: dict) -> None:
    if not rates or not rates.get("axes"):
        return
    rt = rates["rates_type"]
    print(f"\n  Rates ({rt}):")
    for axis, e in rates["axes"].items():
        nat = e["native"]
        nat_str = ", ".join(f"{k}={v}" for k, v in nat.items() if v is not None)
        line = f"    {axis:<6} {nat_str}"
        if e.get("max_dps") is not None:
            line += f"  ->  max {e['max_dps']} deg/s"
            if e.get("center_sensitivity_dps") is not None:
                line += f", center {e['center_sensitivity_dps']} deg/s"
        else:
            line += "  (max deg/s not computed for this rates type)"
        print(line)


def _strip_parsers(result: dict) -> dict:
    for s in result.get("sessions", []):
        s.pop("_parser", None)
    return result


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    ap = argparse.ArgumentParser(description="Betaflight blackbox log analyzer")
    ap.add_argument("log", help="path to a .bbl/.bfl/.txt blackbox log")
    ap.add_argument("--decode", action="store_true", help="decode the binary frame stream")
    ap.add_argument("--stats", action="store_true", help="per-field statistics (implies --decode)")
    ap.add_argument("--csv", metavar="FILE", help="export decoded main frames to CSV ('-' for stdout)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a human summary")
    ap.add_argument("--session", type=int, metavar="N", help="analyze only log session N (1-based)")
    args = ap.parse_args()

    path = Path(args.log)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        return 1

    want_csv = args.csv is not None
    decode = args.decode or args.stats or want_csv

    result = analyze(
        path,
        decode=decode,
        want_stats=args.stats,
        session_sel=args.session,
    )

    # CSV export (writes the selected/first decoded session)
    if want_csv:
        target = next(
            (s for s in result.get("sessions", []) if s.get("decoded") and s.get("_parser")),
            None,
        )
        if target is None:
            print("Error: no decodable session found for CSV export", file=sys.stderr)
            return 1
        if result["session_count"] > 1 and args.session is None and args.csv == "-":
            print("Note: multiple sessions present; exporting session 1. Use --session N to choose.",
                  file=sys.stderr)
        _write_csv(target["_parser"], args.csv)
        if args.csv != "-":
            print(f"Wrote {len(target['_parser'].main_frames):,} main frames to {args.csv}",
                  file=sys.stderr)

    _strip_parsers(result)

    if args.json:
        json.dump(result, sys.stdout, indent=2, default=str)
        print()
    elif not (want_csv and args.csv == "-"):
        _print_human(result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
