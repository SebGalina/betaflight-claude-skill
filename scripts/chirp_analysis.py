#!/usr/bin/env python3
"""
chirp_analysis.py — Betaflight closed-loop chirp frequency-response (Bode) analyser.

Betaflight's chirp generator (`set debug_mode = CHIRP`) adds a swept sine straight
onto `currentPidSetpoint`, cycling roll -> pitch -> yaw, and logs the excitation in
the debug channels. This script turns such a log into a per-axis frequency-response
diagnosis: the transfer function H(f) = setpoint -> gyro estimated by Welch's
cross-spectral method, presented as a Bode plot (gain dB + phase deg) with the
coherence as a per-frequency reliability gate, plus a throttle x frequency
resonance map (the plant is non-stationary: resonances migrate with throttle).

Firmware debug-field mapping — TWO generations, auto-detected:

  Legacy (commit 1fc6ad23, early USE_CHIRP):
    debug[0] = 5000 * sinarg          phase of the excitation (0..2pi)
    debug[1] = active chirp axis      0=roll, 1=pitch, 2=yaw, -1=inactive
    debug[2] = 10 * fchirp            instantaneous chirp frequency in deci-Hz
    debug[3] = 1000 * chirp           raw chirp excitation (pre phase-comp) — FRF reference

  Current (BF 2025.12.3-alpha, db7df6e48 and later): the CHIRP section logs ONLY
    debug[0] = 5000 * sinarg.         debug[1..3] are gone (all zero in the log).

So everything the legacy path read from debug[1..3] is reconstructed from debug[0]:
  - excitation phase / pure sine : sin(debug[0]/5000)
  - instantaneous frequency      : d/dt unwrap(debug[0]/5000) / 2pi   (replaces debug[2])
  - active chirp axis            : argmax setpoint variance per window (replaces debug[1];
                                   the chirp excites one axis at a time, so it dominates)

We use gyroADC[i] as the output y. The input x defaults to the firmware reference
debug[3] when that channel carries signal (legacy logs); otherwise — current
firmware — it falls back to setpoint[i], the *calibrated* injected signal (deg/s),
which yields a closed-loop FRF that sits near 0 dB at low frequency so the phase
margin and 0 dB crossover are readable. --input-col debug0 forces the reconstructed
unit sine (shape only, uncalibrated gain); --input-col setpoint forces setpoint[i].

Requires: numpy, scipy, pandas  (no plotting libs — the --html report is self-contained)

Usage:
    python chirp_analysis.py <log.bbl>                       # text summary, all axes
    python chirp_analysis.py <log.bbl> --json                # machine-readable
    python chirp_analysis.py <log.bbl> --html report.html    # self-contained Bode report
    python chirp_analysis.py <log.bbl> --axis roll           # single axis
    python chirp_analysis.py <log.bbl> --input-col setpoint  # use setpoint[i] as input
    python chirp_analysis.py <log.bbl> --session 2           # multi-session log
"""

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import signal as sp_signal

# ---------------------------------------------------------------------------
# Column names in the decoded CSV (blackbox_decoder output)
# ---------------------------------------------------------------------------
TIME_COL = "time"
THROTTLE_COL = "rcCommand[3]"
AXES = ["roll", "pitch", "yaw"]
GYRO_COL = "gyroADC[{}]"
SETPOINT_COL = "setpoint[{}]"
CHIRP_AXIS_COL = "debug[1]"     # 0=roll 1=pitch 2=yaw -1=inactive (legacy only)
CHIRP_FREQ_COL = "debug[2]"     # deci-Hz (legacy only)
DEFAULT_INPUT_COL = "debug[3]"  # raw chirp excitation x1000 (legacy FRF reference)
PHASE_COL = "debug[0]"          # 5000 * sinarg — always present under debug_mode=CHIRP
PHASE_SCALE = 5000.0            # debug[0] = 5000 * sinarg (phase 0..2pi)

# Reconstruction-mode (current firmware) axis segmentation by setpoint energy
ENERGY_WIN_S = 0.3             # sliding window for per-axis energy labelling
ENERGY_STD_FLOOR = 2.0        # min setpoint std (deg/s) to call a window "excited"
ENERGY_DOMINANCE = 1.8        # excited axis must exceed the runner-up by this factor

# Analysis defaults
DEFAULT_FMIN = 1.0
DEFAULT_FMAX = 1000.0
COHERENCE_GATE = 0.8           # only trust gain/phase where C(f) exceeds this
PEAK_PROMINENCE_DB = 3.0       # a gain bump/peak must rise this far above the local trend
THROTTLE_BINS = 8              # throttle slices for the resonance map
THROTTLE_IDLE = 1100           # below this -> not flying


# ---------------------------------------------------------------------------
# I/O helpers (same conventions as spectral_analysis.py / step_response.py)
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
    """Estimate loop/log rate from the time column (microseconds)."""
    dt_us = float(np.median(np.diff(df[TIME_COL].values[:4000])))
    return 1_000_000.0 / dt_us


def _auto_nperseg(fs: float) -> int:
    """Welch window targeting ~2 Hz resolution, power of 2, generous for averaging."""
    n = int(2 ** round(np.log2(max(fs / 2.0, 512))))
    return int(max(1024, min(n, 8192)))


# ---------------------------------------------------------------------------
# Axis segmentation + input column resolution
# ---------------------------------------------------------------------------

def _col_has_signal(df: pd.DataFrame, col: str) -> bool:
    """True if the column exists and is not flat (carries actual data, not all-zero)."""
    if col not in df.columns:
        return False
    v = df[col].to_numpy(float)
    return float(np.ptp(v)) > 0.0 and float(np.std(v)) > 0.0


def _has_axis_flag(df: pd.DataFrame) -> bool:
    """True if debug[1] carries a real per-axis flag (legacy firmware), not a flat 0."""
    return CHIRP_AXIS_COL in df.columns and int(df[CHIRP_AXIS_COL].nunique()) > 1


