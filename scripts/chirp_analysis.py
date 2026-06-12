#!/usr/bin/env python3
"""
chirp_analysis.py — CLI front-end for Betaflight closed-loop chirp analysis.

This is a **thin wrapper**: all the compute (decode, Welch cross-spectral FRF,
step response, throttle x frequency resonance map, gyro noise spectrum, scoring
and the self-contained HTML report) lives in the `betaflight-chirp-core` package
— the single source of truth shared with the FPVLogForge Oracle worker.

  https://github.com/SebGalina/betaflight-chirp-core

The package is importable either because it is vendored next to this script
(`scripts/betaflight_chirp_core/`, added to the zip by build_skill_zip.py) or
because it is pip-installed in development (`pip install -e ../betaflight-chirp-core`).

What stays here is purely CLI: argument parsing, multi-pass history on disk
(`chirp_history.json`), and the human-readable text output.

Usage:
    python -m scripts.chirp_analysis <log.bbl>                   # text summary, all axes
    python -m scripts.chirp_analysis <log.bbl> --json            # machine-readable
    python -m scripts.chirp_analysis <log.bbl> --html report.html
    python -m scripts.chirp_analysis <log.bbl> --axis roll
    python -m scripts.chirp_analysis <log.bbl> --input-col setpoint
    python -m scripts.chirp_analysis <log.bbl> --session 2
"""

import argparse
import json
import sys
from pathlib import Path

# Vendored next to this script in the distributed skill; pip-installed in dev.
sys.path.insert(0, str(Path(__file__).parent))

from betaflight_chirp_core import (  # noqa: E402
    analyse_log,
    assemble_report,
    build_report,
    decode,
    noise_margin_db,
    signal,
)
from betaflight_chirp_core.analysis.chirp import (  # noqa: E402
    DEFAULT_FMAX,
    DEFAULT_FMIN,
    DEFAULT_INPUT_COL,
)
from betaflight_chirp_core.signal import AXES  # noqa: E402


# ---------------------------------------------------------------------------
# History (CLI-only: accumulate passes across runs in a JSON file on disk)
# ---------------------------------------------------------------------------
def _load_history(path: Path) -> list:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("passes", [])
    except (OSError, ValueError):
        return []


