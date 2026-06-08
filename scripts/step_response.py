#!/usr/bin/env python3
"""
step_response.py — CLI front-end for Betaflight step response analysis.

Thin wrapper: the compute (Welch cross-spectral step response, bandpass,
active-frame masking, metrics, diagnosis, chart payload, optional matplotlib
figure) lives in `betaflight_chirp_core.analysis.step` — the single source of
truth shared with the FPVLogForge Oracle worker.
See https://github.com/SebGalina/betaflight-chirp-core

What stays here is purely CLI: argument parsing and human-readable text output.

Usage:
    python -m scripts.step_response <log.bbl>
    python -m scripts.step_response <log.bbl> --axis roll --bandpass --active-only
    python -m scripts.step_response <log.bbl> --json | --chart | --csv out.csv
"""

import argparse
import json
import sys
from pathlib import Path

# Vendored next to this script in the distributed skill; pip-installed in dev.
sys.path.insert(0, str(Path(__file__).parent))

from betaflight_chirp_core import decode, signal  # noqa: E402
from betaflight_chirp_core.analysis import step  # noqa: E402
from betaflight_chirp_core.signal import AXES  # noqa: E402


def _load(path: Path, session):
    if path.suffix.lower() in (".bbl", ".bfl"):
        df, fs, _ = decode(path.read_bytes(), session)
        return df, fs
    df = signal.load_csv(path)
    return df, signal.sample_rate(df)


def main():
    ap = argparse.ArgumentParser(
        description="Betaflight step response analyser (Welch cross-spectral method)"
    )
    ap.add_argument("input", help=".bbl/.bfl log or decoded CSV from analyze_blackbox --csv")
    ap.add_argument("--axis", choices=AXES, help="Analyse a single axis (default: all)")
    ap.add_argument("--session", type=int, default=None, metavar="N",
                    help="Session index for multi-session logs")
    ap.add_argument("--json", action="store_true", help="Output as JSON")
    ap.add_argument("--chart", action="store_true",
                    help="Emit a generate_line_chart payload for the AntV mcp-server-chart")
    ap.add_argument("--csv", metavar="OUT", help="Write step response curves to CSV")
    # Signal quality flags
    ap.add_argument("--bandpass", action="store_true",
                    help="Bandpass 5-80 Hz before Welch (cuts DC drift and high-freq noise)")
    ap.add_argument("--active-only", action="store_true",
                    help="Keep only frames around fast stick inputs (improves coherence on noisy logs)")
    ap.add_argument("--nperseg", type=int, default=None, metavar="N",
                    help="Welch window size in samples (default: auto ~64 ms, power of 2)")
    ap.add_argument("--plot", action="store_true",
                    help="Render step response + coherence figure (requires matplotlib)")
    args = ap.parse_args()

    path = Path(args.input)
    df, fs = _load(path, args.session)
    axes_filter = [args.axis] if args.axis else None
    results = step.analyse(
        df, fs, axes_filter,
        bandpass=args.bandpass,
        active_only=args.active_only,
        nperseg=args.nperseg,
    )

    flags = []
    if args.bandpass:    flags.append("bandpass")
    if args.active_only: flags.append("active-only")
    if args.nperseg:     flags.append(f"nperseg={args.nperseg}")
    output = {
        "sample_rate_hz": round(fs),
        "flags": flags,
        "axes": results,
    }

    if args.plot:
        flags_str = f"  [{', '.join(flags)}]" if flags else ""
        step.plot_results(output, title_suffix=f" — {path.name}{flags_str}")

    if args.chart:
        print(json.dumps(step.chart_payload(output, path.name), indent=2))
    elif args.json:
        print(json.dumps(output, indent=2))
    elif args.csv:
        rows = [
            {"axis": axis, "time_ms": t, "response": v}
            for axis, data in results.items()
            for t, v in data.get("step_response", [])
        ]
        if rows:
            import pandas as pd
            pd.DataFrame(rows).to_csv(args.csv, index=False)
            print(f"Curves written to {args.csv}", file=sys.stderr)
    else:
        # Human-readable report
        base_mask = signal.active_mask(df)
        used_mask = step.active_only_mask(df, fs) if args.active_only else base_mask
        print(f"Sample rate   : {round(fs):,} Hz")
        print(f"Active frames : {int(used_mask.sum()):,} / {len(df):,}", end="")
        if flags:
            print(f"  [{', '.join(flags)}]", end="")
        print()
        print()
        for axis, data in results.items():
            rt  = data.get("rise_time_ms")
            osp = data.get("overshoot_pct", 0)
            st  = data.get("settling_time_ms")
            dl  = data.get("delay_ms")
            coh = data.get("mean_coherence", 0)
            print(f"-- {axis.upper()} " + "-" * 30)
            print(f"  Rise time     : {rt} ms"          if rt  else "  Rise time     : n/a")
            print(f"  Overshoot     : {osp} %")
            print(f"  Settling time : {st} ms"          if st  else "  Settling time  : n/a")
            print(f"  Delay         : {dl} ms"          if dl  else "  Delay          : n/a")
            print(f"  Coherence     : {coh:.2f}  (> 0.7 = reliable, < 0.5 = noisy data)")
            print()
            for hint in data.get("diagnosis", []):
                print(f"  > {hint}")
            print()


if __name__ == "__main__":
    main()