def _reconstruct_exc(df: pd.DataFrame) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Reconstruct the excitation sine and active mask from debug[0] = 5000*sinarg.

    Returns (exc, active) or (None, None) if debug[0] is absent/flat.
    """
    if not _col_has_signal(df, PHASE_COL):
        return None, None
    d0 = df[PHASE_COL].to_numpy(float)
    return np.sin(d0 / PHASE_SCALE), d0 != 0.0


def _inst_freq(df: pd.DataFrame, fs: float) -> np.ndarray | None:
    """Instantaneous chirp frequency (Hz) from the unwrapped debug[0] phase.

    Replaces debug[2] on current firmware. Sawtooth resets between sub-sweeps
    produce gradient spikes; clip to [0, Nyquist] and treat 0 as inactive.
    """
    if not _col_has_signal(df, PHASE_COL):
        return None
    phase = df[PHASE_COL].to_numpy(float) / PHASE_SCALE
    f = np.gradient(np.unwrap(phase), 1.0 / fs) / (2.0 * np.pi)
    return np.clip(f, 0.0, fs / 2.0)


def _label_axes_by_energy(df: pd.DataFrame, active: np.ndarray, fs: float) -> np.ndarray:
    """Per-sample active-axis labels (0/1/2, -1=none) from setpoint energy.

    No debug[1] on current firmware: the chirp drives one axis at a time, so the
    excited axis carries far more setpoint variance than the (pilot-centred) others.
    Energy beats correlation-with-exc, which decorrelates at the top of the sweep.
    """
    n = len(df)
    labels = np.full(n, -1, dtype=int)
    cols = [SETPOINT_COL.format(a) for a in range(3)]
    if any(c not in df.columns for c in cols):
        return labels
    sp = [df[c].to_numpy(float) for c in cols]
    win = max(1, int(ENERGY_WIN_S * fs))
    hop = max(1, win // 2)
    for s in range(0, max(1, n - win), hop):
        sl = slice(s, s + win)
        if active[sl].mean() < 0.5:
            continue
        v = [float(sp[a][sl].std()) for a in range(3)]
        a = int(np.argmax(v))
        if v[a] > ENERGY_STD_FLOOR and v[a] > ENERGY_DOMINANCE * sorted(v)[1]:
            labels[sl] = a
    return labels


def _swept_band(df: pd.DataFrame, mask: np.ndarray, fmin: float, fmax: float,
                finst: np.ndarray | None = None) -> tuple[float, float]:
    """Restrict the band to the frequencies actually swept on this axis.

    Legacy: from debug[2] (deci-Hz). Current firmware: from the reconstructed
    instantaneous frequency `finst`, using robust 2nd/98th percentiles to shrug off
    the sawtooth-reset spikes.
    """
    mask = np.asarray(mask)
    if _col_has_signal(df, CHIRP_FREQ_COL) and mask.any():
        f = df.loc[mask, CHIRP_FREQ_COL].to_numpy(float) / 10.0
        f = f[f > 0]
        if f.size:
            return max(fmin, float(np.min(f))), min(fmax, float(np.max(f)))
    if finst is not None and mask.any():
        f = finst[mask]
        f = f[f > 0]
        if f.size:
            lo = max(fmin, float(np.percentile(f, 2)))
            hi = min(fmax, float(np.percentile(f, 98)))
            if hi > lo:
                return lo, hi
    return fmin, fmax


def _resolve_input(df: pd.DataFrame, exc: np.ndarray | None, requested: str,
                   axis_idx: int, mask: np.ndarray) -> tuple[np.ndarray | None, str | None]:
    """Resolve the FRF input x for this axis as a (values, label) pair.

    Priority:
      --input-col debug0   -> reconstructed unit sine sin(debug[0]/5000) (shape only)
      --input-col setpoint -> setpoint[i] (calibrated, deg/s)
      explicit column      -> that column if present
      default (debug[3])   -> debug[3] when it carries signal (legacy);
                              otherwise setpoint[i] (current firmware fallback)
    """
    mask = np.asarray(mask)
    spcol = SETPOINT_COL.format(axis_idx)

    def take(col):
        return df.loc[mask, col].to_numpy(float)

    if requested == "debug0":
        return (exc[mask], "sin(debug[0]/5000)") if exc is not None else (None, None)
    if requested == "setpoint":
        return (take(spcol), spcol) if spcol in df.columns else (None, None)
    if requested != DEFAULT_INPUT_COL and requested in df.columns:
        return take(requested), requested
    if requested == DEFAULT_INPUT_COL and _col_has_signal(df, DEFAULT_INPUT_COL):
        return take(DEFAULT_INPUT_COL), DEFAULT_INPUT_COL
    # default debug channel empty -> calibrated setpoint
    return (take(spcol), spcol) if spcol in df.columns else (None, None)


# ---------------------------------------------------------------------------
# Frequency response (Welch cross-spectral method, cf. step_response.py)
# ---------------------------------------------------------------------------

def _frf(x: np.ndarray, y: np.ndarray, fs: float, nperseg: int, regularize: float = 1e-6):
    """Return (freqs, gain_db, phase_deg, coherence) for the transfer x -> y."""
    x = sp_signal.detrend(x.astype(float))
    y = sp_signal.detrend(y.astype(float))
    nperseg = min(nperseg, len(x))
    f, Pxx = sp_signal.welch(x, fs=fs, nperseg=nperseg, window="hann")
    _, Pxy = sp_signal.csd(x, y, fs=fs, nperseg=nperseg, window="hann")
    _, Cxy = sp_signal.coherence(x, y, fs=fs, nperseg=nperseg, window="hann")
    reg = regularize * float(np.max(np.abs(Pxx))) if np.max(np.abs(Pxx)) > 0 else regularize
    H = Pxy / (Pxx + reg)
    gain_db = 20.0 * np.log10(np.abs(H) + 1e-12)
    phase_deg = np.degrees(np.unwrap(np.angle(H)))
    return f, gain_db, phase_deg, Cxy


def _step_response(setpoint: np.ndarray, gyro: np.ndarray, fs: float, band_fmax: float = 200.0,
                   horizon_ms: float = 150.0, npts: int = 160) -> dict:
    """Time-domain step response of setpoint -> gyro (same Welch H(f) as the Bode, via IFFT).

    The closed-loop H(f) is coherence-weighted and band-limited (smooth Hann taper above the
    swept band) BEFORE the IFFT — otherwise the incoherent high-frequency content injects
    spurious ringing and overshoot that contradicts the phase margin.

    Returns {"t_ms": [...], "y": [...], "metrics": {...}} or {} if unusable. The step is
    normalised to 1.0 at steady state so axes / passes are directly comparable.
    """
    sp = sp_signal.detrend(setpoint.astype(float))
    gy = sp_signal.detrend(gyro.astype(float))
    # window ~0.5 s: long enough to both resolve the ~20 Hz crossover (df ~2 Hz) and to hold the
    # full settling transient (nperseg/fs is the step's time span).
    nperseg = int(2 ** round(np.log2(fs * 0.5)))
    nperseg = max(1024, min(nperseg, 8192, len(sp)))
    f, Pxx = sp_signal.welch(sp, fs=fs, nperseg=nperseg, window="hann")
    _, Pxy = sp_signal.csd(sp, gy, fs=fs, nperseg=nperseg, window="hann")
    _, Cxy = sp_signal.coherence(sp, gy, fs=fs, nperseg=nperseg, window="hann")
    reg = 1e-5 * float(np.max(np.abs(Pxx))) if np.max(np.abs(Pxx)) > 0 else 1e-5
    H = Pxy / (Pxx + reg)
    # weight: soft coherence gate (Wiener-like) * Hann taper across the top of the swept band
    w = np.clip((Cxy - 0.3) / 0.6, 0.0, 1.0)
    fcut = max(60.0, min(band_fmax, fs / 2.0))
    f0 = 0.6 * fcut
    taper = np.where(f <= f0, 1.0,
                     np.where(f >= fcut, 0.0, 0.5 * (1.0 + np.cos(np.pi * (f - f0) / (fcut - f0)))))
    h = np.fft.irfft(H * w * taper, n=nperseg)
    step = np.cumsum(h) / fs
    n = len(step)
    ss = float(np.mean(step[int(0.7 * n):]))
    if abs(ss) < 1e-9:
        return {}
    step = step / ss
    t_ms = np.arange(n) * 1000.0 / fs
    keep = t_ms <= horizon_ms
    t_ms, step = t_ms[keep], step[keep]
    if t_ms.size < 4:
        return {}
    # metrics on the kept window
    peak = float(np.max(step))
    overshoot = round((peak - 1.0) * 100.0, 1) if peak > 1.0 else 0.0
    i10 = int(np.argmax(step >= 0.1)); i90 = int(np.argmax(step >= 0.9))
    rise = round(float(t_ms[i90] - t_ms[i10]), 1) if i90 > i10 > 0 else None
    i50 = int(np.argmax(step >= 0.5))
    delay = round(float(t_ms[i50]), 1) if i50 > 0 else None
    out = np.where(np.abs(step - 1.0) > 0.02)[0]
    settle = round(float(t_ms[min(int(out[-1]) + 1, len(t_ms) - 1)]), 1) if len(out) else round(float(t_ms[0]), 1)
    # downsample for the payload
    s = max(1, len(t_ms) // npts)
    return {
        "t_ms": [round(float(v), 1) for v in t_ms[::s]],
        "y": [round(float(v), 3) for v in step[::s]],
        "metrics": {"overshoot_pct": overshoot, "rise_ms": rise,
                    "delay_ms": delay, "settle_ms": settle, "peak": round(peak, 3)},
    }


def _gain_peaks(freqs, gain_db, coh, fmin, fmax) -> list[dict]:
    """Peaks in the gain curve (resonances / overshoot bumps) within the trusted band."""
    band = (freqs >= fmin) & (freqs <= fmax) & (coh >= COHERENCE_GATE)
    if band.sum() < 5:
        return []
    fb, gb = freqs[band], gain_db[band]
    df = float(np.median(np.diff(fb))) or 1.0
    distance = max(1, int(round(8.0 / df)))
    idx, props = sp_signal.find_peaks(gb, prominence=PEAK_PROMINENCE_DB, distance=distance)
    peaks = [
        {"freq_hz": round(float(fb[i]), 1),
         "gain_db": round(float(gb[i]), 1),
         "prominence_db": round(float(props["prominences"][k]), 1)}
        for k, i in enumerate(idx)
    ]
    peaks.sort(key=lambda p: p["prominence_db"], reverse=True)
    return peaks


def _phase_margin(freqs, gain_db, phase_deg, coh, fmin, fmax):
    """Phase margin: distance of phase from -180 deg at the 0 dB gain crossover.

    Searches the trusted band for the highest frequency where gain crosses 0 dB
    downward; reports (crossover_hz, margin_deg). Returns (None, None) if no clean
    crossover exists in coherent data.
    """
    band = (freqs >= fmin) & (freqs <= fmax) & (coh >= COHERENCE_GATE)
    fb, gb, pb = freqs[band], gain_db[band], phase_deg[band]
    if len(fb) < 3:
        return None, None
    cross = None
    for i in range(1, len(gb)):
        if gb[i - 1] >= 0.0 > gb[i]:
            cross = i
    if cross is None:
        return None, None
    fco = float(fb[cross])
    # Phase margin = 180 + phase at crossover, wrapped into (-180, 180].
    # Must be allowed to go <= 0: a phase at/past -180 deg while gain is still
    # >= 0 dB means the loop rings / is unstable (margin near 0 or negative).
    ph = float(pb[cross])
    margin = 180.0 + ph
    margin = margin - 360.0 * np.ceil((margin - 180.0) / 360.0)
    return round(fco, 1), round(margin, 1)


def _diagnose(peaks, phase_margin, fmin, fmax) -> list[dict]:
    """Bode diagnosis hints, each as a {fr, en} pair."""
    hints = []
    fco, margin = phase_margin
    for p in peaks[:3]:
        f, pr = p["freq_hz"], p["prominence_db"]
        if f < 80:
            hints.append({
                "fr": f"Bosse de gain à {f:.0f} Hz (+{pr:.0f} dB) → overshoot en boucle fermée ; "
                      f"c'est la zone P/D — réduire P (ou ajouter du D) si elle dépasse ~3 dB.",
                "en": f"Gain bump at {f:.0f} Hz (+{pr:.0f} dB) → closed-loop overshoot; this is the "
                      f"P/D region — back off P (or add D) if it exceeds ~3 dB.",
            })
        else:
            hints.append({
                "fr": f"Pic de gain marqué à {f:.0f} Hz (+{pr:.0f} dB) → résonance ; à traiter avec "
                      f"un notch (dynamique/statique), pas en changeant les gains PID.",
                "en": f"Sharp gain peak at {f:.0f} Hz (+{pr:.0f} dB) → resonance; target it with a "
                      f"dynamic/static notch, not by changing PID gains.",
            })
    if margin is not None:
        if margin <= 0:
            vfr, ven = "INSTABLE — phase au-delà de -180° avec gain ≥ 0 dB", "UNSTABLE — phase past -180° while gain ≥ 0 dB"
        elif margin >= 30:
            vfr, ven = "sain", "healthy"
        elif margin >= 15:
            vfr, ven = "limite", "marginal"
        else:
            vfr, ven = "faible", "low"
        hints.append({
            "fr": f"Marge de phase ~{margin:.0f}° au crossover 0 dB de {fco:.0f} Hz ({vfr}). "
                  f"Sous ~30° la boucle sonne ; réduire les gains ou ajouter du filtrage.",
            "en": f"Phase margin ~{margin:.0f}° at the {fco:.0f} Hz 0 dB crossover ({ven}). "
                  f"Below ~30° the loop rings; reduce gains or add filtering.",
        })
    else:
        hints.append({
            "fr": "Pas de crossover 0 dB dans la bande cohérente — soit la boucle reste sous 0 dB "
                  "(tune conservateur), soit la cohérence est trop basse pour lire la marge.",
            "en": "No 0 dB gain crossover inside the coherent band — either the loop stays below "
                  "0 dB (conservative tune) or coherence is too low to read the margin.",
        })
    if not peaks:
        hints.append({
            "fr": "Gain plat dans la bande cohérente — aucune bosse d'overshoot ni résonance ne ressort.",
            "en": "Gain is flat in the coherent band — no overshoot bump or resonance stands out.",
        })
    return hints


def _step_diagnosis(m: dict) -> list[dict]:
    """Step-response interpretation (overshoot / rise / settling), each as a {fr, en} pair."""
    if not m:
        return []
    hints = []
    ov = m.get("overshoot_pct") or 0.0
    rise = m.get("rise_ms")
    settle = m.get("settle_ms")
    if ov >= 25:
        hints.append({
            "fr": f"Overshoot ~{ov:.0f}% : fort dépassement → P trop haut ou D insuffisant/trop filtré "
                  f"(rebond, propwash probable). Cohérent avec une marge de phase faible.",
            "en": f"Overshoot ~{ov:.0f}%: large overshoot → P too high or D too low/over-filtered "
                  f"(bounce-back, likely propwash). Consistent with a low phase margin.",
        })
    elif ov >= 10:
        hints.append({
            "fr": f"Overshoot ~{ov:.0f}% : dépassement modéré, acceptable mais réductible (un peu plus "
                  f"de D ou un peu moins de P).",
            "en": f"Overshoot ~{ov:.0f}%: moderate, acceptable but reducible (a touch more D or a "
                  f"touch less P).",
        })
    else:
        hints.append({
            "fr": f"Overshoot ~{ov:.0f}% : réponse bien amortie.",
            "en": f"Overshoot ~{ov:.0f}%: well-damped response.",
        })
    if rise is not None:
        hints.append({
            "fr": f"Temps de montée ~{rise:.0f} ms" + (f", établissement ~{settle:.0f} ms." if settle else "."),
            "en": f"Rise time ~{rise:.0f} ms" + (f", settling ~{settle:.0f} ms." if settle else "."),
        })
    return hints


# ---------------------------------------------------------------------------
# Throttle x frequency resonance map
# ---------------------------------------------------------------------------

def _throttle_series(df: pd.DataFrame) -> tuple:
    """A 'throttle' axis for binning: rcCommand[3] if logged, else the motor-output average
    (DShot scale) — so logs that don't log rcCommand still get a throttle map. Returns
    (values, idle_threshold, source_label) or (None, None, None)."""
    if THROTTLE_COL in df.columns:
        return df[THROTTLE_COL].to_numpy(float), float(THROTTLE_IDLE), "rcCommand[3]"
    mc = [f"motor[{i}]" for i in range(4) if f"motor[{i}]" in df.columns]
    if mc:
        v = df[mc].to_numpy(float).mean(axis=1)
        lo, hi = float(np.percentile(v, 2)), float(np.percentile(v, 98))
        return v, lo + 0.10 * (hi - lo), "motor avg"        # idle/spool floor + margin
    return None, None, None


def _throttle_map(df: pd.DataFrame, fs: float, axis_idx: int, fmin: float, fmax: float,
                  nbins: int = THROTTLE_BINS) -> dict:
    """PSD of gyro per throttle slice -> heatmap of how resonances migrate with throttle."""
    gcol = GYRO_COL.format(axis_idx)
    thr, idle, src = _throttle_series(df)
    if gcol not in df.columns or thr is None:
        return {}
    flying = thr > idle
    if flying.sum() < 1024:
        return {}
    lo, hi = float(np.min(thr[flying])), float(np.max(thr[flying]))
    if hi - lo < 1.0:
        return {}
    edges = np.linspace(lo, hi, nbins + 1)

    # Collect per-bin masks first so every bin can share ONE Welch window size.
    # A per-bin nperseg would give bins of different length different frequency
    # grids -> ragged `levels_db` rows that no longer line up with `freqs`.
    masks, centers = [], []
    for b in range(nbins):
        m = flying & (thr >= edges[b]) & (thr <= edges[b + 1])
        centers.append(round(float((edges[b] + edges[b + 1]) / 2.0)))
        masks.append(m if int(m.sum()) >= 256 else None)
    qualifying = [int(m.sum()) for m in masks if m is not None]
    if not qualifying:
        return {}
    seg = min(1024, min(qualifying))     # one common window -> identical freq grid

    freqs_ref = None
    levels = []
    for m in masks:
        if m is None:
            levels.append(None)
            continue
        sig = sp_signal.detrend(df.loc[m, gcol].to_numpy(float))
        f, pxx = sp_signal.welch(sig, fs=fs, nperseg=seg, window="hann")
        sel = (f >= fmin) & (f <= fmax)
        if freqs_ref is None:
            freqs_ref = f[sel]
        levels.append((10.0 * np.log10(pxx[sel] + 1e-12)).round(1).tolist())
    if freqs_ref is None:
        return {}
    width = len(freqs_ref)
    levels = [row if row is not None else [None] * width for row in levels]
    # downsample frequency axis to keep the payload light
    step = max(1, width // 200)
    return {
        "axis": AXES[axis_idx],
        "source": src,
        "throttle_bins": centers,
        "freqs": [round(float(x), 1) for x in freqs_ref[::step]],
        "levels_db": [row[::step] for row in levels],
    }


def _spectrogram(sig: np.ndarray, fs: float, fmin: float = 5.0, fmax: float | None = None,
                 ntime: int = 200, nfreq: int = 140) -> dict:
    """Time x frequency STFT (dB) of the chirp window — shows the swept sine as a rising diagonal,
    and resonances as bright horizontal bands it crosses. Cropped to the swept band, and
    normalised per time-column so the instantaneous chirp frequency is always the bright cell
    (the diagonal stays crisp even though the gyro attenuates the high-frequency end)."""
    fmax = fmax or fs / 2.0 * 0.98
    if sig.size < 4096:
        return {}
    sig = sp_signal.detrend(sig.astype(float))
    nperseg = 512
    f, t, Sxx = sp_signal.spectrogram(sig, fs=fs, nperseg=nperseg, noverlap=nperseg * 3 // 4, window="hann")
    sel = (f >= fmin) & (f <= fmax)
    f, Sxx = f[sel], Sxx[sel]
    if f.size < 4 or t.size < 4:
        return {}
    db = 10.0 * np.log10(Sxx + 1e-12)
    db = db - np.max(db, axis=0, keepdims=True)   # per-column: 0 dB = loudest freq at each instant
    ts = max(1, -(-db.shape[1] // ntime))         # ceil -> cap the time-axis payload
    db, t = db[:, ::ts], t[::ts]
    # resample the frequency axis onto a LOG grid: the BF chirp sweeps exponentially, so on a log
    # axis the sweep is a straight line and the busy low-frequency region gets the room it needs.
    flo = float(max(fmin, f[0]))
    logf = np.logspace(np.log10(flo), np.log10(float(f[-1])), nfreq)
    db = np.vstack([np.interp(logf, f, db[:, c]) for c in range(db.shape[1])]).T   # nfreq x ntime
    return {
        "t_s": [round(float(x), 2) for x in t],
        "freqs": [round(float(x), 1) for x in logf],
        "logy": True,
        "levels_db": [[round(float(v), 1) for v in row] for row in db],
    }


# ---------------------------------------------------------------------------
# Gyro noise spectrum (PSD in dB) — raw vs filtered, for the filtering decision
# ---------------------------------------------------------------------------

NOISE_PEAK_PROM_DB = 3.0       # a noise peak must rise this far above the local floor to be flagged
NOISE_FLOOR_PCT = 20           # percentile of the raw PSD (>70 Hz) taken as the broadband noise floor
RESIDUAL_OK_DB = 6.0           # a filtered peak within this of the floor is essentially flattened (indicative)


def _noise_spectrum(df: pd.DataFrame, fs: float, axis_idx: int, quiet_mask: np.ndarray,
                    fmin: float = 30.0, fmax: float | None = None) -> dict:
    """Gyro PSD (dB) over a chirp-free window: raw (gyroUnfilt) vs filtered (gyroADC).

    During the chirp the gyro is full of excitation across the whole band, which masks the real
    noise floor — so we measure over the *quiet* window (flying, this axis not excited).

    Both curves are referenced to the RAW broadband noise floor (the flat HF baseline, robust and
    stable from flight to flight, unlike the motion peak): 0 dB = floor. A peak's height above the
    floor is its prominence; the filtered peak's residual above the floor and the raw->filtered
    attenuation are the reference-independent quantities the filtering decision rests on.
    """
    gcol = GYRO_COL.format(axis_idx)
    ucol = f"gyroUnfilt[{axis_idx}]"
    if gcol not in df.columns:
        return {}
    fmax = fmax or fs / 2.0 * 0.98
    m = np.asarray(quiet_mask)
    if int(m.sum()) < 2048:
        return {}
    nperseg = int(min(4096, 2 ** int(np.log2(int(m.sum())))))
    nperseg = max(1024, nperseg)

    def psd(col):
        sig = sp_signal.detrend(df.loc[m, col].to_numpy(float))
        f, pxx = sp_signal.welch(sig, fs=fs, nperseg=min(nperseg, len(sig)), window="hann")
        return f, 10.0 * np.log10(pxx + 1e-10)

    has_unfilt = ucol in df.columns
    f, raw = psd(ucol if has_unfilt else gcol)
    _, filt = psd(gcol)
    sel = (f >= fmin) & (f <= fmax)
    f, raw, filt = f[sel], raw[sel], filt[sel]
    if f.size < 8:
        return {}
    hf = f >= 70.0
    floor = float(np.percentile(raw[hf], NOISE_FLOOR_PCT)) if hf.sum() >= 5 else float(np.median(raw))
    raw = raw - floor                       # 0 dB = raw broadband noise floor
    filt = filt - floor

    peaks = []
    if hf.sum() > 5:
        fb, rb = f[hf], raw[hf]
        dfd = float(np.median(np.diff(fb))) or 1.0
        idx, props = sp_signal.find_peaks(rb, prominence=NOISE_PEAK_PROM_DB, distance=max(1, int(15.0 / dfd)))
        for k, i in enumerate(idx):
            j = int(np.argmin(np.abs(f - fb[i])))
            peaks.append({"freq_hz": round(float(fb[i]), 0),
                          "above_floor_db": round(float(rb[i]), 1),       # raw peak height over the floor
                          "resid_db": round(float(filt[j]), 1),           # filtered residual over the floor
                          "atten_db": round(float(rb[i] - filt[j]), 1),   # raw -> filtered cut (ref-independent)
                          "prom_db": round(float(props["prominences"][k]), 1)})
        peaks.sort(key=lambda p: p["above_floor_db"], reverse=True)
        peaks = peaks[:6]

    step = max(1, len(f) // 400)
    return {
        "axis": AXES[axis_idx], "has_unfilt": bool(has_unfilt),
        "freqs": [round(float(v), 1) for v in f[::step]],
        "raw_db": [round(float(v), 1) for v in raw[::step]],
        "filt_db": [round(float(v), 1) for v in filt[::step]],
        "peaks": peaks,
    }


def _worst_residual_db(noise: dict) -> float | None:
    """The largest filtered residual above the noise floor (dB), or None — how much resonance
    survives filtering. Low (near the floor) = the filtering has flattened the noise."""
    peaks = (noise or {}).get("peaks") or []
    return max((p["resid_db"] for p in peaks), default=None)


def _filter_disable_notes(noise: dict, config: dict) -> list[dict]:
    """Which whole filters could be turned off, judged from the raw noise above their cut-off.

    A lowpass only earns its phase lag if there is noise in its stopband. The second LPF stage
    (gyro_lpf2 / dterm_lpf2) is the usual redundancy: if the raw spectrum is already at the floor
    above its cut-off, it removes nothing the first stage + RPM/dyn_notch didn't already remove.
    """
    if not config:
        return []
    freqs = (noise or {}).get("freqs") or []
    raw = (noise or {}).get("raw_db") or []
    if not freqs:
        return []

    def max_raw_above(fc):
        vals = [r for f, r in zip(freqs, raw) if f >= fc]
        return max(vals) if vals else None

    out = []
    g1 = config.get("gyro_lpf1") or {}
    g1hi = (g1.get("dyn") or [None, None])[-1] or g1.get("static")
    g2c = (config.get("gyro_lpf2") or {}).get("static")
    if g2c:
        mr = max_raw_above(g2c)              # raw level above the cut-off, relative to the floor
        if mr is not None and mr <= RESIDUAL_OK_DB:
            out.append({
                "fr": f"Gyro LPF2 (statique {g2c} Hz) : au-dessus de sa coupure le bruit brut reste à +{max(mr,0):.0f} dB "
                      f"du plancher (LPF1 jusqu'à {g1hi} Hz + RPM/dyn_notch font le travail) → rien à enlever. "
                      f"Candidat à désactiver (gyro_lpf2_static_hz = 0) pour retirer son retard de phase.",
                "en": f"Gyro LPF2 (static {g2c} Hz): above its cut-off the raw noise stays at +{max(mr,0):.0f} dB over the "
                      f"floor (LPF1 up to {g1hi} Hz + RPM/dyn_notch do the work) → nothing to remove. Candidate to "
                      f"disable (gyro_lpf2_static_hz = 0) to drop its phase lag."})
        elif mr is not None:
            out.append({
                "fr": f"Gyro LPF2 ({g2c} Hz) : encore +{mr:.0f} dB de bruit au-dessus du plancher passé sa coupure — "
                      f"il sert toujours, à garder.",
                "en": f"Gyro LPF2 ({g2c} Hz): still +{mr:.0f} dB of noise above the floor past its cut-off — it's still "
                      f"working, keep it."})
    d2c = (config.get("dterm_lpf2") or {}).get("static")
    if d2c:
        mrd = max_raw_above(150.0)
        if mrd is not None and mrd <= RESIDUAL_OK_DB:
            out.append({
                "fr": f"D-term LPF2 (statique {d2c} Hz) : le D-term n'est pas mesuré ici, mais le gyro qui l'alimente "
                      f"est déjà au plancher au-dessus de 150 Hz (+{max(mrd,0):.0f} dB) → ce 2e étage est probablement "
                      f"désactivable aussi (dterm_lpf2_static_hz = 0) ; à confirmer au ressenti/température.",
                "en": f"D-term LPF2 (static {d2c} Hz): the D-term isn't measured here, but the gyro feeding it is already "
                      f"at the floor above 150 Hz (+{max(mrd,0):.0f} dB) → this 2nd stage is likely disable-able too "
                      f"(dterm_lpf2_static_hz = 0); confirm by feel/motor temps."})
        elif mrd is not None:
            out.append({
                "fr": f"D-term LPF2 ({d2c} Hz) : le bruit moteur vers 230 Hz (+{mrd:.0f} dB du plancher) tombe dans la "
                      f"zone que le D amplifie → le filtrage D-term est utile ici, à garder.",
                "en": f"D-term LPF2 ({d2c} Hz): motor noise near 230 Hz (+{mrd:.0f} dB over the floor) lands in the band "
                      f"the D amplifies → D-term filtering earns its keep here, leave it on."})
    return out


def _motor_harmonics(df: pd.DataFrame, mask: np.ndarray, poles, fmax: float) -> dict:
    """Motor rotation harmonics from eRPM telemetry, over the quiet window.

    BF stores eRPM in 100-eRPM LSBs; mechanical rotation Hz = eRPM*100 / (poles/2) / 60. Motors
    run at a spread of rpm (4 motors x throttle variation), so each harmonic is a *band*
    [n*f_lo, n*f_hi] rather than a line — exactly where the dyn_notch/RPM filter has to track.
    """
    if not poles:
        return {}
    cols = [f"eRPM[{i}]" for i in range(4) if f"eRPM[{i}]" in df.columns]
    if not cols:
        return {}
    e = df.loc[np.asarray(mask), cols].to_numpy(float).ravel()
    e = e[e > 0]
    if e.size < 256:
        return {}
    hz = e * 100.0 / (poles / 2.0) / 60.0            # per-sample, per-motor fundamental
    f_lo, f_hi = float(np.percentile(hz, 10)), float(np.percentile(hz, 90))
    if f_hi <= 0:
        return {}
    bands = []
    for n in range(1, 9):
        if n * f_lo > fmax:
            break
        bands.append({"n": n, "lo": round(n * f_lo, 0), "hi": round(min(n * f_hi, fmax), 0)})
    return {"f_lo": round(f_lo, 0), "f_hi": round(f_hi, 0), "bands": bands}


def _noise_suggestions(noise: dict) -> list[dict]:
    """Observations from the noise PSD peaks — prominence over the floor + raw->filtered
    attenuation, both reference-independent ({fr, en})."""
    if not noise:
        return []
    peaks = noise.get("peaks") or []
    out = []
    if not peaks:
        out.append({
            "fr": "Plancher de bruit propre : aucun pic >70 Hz ne dépasse le plancher — rien de discret à notcher.",
            "en": "Clean noise floor: no >70 Hz peak rises above the floor — nothing discrete to notch."})
        return out
    bands = (noise.get("motor") or {}).get("bands") or []
    for p in peaks:
        f, af, resid, att = p["freq_hz"], p["above_floor_db"], p["resid_db"], p["atten_db"]
        hn = next((b["n"] for b in bands if b["lo"] <= f <= b["hi"]), None)
        ofr = f", sur l'harmonique {hn}× moteur" if hn else ""
        oen = f", on the {hn}× motor harmonic" if hn else ""
        head = (f"{f:.0f} Hz : pic de bruit à +{af:.0f} dB au-dessus du plancher{ofr}, atténué de {att:.0f} dB "
                f"par les filtres",
                f"{f:.0f} Hz: noise peak at +{af:.0f} dB above the floor{oen}, cut by {att:.0f} dB by the filters")
        if resid <= RESIDUAL_OK_DB:
            out.append({
                "fr": f"{head[0]} → résiduel +{max(resid,0):.0f} dB, dans le plancher : aplati, rien à faire ici.",
                "en": f"{head[1]} → residual +{max(resid,0):.0f} dB, in the floor: flattened, nothing to do here."})
        else:
            out.append({
                "fr": f"{head[0]} → résiduel encore +{resid:.0f} dB au-dessus du plancher : raie discrète non "
                      f"complètement traitée (notch à renforcer/cibler).",
                "en": f"{head[1]} → residual still +{resid:.0f} dB above the floor: a discrete line not fully "
                      f"handled (notch to strengthen/target)."})
    return out


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def _downsample(freqs, *series, fmin, fmax, max_pts=600):
    band = (freqs >= fmin) & (freqs <= fmax)
    fb = freqs[band]
    step = max(1, len(fb) // max_pts)
    out = [fb[::step]]
    for s in series:
        out.append(s[band][::step])
    return out


def analyse(df, fs, input_col, axes_filter=None, fmin=DEFAULT_FMIN, fmax=DEFAULT_FMAX,
            nperseg=None, motor_poles=None) -> dict:
    nyq = fs / 2.0
    fmax = min(fmax, nyq * 0.98)
    if nperseg is None:
        nperseg = _auto_nperseg(fs)

    # Detect firmware generation and build reconstruction aids once.
    has_flag = _has_axis_flag(df)
    exc, active = _reconstruct_exc(df)
    finst = _inst_freq(df, fs)
    labels = None
    if not has_flag and active is not None:
        labels = _label_axes_by_energy(df, active, fs)
    if not has_flag and labels is None and active is None:
        print("Warning: no debug[1] axis flag and no debug[0] phase channel — "
              "cannot segment chirp axes. Was the log recorded with debug_mode=CHIRP?",
              file=sys.stderr)
    elif not has_flag:
        print("Note: current-firmware chirp log (debug[1..3] empty) — segmenting axes "
              "by setpoint energy from debug[0]; FRF input falls back to calibrated "
              "setpoint[i].", file=sys.stderr)

    results: dict = {}
    primary_axis_idx = None
    primary_n = 0

    for i, axis in enumerate(AXES):
        if axes_filter and axis not in axes_filter:
            continue
        gcol = GYRO_COL.format(i)
        if gcol not in df.columns:
            continue

        # Axis mask: legacy debug[1] flag, else energy labels, else whole flying window.
        if has_flag:
            mask = df[CHIRP_AXIS_COL].to_numpy() == i
        elif labels is not None:
            mask = labels == i
        elif active is not None:
            mask = active
        elif THROTTLE_COL in df.columns:
            mask = df[THROTTLE_COL].to_numpy() > THROTTLE_IDLE
        else:
            mask = np.ones(len(df), dtype=bool)
        if int(mask.sum()) < 512:
            continue

        x, xcol = _resolve_input(df, exc, input_col, i, mask)
        if x is None:
            continue
        if int(mask.sum()) > primary_n:
            primary_n, primary_axis_idx = int(mask.sum()), i

        y = df.loc[np.asarray(mask), gcol].to_numpy(float)
        a_fmin, a_fmax = _swept_band(df, mask, fmin, fmax, finst)

        freqs, gain_db, phase_deg, coh = _frf(x, y, fs, nperseg)
        peaks = _gain_peaks(freqs, gain_db, coh, a_fmin, a_fmax)
        fco, margin = _phase_margin(freqs, gain_db, phase_deg, coh, a_fmin, a_fmax)

        # Step response from the calibrated setpoint -> gyro (time-domain companion to the Bode).
        step = {}
        spcol = SETPOINT_COL.format(i)
        if spcol in df.columns:
            # closed-loop bandwidth is a few × the crossover; cap the step band well below the
            # full swept range so high-frequency noise doesn't fake ringing in the transient.
            sb = min(a_fmax, max(120.0, 6.0 * fco)) if fco else min(a_fmax, 150.0)
            step = _step_response(df.loc[np.asarray(mask), spcol].to_numpy(float), y, fs, band_fmax=sb)

        fb, gb, pb, cb = _downsample(freqs, gain_db, phase_deg, coh, fmin=a_fmin, fmax=a_fmax)
        results[axis] = {
            "input_col": xcol,
            "band_hz": [round(a_fmin, 1), round(a_fmax, 1)],
            "n_samples": int(mask.sum()),
            "freq": [round(float(v), 1) for v in fb],
            "gain_db": [round(float(v), 1) for v in gb],
            "phase_deg": [round(float(v), 1) for v in pb],
            "coherence": [round(float(v), 3) for v in cb],
            "peaks": peaks,
            "phase_margin_deg": margin,
            "crossover_hz": fco,
            "step": step,
            "diagnosis": _diagnose(peaks, (fco, margin), a_fmin, a_fmax),
            "step_diagnosis": _step_diagnosis(step.get("metrics", {})) if step else [],
        }

    throttle_map = {}
    noise = {}
    if primary_axis_idx is not None:
        throttle_map = _throttle_map(df, fs, primary_axis_idx, fmin, fmax)
        # Noise PSD over the chirp-free window for this axis (when it is NOT being excited).
        if labels is not None:
            quiet = labels != primary_axis_idx
        elif active is not None:
            quiet = ~active
        else:
            quiet = np.ones(len(df), dtype=bool)
        thr, idle, _ = _throttle_series(df)
        if thr is not None:
            quiet = quiet & (thr > idle)
        noise = _noise_spectrum(df, fs, primary_axis_idx, quiet, fmin=30.0, fmax=fmax)
        if noise and motor_poles:
            mh = _motor_harmonics(df, quiet, motor_poles, float(noise["freqs"][-1]))
            if mh:
                noise["motor"] = mh

    # Spectrogram of the primary axis over its (contiguous) chirp window -> the rising sweep.
    spectro = {}
    if primary_axis_idx is not None:
        gcol = GYRO_COL.format(primary_axis_idx)
        act = (labels == primary_axis_idx) if labels is not None else active
        if act is not None and gcol in df.columns:
            idx = np.where(np.asarray(act))[0]
            if idx.size:
                seg = df[gcol].to_numpy(float)[int(idx[0]):int(idx[-1]) + 1]
                # crop to the swept band (+10%) so the diagonal fills the plot instead of empty HF
                sweptmax = (results[AXES[primary_axis_idx]]["band_hz"][1]) * 1.1
                spectro = _spectrogram(seg, fs, fmax=min(fmax, sweptmax))
                if spectro:
                    spectro["axis"] = AXES[primary_axis_idx]

    return results, throttle_map, noise, spectro


# ---------------------------------------------------------------------------
# Tuning config (read from the blackbox header) + recommendation engine
# ---------------------------------------------------------------------------

_LPF_TYPES = {"0": "PT1", "1": "BIQUAD", "2": "PT2", "3": "PT3"}


def _parse_header_config(bbl_path: Path) -> dict:
    """Pull PID + filter settings from the blackbox header (the `H key:value` lines).

    Returns {} for CSV input or when the header is unreadable — suggestions then
    degrade to curve-only (no PID/filter cross-reference).
    """
    try:
        raw = bbl_path.read_bytes()[:65536].decode("latin1", "replace")
    except OSError:
        return {}
    h: dict[str, str] = {}
    for line in raw.split("\n"):
        if not line.startswith("H "):
            continue
        k, _, v = line[2:].partition(":")
        if v:
            h[k.strip()] = v.strip()
    if "rollPID" not in h:
        return {}

    def ints(key, n=None):
        try:
            vals = [int(float(x)) for x in h[key].split(",")]
        except (KeyError, ValueError):
            return None
        return vals[:n] if n else vals

    def i1(key):
        v = ints(key)
        return v[0] if v else None

    cfg: dict = {"pids": {}}
    for axis, key in (("roll", "rollPID"), ("pitch", "pitchPID"), ("yaw", "yawPID")):
        p = ints(key, 3)
        if p:
            cfg["pids"][axis] = p
    cfg["d_max"] = ints("d_max", 3)
    g1 = ints("gyro_lpf1_dyn_hz")
    cfg["gyro_lpf1"] = {"dyn": g1, "static": i1("gyro_lpf1_static_hz"),
                        "type": _LPF_TYPES.get(h.get("gyro_lpf1_type"), h.get("gyro_lpf1_type"))}
    cfg["gyro_lpf2"] = {"static": i1("gyro_lpf2_static_hz"),
                        "type": _LPF_TYPES.get(h.get("gyro_lpf2_type"), h.get("gyro_lpf2_type"))}
    d1 = ints("dterm_lpf1_dyn_hz")
    cfg["dterm_lpf1"] = {"dyn": d1, "static": i1("dterm_lpf1_static_hz"),
                         "type": _LPF_TYPES.get(h.get("dterm_lpf1_type"), h.get("dterm_lpf1_type"))}
    cfg["dterm_lpf2"] = {"static": i1("dterm_lpf2_static_hz"),
                         "type": _LPF_TYPES.get(h.get("dterm_lpf2_type"), h.get("dterm_lpf2_type"))}
    dn_min = (ints("dyn_notch_min_hz") or [None])[0]
    dn_max = (ints("dyn_notch_max_hz") or [None])[0]
    cfg["dyn_notch"] = {"count": (ints("dyn_notch_count") or [None])[0],
                        "q": (ints("dyn_notch_q") or [None])[0],
                        "min": dn_min, "max": dn_max}
    cfg["rpm_harmonics"] = (ints("rpm_filter_harmonics") or [0])[0]
    cfg["motor_poles"] = i1("motor_poles")
    return cfg


def _psd_resonances(throttle_map: dict) -> list[dict]:
    """Resonances in the throttle-averaged gyro PSD, flagged if they migrate with throttle.

    The throttle map is the curve behind the filtering advice: a peak that grows /
    shifts with throttle is motor/desync (RPM filter, dyn notch); a fixed peak is a
    frame resonance (static notch).
    """
    freqs = throttle_map.get("freqs")
    levels = throttle_map.get("levels_db")
    if not freqs or not levels:
        return []
    f = np.asarray(freqs, float)
    arr = np.array([[np.nan if v is None else v for v in row] for row in levels], float)
    mean_psd = np.nanmean(arr, axis=0)
    if not np.isfinite(mean_psd).any():
        return []
    # only look above ~70 Hz: below that is the closed-loop band, not a filter target
    lo = f >= 70.0
    if lo.sum() < 5:
        return []
    fb, mb = f[lo], mean_psd[lo]
    df = float(np.median(np.diff(fb))) or 1.0
    idx, props = sp_signal.find_peaks(mb, prominence=6.0, distance=max(1, int(20.0 / df)))
    out = []
    nrows = arr.shape[0]
    for k, i in enumerate(idx):
        gi = np.where(f == fb[i])[0]
        col = int(gi[0]) if gi.size else None
        migrates = False
        if col is not None and nrows >= 4:
            half = nrows // 2
            win = slice(max(0, col - 3), col + 4)
            low_pk = np.nanargmax(np.nanmean(arr[:half, win], axis=0)) if np.isfinite(arr[:half, win]).any() else 0
            high_pk = np.nanargmax(np.nanmean(arr[half:, win], axis=0)) if np.isfinite(arr[half:, win]).any() else 0
            migrates = abs(int(low_pk) - int(high_pk)) >= 2
        out.append({"freq_hz": round(float(fb[i]), 0),
                    "prominence_db": round(float(props["prominences"][k]), 1),
                    "migrates": bool(migrates)})
    out.sort(key=lambda p: p["prominence_db"], reverse=True)
    return out[:5]


def _filter_suggestions(throttle_map: dict, cfg: dict) -> list[dict]:
    """Filtering leads, each tied to a measured resonance frequency (evidence for the curve)."""
    res = _psd_resonances(throttle_map)
    dn = cfg.get("dyn_notch") or {}
    nmin, nmax = dn.get("min"), dn.get("max")
    cnt, q = dn.get("count"), dn.get("q")
    sug = []
    for r in res:
        f, pr = r["freq_hz"], r["prominence_db"]
        ofr = ("migre avec le throttle → moteur/desync (RPM filter, dyn notch)" if r["migrates"]
               else "stable en throttle → résonance de frame (notch statique)")
        oen = ("migrates with throttle → motor/desync (RPM filter, dyn notch)" if r["migrates"]
               else "throttle-stable → frame resonance (static notch)")
        if nmin is not None and nmax is not None and nmin <= f <= nmax:
            fr = (f"Résonance {f:.0f} Hz (+{pr:.0f} dB), {ofr} — dans la plage dyn_notch "
                  f"({nmin}-{nmax} Hz, ×{cnt}, Q={q}), donc déjà ciblée. Si elle reste visible, c'est que "
                  f"le notch ne la couvre pas assez (count ou Q insuffisant).")
            en = (f"Resonance {f:.0f} Hz (+{pr:.0f} dB), {oen} — inside the dyn_notch range "
                  f"({nmin}-{nmax} Hz, ×{cnt}, Q={q}), so already targeted. If it remains visible, the notch "
                  f"isn't covering it enough (count or Q too low).")
        elif nmax is not None and f > nmax:
            fr = (f"Résonance {f:.0f} Hz (+{pr:.0f} dB), {ofr} — AU-DESSUS de dyn_notch_max ({nmax} Hz), "
                  f"donc hors de portée du notch : un dyn_notch_max plus haut (~{int(f + 50)}) la couvrirait.")
            en = (f"Resonance {f:.0f} Hz (+{pr:.0f} dB), {oen} — ABOVE dyn_notch_max ({nmax} Hz), so beyond "
                  f"the notch's reach: a higher dyn_notch_max (~{int(f + 50)}) would cover it.")
        elif nmin is not None and f < nmin:
            fr = (f"Résonance {f:.0f} Hz (+{pr:.0f} dB), {ofr} — SOUS dyn_notch_min ({nmin} Hz), "
                  f"donc hors plage : un dyn_notch_min plus bas (~{max(60, int(f - 20))}) la prendrait.")
            en = (f"Resonance {f:.0f} Hz (+{pr:.0f} dB), {oen} — BELOW dyn_notch_min ({nmin} Hz), so out of "
                  f"range: a lower dyn_notch_min (~{max(60, int(f - 20))}) would catch it.")
        else:
            fr = f"Résonance {f:.0f} Hz (+{pr:.0f} dB), {ofr}."
            en = f"Resonance {f:.0f} Hz (+{pr:.0f} dB), {oen}."
        sug.append({"freq_hz": f, "fr": fr, "en": en})
    if not sug:
        sug.append({"freq_hz": None,
                    "fr": (f"Aucune résonance marquée (>70 Hz) dans la carte throttle : le filtrage en place "
                           f"(dyn_notch ×{cnt} Q={q}, RPM filter ×{cfg.get('rpm_harmonics')}) tient le spectre "
                           f"propre — voir le spectre de bruit pour la marge réelle d'assouplissement."),
                    "en": (f"No prominent resonance (>70 Hz) in the throttle map: the filtering in place "
                           f"(dyn_notch ×{cnt} Q={q}, RPM filter ×{cfg.get('rpm_harmonics')}) keeps the spectrum "
                           f"clean — see the noise spectrum for the actual room to loosen it.")})
    return sug


def _pid_suggestions(axes: dict, cfg: dict, noise: dict | None = None) -> dict:
    """Per-axis observations from the phase margin + step, cross-linked to the filtering
    headroom (constatation style, not prescriptions) — {fr, en} each."""
    out: dict = {}
    pids = cfg.get("pids") or {}
    worst = _worst_residual_db(noise)
    has_margin = worst is not None and worst <= RESIDUAL_OK_DB   # noise back in the floor -> filtering headroom
    # cross-link clause used on margin-limited axes
    if has_margin:
        link_fr = " Comme le spectre de bruit montre de la marge de filtrage, l'alléger relèverait d'abord cette marge sans toucher aux gains"
        link_en = " Since the noise spectrum shows filtering headroom, loosening it would lift this margin first without touching the gains"
    else:
        link_fr = link_en = ""
    for axis, d in axes.items():
        m = d.get("phase_margin_deg")
        fco = d.get("crossover_hz")
        p = pids.get(axis)
        P, D = (p[0], p[2]) if p else (None, None)
        pd = f"P={P}/D={D}" if p else "PID ?"
        ov = (d.get("step") or {}).get("metrics", {}).get("overshoot_pct")
        ovf = f", la step montre {ov:.0f}% d'overshoot" if ov is not None else ""
        ove = f", the step shows {ov:.0f}% overshoot" if ov is not None else ""
        at = f"@ {fco:.0f} Hz" if fco else ""
        if m is None:
            out[axis] = {
                "fr": f"Pas de crossover 0 dB dans la bande cohérente : la boucle reste sous 0 dB (tune conservateur) "
                      f"ou la cohérence est trop basse pour lire la marge. ({pd})",
                "en": f"No 0 dB crossover in the coherent band: the loop stays below 0 dB (conservative tune) or "
                      f"coherence is too low to read the margin. ({pd})"}
        elif m <= 5:
            if has_margin:
                tf, te = link_fr + ".", link_en + "."
            elif P:
                tf = f" Les réduire (P vers ~{int(round(P*0.85))}) ramènerait la marge en positif."
                te = f" Reducing them (P toward ~{int(round(P*0.85))}) would bring the margin positive."
            else:
                tf = te = ""
            out[axis] = {
                "fr": f"Marge {m:.0f}° {at} — négative/quasi nulle : la boucle est au bord de l'auto-oscillation "
                      f"(le Bode passe −180° avec gain ≥ 0 dB{ovf}). À {pd}, les gains sont trop hauts pour la "
                      f"marge disponible.{tf}",
                "en": f"Margin {m:.0f}° {at} — negative/near zero: the loop is on the edge of self-oscillation "
                      f"(the Bode passes −180° with gain ≥ 0 dB{ove}). At {pd}, the gains are too high for the "
                      f"available margin.{te}"}
        elif m < 20:
            if has_margin:
                tf, te = link_fr + ".", link_en + "."
            elif P:
                tf = f" Réduire un peu P (vers ~{int(round(P*0.9))}) l'assainirait."
                te = f" A small P cut (toward ~{int(round(P*0.9))}) would clean it up."
            else:
                tf = te = ""
            out[axis] = {
                "fr": f"Marge faible {m:.0f}° {at} : la boucle rebondit encore{ovf}. {pd}.{tf}",
                "en": f"Low margin {m:.0f}° {at}: the loop still bounces{ove}. {pd}.{te}"}
        elif m < 35:
            out[axis] = {
                "fr": f"Marge correcte mais limite {m:.0f}° {at}{ovf} : tune sain, peu de marge à regagner côté gains. ({pd})",
                "en": f"OK-but-limited margin {m:.0f}° {at}{ove}: healthy tune, little margin to gain on the gains. ({pd})"}
        elif m < 55:
            out[axis] = {
                "fr": f"Marge saine {m:.0f}° {at}{ovf} : axe bien amorti. ({pd})",
                "en": f"Healthy margin {m:.0f}° {at}{ove}: well-damped axis. ({pd})"}
        else:
            out[axis] = {
                "fr": f"Grande marge {m:.0f}° {at}{ovf} → réserve confortable : une fois le filtrage figé, c'est "
                      f"l'axe où P ({P}) a le plus de place pour monter si le ressenti est mou. ({pd})",
                "en": f"Large margin {m:.0f}° {at}{ove} → comfortable reserve: once filtering is frozen, this is the "
                      f"axis where P ({P}) has the most room to rise if it feels soft. ({pd})"}
    return out


def _synthesis(axes: dict, noise: dict, config: dict, throttle_max: float | None = None) -> list[dict]:
    """Top-level 'read' of the whole report as linked observations (filter -> phase -> P/D).

    Data-driven: it states what the curves show and how the levers chain, without prescribing.
    """
    obs: list[dict] = []
    worst = _worst_residual_db(noise)       # largest filtered residual above the noise floor
    has_unfilt = (noise or {}).get("has_unfilt")
    margin_avail = worst is not None and worst <= RESIDUAL_OK_DB
    margins = {ax: d["phase_margin_deg"] for ax, d in axes.items() if d.get("phase_margin_deg") is not None}
    low = {ax: mv for ax, mv in margins.items() if mv < 35.0}
    low_str = ", ".join(f"{ax} {mv:.0f}°" for ax, mv in low.items())

    # 1) Filtering state — judged on the filtered residual above the floor (reference-stable)
    if worst is not None:
        if has_unfilt and margin_avail:
            obs.append({
                "fr": f"Filtrage — après filtres, le bruit retombe dans son plancher (résiduel max +{max(worst,0):.0f} dB) : "
                      f"le filtrage en place est plus fort que ne l'exige le bruit présent.",
                "en": f"Filtering — after filtering, the noise falls back into its floor (max residual +{max(worst,0):.0f} dB): "
                      f"current filtering is stronger than the present noise requires."})
        else:
            obs.append({
                "fr": f"Filtrage — il subsiste un résiduel à +{worst:.0f} dB au-dessus du plancher après filtres : "
                      f"le filtrage travaille encore, peu de marge pour l'alléger.",
                "en": f"Filtering — a +{worst:.0f} dB residual remains above the floor after filtering: the filters "
                      f"are still working, little room to loosen them."})

    # 2) The chain filter -> phase -> P/D
    if margin_avail and low:
        obs.append({
            "fr": f"Chaînage — ces marges de phase basses ({low_str}) sont aujourd'hui le facteur limitant. Alléger "
                  f"le filtrage réduit le retard de phase, donc relèverait d'abord ces marges ; et une marge "
                  f"regagnée, c'est ensuite du headroom pour monter P et D sans que la boucle oscille.",
            "en": f"Chain — these low phase margins ({low_str}) are the current limiting factor. Loosening the "
                  f"filtering cuts phase lag, so it would lift these margins first; and margin regained is then "
                  f"headroom to raise P and D without the loop oscillating."})
    elif low:
        obs.append({
            "fr": f"Chaînage — marges basses ({low_str}) mais peu de marge de filtrage : ici le levier direct est "
                  f"de réduire P/D plutôt que de toucher au filtre.",
            "en": f"Chain — low margins ({low_str}) but little filtering room: here the direct lever is reducing "
                  f"P/D rather than the filtering."})

    # 3) Throttle-coverage caveat
    if throttle_max is not None and throttle_max < 1450:
        obs.append({
            "fr": f"Réserve — ce log monte peu en gaz (~{throttle_max:.0f} sur 2000) : la marge de bruit est "
                  f"mesurée à bas régime, or le bruit moteur augmente avec le throttle. À confirmer avec une passe "
                  f"plus engagée avant d'alléger franchement le filtrage.",
            "en": f"Caveat — this log barely climbs in throttle (~{throttle_max:.0f} of 2000): the noise margin is "
                  f"measured at low rpm, and motor noise grows with throttle. Confirm with a more aggressive pass "
                  f"before loosening the filtering for real."})
    return obs


# ---------------------------------------------------------------------------
# Multi-pass history: each chirp re-fly is appended and overlaid for before/after
# ---------------------------------------------------------------------------

MAX_OVERLAY_PASSES = 8   # keep the full file, but only render the last N passes


def _build_pass(path: Path, df: pd.DataFrame, fs: float, args) -> dict:
    """Run the analysis on one log and package it as a self-contained 'pass'."""
    axes_filter = [args.axis] if args.axis else None
    config = _parse_header_config(path) if path.suffix.lower() in (".bbl", ".bfl") else {}
    results, throttle_map, noise, spectro = analyse(df, fs, args.input_col, axes_filter,
                                                    fmin=args.fmin, fmax=args.fmax, nperseg=args.nperseg,
                                                    motor_poles=config.get("motor_poles"))
    nyq = fs / 2.0
    throttle_max = None
    thr, idle, thr_src = _throttle_series(df)
    if thr is not None and thr_src == "rcCommand[3]":   # only meaningful as a stick % vs 2000
        fly = thr[thr > idle]
        throttle_max = round(float(fly.max()), 0) if fly.size else None
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "file": path.name,
        "sample_rate_hz": round(fs),
        "input_col": args.input_col,
        "band_hz": [args.fmin, round(min(args.fmax, nyq * 0.98), 1)],
        "throttle_max": throttle_max,
        "config": config,
        "axes": results,
        "throttle_map": throttle_map,
        "noise_spectrum": noise,
        "spectrogram": spectro,
        "synthesis": _synthesis(results, noise, config, throttle_max),
        "filter_suggestions": _filter_suggestions(throttle_map, config) if config else [],
        "noise_suggestions": _noise_suggestions(noise) + _filter_disable_notes(noise, config),
        "pid_suggestions": _pid_suggestions(results, config, noise) if config else {},
    }


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


def _config_fields(cfg: dict) -> list[tuple]:
    """Flat, ordered (label, value) list of the comparable PID + filter fields — the single
    source of truth for both the textual diff and the HTML comparison table."""
    if not cfg:
        return []
    out = []
    for ax in AXES:
        p = (cfg.get("pids") or {}).get(ax)
        if p:
            out.append((f"{ax} P/I/D", "/".join(map(str, p))))
    if cfg.get("d_max"):
        out.append(("D_max", "/".join(map(str, cfg["d_max"]))))

    def lpf(key, lbl):
        d = cfg.get(key) or {}
        v = d.get("dyn") or d.get("static")
        if v is not None:
            vs = "–".join(map(str, v)) if isinstance(v, list) else str(v)
            out.append((lbl, (f"{vs} Hz {d.get('type') or ''}").strip()))

    lpf("gyro_lpf1", "gyro LPF1")
    lpf("gyro_lpf2", "gyro LPF2")
    lpf("dterm_lpf1", "D-term LPF1")
    lpf("dterm_lpf2", "D-term LPF2")
    dn = cfg.get("dyn_notch") or {}
    if dn.get("count") is not None:
        out.append(("dyn_notch", f"×{dn.get('count')} Q{dn.get('q')} [{dn.get('min')}–{dn.get('max')} Hz]"))
    if cfg.get("rpm_harmonics") is not None:
        out.append(("RPM filter", f"×{cfg['rpm_harmonics']}"))
    return out


def _config_diff(prev: dict, cur: dict) -> str:
    """Exhaustive human summary of what changed between two passes' tuning configs."""
    if not prev or not cur:
        return ""
    pf, cf = dict(_config_fields(prev)), dict(_config_fields(cur))
    changes = [f"{k} {pf.get(k)}→{cf[k]}" for k, _ in _config_fields(cur)
               if k in pf and pf[k] != cf[k]]
    return ", ".join(changes)


