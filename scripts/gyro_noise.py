#!/usr/bin/env python3
"""
gyro_noise.py — Betaflight gyro and motor noise spectrum analyzer.

Computes power spectral density (Welch method) of gyro (pre- and post-filter)
and motor outputs. Equivalent to the noise analysis tab in Blackbox Explorer.

Fields used:
  gyroUnfilt[0/1/2]  — gyro before software filters (needs blackbox_disable_gyrounfilt=OFF)
  gyroADC[0/1/2]     — gyro after all software filters (LPF, notch, RPM filter)
  motor[0/1/2/3]     — motor outputs

Requires: numpy, scipy, pandas
Optional:  matplotlib (for --plot)

Usage:
    python -m scripts.gyro_noise log.bbl                    # text report
    python -m scripts.gyro_noise log.bbl --plot             # frequency plot
    python -m scripts.gyro_noise log.bbl --axis roll        # single axis
    python -m scripts.gyro_noise log.bbl --max-freq 500     # limit to 500 Hz
    python -m scripts.gyro_noise log.bbl --no-motors        # skip motor spectrum
    python -m scripts.gyro_noise log.bbl --csv spectra.csv  # export PSD data
    python -m scripts.gyro_noise log.bbl --json             # JSON output
    python -m scripts.gyro_noise log.bbl --session 2        # multi-session log
    python -m scripts.gyro_noise decoded.csv                # from analyze_blackbox --csv
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import signal as sp_signal

TIME_COL         = "time"
AXES             = ["roll", "pitch", "yaw"]
GYRO_FILT_COLS   = ["gyroADC[0]",    "gyroADC[1]",    "gyroADC[2]"]
GYRO_UNFILT_COLS = ["gyroUnfilt[0]", "gyroUnfilt[1]", "gyroUnfilt[2]"]
MOTOR_COLS       = ["motor[0]", "motor[1]", "motor[2]", "motor[3]"]

# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _decode_bbl(bbl_path: Path, session=None) -> Path:
    script = Path(__file__).parent / "analyze_blackbox.py"
    tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    tmp.close()
    cmd = [sys.executable, str(script), str(bbl_path), "--csv", tmp.name]
    if session is not None:
        cmd += ["--session", str(session)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"analyze_blackbox failed:\n{r.stderr}")
    return Path(tmp.name)


def _load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, comment="#")
    df.columns = [c.strip() for c in df.columns]
    return df


def _sample_rate(df: pd.DataFrame) -> float:
    dt_us = float(np.median(np.diff(df[TIME_COL].values[:4000])))
    return 1_000_000.0 / dt_us


# ---------------------------------------------------------------------------
# PSD computation
# ---------------------------------------------------------------------------

def _welch_psd(
    sig: np.ndarray, fs: float, nperseg: int | None
) -> tuple[np.ndarray, np.ndarray]:
    """Return (freqs_hz, psd_db) using Welch's averaged periodogram."""
    if nperseg is None:
        # Target ~1 Hz frequency resolution, power of 2
        nperseg = int(2 ** round(np.log2(fs)))
        nperseg = max(512, min(nperseg, 8192))
    f, Pxx = sp_signal.welch(sig.astype(float), fs=fs, nperseg=nperseg, window="hann")
    psd_db = 10.0 * np.log10(np.maximum(Pxx, 1e-10))
    return f, psd_db


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyse(
    df: pd.DataFrame,
    fs: float,
    axes_filter=None,
    include_motors: bool = True,
    max_freq: float = 1000.0,
    nperseg: int | None = None,
) -> dict:
    """
    Compute gyro and motor PSD spectra.

    Returns a dict with keys:
      sample_rate_hz, gyro {axis: {filtered, unfiltered, peaks}}, motors {col: {psd, peak}}
    """
    results: dict = {
        "sample_rate_hz": round(fs),
        "gyro": {},
        "motors": {},
    }

    for i, axis in enumerate(AXES):
        if axes_filter and axis not in axes_filter:
            continue

        filt_col   = GYRO_FILT_COLS[i]
        unfilt_col = GYRO_UNFILT_COLS[i]

        if filt_col not in df.columns:
            continue

        f, psd_filt = _welch_psd(df[filt_col].to_numpy(), fs, nperseg)
        freq_mask = f <= max_freq
        f_trim       = f[freq_mask]
        pf_trim      = psd_filt[freq_mask]

        entry: dict = {
            "filtered": {
                "freqs_hz": [round(float(x), 2) for x in f_trim],
                "psd_db":   [round(float(x), 2) for x in pf_trim],
            },
            "unfiltered": None,
            "peak_filtered_hz":     round(float(f_trim[int(np.argmax(pf_trim))]), 1),
            "peak_unfiltered_hz":   None,
            "filter_attenuation_db": None,
        }

        if unfilt_col in df.columns:
            _, psd_unfilt = _welch_psd(df[unfilt_col].to_numpy(), fs, nperseg)
            pu_trim = psd_unfilt[freq_mask]
            peak_idx = int(np.argmax(pu_trim))
            entry["unfiltered"] = {
                "freqs_hz": entry["filtered"]["freqs_hz"],
                "psd_db":   [round(float(x), 2) for x in pu_trim],
            }
            entry["peak_unfiltered_hz"]    = round(float(f_trim[peak_idx]), 1)
            entry["filter_attenuation_db"] = round(float(pu_trim[peak_idx] - pf_trim[peak_idx]), 1)

        results["gyro"][axis] = entry

    if include_motors:
        for col in MOTOR_COLS:
            if col not in df.columns:
                continue
            f, psd = _welch_psd(df[col].to_numpy(), fs, nperseg)
            freq_mask = f <= max_freq
            f_trim  = f[freq_mask]
            pd_trim = psd[freq_mask]
            results["motors"][col] = {
                "freqs_hz": [round(float(x), 2) for x in f_trim],
                "psd_db":   [round(float(x), 2) for x in pd_trim],
                "peak_hz":  round(float(f_trim[int(np.argmax(pd_trim))]), 1),
            }

    return results


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------

