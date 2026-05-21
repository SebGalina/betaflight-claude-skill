#!/usr/bin/env python3
"""
step_response.py -Betaflight closed-loop step response analyser.

Estimates the step response of the PID loop (setpoint → gyro) per axis using
Welch's averaged cross-spectral density method -the same signal-processing
approach used by PIDToolbox:

    H(f)  = Pxy(f) / Pxx(f)          # transfer function estimate
    h(t)  = IFFT(H)                   # impulse response
    s(t)  = cumsum(h) * dt            # step response

Using Welch's method (averaged periodograms) rather than naive step detection
gives a statistically robust result from the entire flight, not just hand-picked
"clean" stick inputs.

Requires: numpy, scipy, pandas

Usage:
    python step_response.py <log.bbl>                  # text report, all axes
    python step_response.py <log.bbl> --axis roll      # single axis
    python step_response.py <log.bbl> --session 2      # multi-session log
    python step_response.py <decoded.csv>              # from analyze_blackbox --csv
    python step_response.py <log.bbl> --json           # machine-readable output
    python step_response.py <log.bbl> --csv curves.csv # export response curves
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

# ---------------------------------------------------------------------------
# Column names in the decoded CSV (blackbox_decoder output)
# ---------------------------------------------------------------------------
TIME_COL = "time"
SETPOINT_COLS = ["setpoint[0]", "setpoint[1]", "setpoint[2]"]  # roll, pitch, yaw
GYRO_COLS     = ["gyroADC[0]",  "gyroADC[1]",  "gyroADC[2]"]
THROTTLE_COL  = "rcCommand[3]"
AXES = ["roll", "pitch", "yaw"]

# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _decode_bbl(bbl_path: Path, session=None) -> Path:
    """Decode a .bbl/.bfl to a temporary CSV via analyze_blackbox.py."""
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
    """Estimate loop rate from the time column (microseconds)."""
    dt_us = float(np.median(np.diff(df[TIME_COL].values[:4000])))
    return 1_000_000.0 / dt_us


def _active_mask(df: pd.DataFrame, throttle_min: int = 1100) -> np.ndarray:
    """Keep only frames where the craft is actively flying (throttle above idle)."""
    if THROTTLE_COL in df.columns:
        return df[THROTTLE_COL].values > throttle_min
    return np.ones(len(df), dtype=bool)

# ---------------------------------------------------------------------------
# Step response via Welch's cross-spectral method
# ---------------------------------------------------------------------------

def _welch_step_response(
    setpoint: np.ndarray,
    gyro: np.ndarray,
    fs: float,
    nperseg: int | None = None,
    regularize: float = 1e-5,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Estimate step response from arbitrary input/output signals.

    Returns:
        times_ms  -time axis for the step response, in milliseconds
        step      -normalized step response (1.0 = steady state)
        freqs     -frequency axis (Hz) for the coherence output
        coherence -signal coherence per frequency band (quality indicator)
    """
    if nperseg is None:
        # ~64 ms window, rounded to nearest power of 2
        nperseg = int(2 ** round(np.log2(fs * 0.064)))
        nperseg = max(256, min(nperseg, 4096))

    # Detrend
    sp = sp_signal.detrend(setpoint.astype(float))
    gy = sp_signal.detrend(gyro.astype(float))

    # Welch's cross-PSD and auto-PSD
    f, Pxx = sp_signal.welch(sp, fs=fs, nperseg=nperseg, window="hann")
    _, Pxy = sp_signal.csd(sp, gy, fs=fs, nperseg=nperseg, window="hann")
    _, Cxy = sp_signal.coherence(sp, gy, fs=fs, nperseg=nperseg)

    # Transfer function estimate H(f) = Pxy / Pxx, regularised
    reg = regularize * float(np.max(np.abs(Pxx)))
    H = Pxy / (Pxx + reg)

    # Impulse response via IFFT, then integrate to step response
    h = np.fft.irfft(H)
    dt = 1.0 / fs
    step = np.cumsum(h) * dt

    # Normalise to steady state (mean of last 30 % of the window)
    n = len(step)
    ss = float(np.mean(step[int(0.7 * n):]))
    if abs(ss) > 1e-9:
        step = step / ss

    times_ms = np.arange(n) * dt * 1000.0
    return times_ms, step, f, Cxy

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _metrics(times_ms: np.ndarray, step: np.ndarray) -> dict:
    """
    Standard step response metrics.

    Rise time (10 %→90 %), overshoot %, settling time (±2 % band), delay (50 %).
    """
    n = len(step)
    ss = float(np.mean(step[int(0.7 * n):]))
    if abs(ss) < 1e-9:
        return {}

    sn = step / ss  # normalised to 1.0

    result: dict = {}

    # Delay -first crossing of 50 %
    idx_50 = int(np.argmax(sn >= 0.5))
    result["delay_ms"] = round(float(times_ms[idx_50]), 1) if idx_50 > 0 else None

    # Rise time -10 % to 90 %
    idx_10 = int(np.argmax(sn >= 0.1))
    idx_90 = int(np.argmax(sn >= 0.9))
    if idx_10 > 0 and idx_90 > idx_10:
        result["rise_time_ms"] = round(float(times_ms[idx_90] - times_ms[idx_10]), 1)
    else:
        result["rise_time_ms"] = None

    # Overshoot
    peak = float(np.max(sn))
    result["overshoot_pct"] = round((peak - 1.0) * 100.0, 1) if peak > 1.0 else 0.0

    # Settling time -last exit from ±2 % band
    out = np.where(np.abs(sn - 1.0) > 0.02)[0]
    if len(out):
        last = int(out[-1])
        result["settling_time_ms"] = round(
            float(times_ms[last + 1]) if last + 1 < n else float(times_ms[-1]), 1
        )
    else:
        result["settling_time_ms"] = round(float(times_ms[0]), 1)

    return result