def _assemble_report(passes: list, lang: str = "fr") -> dict:
    """Trim to the last MAX_OVERLAY_PASSES, attach pass numbers + config diffs, mark primary."""
    shown = passes[-MAX_OVERLAY_PASSES:]
    base = len(passes) - len(shown)
    primary = len(shown) - 1
    for k, p in enumerate(shown):
        p["n"] = base + k + 1
        p["ts"] = p.get("timestamp", "").replace("T", " ")
        p["diff"] = _config_diff(shown[k - 1]["config"], p["config"]) if k > 0 else ""
        # only the primary pass renders its heatmaps -> drop them from the others to keep the HTML light
        if k != primary:
            for heavy in ("spectrogram", "throttle_map", "noise_spectrum"):
                p.pop(heavy, None)
    return {"passes": shown, "primary_index": len(shown) - 1, "total_passes": len(passes),
            "lang": lang, "_glossary": GLOSSARY, "_strings": STRINGS}


# ---------------------------------------------------------------------------
# Pedagogical glossary (FR/EN) — detailed tooltips for every technical term
# ---------------------------------------------------------------------------

GLOSSARY = {
    "chirp": {
        "fr": "Chirp : un signal sinusoïdal dont la fréquence balaie lentement du bas vers le haut "
              "(ici ~0 à 500 Hz), injecté sur la consigne d'un axe. En mesurant comment le drone y "
              "répond fréquence par fréquence, on obtient sa réponse en fréquence (Bode) — la "
              "signature dynamique complète de la boucle de stabilisation.",
        "en": "Chirp: a sine signal whose frequency slowly sweeps from low to high (here ~0 to "
              "500 Hz), injected onto an axis' setpoint. Measuring how the drone responds frequency "
              "by frequency gives its frequency response (Bode) — the full dynamic signature of the "
              "stabilisation loop.",
    },
    "gain": {
        "fr": "Gain (dB) : rapport entre le mouvement obtenu (gyro) et le mouvement demandé "
              "(consigne), en décibels. 0 dB = le drone suit exactement la demande. Au-dessus de 0 dB "
              "il en fait trop (surréaction/résonance), en dessous il atténue. Une bosse de gain = "
              "tendance à osciller à cette fréquence.",
        "en": "Gain (dB): ratio of the motion obtained (gyro) to the motion commanded (setpoint), in "
              "decibels. 0 dB = the drone tracks the command exactly. Above 0 dB it overreacts "
              "(overshoot/resonance), below it attenuates. A gain bump = tendency to oscillate there.",
    },
    "phase": {
        "fr": "Phase (°) : le retard entre la demande et la réponse, en degrés. Plus la fréquence "
              "monte, plus le retard s'accumule (filtres, inertie). Quand la phase atteint -180°, la "
              "correction arrive en opposition : si le gain est encore ≥ 0 dB à ce point, la boucle "
              "s'auto-entretient et oscille.",
        "en": "Phase (°): the lag between command and response, in degrees. The higher the frequency, "
              "the more lag accumulates (filters, inertia). When phase reaches -180°, the correction "
              "arrives in opposition: if gain is still ≥ 0 dB there, the loop self-sustains and oscillates.",
    },
    "phase_margin": {
        "fr": "Marge de phase : de combien de degrés on est encore au-dessus de -180° à l'endroit où "
              "le gain croise 0 dB. C'est la réserve de stabilité. >45° = sain et amorti ; 30-45° = "
              "correct ; 15-30° = limite, ça commence à rebondir ; <15° ou négatif = la boucle sonne. "
              "Baisser P/D ou filtrer redonne de la marge.",
        "en": "Phase margin: how many degrees you are still above -180° at the point where gain crosses "
              "0 dB. It is the stability reserve. >45° = healthy and damped; 30-45° = fine; 15-30° = "
              "marginal, starts to bounce; <15° or negative = the loop rings. Lowering P/D or adding "
              "filtering restores margin.",
    },
    "crossover": {
        "fr": "Crossover 0 dB : la fréquence où le gain passe sous 0 dB. C'est en gros la bande "
              "passante de l'axe — jusqu'où le drone suit fidèlement les ordres. Plus elle est haute, "
              "plus la réponse est vive, mais plus il faut de marge de phase pour rester stable.",
        "en": "0 dB crossover: the frequency where gain drops below 0 dB. Roughly the axis bandwidth — "
              "how far the drone tracks commands faithfully. Higher = sharper response, but it needs "
              "more phase margin to stay stable.",
    },
    "coherence": {
        "fr": "Cohérence (0 à 1) : à quel point la réponse mesurée est réellement causée par "
              "l'excitation chirp, et non par du bruit/vibrations. 1 = mesure fiable. En dessous de "
              "~0.6 la courbe de gain/phase n'est pas fiable à cette fréquence — on l'affiche en grisé. "
              "La cohérence chute naturellement en haute fréquence.",
        "en": "Coherence (0 to 1): how much of the measured response is really caused by the chirp "
              "excitation rather than noise/vibration. 1 = trustworthy. Below ~0.6 the gain/phase curve "
              "is unreliable at that frequency — shown greyed out. Coherence naturally falls at high "
              "frequency (weaker signal).",
    },
    "resonance": {
        "fr": "Résonance : un pic d'énergie marqué à une fréquence précise, dû au cadre, aux pales ou "
              "aux moteurs. Si elle remonte dans la boucle, elle fait vibrer/chauffer. On la traite par "
              "du filtrage (notch), PAS en touchant les PID — baisser les gains pour masquer une "
              "résonance dégrade le pilotage pour rien.",
        "en": "Resonance: a sharp energy peak at a specific frequency, from the frame, props or motors. "
              "If it feeds into the loop it causes vibration/heat. Treat it with filtering (a notch), "
              "NOT by changing PIDs — lowering gains to mask a resonance degrades handling for nothing.",
    },
    "gyro_lpf": {
        "fr": "Gyro lowpass (LPF) : filtre passe-bas sur le signal du gyroscope, avant tout calcul PID. "
              "Il enlève le bruit haute fréquence (moteurs/vibrations). Trop bas, il ajoute du retard de "
              "phase et déstabilise ; trop haut, il laisse passer le bruit dans les moteurs (chaleur). "
              "'dyn' = la coupure suit le throttle entre deux bornes.",
        "en": "Gyro lowpass (LPF): a lowpass filter on the gyro signal, before any PID maths. It removes "
              "high-frequency noise (motors/vibration). Too low it adds phase lag and destabilises; too "
              "high it lets noise into the motors (heat). 'dyn' = the cutoff follows throttle between two "
              "bounds.",
    },
    "dterm_lpf": {
        "fr": "D-term lowpass : filtre passe-bas sur le terme dérivé (D) des PID. Le D amplifie fortement "
              "le bruit, donc on le filtre plus que le reste. Souvent le filtre le plus critique : trop "
              "haut → moteurs chauds et bruit ; trop bas → D mou et retard qui ramène du propwash. À "
              "régler en priorité avec le RPM filter.",
        "en": "D-term lowpass: a lowpass on the PID derivative (D) term. D strongly amplifies noise, so "
              "it is filtered more than the rest. Often the most critical filter: too high → hot motors "
              "and noise; too low → mushy D and lag that brings propwash back. Tune it first, alongside "
              "the RPM filter.",
    },
    "dyn_notch": {
        "fr": "Dynamic notch : filtres très étroits qui pistent en temps réel les pics de bruit "
              "(résonances) et les coupent sans toucher au reste du spectre. 'count' = combien de pics "
              "traqués, 'Q' = finesse (Q haut = encoche étroite, moins de retard), 'min/max' = plage "
              "surveillée. C'est l'outil principal contre les résonances.",
        "en": "Dynamic notch: very narrow filters that track noise peaks (resonances) in real time and "
              "cut them without touching the rest of the spectrum. 'count' = how many peaks tracked, 'Q' "
              "= sharpness (high Q = narrow notch, less lag), 'min/max' = the watched range. The main "
              "tool against resonances.",
    },
    "rpm_filter": {
        "fr": "RPM filter : utilise la vitesse réelle des moteurs (télémétrie ESC/DShot) pour placer des "
              "encoches pile sur les harmoniques de rotation des hélices. Le filtre le plus efficace "
              "contre le bruit moteur : bien réglé, il permet d'ouvrir les autres filtres (gyro/D-term "
              "plus hauts) et donc de gagner en réactivité.",
        "en": "RPM filter: uses real motor speed (ESC/DShot telemetry) to place notches exactly on the "
              "props' rotation harmonics. The most effective filter against motor noise: when set right "
              "it lets you open the other filters (higher gyro/D-term) and so gain responsiveness.",
    },
    "dmax": {
        "fr": "D_max : valeur haute du terme D, atteinte seulement lors de mouvements brusques. Au repos "
              "le D reste à sa valeur basse (D_min, le D des PID) pour limiter le bruit ; il monte vers "
              "D_max sur les à-coups pour amortir. Si D_min = D_max, le D est fixe (pas de boost).",
        "en": "D_max: the high value of the D term, reached only on sharp moves. At rest D stays at its "
              "low value (D_min, the PID's D) to limit noise; it rises toward D_max on stick jabs to "
              "damp. If D_min = D_max, D is fixed (no boost).",
    },
    "pid": {
        "fr": "PID (P, I, D) : le cœur de la stabilisation. P = réactivité immédiate à l'erreur (trop "
              "haut = oscillation rapide) ; I = tient la consigne dans la durée et contre le vent (trop "
              "haut = rebond lent) ; D = amortit/anticipe (trop haut = bruit et chaleur). On les règle "
              "APRÈS le filtrage, car les filtres changent la marge de phase disponible.",
        "en": "PID (P, I, D): the heart of stabilisation. P = immediate reaction to error (too high = "
              "fast oscillation); I = holds the setpoint over time and against wind (too high = slow "
              "bounce); D = damps/anticipates (too high = noise and heat). Tune them AFTER filtering, "
              "because filters change the available phase margin.",
    },
    "throttle_map": {
        "fr": "Carte throttle × fréquence : spectre du gyro découpé par tranches de gaz. Les résonances "
              "moteur migrent avec le régime — une raie qui se décale en montant le gaz est d'origine "
              "moteur (RPM filter / dyn notch), une raie fixe est une résonance de cadre (notch statique).",
        "en": "Throttle × frequency map: the gyro spectrum sliced by throttle. Motor resonances migrate "
              "with rpm — a line that shifts as throttle rises is motor-borne (RPM filter / dyn notch), a "
              "fixed line is a frame resonance (static notch).",
    },
    "motor_harmonics": {
        "fr": "Harmoniques moteur : le bruit moteur se loge aux multiples de la fréquence de rotation des "
              "hélices, déduite de l'eRPM (rotation Hz = eRPM×100 / (pôles/2) / 60). Comme les 4 moteurs "
              "tournent à des régimes un peu différents et que le gaz varie, chaque harmonique (1×, 2×, 3×…) "
              "est une bande, pas une raie. Un pic de bruit DANS une bande = bruit moteur (du ressort du RPM "
              "filter / dyn_notch) ; un pic HORS bande = résonance de cadre/pale (notch statique).",
        "en": "Motor harmonics: motor noise sits at multiples of the prop rotation frequency, derived from "
              "eRPM (rotation Hz = eRPM×100 / (poles/2) / 60). Since the 4 motors run at slightly different "
              "rpm and throttle varies, each harmonic (1×, 2×, 3×…) is a band, not a line. A noise peak INSIDE "
              "a band = motor noise (RPM filter / dyn_notch territory); a peak OUTSIDE = a frame/prop "
              "resonance (static notch).",
    },
    "spectrogram": {
        "fr": "Spectrogramme : une carte temps × fréquence de l'énergie du gyro pendant le chirp. Le "
              "balayage du chirp apparaît comme une diagonale qui monte en fréquence ; une résonance "
              "apparaît comme une bande horizontale qui s'allume quand le sweep passe à sa fréquence. "
              "Sert à vérifier que le chirp a bien balayé toute la bande et à repérer visuellement les "
              "résonances et leur étalement.",
        "en": "Spectrogram: a time × frequency map of the gyro energy during the chirp. The chirp sweep "
              "shows up as a diagonal rising in frequency; a resonance shows up as a horizontal band that "
              "lights up when the sweep reaches its frequency. Use it to check the chirp actually swept the "
              "whole band and to spot resonances and their spread visually.",
    },
    "step_response": {
        "fr": "Réponse indicielle : la réaction de l'axe à un échelon de consigne, reconstruite depuis "
              "la même mesure que le Bode. C'est le pendant temporel : on y lit l'overshoot (dépassement "
              "%), le temps de montée et l'établissement. Un fort overshoot ≙ une marge de phase faible "
              "sur le Bode ; les deux courbes racontent la même histoire.",
        "en": "Step response: the axis' reaction to a step in setpoint, reconstructed from the same "
              "measurement as the Bode. It is the time-domain companion: read off overshoot (%), rise "
              "time and settling. A large overshoot ≙ a low phase margin on the Bode; both curves tell "
              "the same story.",
    },
    "noise_psd": {
        "fr": "Spectre de bruit (PSD, dB) : densité de puissance du gyro vs fréquence, mesurée hors chirp. "
              "Référence = le plancher de bruit (la base plate en haute fréquence, stable d'un vol à l'autre), "
              "donc 0 dB = plancher et un pic se lit par sa hauteur AU-DESSUS du plancher. Les deux grandeurs "
              "fiables (indépendantes de la référence) : l'atténuation brut→filtré (ce que les filtres enlèvent) "
              "et le résiduel filtré au-dessus du plancher (ce qui reste). Pas de seuil absolu type « −10 dB » : "
              "c'est arbitraire et dépendant du vol ; les vrais juges sont la marge de phase et la température moteur.",
        "en": "Noise spectrum (PSD, dB): gyro power density vs frequency, measured outside the chirp. Reference "
              "= the noise floor (the flat HF baseline, stable across flights), so 0 dB = floor and a peak is read "
              "by its height ABOVE the floor. The two reliable (reference-independent) quantities: the raw→filtered "
              "attenuation (what the filters remove) and the filtered residual above the floor (what remains). No "
              "absolute '−10 dB' threshold: it is arbitrary and flight-dependent; the real judges are phase margin "
              "and motor temperature.",
    },
    "propwash": {
        "fr": "Propwash : les oscillations/secousses quand le drone retombe dans ses propres turbulences "
              "(descentes rapides, sorties de virage). Souvent lié à un D mou ou trop filtré, ou à une "
              "marge de phase faible : la boucle n'amortit pas assez vite.",
        "en": "Propwash: the wobble/shaking when the drone falls back into its own turbulence (fast "
              "descents, corner exits). Often tied to a mushy or over-filtered D, or a low phase margin: "
              "the loop does not damp fast enough.",
    },
}