def _save_history(path: Path, passes: list) -> None:
    try:
        path.write_text(json.dumps({"passes": passes}), encoding="utf-8")
    except OSError as e:
        print(f"Warning: could not write history {path}: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Human-readable text output (CLI-only; consumes the assembled report dict)
# ---------------------------------------------------------------------------
def _print_human(report: dict, lang: str = "fr") -> None:
    def loc(o):
        return (o.get(lang) or o.get("fr") or o.get("en") or "") if isinstance(o, dict) else o

    passes = report.get("passes") or []
    if not passes:
        print("No passes to report.")
        return
    output = passes[report.get("primary_index", len(passes) - 1)]
    total = report.get("total_passes", len(passes))
    if total > 1:
        print(f"History     : {total} passes (showing latest as reference)")
    print(f"Sample rate : {output['sample_rate_hz']:,} Hz")
    print(f"Input column: {output['input_col']}")
    print(f"Band        : {output['band_hz'][0]:g}–{output['band_hz'][1]:g} Hz")
    print()
    if not output["axes"]:
        print("No chirp-excited axis found. Was the log recorded with debug_mode = CHIRP?")
        print("Try --input-col setpoint if the debug channel is absent.")
        return
    for axis, d in output["axes"].items():
        print(f"-- {axis.upper()} " + "-" * 30)
        print(f"  Input / band     : {d['input_col']}  [{d['band_hz'][0]:g}–{d['band_hz'][1]:g} Hz]  n={d['n_samples']}")
        if d.get("ms") is not None:
            print(f"  Sensitivity peak : Ms {d['ms']:.2f} @ {d['f_ms_hz']:.0f} Hz")
        # Guaranteed margin (robust: PM >= 2*asin(1/2Ms)) replaces the 0 dB-crossover margin, which
        # breaks down on damped axes (e.g. 165° @ 2 Hz). Fall back to the measured one only if Ms is absent.
        if d.get("pm_guaranteed_deg") is not None:
            print(f"  Phase margin     : >= {d['pm_guaranteed_deg']:.0f} deg (guaranteed from Ms)")
        elif d["phase_margin_deg"] is not None:
            mu = d.get("phase_margin_unc_deg")
            print(f"  Phase margin     : {d['phase_margin_deg']:.0f}{(' ±' + format(mu, '.0f')) if mu else ''} deg @ {d['crossover_hz']:.0f} Hz (measured; scalar fragile on damped axes)")
        else:
            print("  Phase margin     : no 0 dB crossover in coherent band")
        st = (d.get("step") or {}).get("metrics")
        if st:
            print(f"  Step response    : overshoot {st['overshoot_pct']}%  rise {st['rise_ms']} ms  settle {st['settle_ms']} ms")
        # Resonances in the closed-loop gain (full-resolution, coherent band) — list every one so a
        # suggestion can reason about how many and how tall, not just the worst-case scalar below.
        pks = d.get("peaks") or []
        if pks:
            print("  Gain resonances  : " + ", ".join(
                f"{p['freq_hz']:.0f} Hz {p['gain_db']:+.0f} dB (prom {p['prominence_db']:.0f})" for p in pks[:5]))
        nm = noise_margin_db(d)
        if nm is not None:
            print(f"  HF noise margin  : {nm:.0f} dB below 0 dB (worst HF gain incl. resonances; D-term ceiling)")
        print()
        for hint in d["diagnosis"]:
            print(f"  > {loc(hint)}")
        for hint in d.get("step_diagnosis", []):
            print(f"  > {loc(hint)}")
        print()
    tm = output.get("throttle_map") or {}
    if tm:
        print(f"Throttle map     : {tm['axis']} gyro, {len(tm['throttle_bins'])} throttle bins "
              f"× {len(tm['freqs'])} freqs (see --html / --json for the heatmap)")

    synth = output.get("synthesis") or []
    if synth:
        print("\n=== Lecture d'ensemble ===" if lang == "fr" else "\n=== Overview ===")
        for o in synth:
            print(f"  - {loc(o)}")

    flt_sug = output.get("filter_suggestions") or []
    noise = output.get("noise_spectrum") or {}
    if noise.get("peaks") or flt_sug:
        print("\n=== Filtering ===")
    if noise.get("peaks"):
        print(f"\nNoise PSD ({noise['axis']} gyro, 0 dB = noise floor):")
        for pk in noise["peaks"]:
            print(f"  {pk['freq_hz']:.0f} Hz : +{pk['above_floor_db']:.0f} dB over floor, "
                  f"-{pk['atten_db']:.0f} dB by filters, residual +{max(pk['resid_db'],0):.0f} dB")

    flt_all = flt_sug + (output.get("noise_suggestions") or [])
    if flt_all:
        print("\nFiltering:")
        for s in flt_all:
            print(f"  - {loc(s)}")

    if len(passes) > 1:
        print("\n=== Passes ===")
        for p in passes:
            label = f"Pass {p.get('n', '?')} — {p.get('ts', '')} ({p.get('file', '')})"
            if p.get("diff"):
                label += f"  [Δ {p['diff']}]"
            print(f"  {label}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _decode_input(path: Path, session):
    """One CLI input -> (df, fs, config). .bbl/.bfl decode in-core; CSV loads directly."""
    if path.suffix.lower() in (".bbl", ".bfl"):
        return decode(path.read_bytes(), session)
    df = signal.load_csv(path)
    return df, signal.sample_rate(df), {}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    ap = argparse.ArgumentParser(
        description="Betaflight closed-loop chirp frequency-response (Bode + coherence) analyser"
    )
    ap.add_argument("input", nargs="+",
                    help=".bbl/.bfl log(s) or decoded CSV. Several logs overlay as successive passes.")
    ap.add_argument("--axis", choices=AXES, help="Analyse a single axis (default: all excited axes)")
    ap.add_argument("--session", type=int, default=None, metavar="N",
                    help="Session index for multi-session logs")
    ap.add_argument("--input-col", default=DEFAULT_INPUT_COL, metavar="COL",
                    help=f"Excitation input column (default {DEFAULT_INPUT_COL}, the legacy "
                         f"firmware reference; auto-falls back to setpoint[i] when empty). "
                         f"'setpoint' forces setpoint[i] (calibrated); 'debug0' forces the "
                         f"reconstructed sine sin(debug[0]/5000) (shape only)")
    ap.add_argument("--fmin", type=float, default=DEFAULT_FMIN, metavar="HZ",
                    help=f"Lower edge of the analysis band (default {DEFAULT_FMIN:g})")
    ap.add_argument("--fmax", type=float, default=DEFAULT_FMAX, metavar="HZ",
                    help=f"Upper edge of the analysis band (default {DEFAULT_FMAX:g}, clamped to Nyquist)")
    ap.add_argument("--nperseg", type=int, default=None, metavar="N",
                    help="Welch window size in samples (default: auto, ~2 Hz resolution)")
    ap.add_argument("--lang", choices=("fr", "en"), default="fr",
                    help="Default language for the report (FR/EN switchable live in the HTML; "
                         "this sets the initial language and the text-mode language)")
    ap.add_argument("--json", action="store_true", help="Output as JSON")
    ap.add_argument("--html", metavar="OUT", help="Write a self-contained HTML Bode report")
    ap.add_argument("--history", metavar="FILE", default=None,
                    help="History JSON to accumulate passes into (default: chirp_history.json next "
                         "to the --html output, else in the working directory)")
    ap.add_argument("--no-history", action="store_true",
                    help="Do not read or write any history; report only the log(s) given now")
    args = ap.parse_args()

    # Analyse each input log into a self-contained "pass".
    new_passes = []
    for raw in args.input:
        path = Path(raw)
        df, fs, config = _decode_input(path, args.session)
        new_passes.append(analyse_log(
            df, fs, config, file=path.name, input_col=args.input_col,
            fmin=args.fmin, fmax=args.fmax, nperseg=args.nperseg, axis=args.axis))

    # History: accumulate unless disabled. The report shows the full (trimmed) history.
    if args.no_history:
        passes = new_passes
    else:
        if args.history:
            hist_path = Path(args.history)
        elif args.html:
            hist_path = Path(args.html).with_name("chirp_history.json")
        else:
            hist_path = Path("chirp_history.json")
        passes = _load_history(hist_path) + new_passes
        _save_history(hist_path, passes)
        print(f"History     : {len(passes)} passes total -> {hist_path}", file=sys.stderr)

    if args.html:
        Path(args.html).write_text(build_report(passes, args.lang), encoding="utf-8")
        print(f"Report written to {args.html}", file=sys.stderr)
    elif args.json:
        print(json.dumps(assemble_report(passes, args.lang), indent=2))
    else:
        _print_human(assemble_report(passes, args.lang), args.lang)


if __name__ == "__main__":
    main()