# ---------------------------------------------------------------------------
# Diagnosis
# ---------------------------------------------------------------------------

def _diagnose(m: dict) -> list[str]:
    hints = []
    rt   = m.get("rise_time_ms")
    os_p = m.get("overshoot_pct", 0.0)
    st   = m.get("settling_time_ms")
    dl   = m.get("delay_ms")

    if rt is not None:
        if rt < 8:
            hints.append(f"Rise time very fast ({rt} ms) - risk of P oscillation or FF too high")
        elif rt < 15:
            hints.append(f"Rise time good ({rt} ms)")
        elif rt < 30:
            hints.append(f"Rise time acceptable ({rt} ms) - raise FF slightly if response feels sluggish")
        else:
            hints.append(f"Slow rise ({rt} ms) - raise FF first, then P")

    if os_p > 25:
        hints.append(f"High overshoot {os_p:.0f} % - lower P or raise D")
    elif os_p > 12:
        hints.append(f"Moderate overshoot {os_p:.0f} % - consider raising D slightly")
    elif os_p < 2 and (rt or 0) > 25:
        hints.append("Underdamped -raise P or FF")
    else:
        hints.append(f"Overshoot clean ({os_p:.0f} %)")

    if rt is not None and st is not None and (st - rt) > 50:
        hints.append(f"Long settling ({st} ms) - I too high or iterm_relax_cutoff too high")

    if dl is not None:
        if dl > 15:
            hints.append(f"High latency ({dl} ms) - filters may be too aggressive")
        elif dl < 5:
            hints.append(f"Low latency ({dl} ms) - filters are light (good)")

    return hints or ["Step response looks clean"]


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def analyse(df: pd.DataFrame, fs: float, axes_filter=None) -> dict:
    mask = _active_mask(df)
    results: dict = {}

    for i, axis in enumerate(AXES):
        if axes_filter and axis not in axes_filter:
            continue
        sp_col = SETPOINT_COLS[i]
        gy_col = GYRO_COLS[i]
        if sp_col not in df.columns or gy_col not in df.columns:
            continue

        sp = df.loc[mask, sp_col].to_numpy(float)
        gy = df.loc[mask, gy_col].to_numpy(float)
        if len(sp) < 512:
            continue

        times_ms, step, freqs, coh = _welch_step_response(sp, gy, fs)
        m = _metrics(times_ms, step)

        # Mean coherence in the PID-relevant band (5–80 Hz)
        band = (freqs >= 5) & (freqs <= 80)
        mean_coh = float(np.mean(coh[band])) if band.any() else float(np.mean(coh))

        results[axis] = {
            **m,
            "mean_coherence": round(mean_coh, 3),
            "diagnosis": _diagnose(m),
            # first 80 ms of step response curve (time_ms, normalised_value)
            "step_response": [
                [round(float(t), 2), round(float(s), 4)]
                for t, s in zip(times_ms, step)
                if t <= 80.0
            ],
        }

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Betaflight step response analyser (Welch cross-spectral method)"
    )
    ap.add_argument("input", help=".bbl/.bfl log or decoded CSV from analyze_blackbox --csv")
    ap.add_argument("--axis", choices=AXES, help="Analyse a single axis (default: all)")
    ap.add_argument("--session", type=int, default=None, metavar="N",
                    help="Session index for multi-session logs")
    ap.add_argument("--json", action="store_true", help="Output as JSON")
    ap.add_argument("--csv", metavar="OUT", help="Write step response curves to CSV")
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
        axes_filter = [args.axis] if args.axis else None
        results = analyse(df, fs, axes_filter)

        output = {"sample_rate_hz": round(fs), "axes": results}

        if args.json:
            print(json.dumps(output, indent=2))
        elif args.csv:
            rows = [
                {"axis": axis, "time_ms": t, "response": v}
                for axis, data in results.items()
                for t, v in data.get("step_response", [])
            ]
            if rows:
                pd.DataFrame(rows).to_csv(args.csv, index=False)
                print(f"Curves written to {args.csv}", file=sys.stderr)
        else:
            # Human-readable report
            print(f"Sample rate : {round(fs):,} Hz")
            print(f"Active frames : {int((_active_mask(df)).sum()):,} / {len(df):,}")
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
    finally:
        if tmp_csv:
            tmp_csv.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