# ---------------------------------------------------------------------------
# UI strings (FR/EN) for the switchable report
# ---------------------------------------------------------------------------

STRINGS = {
    "fr": {
        "title": "Chirp — assistant de tuning", "lang_btn": "EN", "pass_word": "Passe",
        "guide_h": "Guide de tuning",
        "guide_order": "<b>Ordre recommandé :</b> on règle {filt} AVANT {pid}. Chaque filtre ajoute du retard "
                       "de {phase} qui grignote la {pm} : régler les gains avant d'avoir figé le filtrage donne "
                       "des PID qui ne tiendront plus ensuite. On nettoie donc le bruit et les {res} d'abord, "
                       "puis on monte les gains.",
        "guide_single": "📍 <b>Passe unique.</b> Si ce log a été pris juste après un reflash, les PID et filtres "
                        "sont probablement aux <b>valeurs par défaut</b> : c'est ton point de référence (baseline). "
                        "Applique les pistes de l'<b>Étape 1 (Filtrage)</b>, refais un vol en <code>debug_mode="
                        "CHIRP</code>, puis compare — et seulement ensuite l'<b>Étape 2 (PID)</b>.",
        "guide_multi": "📍 <b>{n} passes accumulées.</b> Les courbes de l'Étape 2 superposent toutes les passes "
                       "pour voir l'effet de tes changements (diffs de config en Étape 3). La passe la plus récente "
                       "sert de référence pour les pistes ci-dessous.",
        "guide_add": "➕ Pour ajouter une passe : modifie filtres/PID, refais un log chirp, puis relance "
                     "<code>chirp_analysis.py nouveau.bbl --html report.html</code> — il s'ajoute à l'historique.",
        "cfg_h": "Réglages actuels", "cfg_sub": "(extraits du log — passe de référence)",
        "synth_h": "Lecture d'ensemble",
        "step1_h": "Filtrage", "step1_sub": "— à régler en premier",
        "tmap_h": "Carte throttle × fréquence", "filt_h": "Pistes de filtrage",
        "tmap_none": "indisponible (ni rcCommand[3] ni motor loggés). Active le throttle/les moteurs en blackbox.",
        "noise_h": "Spectre de bruit gyro (PSD, dB)",
        "noise_cap": "{psd} — brut (gyroUnfilt) vs filtré (gyroADC), hors chirp. 0 dB = plancher de bruit ; "
                     "un pic dont le résiduel filtré retombe dans le plancher est aplati. Le repère +6 dB est "
                     "indicatif (prominence d'une raie), pas une spec — les vrais juges sont la marge de phase et "
                     "la température moteur.",
        "noise_cap_nounfilt": "{psd} — gyro filtré (gyroUnfilt absent du log). 0 dB = plancher de bruit.",
        "leg_raw": "brut (unfilt)", "leg_filt": "filtré (gyroADC)",
        "leg_floor": "plancher", "leg_resid": "résiduel (indicatif)", "leg_motor": "harmoniques moteur",
        "step2_h": "PID", "step2_sub": "— après avoir figé le filtrage",
        "spectro_h": "Balayage du chirp (spectrogramme)",
        "spectro_cap": "{sg} — gyro {ax} pendant le sweep. La diagonale qui monte = le chirp ; les bandes "
                       "horizontales = résonances qui s'allument quand le sweep les traverse.",
        "overlay": "Courbes superposées :",
        "bode_h": "Réponse en fréquence (Bode)", "step_h": "Réponse indicielle (temporel)",
        "coh_cap": "{coh} — fiabilité de la mesure par fréquence (grisé si &lt; {gate})",
        "lead_pid": "Piste PID :", "margin": "marge", "no_xover": "pas de crossover",
        "step3_h": "Historique & comparaison",
        "step3_single": "Une seule passe pour l'instant. Refais un log chirp après tes modifs : il s'empilera "
                        "ici pour la comparaison avant/après.",
        "step3_changes": "↳ changements vs passe précédente :",
        "cmp_h": "Comparaison des réglages",
        "cmp_none": "Réglages PID + filtres identiques sur toutes les passes — les écarts de courbes "
                    "viennent du vol (batterie, throttle, bruit), pas du tune.",
        "glossary_h": "Glossaire",
        "w_filt": "le filtrage", "w_pid": "les PID", "w_phase": "phase",
        "w_pm": "marge de stabilité", "w_res": "résonances",
        "leg_gyro": "gyro lpf", "leg_dterm": "dterm lpf", "leg_notch": "plage dyn_notch", "leg_xover": "crossover 0 dB",
        "metrics": "overshoot {ov}% · montée {rise} ms · établi {settle} ms",
        "render_err": "⚠ Rendu interrompu : ",
    },
    "en": {
        "title": "Chirp — tuning assistant", "lang_btn": "FR", "pass_word": "Pass",
        "guide_h": "Tuning guide",
        "guide_order": "<b>Recommended order:</b> set {filt} BEFORE {pid}. Every filter adds {phase} lag that "
                       "eats into the {pm}: tuning gains before the filtering is frozen gives PIDs that won't "
                       "hold afterwards. So clean up noise and {res} first, then raise the gains.",
        "guide_single": "📍 <b>Single pass.</b> If this log was taken right after a reflash, the PIDs and filters "
                        "are probably at their <b>defaults</b>: that's your baseline. Apply the <b>Step 1 "
                        "(Filtering)</b> leads, re-fly in <code>debug_mode=CHIRP</code>, then compare — and only "
                        "then <b>Step 2 (PID)</b>.",
        "guide_multi": "📍 <b>{n} passes accumulated.</b> The Step 2 curves overlay every pass so you can see the "
                       "effect of your changes (config diffs in Step 3). The most recent pass is the reference for "
                       "the leads below.",
        "guide_add": "➕ To add a pass: change filters/PID, re-fly a chirp log, then re-run "
                     "<code>chirp_analysis.py new.bbl --html report.html</code> — it appends to the history.",
        "cfg_h": "Current settings", "cfg_sub": "(read from the log — reference pass)",
        "synth_h": "Overview",
        "step1_h": "Filtering", "step1_sub": "— set this first",
        "tmap_h": "Throttle × frequency map", "filt_h": "Filtering leads",
        "tmap_none": "unavailable (neither rcCommand[3] nor motors logged). Enable throttle/motors in blackbox.",
        "noise_h": "Gyro noise spectrum (PSD, dB)",
        "noise_cap": "{psd} — raw (gyroUnfilt) vs filtered (gyroADC), outside the chirp. 0 dB = noise floor; a "
                     "peak whose filtered residual falls back into the floor is flattened. The +6 dB line is "
                     "indicative (a line's prominence), not a spec — the real judges are phase margin and motor "
                     "temperature.",
        "noise_cap_nounfilt": "{psd} — filtered gyro (gyroUnfilt absent from the log). 0 dB = noise floor.",
        "leg_raw": "raw (unfilt)", "leg_filt": "filtered (gyroADC)",
        "leg_floor": "floor", "leg_resid": "residual (indicative)", "leg_motor": "motor harmonics",
        "step2_h": "PID", "step2_sub": "— after the filtering is frozen",
        "spectro_h": "Chirp sweep (spectrogram)",
        "spectro_cap": "{sg} — {ax} gyro during the sweep. The rising diagonal = the chirp; horizontal "
                       "bands = resonances lighting up as the sweep crosses them.",
        "overlay": "Overlaid curves:",
        "bode_h": "Frequency response (Bode)", "step_h": "Step response (time domain)",
        "coh_cap": "{coh} — per-frequency measurement reliability (greyed if &lt; {gate})",
        "lead_pid": "PID lead:", "margin": "margin", "no_xover": "no crossover",
        "step3_h": "History & comparison",
        "step3_single": "Only one pass so far. Re-fly a chirp log after your changes: it will stack up here for "
                        "before/after comparison.",
        "step3_changes": "↳ changes vs previous pass:",
        "cmp_h": "Settings comparison",
        "cmp_none": "Identical PID + filter settings across all passes — curve differences come from the "
                    "flight (battery, throttle, noise), not the tune.",
        "glossary_h": "Glossary",
        "w_filt": "filtering", "w_pid": "the PIDs", "w_phase": "phase",
        "w_pm": "stability margin", "w_res": "resonances",
        "leg_gyro": "gyro lpf", "leg_dterm": "dterm lpf", "leg_notch": "dyn_notch range", "leg_xover": "0 dB crossover",
        "metrics": "overshoot {ov}% · rise {rise} ms · settle {settle} ms",
        "render_err": "⚠ Render interrupted: ",
    },
}


