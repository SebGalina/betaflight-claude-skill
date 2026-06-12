#!/usr/bin/env python3
"""
spectral_analysis.py — CLI front-end for Betaflight noise spectrum / FFT analysis.

Thin wrapper: the compute (PSD, peak detection, harmonic grouping, band RMS,
diagnosis, chart payload, optional matplotlib figure) lives in
`betaflight_chirp_core.analysis.spectral` — the single source of truth shared
with the FPVLogForge Oracle worker. See https://github.com/SebGalina/betaflight-chirp-core

What stays here is purely CLI: argument parsing and human-readable text output.

Usage:
    python -m scripts.spectral_analysis <log.bbl>
    python -m scripts.spectral_analysis <log.bbl> --signal gyro --axis roll
    python -m scripts.spectral_analysis <log.bbl> --json | --chart | --csv out.csv
"""

import argparse
import json
import sys
from pathlib import Path

# Vendored next to this script in the distributed skill; pip-installed in dev.
sys.path.insert(0, str(Path(__file__).parent))

from betaflight_chirp_core import decode, signal  # noqa: E402
from betaflight_chirp_core.analysis import spectral  # noqa: E402
from betaflight_chirp_core.signal import AXES  # noqa: E402


def _load(path: Path, session):
    if path.suffix.lower() in (".bbl", ".bfl"):
        df, fs, _ = decode(path.read_bytes(), session)
        return df, fs
    df = signal.load_csv(path)
    return df, signal.sample_rate(df)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    ap = argparse.ArgumentParser(
        description="Betaflight noise spectrum / FFT peak & harmonic analyser"
    )
    ap.add_argument("input", help=".bbl/.bfl log or decoded CSV from analyze_blackbox --csv")
    ap.add_argument("--signal", choices=list(spectral.SIGNALS), default="gyro",
                    help="Signal to analyse (default: gyro)")
    ap.add_argument("--axis", choices=AXES, help="Analyse a single axis (default: all)")
    ap.add_argument("--session", type=int, default=None, metavar="N",
                    help="Session index for multi-session logs")
    ap.add_argument("--fmin", type=float, default=spectral.DEFAULT_FMIN, metavar="HZ",
                    help=f"Lower edge of the analysis band (default {spectral.DEFAULT_FMIN:g})")
    ap.add_argument("--fmax", type=float, default=spectral.DEFAULT_FMAX, metavar="HZ",
                    help=f"Upper edge of the analysis band (default {spectral.DEFAULT_FMAX:g}, clamped to Nyquist)")
    ap.add_argument("--nperseg", type=int, default=None, metavar="N",
                    help="Welch window size in samples (default: auto ~4 Hz resolution)")
    ap.add_argument("--json", action="store_true", help="Output as JSON")
    ap.add_argument("--chart", action="store_true",
                    help="Emit a generate_line_chart payload for the AntV mcp-server-chart")
    ap.add_argument("--csv", metavar="OUT", help="Write spectra to CSV (axis, freq_hz, level_db)")
    ap.add_argument("--plot", action="store_true",
                    help="Render PSD + spectrogram figure (requires matplotlib)")
    args = ap.parse_args()

    path = Path(args.input)
    df, fs = _load(path, args.session)
    axes_filter = [args.axis] if args.axis else None
    results = spectral.analyse(
        df, fs, args.signal, axes_filter,
        fmin=args.fmin, fmax=args.fmax, nperseg=args.nperseg,
    )
    nyq = fs / 2.0
    output = {
        "sample_rate_hz": round(fs),
        "signal": args.signal,
        "band_hz": [args.fmin, round(min(args.fmax, nyq * 0.98), 1)],
        "axes": results,
    }

    if args.plot:
        spectral.plot_results(df, fs, args.signal, output, args.fmin,
                              min(args.fmax, nyq * 0.98), title_suffix=f" — {path.name}")

    if args.chart:
        print(json.dumps(spectral.chart_payload(output, path.name), indent=2))
    elif args.json:
        print(json.dumps(output, indent=2))
    elif args.csv:
        rows = [
            {"axis": axis, "freq_hz": f, "level_db": d}
            for axis, data in results.items()
            for f, d in data.get("spectrum", [])
        ]
        if rows:
            import pandas as pd
            pd.DataFrame(rows).to_csv(args.csv, index=False)
            print(f"Spectra written to {args.csv}", file=sys.stderr)
    else:
        _print_human(output)


def _print_human(output: dict) -> None:
    print(f"Sample rate : {output['sample_rate_hz']:,} Hz")
    print(f"Signal      : {output['signal']}")
    print(f"Band        : {output['band_hz'][0]:g}–{output['band_hz'][1]:g} Hz")
    print()
    for axis, data in output["axes"].items():
        print(f"-- {axis.upper()} " + "-" * 30)
        print(f"  Noise floor      : {data['noise_floor_db']} dB")
        print(f"  Band RMS         : {data['band_rms']}")
        print(f"  Broadband ratio  : {data['broadband_ratio']:.0%}")

        if data["harmonic_series"]:
            print("  Harmonic series  :")
            for s in data["harmonic_series"]:
                fam = ", ".join(f"{m['freq_hz']:.0f}Hz" for m in s["members"])
                print(f"    f0={s['fundamental_hz']:.0f} Hz  [{fam}]")
        if data["isolated_peaks"]:
            tops = data["isolated_peaks"][:5]
            peaks_str = ", ".join(
                f"{p['freq_hz']:.0f}Hz(+{p['prominence_db']:.0f}dB)" for p in tops
            )
            print(f"  Isolated peaks   : {peaks_str}")
        if not data["peaks"]:
            print("  Peaks            : none above threshold")
        print()
        for hint in data.get("diagnosis", []):
            print(f"  > {hint}")
        print()


if __name__ == "__main__":
    main()