def _print_report(results: dict) -> None:
    fs = results["sample_rate_hz"]
    print(f"Sample rate : {fs:,} Hz  (Nyquist: {fs // 2:,} Hz)")
    print()

    gyro = results.get("gyro", {})
    if gyro:
        has_unfilt = any(d.get("unfiltered") for d in gyro.values())
        if has_unfilt:
            print(f"Gyro noise — {'Axis':<8} {'Peak unfilt.':<16} {'Peak filt.':<16} {'Filter atten.'}")
            for axis, d in gyro.items():
                pu = f"{d['peak_unfiltered_hz']} Hz" if d["peak_unfiltered_hz"] is not None else "n/a"
                pf = f"{d['peak_filtered_hz']} Hz"
                at = f"{d['filter_attenuation_db']} dB" if d["filter_attenuation_db"] is not None else "n/a"
                print(f"             {axis:<8} {pu:<16} {pf:<16} {at}")
        else:
            print(f"Gyro noise — {'Axis':<8} {'Peak filt.'}")
            for axis, d in gyro.items():
                print(f"             {axis:<8} {d['peak_filtered_hz']} Hz")
            print()
            print("  Note: gyroUnfilt not found in this log.")
            print("  Verify that blackbox_disable_gyrounfilt = OFF (Betaflight default).")

    motors = results.get("motors", {})
    if motors:
        print()
        print(f"Motor noise — {'Motor':<10} {'Peak'}")
        for col, d in motors.items():
            print(f"              {col:<10} {d['peak_hz']} Hz")


# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------