# ---------------------------------------------------------------------------
# Self-contained HTML report (vanilla JS / <canvas>, no external dependencies)
# ---------------------------------------------------------------------------

def _html_report(report: dict, file_name: str) -> str:
    payload = json.dumps(report)
    # The renderer is intentionally dependency-free: a tiny canvas plotting engine.
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Chirp report — {file_name}</title>
<style>
  body {{ font: 13px/1.5 system-ui, sans-serif; margin: 20px; background:#11141a; color:#dfe3ea; max-width:1800px; }}
  h1 {{ font-size: 19px; }} h2 {{ font-size: 15px; margin: 22px 0 8px; color:#9ecbff; }}
  h3 {{ font-size: 13px; color:#8893a5; margin:14px 0 4px; text-transform:uppercase; letter-spacing:.5px; }}
  .axis {{ border:1px solid #2a2f3a; border-radius:8px; padding:12px 14px; margin-bottom:18px; background:#171b22; }}
  .step {{ border-left:3px solid #4fc3f7; }}
  .step.pid {{ border-left-color:#ffd479; }}
  .step.cmp {{ border-left-color:#80cbc4; }}
  canvas {{ display:block; background:#0d1016; border-radius:4px; margin:6px 0; }}
  .diag {{ color:#c9d2e0; }} .diag li {{ margin:2px 0; }}
  .meta {{ color:#8893a5; font-size:12px; }}
  .sugg {{ margin:8px 0 0; padding-left:18px; }} .sugg li {{ margin:3px 0; }}
  .pid {{ color:#ffd479; margin:8px 0 0; }}
  .step-d {{ color:#9cd0e0; margin:6px 0 0; }}
  .filt li {{ color:#9ce0c0; }}
  .cfg {{ color:#aab4c4; font-size:12px; line-height:1.8; }}
  .legend {{ font-size:11px; color:#8893a5; margin:0 0 6px; }}
  .legend span {{ margin-right:14px; white-space:nowrap; }}
  .guide {{ background:#141c26; border:1px solid #28425c; }}
  .guide b {{ color:#9ecbff; }}
  .stepnum {{ display:inline-block; min-width:20px; height:20px; line-height:20px; text-align:center;
             border-radius:50%; background:#28425c; color:#cfe3ff; font-weight:600; margin-right:6px; }}
  .term {{ border-bottom:1px dotted #6b7689; cursor:help; position:relative; }}
  .term:hover::after {{ content:attr(data-tip); position:absolute; left:0; top:1.5em; z-index:20;
     width:340px; white-space:normal; background:#0b0e13; color:#e6eaf2; border:1px solid #3a4150;
     border-radius:6px; padding:9px 11px; font:12px/1.55 system-ui; box-shadow:0 6px 18px rgba(0,0,0,.55); }}
  .glos dt {{ color:#9ecbff; font-weight:600; margin-top:8px; }}
  .glos dd {{ margin:2px 0 0; color:#c2cad6; }}
  .swatch {{ display:inline-block; width:11px; height:11px; border-radius:2px; margin-right:5px; vertical-align:middle; }}
  .diff {{ color:#ffd479; }}
  table.cmp {{ border-collapse:collapse; font-size:12px; margin-top:8px; }}
  table.cmp th, table.cmp td {{ border:1px solid #2a2f3a; padding:3px 9px; text-align:left; color:#c2cad6; }}
  table.cmp th {{ color:#9ecbff; font-weight:600; }}
  table.cmp td.lbl {{ color:#8893a5; }}
  table.cmp td.chg {{ color:#ffd479; font-weight:600; background:#241f12; }}
  .langbtn {{ position:fixed; top:16px; right:16px; z-index:30; background:#28425c; color:#cfe3ff;
     border:1px solid #3a5a78; border-radius:6px; padding:5px 12px; cursor:pointer; font:600 12px system-ui; }}
  .twocol {{ display:flex; gap:14px; flex-wrap:wrap; }}
  .twocol > div {{ flex:1 1 380px; }}
</style></head><body>
<button id="langbtn" class="langbtn"></button>
<h1 id="h1"></h1>
<div id="root"></div>
<script>
const FILE = {json.dumps(file_name)};
const R = {payload};
const GL = R._glossary || {{}};
const STR = R._strings || {{}};
const PASSES = R.passes || [];
const PRIMARY = R.primary_index || 0;
const PRI = PASSES[PRIMARY] || {{}};
const CFG = PRI.config || {{}};
const GATE = {COHERENCE_GATE};
const PAL = ['#7686a0','#9ad','#80cbc4','#ba9cff','#f48fb1','#aed581','#ffb74d','#4fc3f7'];
let W = 880; const Hh = 150, PAD = 46;   // W is recomputed responsively at each render()
let LANG = R.lang || 'fr';
window.addEventListener('error', e => {{
  const d=document.createElement('pre'); d.style.color='#ff8a80';
  d.textContent=(T('render_err'))+e.message+(e.lineno?(' ('+e.lineno+')'):'');
  document.body.appendChild(d);
}});
function T(k) {{ const s=STR[LANG]||STR.fr||{{}}; return (k in s)? s[k] : k; }}
function tip(k,label) {{ const g=GL[k]||{{}}; const t=(g[LANG]||g.fr||'').replace(/"/g,'&quot;');
  return '<span class="term" data-tip="'+t+'">'+(label||k)+'</span>'; }}
function loc(o) {{ return o ? (o[LANG]||o.fr||o.en||'') : ''; }}
function passLabel(p) {{ return T('pass_word')+' '+p.n+' — '+p.ts+(p.file?(' ('+p.file+')'):''); }}
function cfgFields(cfg) {{
  if (!cfg) return [];
  const o=[];
  for (const ax of ['roll','pitch','yaw']) {{ const p=(cfg.pids||{{}})[ax]; if (p) o.push([ax+' P/I/D', p.join('/')]); }}
  if (cfg.d_max) o.push(['D_max', cfg.d_max.join('/')]);
  const lpf=(key,lbl)=>{{ const d=cfg[key]||{{}}; const v=(d.dyn||d.static); if(v!=null){{ const vs=Array.isArray(v)?v.join('–'):v; o.push([lbl,(vs+' Hz '+(d.type||'')).trim()]); }} }};
  lpf('gyro_lpf1','gyro LPF1'); lpf('gyro_lpf2','gyro LPF2'); lpf('dterm_lpf1','D-term LPF1'); lpf('dterm_lpf2','D-term LPF2');
  const dn=cfg.dyn_notch||{{}}; if(dn.count!=null) o.push(['dyn_notch','×'+dn.count+' Q'+dn.q+' ['+dn.min+'–'+dn.max+' Hz]']);
  if(cfg.rpm_harmonics!=null) o.push(['RPM filter','×'+cfg.rpm_harmonics]);
  return o;
}}
function el(tag,cls,html) {{ const e=document.createElement(tag); if(cls)e.className=cls; if(html!=null)e.innerHTML=html; return e; }}
function mkCanvas(parent,h) {{ const c=document.createElement('canvas'); c.width=W; c.height=h; parent.appendChild(c); return c; }}
function lerp(v,a,b,A,B) {{ return A + (v-a)*(B-A)/((b-a)||1); }}
function logx(f,fmin,fmax) {{ return lerp(Math.log10(f), Math.log10(fmin), Math.log10(fmax), PAD, W-12); }}
function drawAxes(ctx,h,fmin,fmax,ymin,ymax,ylabel) {{
  ctx.clearRect(0,0,W,h); ctx.strokeStyle='#2a2f3a'; ctx.fillStyle='#8893a5'; ctx.font='10px sans-serif'; ctx.lineWidth=1;
  for (let k=0;k<=4;k++) {{ const yv=ymin+(ymax-ymin)*k/4, y=lerp(yv,ymin,ymax,h-22,8);
    ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-12,y); ctx.stroke();
    ctx.fillText(yv.toFixed(ymax-ymin>=10?0:1), 4, y+3); }}
  for (let d=Math.floor(Math.log10(fmin)); d<=Math.ceil(Math.log10(fmax)); d++) for (const m of [1,2,5]) {{
    const f=m*Math.pow(10,d); if (f<fmin||f>fmax) continue; const x=logx(f,fmin,fmax);
    ctx.strokeStyle='#20242e'; ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,h-22); ctx.stroke();
    ctx.fillStyle='#8893a5'; ctx.fillText(f>=1000?(f/1000)+'k':f, x-6, h-8); }}
  ctx.fillStyle='#9ecbff'; ctx.fillText(ylabel, PAD, 7);
}}
function drawAxesLin(ctx,h,xmax,ymin,ymax,ylabel) {{
  ctx.clearRect(0,0,W,h); ctx.strokeStyle='#2a2f3a'; ctx.fillStyle='#8893a5'; ctx.font='10px sans-serif'; ctx.lineWidth=1;
  for (let k=0;k<=4;k++) {{ const yv=ymin+(ymax-ymin)*k/4, y=lerp(yv,ymin,ymax,h-22,8);
    ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-12,y); ctx.stroke(); ctx.fillText(yv.toFixed(2), 4, y+3); }}
  for (let k=0;k<=5;k++) {{ const xv=xmax*k/5, x=lerp(xv,0,xmax,PAD,W-12);
    ctx.strokeStyle='#20242e'; ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,h-22); ctx.stroke();
    ctx.fillStyle='#8893a5'; ctx.fillText(xv.toFixed(0)+(k===5?' ms':''), x-6, h-8); }}
  ctx.fillStyle='#9ecbff'; ctx.fillText(ylabel, PAD, 7);
}}
function plotLine(ctx,h,F,Y,coh,fmin,fmax,ymin,ymax,color,opts) {{
  opts=opts||{{}}; const lw=opts.lw||1.8;
  for (let i=1;i<F.length;i++) {{
    const trusted = coh[i]>=GATE && coh[i-1]>=GATE;
    ctx.globalAlpha = opts.dim ? 0.5 : 1;
    ctx.strokeStyle = trusted ? color : (opts.dim?'rgba(120,130,150,0.15)':'rgba(120,130,150,0.35)');
    ctx.lineWidth = trusted ? lw : 1;
    ctx.beginPath();
    ctx.moveTo(logx(F[i-1],fmin,fmax), lerp(Y[i-1],ymin,ymax,h-22,8));
    ctx.lineTo(logx(F[i],fmin,fmax),   lerp(Y[i],ymin,ymax,h-22,8));
    ctx.stroke();
  }}
  ctx.globalAlpha=1;
}}
function plotLin(ctx,h,X,Y,xmax,ymin,ymax,color,opts) {{
  opts=opts||{{}}; ctx.globalAlpha=opts.dim?0.5:1; ctx.strokeStyle=color; ctx.lineWidth=opts.lw||1.8;
  ctx.beginPath();
  for (let i=0;i<X.length;i++) {{ const px=lerp(X[i],0,xmax,PAD,W-12), py=lerp(Y[i],ymin,ymax,h-22,8);
    i?ctx.lineTo(px,py):ctx.moveTo(px,py); }}
  ctx.stroke(); ctx.globalAlpha=1;
}}
function hline(ctx,h,val,ymin,ymax,color,label) {{
  const y=lerp(val,ymin,ymax,h-22,8); ctx.strokeStyle=color; ctx.setLineDash([4,3]);
  ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-12,y); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle=color; ctx.fillText(label, W-70, y-3);
}}
function vline(ctx,h,f,fmin,fmax,color,label) {{
  if (!f || f<fmin || f>fmax) return;
  const x=logx(f,fmin,fmax); ctx.strokeStyle=color; ctx.lineWidth=1; ctx.setLineDash([2,3]);
  ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,h-22); ctx.stroke(); ctx.setLineDash([]);
  if (label) {{ ctx.fillStyle=color; ctx.fillText(label, x+2, 16); }}
}}
function vband(ctx,h,f0,f1,fmin,fmax,color) {{
  if (!f0||!f1) return; const a=logx(Math.max(f0,fmin),fmin,fmax), b=logx(Math.min(f1,fmax),fmin,fmax);
  if (b<=a) return; ctx.fillStyle=color; ctx.fillRect(a,8,b-a,h-30);
}}
function filterOverlay(ctx,h,fmin,fmax,xover) {{
  if (CFG.dyn_notch) vband(ctx,h,CFG.dyn_notch.min,CFG.dyn_notch.max,fmin,fmax,'rgba(255,212,121,0.07)');
  if (CFG.gyro_lpf1 && CFG.gyro_lpf1.dyn) {{ vline(ctx,h,CFG.gyro_lpf1.dyn[0],fmin,fmax,'#5a9bd4','gyroLPF'); vline(ctx,h,CFG.gyro_lpf1.dyn[1],fmin,fmax,'#5a9bd4',''); }}
  if (CFG.dterm_lpf1 && CFG.dterm_lpf1.dyn) {{ vline(ctx,h,CFG.dterm_lpf1.dyn[0],fmin,fmax,'#d48fd4','dtermLPF'); vline(ctx,h,CFG.dterm_lpf1.dyn[1],fmin,fmax,'#d48fd4',''); }}
  vline(ctx,h,xover,fmin,fmax,'#ff8a80','xover');
}}
const root=document.getElementById('root');
const single = R.total_passes<=1;

function render() {{
  root.innerHTML='';
  W = Math.max(720, Math.min(1760, window.innerWidth - 48));   // responsive: fill the window
  document.getElementById('h1').innerHTML = T('title')+' <span class="meta">— '+FILE+'</span>';
  document.getElementById('langbtn').textContent = T('lang_btn');

  // ---- Guide ----
  {{
    const g=el('div','axis guide'); let s='<h2>'+T('guide_h')+'</h2>';
    s+='<p>'+T('guide_order')
        .replace('{{filt}}',tip('gyro_lpf','<b>'+T('w_filt')+'</b>'))
        .replace('{{pid}}',tip('pid','<b>'+T('w_pid')+'</b>'))
        .replace('{{phase}}',tip('phase',T('w_phase')))
        .replace('{{pm}}',tip('phase_margin',T('w_pm')))
        .replace('{{res}}',tip('resonance',T('w_res')))+'</p>';
    s+='<p>'+(single ? T('guide_single') : T('guide_multi').replace('{{n}}',R.total_passes))+'</p>';
    s+='<p class=meta>'+T('guide_add')+'</p>';
    g.innerHTML=s; root.appendChild(g);
  }}

  // ---- Current settings ----
  if (CFG.pids) {{
    const cb=el('div','axis'); let s='<h2>'+T('cfg_h')+' <span class=meta>'+T('cfg_sub')+'</span></h2><div class=cfg>';
    s+='<b>'+tip('pid','PID')+'</b> — '+Object.entries(CFG.pids).map(([a,v])=>a+' P'+v[0]+'/I'+v[1]+'/D'+v[2]).join(' &nbsp; ');
    if (CFG.d_max) s+=' &nbsp; '+tip('dmax','D_max')+' '+CFG.d_max.join('/');
    s+='<br>';
    if (CFG.gyro_lpf1) s+='<b>'+tip('gyro_lpf','gyro')+'</b> lpf1 '+(CFG.gyro_lpf1.dyn?CFG.gyro_lpf1.dyn.join('–'):CFG.gyro_lpf1.static)+' Hz ('+CFG.gyro_lpf1.type+'), lpf2 '+(CFG.gyro_lpf2?CFG.gyro_lpf2.static:'?')+' Hz<br>';
    if (CFG.dterm_lpf1) s+='<b>'+tip('dterm_lpf','D-term')+'</b> lpf1 '+(CFG.dterm_lpf1.dyn?CFG.dterm_lpf1.dyn.join('–'):CFG.dterm_lpf1.static)+' Hz, lpf2 '+(CFG.dterm_lpf2?CFG.dterm_lpf2.static:'?')+' Hz<br>';
    if (CFG.dyn_notch) s+='<b>'+tip('dyn_notch','dyn_notch')+'</b> ×'+CFG.dyn_notch.count+' Q'+CFG.dyn_notch.q+' ['+CFG.dyn_notch.min+'–'+CFG.dyn_notch.max+' Hz] &nbsp; <b>'+tip('rpm_filter','RPM filter')+'</b> ×'+CFG.rpm_harmonics;
    s+='</div>'; cb.innerHTML=s; root.appendChild(cb);
  }}

  // ---- Overview (linked observations: filter -> phase -> P/D) ----
  if (PRI.synthesis && PRI.synthesis.length) {{
    const box=el('div','axis guide'); root.appendChild(box);
    box.appendChild(el('h2',null,T('synth_h')));
    const ul=el('ul','sugg'); for (const o of PRI.synthesis) ul.appendChild(el('li',null,loc(o)));
    box.appendChild(ul);
  }}

  // ---- Step 1: Filtering ----
  {{
    const box=el('div','axis step'); root.appendChild(box);
    box.appendChild(el('h2',null,'<span class=stepnum>1</span>'+tip('gyro_lpf',T('step1_h'))+' '+T('step1_sub')));
    const tm=PRI.throttle_map;
    if (tm && tm.freqs && tm.freqs.length) {{
      box.appendChild(el('h3',null,tip('throttle_map',T('tmap_h'))+' ('+tm.axis+' gyro · '+(tm.source||'?')+')'));
      const rows=tm.levels_db.length, cols=tm.freqs.length;
      const flat=tm.levels_db.flat().filter(v=>v!==null);
      const lo=Math.min(...flat), hi=Math.max(...flat);
      const cw=W-PAD-12, chh=22, H2=rows*chh+30;
      const ctx=mkCanvas(box,H2).getContext('2d'); ctx.clearRect(0,0,W,H2);
      for (let r=0;r<rows;r++) for (let c=0;c<cols;c++) {{
        const v=tm.levels_db[r][c]; if (v===null) continue; const tn=(v-lo)/((hi-lo)||1);
        ctx.fillStyle='rgb('+Math.round(255*Math.min(1,tn*1.6))+','+Math.round(120*Math.max(0,1-Math.abs(tn-0.5)*2))+','+Math.round(255*(1-tn))+')';
        ctx.fillRect(PAD+c*cw/cols, 8+(rows-1-r)*chh, cw/cols+1, chh);
      }}
      ctx.fillStyle='#8893a5'; ctx.font='10px sans-serif';
      for (let r=0;r<rows;r++) ctx.fillText(tm.throttle_bins[r], 4, 8+(rows-1-r)*chh+14);
      const fmin=tm.freqs[0], fmax=tm.freqs[cols-1];
      for (let d=Math.floor(Math.log10(fmin));d<=Math.ceil(Math.log10(fmax));d++) for (const m of [1,2,5]) {{
        const f=m*Math.pow(10,d); if (f<fmin||f>fmax) continue;
        const x=PAD+(Math.log10(f)-Math.log10(fmin))/(Math.log10(fmax)-Math.log10(fmin))*cw;
        ctx.fillText(f>=1000?(f/1000)+'k':f, x-6, H2-6); }}
      ctx.fillStyle='#9ecbff'; ctx.fillText('throttle ↑   freq (Hz) →', PAD, H2-18);
      const tmx=f=>PAD+(Math.log10(f)-Math.log10(fmin))/(Math.log10(fmax)-Math.log10(fmin))*cw;
      const tvl=(f,col,lab)=>{{ if(!f||f<fmin||f>fmax)return; const x=tmx(f);
        ctx.strokeStyle=col; ctx.setLineDash([3,3]); ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,H2-26); ctx.stroke(); ctx.setLineDash([]);
        if(lab){{ctx.fillStyle=col; ctx.fillText(lab,x+2,18);}} }};
      if (CFG.dyn_notch) {{ tvl(CFG.dyn_notch.min,'#ffd479','dyn_notch'); tvl(CFG.dyn_notch.max,'#ffd479',''); }}
      for (const su of (PRI.filter_suggestions||[])) tvl(su.freq_hz,'#ff8a80','rés');
    }} else {{
      box.appendChild(el('p','meta',tip('throttle_map',T('tmap_h'))+' — '+T('tmap_none')));
    }}

    // noise spectrum (raw vs filtered PSD, dB) — drives the filtering decision
    const ns=PRI.noise_spectrum;
    if (ns && ns.freqs && ns.freqs.length) {{
      box.appendChild(el('h3',null,tip('noise_psd',T('noise_h'))+' ('+ns.axis+' gyro)'));
      const F=ns.freqs, fmin=Math.max(30,F[0]), fmax=F[F.length-1];
      // floor-relative axis: 0 = noise floor. Scale to the noise region (95th pct) so a stray
      // low-freq motion bump doesn't squash the plot.
      const sorted=ns.raw_db.slice().sort((a,b)=>a-b); const hiR=sorted[Math.floor(sorted.length*0.97)];
      let lo=Math.max(-25,Math.min(-6,...ns.filt_db)), hi=Math.max(12,Math.ceil(hiR/5)*5+3);
      const H3=180, nc=mkCanvas(box,H3).getContext('2d');
      drawAxes(nc,H3,fmin,fmax,lo,hi,'dB/plancher');
      if (CFG.dyn_notch) vband(nc,H3,CFG.dyn_notch.min,CFG.dyn_notch.max,fmin,fmax,'rgba(255,212,121,0.07)');
      nc.font='10px sans-serif';
      // motor-harmonic bands (from eRPM): where motor noise lives -> a peak in a band is motor noise
      const mh=ns.motor;
      if (mh && mh.bands) for (const b of mh.bands) {{
        vband(nc,H3,b.lo,b.hi,fmin,fmax,'rgba(255,138,80,0.12)');
        if (b.hi>fmin && b.lo<fmax) {{ nc.fillStyle='#ff9a6a'; nc.fillText(b.n+'×', logx(Math.max(b.lo,fmin),fmin,fmax)+1, H3-24); }}
      }}
      // vertical lines = each filter's cut-off frequency (the LPF starts attenuating above it)
      const vcut=(fc,col,lab,yl)=>{{ if(!fc||fc<fmin||fc>fmax)return; const x=logx(fc,fmin,fmax);
        nc.strokeStyle=col; nc.lineWidth=1; nc.setLineDash([3,3]); nc.beginPath(); nc.moveTo(x,8); nc.lineTo(x,H3-22); nc.stroke(); nc.setLineDash([]);
        if(lab){{ nc.fillStyle=col; nc.fillText(lab,Math.min(x+2,W-44),yl||16); }} }};
      if (CFG.gyro_lpf1 && CFG.gyro_lpf1.dyn) {{ vcut(CFG.gyro_lpf1.dyn[0],'#5a9bd4'); vcut(CFG.gyro_lpf1.dyn[1],'#5a9bd4','gLPF1',16); }}
      if (CFG.gyro_lpf2) vcut(CFG.gyro_lpf2.static,'#79c0ff','gLPF2',16);
      if (CFG.dterm_lpf1 && CFG.dterm_lpf1.dyn) vcut(CFG.dterm_lpf1.dyn[1],'#d48fd4','dLPF1',28);
      if (CFG.dterm_lpf2) vcut(CFG.dterm_lpf2.static,'#d48fd4','dLPF2',28);
      hline(nc,H3,0,lo,hi,'#7e8aa0','plancher');                              // 0 dB = noise floor
      hline(nc,H3,{RESIDUAL_OK_DB:g},lo,hi,'#ff8a80','+{RESIDUAL_OK_DB:g} dB');  // indicative residual-resonance guide
      const ones=F.map(_=>1);
      if (ns.has_unfilt) plotLine(nc,H3,F,ns.filt_db,ones,fmin,fmax,lo,hi,'#80cbc4',{{lw:1.6}});
      plotLine(nc,H3,F,ns.raw_db,ones,fmin,fmax,lo,hi,'#4fc3f7',{{lw:1.8}});
      nc.font='10px sans-serif'; let _lab=0;
      for (const pk of (ns.peaks||[])) {{ if (pk.freq_hz<fmin||pk.freq_hz>fmax) continue;
        const x=logx(pk.freq_hz,fmin,fmax), y=lerp(pk.above_floor_db,lo,hi,H3-22,8);
        nc.fillStyle='#ffd479'; nc.beginPath(); nc.arc(x,y,2.6,0,7); nc.fill();
        if (pk.above_floor_db >= {RESIDUAL_OK_DB:g}) {{ const dy=(_lab++ %2)?12:-3;  // stagger to avoid overlap
          nc.fillText(pk.freq_hz.toFixed(0)+'Hz +'+pk.above_floor_db.toFixed(0)+'dB', x+4, y+dy); }} }}
      box.appendChild(el('div','legend',
        (ns.has_unfilt?('<span style="color:#4fc3f7">— '+T('leg_raw')+'</span><span style="color:#80cbc4">— '+T('leg_filt')+'</span>'):'<span style="color:#4fc3f7">— gyro</span>')+
        '<span style="color:#7e8aa0">-- '+T('leg_floor')+'</span>'+
        '<span style="color:#ff8a80">-- '+T('leg_resid')+'</span>'+
        '<span style="color:#5a9bd4">| '+tip('gyro_lpf','coupures gyro LPF')+'</span>'+
        '<span style="color:#d48fd4">| '+tip('dterm_lpf','coupures D-term LPF')+'</span>'+
        '<span style="color:#ffd479">▮ '+tip('dyn_notch','dyn_notch')+'</span>'+
        (ns.motor?'<span style="color:#ff9a6a">▮ '+tip('motor_harmonics',T('leg_motor'))+'</span>':'')));
      box.appendChild(el('div','legend',(ns.has_unfilt?T('noise_cap'):T('noise_cap_nounfilt')).replace('{{psd}}',tip('noise_psd','PSD'))));
    }}

    const fsug=PRI.filter_suggestions||[], nsug=PRI.noise_suggestions||[];
    let s='<h3>'+tip('resonance',T('filt_h'))+'</h3><ul class="sugg filt">';
    for (const x of fsug) s+='<li>'+loc(x)+'</li>';
    for (const x of nsug) s+='<li>'+loc(x)+'</li>';
    if (!fsug.length && !nsug.length) s+='<li>—</li>';
    s+='</ul>'; box.appendChild(el('div',null,s));
  }}

  // ---- Step 2: PID (Bode + step response, all passes overlaid) ----
  {{
    const head=el('div','axis step pid'); root.appendChild(head);
    head.appendChild(el('h2',null,'<span class=stepnum>2</span>'+tip('pid',T('step2_h'))+' '+T('step2_sub')));
    head.appendChild(el('p','meta',T('overlay')+' '+PASSES.map((p,i)=>'<span class=swatch style="background:'+PAL[i%PAL.length]+'"></span>'+passLabel(p)).join(' &nbsp; ')));
    // chirp spectrogram (primary pass): the rising sweep + resonances as horizontal bands
    const sg=PRI.spectrogram;
    if (sg && sg.levels_db && sg.levels_db.length) {{
      head.appendChild(el('h3',null,tip('spectrogram',T('spectro_h'))+' ('+sg.axis+' gyro)'));
      const rows=sg.levels_db.length, cols=sg.levels_db[0].length;
      const cw=W-PAD-12, Hs=Math.max(220,rows*1.6), cellW=cw/cols, cellH=(Hs-30)/rows;
      const ctx=mkCanvas(head,Hs).getContext('2d'); ctx.clearRect(0,0,W,Hs);
      const lo=-28, hi=0;   // fixed window for contrast: cells within 28 dB of each column's max
      for (let r=0;r<rows;r++) for (let c=0;c<cols;c++) {{
        const v=sg.levels_db[r][c]; const tn=Math.max(0,Math.min(1,(v-lo)/((hi-lo)||1)));
        ctx.fillStyle='rgb('+Math.round(255*Math.min(1,tn*1.6))+','+Math.round(150*Math.max(0,1-Math.abs(tn-0.55)*2))+','+Math.round(255*(1-tn))+')';
        ctx.fillRect(PAD+c*cellW, 8+(rows-1-r)*cellH, cellW+1, cellH+1);
      }}
      ctx.fillStyle='#8893a5'; ctx.font='10px sans-serif';
      // log frequency axis: decade ticks (1/2/5) placed by log position
      const fmn=sg.freqs[0], fmx=sg.freqs[sg.freqs.length-1];
      const lyy=fv=>8+(1-(Math.log10(fv)-Math.log10(fmn))/(Math.log10(fmx)-Math.log10(fmn)))*(Hs-30);
      for (let d=Math.floor(Math.log10(fmn)); d<=Math.ceil(Math.log10(fmx)); d++) for (const mm of [1,2,5]) {{
        const fv=mm*Math.pow(10,d); if (fv<fmn||fv>fmx) continue;
        ctx.fillText(fv>=1000?(fv/1000)+'k':fv, 4, lyy(fv)+3); }}
      const tmaxS=sg.t_s[sg.t_s.length-1]-sg.t_s[0];
      for (let k=0;k<=5;k++) {{ const x=PAD+k/5*cw; ctx.fillText((tmaxS*k/5).toFixed(1)+(k===5?' s':''), x-6, Hs-6); }}
      ctx.fillStyle='#9ecbff'; ctx.fillText('freq (Hz) ↑   temps →', PAD, Hs-18);
      head.appendChild(el('div','legend',T('spectro_cap').replace('{{sg}}',tip('spectrogram','spectrogramme')).replace('{{ax}}',sg.axis)));
    }}
  }}
  for (const axis of Object.keys(PRI.axes||{{}})) {{
    const d=PRI.axes[axis]; if(!d||!d.freq) continue;
    const box=el('div','axis'); root.appendChild(box);
    const m=d.phase_margin_deg, fco=d.crossover_hz;
    const mtxt = m==null ? T('no_xover') : (tip('phase_margin',T('margin'))+' '+m.toFixed(0)+'° @ '+(fco?fco.toFixed(0):'?')+' Hz');
    box.appendChild(el('h2',null,axis.toUpperCase()+' <span class=meta>['+d.band_hz[0]+'–'+d.band_hz[1]+' Hz] — '+mtxt+'</span>'));
    const fmin=d.band_hz[0]||1, fmax=d.band_hz[1]||500;
    const ser=PASSES.map((p,i)=>({{p:p.axes&&p.axes[axis], i:i, primary:i===PRIMARY}})).filter(o=>o.p&&o.p.freq);

    box.appendChild(el('h3',null,tip('gain',T('bode_h'))));
    let gAll=[]; ser.forEach(o=>gAll=gAll.concat(o.p.gain_db));
    let gmin=Math.min(-12,...gAll), gmax=Math.max(12,...gAll);
    let g=mkCanvas(box,Hh).getContext('2d');
    drawAxes(g,Hh,fmin,fmax,gmin,gmax,'gain dB');
    filterOverlay(g,Hh,fmin,fmax,fco);
    hline(g,Hh,0,gmin,gmax,'#5a6273','0 dB');
    for (const o of ser) plotLine(g,Hh,o.p.freq,o.p.gain_db,o.p.coherence,fmin,fmax,gmin,gmax,PAL[o.i%PAL.length],{{dim:!o.primary, lw:o.primary?2.2:1.5}});
    box.appendChild(el('div','legend',
      '<span style="color:#5a9bd4">│ '+tip('gyro_lpf',T('leg_gyro'))+'</span>'+
      '<span style="color:#d48fd4">│ '+tip('dterm_lpf',T('leg_dterm'))+'</span>'+
      '<span style="color:#ffd479">▮ '+tip('dyn_notch',T('leg_notch'))+'</span>'+
      '<span style="color:#ff8a80">│ '+tip('crossover',T('leg_xover'))+'</span>'));
    let p=mkCanvas(box,Hh).getContext('2d');
    drawAxes(p,Hh,fmin,fmax,-360,0,'phase °'); hline(p,Hh,-180,-360,0,'#ff8a80','-180°');
    for (const o of ser) plotLine(p,Hh,o.p.freq,o.p.phase_deg.map(v=>((v%360)+360)%360-360),o.p.coherence,fmin,fmax,-360,0,PAL[o.i%PAL.length],{{dim:!o.primary, lw:o.primary?2.2:1.5}});
    box.appendChild(el('div','legend',T('coh_cap').replace('{{coh}}',tip('coherence','coh')).replace('{{gate}}',GATE.toFixed(1))));
    let ch=mkCanvas(box,Hh-40).getContext('2d');
    drawAxes(ch,Hh-40,fmin,fmax,0,1,'coh'); hline(ch,Hh-40,GATE,0,1,'#7e8aa0',GATE.toFixed(1));
    for (const o of ser) plotLine(ch,Hh-40,o.p.freq,o.p.coherence,o.p.coherence.map(_=>1),fmin,fmax,0,1,PAL[o.i%PAL.length],{{dim:!o.primary, lw:o.primary?2:1.3}});

    // step response (time domain)
    const sser=ser.filter(o=>o.p.step && o.p.step.t_ms && o.p.step.t_ms.length);
    if (sser.length) {{
      box.appendChild(el('h3',null,tip('step_response',T('step_h'))));
      let xmax=0, ymax=1.3; sser.forEach(o=>{{ xmax=Math.max(xmax,o.p.step.t_ms[o.p.step.t_ms.length-1]); ymax=Math.max(ymax,...o.p.step.y); }});
      let st=mkCanvas(box,Hh).getContext('2d');
      drawAxesLin(st,Hh,xmax,0,ymax,'step');
      hline(st,Hh,1,0,ymax,'#5a6273','1.0');
      for (const o of sser) plotLin(st,Hh,o.p.step.t_ms,o.p.step.y,xmax,0,ymax,PAL[o.i%PAL.length],{{dim:!o.primary, lw:o.primary?2.2:1.5}});
      const mt=d.step&&d.step.metrics;
      if (mt) box.appendChild(el('div','legend',T('metrics').replace('{{ov}}',mt.overshoot_pct).replace('{{rise}}',mt.rise_ms==null?'–':mt.rise_ms).replace('{{settle}}',mt.settle_ms==null?'–':mt.settle_ms)));
    }}

    // diagnosis + step diagnosis + PID lead (reference pass)
    const ul=el('ul','diag');
    for (const line of (d.diagnosis||[])) ul.appendChild(el('li',null,loc(line)));
    for (const line of (d.step_diagnosis||[])) ul.appendChild(el('li',null,loc(line)));
    box.appendChild(ul);
    if (PRI.pid_suggestions && PRI.pid_suggestions[axis])
      box.appendChild(el('p','pid','<b>'+T('lead_pid')+'</b> '+loc(PRI.pid_suggestions[axis])));
  }}

  // ---- Step 3: History ----
  {{
    const box=el('div','axis step cmp'); root.appendChild(box);
    box.appendChild(el('h2',null,'<span class=stepnum>3</span>'+T('step3_h')));
    let s='<div class=cfg>';
    PASSES.forEach((p,i)=>{{
      s+='<div><span class=swatch style="background:'+PAL[i%PAL.length]+'"></span><b>'+passLabel(p)+'</b>';
      const pd=p.config&&p.config.pids;
      if (pd) s+=' <span class=meta>— P/D '+Object.entries(pd).map(([a,v])=>a[0]+' '+v[0]+'/'+v[2]).join(' ')+'</span>';
      if (p.diff) s+='<br><span class=diff>'+T('step3_changes')+' '+p.diff+'</span>';
      s+='</div>';
    }});
    if (PASSES.length<=1) s+='<p class=meta>'+T('step3_single')+'</p>';
    s+='</div>'; box.appendChild(el('div',null,s));
    // exhaustive settings comparison table (PID + every filter), changed cells highlighted
    if (PASSES.length>=2) {{
      const ref=PASSES.map(p=>cfgFields(p.config||{{}})).filter(a=>a.length).slice(-1)[0]||[];
      if (ref.length) {{
        let changedAny=false, t='<h3>'+T('cmp_h')+'</h3><table class=cmp><tr><th></th>';
        PASSES.forEach((p,i)=>{{ t+='<th><span class=swatch style="background:'+PAL[i%PAL.length]+'"></span>'+T('pass_word')+' '+p.n+'</th>'; }});
        t+='</tr>';
        for (const [lbl] of ref) {{
          t+='<tr><td class=lbl>'+lbl+'</td>'; let prev=null;
          PASSES.forEach(p=>{{ const m=Object.fromEntries(cfgFields(p.config||{{}})); const v=(lbl in m)?m[lbl]:'—';
            const chg=(prev!==null && v!==prev); if(chg)changedAny=true;
            t+='<td'+(chg?' class=chg':'')+'>'+v+'</td>'; prev=v; }});
          t+='</tr>';
        }}
        t+='</table>';
        if (!changedAny) t+='<p class=meta>'+T('cmp_none')+'</p>';
        box.appendChild(el('div',null,t));
      }}
    }}
  }}

  // ---- Glossary ----
  {{
    const order=['chirp','gain','phase','phase_margin','crossover','coherence','resonance',
      'noise_psd','motor_harmonics','gyro_lpf','dterm_lpf','dyn_notch','rpm_filter','dmax','pid','throttle_map','spectrogram','step_response','propwash'];
    const box=el('div','axis'); root.appendChild(box); box.appendChild(el('h2',null,T('glossary_h')));
    let s='<dl class=glos>';
    for (const k of order) {{ const g=GL[k]; if (g && (g[LANG]||g.fr)) {{
      const head=(g[LANG]||g.fr).split(/ : | — |: /)[0];
      s+='<dt>'+head+'</dt><dd>'+(g[LANG]||g.fr)+'</dd>'; }} }}
    s+='</dl>'; box.appendChild(el('div',null,s));
  }}
}}
document.getElementById('langbtn').onclick=()=>{{ LANG = (LANG==='fr'?'en':'fr'); render(); }};
let _rt; window.addEventListener('resize', ()=>{{ clearTimeout(_rt); _rt=setTimeout(render, 150); }});
render();
</script></body></html>
"""


# ---------------------------------------------------------------------------
# Text summary
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
        if d["phase_margin_deg"] is not None:
            print(f"  Phase margin     : {d['phase_margin_deg']:.0f} deg @ {d['crossover_hz']:.0f} Hz")
        else:
            print("  Phase margin     : no 0 dB crossover in coherent band")
        st = (d.get("step") or {}).get("metrics")
        if st:
            print(f"  Step response    : overshoot {st['overshoot_pct']}%  rise {st['rise_ms']} ms  settle {st['settle_ms']} ms")
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

    pid_sug = output.get("pid_suggestions") or {}
    flt_sug = output.get("filter_suggestions") or []
    if pid_sug or flt_sug:
        print("\n=== Tuning suggestions ===")
    if pid_sug:
        print("\nPID:")
        for axis, txt in pid_sug.items():
            print(f"  [{axis}] {loc(txt)}")
    noise = output.get("noise_spectrum") or {}
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
        tmp_csv = None
        try:
            if path.suffix.lower() in (".bbl", ".bfl"):
                tmp_csv = _decode_bbl(path, args.session)
                df = _load_csv(tmp_csv)
            else:
                df = _load_csv(path)
            fs = _sample_rate(df)
            new_passes.append(_build_pass(path, df, fs, args))
        finally:
            if tmp_csv:
                tmp_csv.unlink(missing_ok=True)

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

    report = _assemble_report(passes, args.lang)
    primary_name = report["passes"][report["primary_index"]].get("file", "report")

    if args.html:
        Path(args.html).write_text(_html_report(report, primary_name), encoding="utf-8")
        print(f"Report written to {args.html}", file=sys.stderr)
    elif args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report, args.lang)


if __name__ == "__main__":
    main()