def _plot_results(results: dict, title_suffix: str = "") -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        sys.exit("matplotlib required for --plot: pip install matplotlib")

    gyro   = results.get("gyro", {})
    motors = results.get("motors", {})
    n_panels = 1 + (1 if motors else 0)

    fig, panel_axes = plt.subplots(n_panels, 1, figsize=(12, 5 * n_panels),
                                   squeeze=False)
    fig.suptitle(f"Betaflight Noise Spectrum{title_suffix}", fontsize=13, fontweight="bold")

    GYRO_COLORS  = {"roll": "#1f77b4", "pitch": "#ff7f0e", "yaw": "#2ca02c"}
    MOTOR_COLORS = ["#d62728", "#9467bd", "#8c564b", "#e377c2"]

    ax_g = panel_axes[0][0]
    for axis, d in gyro.items():
        color = GYRO_COLORS.get(axis, "gray")
        fq = d["filtered"]["freqs_hz"]
        ax_g.plot(fq, d["filtered"]["psd_db"],
                  color=color, linewidth=1.6, label=f"{axis} filtered")
        if d.get("unfiltered"):
            ax_g.plot(fq, d["unfiltered"]["psd_db"],
                      color=color, linewidth=1.0, linestyle="--", alpha=0.6,
                      label=f"{axis} unfiltered")

    ax_g.set_xlabel("Frequency (Hz)")
    ax_g.set_ylabel("PSD (dB)")
    ax_g.set_title("Gyro — filtered (solid) vs unfiltered (dashed)", fontsize=10)
    ax_g.legend(fontsize=8, ncol=2)
    ax_g.grid(True, alpha=0.3)

    if motors:
        ax_m = panel_axes[1][0]
        for i, (col, d) in enumerate(motors.items()):
            ax_m.plot(d["freqs_hz"], d["psd_db"],
                      color=MOTOR_COLORS[i % len(MOTOR_COLORS)], linewidth=1.5,
                      label=col)
        ax_m.set_xlabel("Frequency (Hz)")
        ax_m.set_ylabel("PSD (dB)")
        ax_m.set_title("Motor output spectrum", fontsize=10)
        ax_m.legend(fontsize=8)
        ax_m.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def _write_csv(results: dict, dest: str) -> None:
    rows = []
    for axis, d in results.get("gyro", {}).items():
        fq = d["filtered"]["freqs_hz"]
        for f, p in zip(fq, d["filtered"]["psd_db"]):
            rows.append({"source": f"gyro_{axis}", "series": "filtered",
                         "freq_hz": f, "psd_db": p})
        if d.get("unfiltered"):
            for f, p in zip(fq, d["unfiltered"]["psd_db"]):
                rows.append({"source": f"gyro_{axis}", "series": "unfiltered",
                             "freq_hz": f, "psd_db": p})
    for col, d in results.get("motors", {}).items():
        for f, p in zip(d["freqs_hz"], d["psd_db"]):
            rows.append({"source": col, "series": "motor", "freq_hz": f, "psd_db": p})

    if not rows:
        print("No spectral data to export.", file=sys.stderr)
        return
    df_out = pd.DataFrame(rows)
    if dest == "-":
        df_out.to_csv(sys.stdout, index=False)
    else:
        df_out.to_csv(dest, index=False)
        print(f"Spectral data written to {dest}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Betaflight gyro and motor noise spectrum analyzer"
    )
    ap.add_argument("input", help=".bbl/.bfl log or decoded CSV from analyze_blackbox --csv")
    ap.add_argument("--axis", choices=AXES, help="Single axis (default: all)")
    ap.add_argument("--no-motors", action="store_true", help="Skip motor spectrum")
    ap.add_argument("--max-freq", type=float, default=1000.0, metavar="HZ",
                    help="Frequency ceiling in Hz (default: 1000)")
    ap.add_argument("--nperseg", type=int, default=None, metavar="N",
                    help="Welch window size in samples (default: auto ~1 Hz resolution)")
    ap.add_argument("--session", type=int, default=None, metavar="N",
                    help="Session index for multi-session logs")
    ap.add_argument("--plot", action="store_true",
                    help="Render matplotlib figure (requires matplotlib)")
    ap.add_argument("--csv", metavar="OUT",
                    help="Export PSD data to CSV ('-' for stdout)")
    ap.add_argument("--json", action="store_true", help="Output as JSON")
    args = ap.parse_args()

    path = Path(args.input)
    tmp_csv = None
    if path.suffix.lower() in (".bbl", ".bfl"):
        tmp_csv = _decode_bbl(path, args.session)
        df = _load_csv(tmp_csv)
    else:
        df = _load_csv(path)

    try:
        fs = _sample_rate(df)
        max_freq = min(args.max_freq, fs / 2.0)
        axes_filter = [args.axis] if args.axis else None

        results = analyse(
            df, fs,
            axes_filter=axes_filter,
            include_motors=not args.no_motors,
            max_freq=max_freq,
            nperseg=args.nperseg,
        )

        if args.plot:
            _plot_results(results, title_suffix=f" — {path.name}")

        if args.json:
            print(json.dumps(results, indent=2))
        elif args.csv:
            _write_csv(results, args.csv)
        else:
            _print_report(results)

    finally:
        if tmp_csv:
            tmp_csv.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
