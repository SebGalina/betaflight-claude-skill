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
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import signal as sp_signal

sys.path.insert(0, str(Path(__file__).parent))
import blackbox_signal as bbs  # noqa: E402  (shared decode/load/sample-rate helpers)

# ---------------------------------------------------------------------------
# Column names in the decoded CSV (blackbox_decoder output)
# ---------------------------------------------------------------------------
TIME_COL = bbs.TIME_COL
THROTTLE_COL = bbs.THROTTLE_COL
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
# I/O helpers — shared with spectral_analysis.py / step_response.py
# (see scripts/blackbox_signal.py; the bbl→DataFrame load goes through
# bbs.load_dataframe directly in main())
# ---------------------------------------------------------------------------
_sample_rate = bbs.sample_rate


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
    """Return (freqs, gain_db, phase_deg, coherence, H) for the transfer x -> y.

    H is the complex closed-loop FRF (the complementary sensitivity T = gyro/setpoint);
    callers that need the sensitivity S = 1 - T read it from H directly.
    """
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
    return f, gain_db, phase_deg, Cxy, H


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


def _smooth(y, w):
    """Centred moving average (odd window), edge-preserving."""
    w = int(max(1, w) | 1)
    if w <= 1 or len(y) < w:
        return np.asarray(y, float)
    k = np.ones(w) / w
    return np.convolve(np.asarray(y, float), k, mode="same")


def _phase_margin(freqs, gain_db, phase_deg, coh, fmin, fmax):
    """Phase margin at the 0 dB gain crossover, made robust to curve wiggle and flight noise.

    The phase is steep at the crossover (~10°/Hz), so reading it at one raw sample is jumpy.
    We smooth the gain/phase, interpolate the exact 0 dB crossing, read the phase there, and
    estimate an uncertainty from the local gain scatter / slope propagated through the phase
    slope. Returns (crossover_hz, margin_deg, margin_unc_deg) or (None, None, None).
    """
    band = (freqs >= fmin) & (freqs <= fmax) & (coh >= COHERENCE_GATE)
    fb, gb, pb = freqs[band], gain_db[band], phase_deg[band]
    if len(fb) < 6:
        return None, None, None
    w = min(9, len(gb) | 1)
    gs, ps = _smooth(gb, w), _smooth(pb, w)
    crossings = [i for i in range(1, len(gs)) if gs[i - 1] >= 0.0 > gs[i]]
    if not crossings:
        return None, None, None
    i = crossings[-1]                        # highest-freq crossover = the loop bandwidth
    g0, g1, f0, f1 = gs[i - 1], gs[i], fb[i - 1], fb[i]
    t = float(g0 / (g0 - g1)) if g0 != g1 else 0.0      # interpolate 0 dB crossing
    fco = float(f0 + t * (f1 - f0))
    ph = float(ps[i - 1] + t * (ps[i] - ps[i - 1]))
    margin = 180.0 + ph
    margin = margin - 360.0 * np.ceil((margin - 180.0) / 360.0)
    # uncertainty: Δfco = gain scatter / |dgain/df|, propagated through |dphase/df|
    lo, hi = max(0, i - w), min(len(fb), i + w + 1)
    span = float(fb[hi - 1] - fb[lo]) or 1.0
    dgdf = float(gs[hi - 1] - gs[lo]) / span
    dpdf = float(ps[hi - 1] - ps[lo]) / span
    resid = float(np.std(gb[lo:hi] - gs[lo:hi]))
    dfco = abs(resid / dgdf) if abs(dgdf) > 1e-6 else span
    unc = min(90.0, abs(dpdf) * dfco)
    return round(fco, 1), round(margin, 1), round(unc, 0)


def _sensitivity_peak(freqs, H, coh, fmin, fmax):
    """Peak of the sensitivity S(f) = 1 - T(f), where T = H is the measured closed-loop FRF.

    Ms = max|S| is the robustness headline: by Bode's integral |S| exceeds 1 somewhere, so
    Ms >= 1 always, and Ms bounds the phase margin from below via PM >= 2*arcsin(1/(2*Ms)).
    The frequency f_Ms where |S| peaks is the loop's most fragile point — near the open-loop
    crossover / main resonance — and is what actually governs the phase margin (unlike the
    0 dB crossover of T, which is the closed-loop bandwidth). Restricted to the coherent swept
    band so incoherent high-frequency garbage can't fake a peak. The curve is lightly smoothed
    so a single noisy bin doesn't win the argmax.

    Returns (f_ms_hz, ms, pm_guaranteed_deg) or (None, None, None).
    """
    band = (freqs >= fmin) & (freqs <= fmax) & (coh >= COHERENCE_GATE)
    if int(band.sum()) < 6:
        return None, None, None
    fb = freqs[band]
    s = _smooth(np.abs(1.0 - H[band]), min(9, int(band.sum()) | 1))
    i = int(np.argmax(s))
    ms = float(s[i])
    if ms <= 1e-6:
        return None, None, None
    pm = float(np.degrees(2.0 * np.arcsin(min(1.0, 1.0 / (2.0 * ms)))))
    return round(float(fb[i]), 1), round(ms, 2), round(pm, 0)


def _diagnose(peaks, phase_margin, fmin, fmax) -> list[dict]:
    """Bode diagnosis hints, each as a {fr, en} pair."""
    hints = []
    fco, margin, unc = (phase_margin + (None,))[:3] if len(phase_margin) == 2 else phase_margin
    pm = f"±{unc:.0f}° " if unc else ""
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
            "fr": f"Marge de phase ~{margin:.0f}° {pm}au crossover 0 dB de {fco:.0f} Hz ({vfr}). "
                  f"Sous ~30° la boucle sonne ; réduire les gains ou ajouter du filtrage. "
                  f"(Le scalaire est sensible à la pente de phase — compare plutôt les courbes/la step.)",
            "en": f"Phase margin ~{margin:.0f}° {pm}at the {fco:.0f} Hz 0 dB crossover ({ven}). "
                  f"Below ~30° the loop rings; reduce gains or add filtering. "
                  f"(The scalar is sensitive to the phase slope — prefer comparing the curves/step.)",
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


def _spectrogram_median(segs, fs: float, fmin: float = 5.0, fmax: float | None = None,
                        ntime: int = 200, nfreq: int = 140) -> dict:
    """Median spectrogram across the repeated sweeps of one axis (n >= 2).

    Each sweep is the same construction (monotone exponential 0->fmax sweep), so they align
    on RELATIVE time. We resample every sweep onto a shared (log-f x relative-time) grid,
    per-column normalise as in `_spectrogram`, then take the per-cell median dB — a cleaner
    ridge than any single sweep, with sweep-to-sweep noise averaged down. Same dict shape as
    `_spectrogram` (+ `n_sweeps`); the time axis is rescaled to the median sweep duration so it
    still reads in seconds. Returns {} if fewer than two usable sweeps survive."""
    fmax = fmax or fs / 2.0 * 0.98
    nperseg = 512
    tgrid = np.linspace(0.0, 1.0, ntime)
    logf = None
    grids, durs = [], []
    for seg in segs:
        seg = np.asarray(seg, float)
        if seg.size < 4096:
            continue
        sig = sp_signal.detrend(seg)
        f, t, Sxx = sp_signal.spectrogram(sig, fs=fs, nperseg=nperseg,
                                          noverlap=nperseg * 3 // 4, window="hann")
        sel = (f >= fmin) & (f <= fmax)
        f, Sxx = f[sel], Sxx[sel]
        if f.size < 4 or t.size < 4:
            continue
        db = 10.0 * np.log10(Sxx + 1e-12)
        db = db - np.max(db, axis=0, keepdims=True)       # per-column 0 dB = loudest freq
        if logf is None:                                   # lock the shared freq grid on sweep 0
            flo = float(max(fmin, f[0]))
            logf = np.logspace(np.log10(flo), np.log10(float(f[-1])), nfreq)
        db = np.vstack([np.interp(logf, f, db[:, c]) for c in range(db.shape[1])]).T  # nfreq x ncols
        trel = (t - t[0]) / (t[-1] - t[0])
        db = np.vstack([np.interp(tgrid, trel, db[r, :]) for r in range(db.shape[0])])  # nfreq x ntime
        grids.append(db); durs.append(float(t[-1] - t[0]))
    if len(grids) < 2:
        return {}
    med = np.median(np.stack(grids), axis=0)
    tsec = tgrid * float(np.median(durs))
    return {
        "t_s": [round(float(x), 2) for x in tsec],
        "freqs": [round(float(x), 1) for x in logf],
        "logy": True,
        "levels_db": [[round(float(v), 1) for v in row] for row in med],
        "n_sweeps": len(grids),
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


# ---------------------------------------------------------------------------
# Multi-sweep repeatability: split an axis into its repeated chirp activations
# and aggregate the per-sweep FRF/step into median + min/max bands.
# ---------------------------------------------------------------------------

SWEEP_MIN_GAP_S = 0.5     # idle stretch (debug[0]==0) that separates two chirp activations
SWEEP_MIN_DUR_S = 2.0     # a run shorter than this is a fragment, not a full sweep


def _split_sweeps(mask: np.ndarray, fs: float,
                  min_gap_s: float = SWEEP_MIN_GAP_S, min_dur_s: float = SWEEP_MIN_DUR_S):
    """Contiguous runs of `mask` (one per chirp activation), bridging sub-second gaps.

    A multi-sweep log triggers the chirp several times on the same axis; each trigger is a
    full 0->fmax sweep separated from the next by idle samples (the energy/flag mask drops to
    False). Splitting the axis mask into runs — short intra-sweep dropouts bridged, the long
    inter-activation gaps preserved — recovers the individual sweeps for repeatability stats.
    Returns [(start, end_exclusive), ...], keeping only runs >= min_dur_s.
    """
    idx = np.where(np.asarray(mask))[0]
    if idx.size == 0:
        return []
    gap = max(1, int(min_gap_s * fs))
    splits = np.where(np.diff(idx) > gap)[0]
    starts = np.concatenate(([idx[0]], idx[splits + 1]))
    ends = np.concatenate((idx[splits], [idx[-1]]))
    mindur = int(min_dur_s * fs)
    return [(int(s), int(e) + 1) for s, e in zip(starts, ends) if e - s >= mindur]


def _med_range(vals):
    """(median, lo, hi) over the non-None values, or (None, None, None) if all None."""
    a = np.array([v for v in vals if v is not None], float)
    if a.size == 0:
        return None, None, None
    return round(float(np.median(a)), 2), round(float(a.min()), 2), round(float(a.max()), 2)


def _curve_band(series):
    """Element-wise (median, lo, hi) over a list of equal-length arrays."""
    arr = np.vstack(series)
    return np.median(arr, axis=0), arr.min(axis=0), arr.max(axis=0)


def _aggregate_step(steps: list, band_fields: dict) -> dict:
    """Median step curve + min/max envelope from per-sweep step responses.

    The per-sweep steps share the same time grid (identical nperseg/fs/horizon/downsample), so
    they line up; a stray fragment is truncated to the common length. Scalar metrics are the
    median across sweeps, with the inter-sweep range recorded in `band_fields[...]_range`.
    Returns {} if fewer than two usable steps (caller then has no band to draw).
    """
    steps = [s for s in steps if s and s.get("y")]
    if len(steps) < 2:
        return steps[0] if steps else {}
    n = min(len(s["y"]) for s in steps)
    t_ms = steps[0]["t_ms"][:n]
    ys = [np.array(s["y"][:n], float) for s in steps]
    y_med, y_lo, y_hi = _curve_band(ys)
    ov, ov_lo, ov_hi = _med_range([s["metrics"].get("overshoot_pct") for s in steps])
    rise, ri_lo, ri_hi = _med_range([s["metrics"].get("rise_ms") for s in steps])
    settle, se_lo, se_hi = _med_range([s["metrics"].get("settle_ms") for s in steps])
    delay, _, _ = _med_range([s["metrics"].get("delay_ms") for s in steps])
    peak, _, _ = _med_range([s["metrics"].get("peak") for s in steps])
    band_fields["overshoot_range"] = [ov_lo, ov_hi]
    band_fields["rise_range"] = [ri_lo, ri_hi]
    band_fields["settle_range"] = [se_lo, se_hi]
    return {
        "t_ms": t_ms,
        "y": [round(float(v), 3) for v in y_med],
        "y_lo": [round(float(v), 3) for v in y_lo],
        "y_hi": [round(float(v), 3) for v in y_hi],
        "metrics": {"overshoot_pct": ov, "rise_ms": rise, "delay_ms": delay,
                    "settle_ms": settle, "peak": peak},
    }


def _frf_pack(x, y, sp_vals, fs, nperseg, a_fmin, a_fmax):
    """One sweep's FRF + robustness scalars + step response, bundled for aggregation."""
    freqs, gain, phase, coh, H = _frf(x, y, fs, nperseg)
    fco, margin, m_unc = _phase_margin(freqs, gain, phase, coh, a_fmin, a_fmax)
    f_ms, ms, pm_ms = _sensitivity_peak(freqs, H, coh, a_fmin, a_fmax)
    step = {}
    if sp_vals is not None:
        sb = min(a_fmax, max(120.0, 6.0 * fco)) if fco else min(a_fmax, 150.0)
        step = _step_response(sp_vals, y, fs, band_fmax=sb)
    return {"freqs": freqs, "gain": gain, "phase": phase, "coh": coh, "H": H,
            "fco": fco, "margin": margin, "m_unc": m_unc,
            "f_ms": f_ms, "ms": ms, "pm_ms": pm_ms, "step": step}


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
    sweep_windows: dict = {}   # axis index -> [(start, end_exclusive), ...] for the spectrogram merge

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
        spcol = SETPOINT_COL.format(i)

        # Repeatability: if the chirp was triggered several times on this axis, each activation is
        # an independent full sweep. Compute one FRF/step per sweep and aggregate into a median
        # curve + min/max band. With a single sweep we keep the exact original single-FRF path.
        # Split on the chirp-ON mask (debug[0]!=0), not the energy labels: the labeller bleeds ~half
        # a window past the activation, and those zero-excitation edge samples corrupt the per-sweep
        # Welch/step (steady-state drifts -> false overshoot). The active mask is the true window.
        axis_active = (mask & active) if active is not None else mask
        sweeps = _split_sweeps(axis_active, fs)
        sweep_windows[i] = sweeps
        packs = []
        if len(sweeps) >= 2:
            for s, e in sweeps:
                sm = np.zeros(len(df), dtype=bool); sm[s:e] = axis_active[s:e]
                xs, _ = _resolve_input(df, exc, input_col, i, sm)
                if xs is None:
                    continue
                ys = df.loc[sm, gcol].to_numpy(float)
                sps = df.loc[sm, spcol].to_numpy(float) if spcol in df.columns else None
                packs.append(_frf_pack(xs, ys, sps, fs, nperseg, a_fmin, a_fmax))
            if packs:   # drop any short fragment whose Welch grid doesn't match the others
                gl = max(len(p["freqs"]) for p in packs)
                packs = [p for p in packs if len(p["freqs"]) == gl]
        multi = len(packs) >= 2

        band_fields: dict = {}
        if multi:
            freqs = packs[0]["freqs"]
            gain_db, g_lo, g_hi = _curve_band([p["gain"] for p in packs])
            phase_deg, p_lo, p_hi = _curve_band([p["phase"] for p in packs])
            coh, c_lo, c_hi = _curve_band([p["coh"] for p in packs])
            fco, fco_lo, fco_hi = _med_range([p["fco"] for p in packs])
            margin, m_lo, m_hi = _med_range([p["margin"] for p in packs])
            m_unc, _, _ = _med_range([p["m_unc"] for p in packs])
            f_ms, fms_lo, fms_hi = _med_range([p["f_ms"] for p in packs])
            ms, ms_lo, ms_hi = _med_range([p["ms"] for p in packs])
            pm_ms, pmg_lo, pmg_hi = _med_range([p["pm_ms"] for p in packs])
            fb, gb, pb, cb, glo, ghi, plo, phi, clo, chi = _downsample(
                freqs, gain_db, phase_deg, coh, g_lo, g_hi, p_lo, p_hi, c_lo, c_hi,
                fmin=a_fmin, fmax=a_fmax)
            band_fields = {
                "n_sweeps": len(packs),
                "gain_band": [[round(float(v), 1) for v in glo], [round(float(v), 1) for v in ghi]],
                "phase_band": [[round(float(v), 1) for v in plo], [round(float(v), 1) for v in phi]],
                "coherence_band": [[round(float(v), 3) for v in clo], [round(float(v), 3) for v in chi]],
                "crossover_range": [fco_lo, fco_hi],
                "phase_margin_range": [m_lo, m_hi],
                "ms_range": [ms_lo, ms_hi],
                "f_ms_range": [fms_lo, fms_hi],
                "pm_guaranteed_range": [pmg_lo, pmg_hi],
            }
            step = _aggregate_step([p["step"] for p in packs], band_fields)
        else:
            freqs, gain_db, phase_deg, coh, H = _frf(x, y, fs, nperseg)
            fco, margin, m_unc = _phase_margin(freqs, gain_db, phase_deg, coh, a_fmin, a_fmax)
            f_ms, ms, pm_ms = _sensitivity_peak(freqs, H, coh, a_fmin, a_fmax)
            # Step response from the calibrated setpoint -> gyro (time-domain companion to the Bode).
            step = {}
            if spcol in df.columns:
                # closed-loop bandwidth is a few × the crossover; cap the step band well below the
                # full swept range so high-frequency noise doesn't fake ringing in the transient.
                sb = min(a_fmax, max(120.0, 6.0 * fco)) if fco else min(a_fmax, 150.0)
                step = _step_response(df.loc[np.asarray(mask), spcol].to_numpy(float), y, fs, band_fmax=sb)
            fb, gb, pb, cb = _downsample(freqs, gain_db, phase_deg, coh, fmin=a_fmin, fmax=a_fmax)

        peaks = _gain_peaks(freqs, gain_db, coh, a_fmin, a_fmax)
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
            "phase_margin_unc_deg": m_unc,
            "crossover_hz": fco,
            "ms": ms,
            "f_ms_hz": f_ms,
            "pm_guaranteed_deg": pm_ms,
            "step": step,
            "diagnosis": _diagnose(peaks, (fco, margin, m_unc), a_fmin, a_fmax),
            "step_diagnosis": _step_diagnosis(step.get("metrics", {})) if step else [],
            **band_fields,
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

    # Spectrogram of the primary axis over its chirp window -> the rising sweep. With several
    # sweeps on that axis we median them (cleaner ridge); a single sweep keeps the original path
    # verbatim, so single-sweep logs render byte-identically.
    spectro = {}
    if primary_axis_idx is not None:
        gcol = GYRO_COL.format(primary_axis_idx)
        act = (labels == primary_axis_idx) if labels is not None else active
        if act is not None and gcol in df.columns:
            idx = np.where(np.asarray(act))[0]
            sweeps = sweep_windows.get(primary_axis_idx, [])
            # crop to the swept band (+10%) so the diagonal fills the plot instead of empty HF
            sweptmax = (results[AXES[primary_axis_idx]]["band_hz"][1]) * 1.1
            gyro = df[gcol].to_numpy(float)
            if len(sweeps) >= 2:
                segs = [gyro[s:e] for s, e in sweeps]
                spectro = _spectrogram_median(segs, fs, fmax=min(fmax, sweptmax))
            elif idx.size:
                seg = gyro[int(idx[0]):int(idx[-1]) + 1]
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
# Composite tune score: one 0-100 grade per axis (+ overall) so a config can be
# judged better/worse than the previous pass at a glance. Each sub-metric maps to
# 0-100 through a physical, monotone scoring ramp, then a weighted average.
# Rise rewards speed; overshoot and noise-margin penalise its cost -> the blend
# balances a fast-but-edgy tune against a slow-but-clean one.
# ---------------------------------------------------------------------------

# weight per sub-score; Ms is light because it overlaps the phase margin (avoid double-count)
SCORE_WEIGHTS = {"overshoot": 0.25, "rise": 0.25, "margin": 0.20, "noise": 0.20, "ms": 0.10}


def _ramp(v, good, bad):
    """Linear 0..100: 100 at the `good` end, 0 at the `bad` end, clamped. Direction-agnostic
    (good may be greater or smaller than bad). None in -> None out (term dropped from the blend)."""
    if v is None or good == bad:
        return None if v is None else (100.0 if v == good else 0.0)
    return float(max(0.0, min(1.0, (v - bad) / (good - bad))) * 100.0)


def _noise_margin_db(d):
    """Head-room before the loop gain would reach 0 dB in the HIGH band — the D-term ceiling.
    Measured well ABOVE the passband (where closed-loop gain sits at ~0 dB by design and must
    not be mistaken for a noise problem): the worst (highest) gain over freq > max(60, 2.5*f(Ms)).
    A buried roll-off (~-32 dB) scores well; a resonance climbing back toward 0 dB scores poorly.

    Worst-case by construction (a single bad peak sets it, not the average), and the resolved
    full-resolution resonances in d["peaks"] are folded in — the downsampled curve alone can
    smooth a narrow spike away. The peak COUNT is not captured here (scalar); list d["peaks"]
    separately to reason about multiple resonances."""
    freq, gain = d.get("freq") or [], d.get("gain_db") or []
    if not freq or not gain:
        return None
    pivot = d.get("f_ms_hz") or d.get("crossover_hz") or 24.0
    fref = max(60.0, 2.5 * pivot)
    cand = [gain[i] for i in range(len(freq)) if freq[i] > fref]
    if not cand:                                # band never reaches the HF region: use its top quarter
        cand = gain[max(1, len(gain) * 3 // 4):]
    # fold in full-resolution HF resonances (a narrow spike the downsampled curve missed)
    cand += [p["gain_db"] for p in (d.get("peaks") or []) if p.get("freq_hz", 0) > fref]
    return -max(cand) if cand else None


def _axis_score(d):
    """Per-axis composite. Returns {score, subs:{...}} or None if nothing is measurable."""
    sm = (d.get("step") or {}).get("metrics") or {}
    subs = {
        "overshoot": _ramp(sm.get("overshoot_pct"), 8.0, 22.0),   # %: target <=8, ceiling ~15, bad >=22
        "rise":      _ramp(sm.get("rise_ms"), 15.0, 50.0),         # ms: faster better, floor 15, slow 50
        "margin":    _ramp(d.get("pm_guaranteed_deg"), 45.0, 20.0),# deg guaranteed: >=45 great, <20 risky
        "ms":        _ramp(d.get("ms"), 1.3, 2.2),                 # sensitivity peak: 1.3 healthy, >=2.2 bad
        "noise":     _ramp(_noise_margin_db(d), 32.0, 8.0),        # dB HF head-room: ~32 healthy, <=8 = D ceiling
    }
    num = den = 0.0
    for k, w in SCORE_WEIGHTS.items():
        if subs[k] is not None:
            num += w * subs[k]; den += w
    if den == 0:
        return None
    return {"score": round(num / den, 1),
            "subs": {k: (round(v) if v is not None else None) for k, v in subs.items()}}


def _grade(score):
    """Letter grade tuned so ~75 reads as a solid B (FPV-realistic, not academic)."""
    if score is None:
        return "—"
    return next(g for thr, g in [(85, "A"), (70, "B"), (55, "C"), (40, "D"), (0, "F")] if score >= thr)


def _tune_score(results):
    """Overall = mean of the per-axis scores; carried in the pass so history gives the trend."""
    per = {ax: s for ax, d in results.items() if d for s in [_axis_score(d)] if s}
    if not per:
        return {}
    overall = round(sum(s["score"] for s in per.values()) / len(per), 1)
    return {"overall": overall, "grade": _grade(overall), "axes": per}


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
        "tune_score": _tune_score(results),
        "throttle_map": throttle_map,
        "noise_spectrum": noise,
        "spectrogram": spectro,
        "synthesis": _synthesis(results, noise, config, throttle_max),
        "filter_suggestions": _filter_suggestions(throttle_map, config) if config else [],
        "noise_suggestions": _noise_suggestions(noise) + _filter_disable_notes(noise, config),
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
        "fr": "Marge de phase : la réserve de stabilité (degrés avant -180°). >45° = sain et amorti ; "
              "30-45° = correct ; 15-30° = limite, ça commence à rebondir ; <15° = la boucle sonne. "
              "Classiquement lue au croisement 0 dB, mais ce point décroche sur une réponse très amortie ; "
              "le rapport reporte donc la marge GARANTIE déduite du pic de sensibilité Ms "
              "(PM ≥ 2·arcsin(1/2·Ms)) — d'où le repère f(Ms) sur les graphes, pas le croisement 0 dB. "
              "Baisser P/D ou filtrer redonne de la marge.",
        "en": "Phase margin: the stability reserve (degrees before -180°). >45° = healthy and damped; "
              "30-45° = fine; 15-30° = marginal, starts to bounce; <15° = the loop rings. Classically "
              "read at the 0 dB crossover, but that point breaks down on a very damped response; the "
              "report therefore states the GUARANTEED margin from the sensitivity peak Ms "
              "(PM ≥ 2·arcsin(1/2·Ms)) — hence the f(Ms) marker on the plots, not the 0 dB crossover. "
              "Lowering P/D or adding filtering restores margin.",
    },
    "sensitivity": {
        "fr": "Pic de sensibilité Ms : Ms = max|S(f)|, avec S = 1/(1+L) = 1−T la fonction de "
              "sensibilité (T étant la réponse boucle fermée mesurée par le chirp). C'est LE chiffre "
              "de robustesse : il borne la marge de phase par PM ≥ 2·arcsin(1/(2·Ms)). Physiquement "
              "Ms = à quel point la boucle amplifie les perturbations à sa fréquence la plus fragile "
              "f(Ms) — d'où la raie verticale. Repères : Ms ≲ 1.5 confortable et amorti ; ~2 limite ; "
              ">2 ça résonne (l'overshoot de la step monte, le propwash s'installe). Ms se baisse en "
              "redonnant de la marge (moins de P/D, ou plus de filtrage avant les PID).",
        "en": "Sensitivity peak Ms: Ms = max|S(f)|, where S = 1/(1+L) = 1−T is the sensitivity "
              "function (T being the closed-loop response the chirp measures). It is THE robustness "
              "number: it bounds the phase margin via PM ≥ 2·arcsin(1/(2·Ms)). Physically Ms is how "
              "much the loop amplifies disturbances at its most fragile frequency f(Ms) — hence the "
              "vertical marker. Rules of thumb: Ms ≲ 1.5 comfortable and damped; ~2 marginal; >2 it "
              "rings (step overshoot climbs, propwash sets in). Lower Ms by restoring margin (less "
              "P/D, or more filtering before the PIDs).",
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
              "0.8 la courbe de gain/phase n'est pas fiable à cette fréquence — on l'affiche en grisé. "
              "La cohérence chute naturellement en haute fréquence.",
        "en": "Coherence (0 to 1): how much of the measured response is really caused by the chirp "
              "excitation rather than noise/vibration. 1 = trustworthy. Below 0.8 the gain/phase curve "
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
        "title": "CHIRP ANALYZER", "subtitle": "analyse de réponse fréquentielle · Betaflight",
        "lang_btn": "EN", "pass_word": "Passe",
        "guide_h": "Guide de tuning",
        "pipe": "Blackbox | Identification fréquentielle | Réponse en fréquence | Phase margin / crossover | Step response simulée | Analyse bruit & filtrage | Scoring | Recommandations",
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
        "cfg_init": "Réglages initiaux", "cfg_last": "Réglages — dernière passe", "cfg_sub": "(extraits du log)",
        "synth_h": "Lecture d'ensemble", "synth_intro": "D'après la dernière passe",
        "synth_evo": "Évolution depuis la passe 1",
        "score_h": "Note de tune", "score_vs": "vs passe précédente", "score_all": "Toutes les passes :",
        "sc_rise": "montée", "sc_margin": "marge", "sc_noise": "bruit",
        "score_cap": "Note composite 0–100 (moyenne des axes) : overshoot, montée, marge garantie, Ms et marge "
                     "au bruit, chacun ramené sur 0–100 par une courbe physique puis moyenné (montée et overshoot "
                     "pèsent le plus). Sert à dire si cette config est meilleure ou pire que la précédente — le "
                     "delta compare à la passe d'avant. À lire avec les graphes, pas à la place : une note ne "
                     "remplace pas le jugement manche en main.",
        "guide_vsag": "⚙️ Pour des passes <b>comparables</b> : active <code>vbat_sag_compensation</code> "
                      "(et/ou vole à niveau de batterie similaire). Sinon l'autorité moteur varie d'un vol à "
                      "l'autre et déplace les courbes et les marges, même sans toucher au tune.",
        "overlay_hint": "pastilles en haut à droite de chaque axe : clique une passe pour masquer/afficher ses courbes",
        "pill_off": "masquée",
        "tmap_howto": "Comment lire — chaque ligne = une tranche de gaz (ralenti en bas, plein gaz en haut), "
                      "couleur = puissance de bruit du gyro à cette fréquence. Une raie verticale qui <b>monte en "
                      "fréquence quand le gaz augmente</b> = harmonique moteur ; une raie à <b>fréquence fixe</b> "
                      "quel que soit le gaz = résonance de cadre/pale.",
        "tmap_lo": "peu de bruit", "tmap_hi": "beaucoup",
        "mapex_h": "Exemple — à quoi ressemble une MAUVAISE carte",
        "mapex_cap": "Une raie qui MONTE en fréquence avec le gaz = harmonique moteur (à traiter par RPM filter / dyn_notch). "
                     "Une raie VERTICALE à fréquence fixe = résonance de cadre/pale (notch). Une bonne carte : plancher bas "
                     "et uniforme, sans raie franche.",
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
        "coh_cap": "fiabilité de la mesure par fréquence (grisé si &lt; {gate})",
        "margin": "marge mesurée", "no_xover": "pas de crossover",
        "pm_gtd": "marge garantie", "bandwidth": "bande passante",
        "step3_h": "Historique & comparaison",
        "step3_single": "Une seule passe pour l'instant. Refais un log chirp après tes modifs : il s'empilera "
                        "ici pour la comparaison avant/après.",
        "step3_changes": "↳ changements vs passe précédente :",
        "evo_h": "Évolution des indicateurs par axe",
        "evo_cap": "Une vignette par axe × indicateur : les passes en abscisse, la valeur (unité rappelée "
                   "sur l'ordonnée) en y. Chaque indicateur a sa couleur + picto, repris dans la note de tune. "
                   "Survole un point pour lire sa valeur exacte. Le point = médiane ; la "
                   "moustache = l'étendue min/max inter-sweeps quand la passe a plusieurs chirps (sinon point "
                   "seul). Un trou dans la ligne = indicateur non mesurable sur cette passe. La vignette "
                   "« marge · f(Ms) » est la seule à deux courbes : marge garantie à gauche en ° (trait plein), "
                   "fréquence f(Ms) à droite en Hz (tireté). Sur le Ms, la bande verte (1,3–2) est la zone saine "
                   "visée, le rouge (>2) la zone nerveuse peu robuste.",
        "cmp_h": "Comparaison des réglages",
        "cmp_none": "Réglages PID + filtres identiques sur toutes les passes — les écarts de courbes "
                    "viennent du vol (batterie, throttle, bruit), pas du tune.",
        "glossary_h": "Glossaire",
        "w_filt": "le filtrage", "w_pid": "les PID", "w_phase": "phase",
        "w_pm": "marge de stabilité", "w_res": "résonances",
        "leg_gyro": "gyro lpf", "leg_dterm": "dterm lpf", "leg_notch": "plage dyn_notch",
        "leg_xover": "crossover 0 dB", "leg_fms": "f(Ms) — pic de sensibilité",
        "metrics": "overshoot {ov}% · montée {rise} ms · établi {settle} ms",
        "render_err": "⚠ Rendu interrompu : ",
    },
    "en": {
        "title": "CHIRP ANALYZER", "subtitle": "frequency-response analysis · Betaflight",
        "lang_btn": "FR", "pass_word": "Pass",
        "guide_h": "Tuning guide",
        "pipe": "Blackbox | Frequency identification | Frequency response | Phase margin / crossover | Simulated step response | Noise & filtering analysis | Scoring | Recommendations",
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
        "cfg_init": "Initial settings", "cfg_last": "Settings — latest pass", "cfg_sub": "(read from the log)",
        "synth_h": "Overview", "synth_intro": "Based on the latest pass",
        "synth_evo": "Change since pass 1",
        "score_h": "Tune score", "score_vs": "vs previous pass", "score_all": "All passes:",
        "sc_rise": "rise", "sc_margin": "margin", "sc_noise": "noise",
        "score_cap": "Composite 0–100 score (mean of the axes): overshoot, rise, guaranteed margin, Ms and noise "
                     "margin, each mapped to 0–100 by a physical curve then averaged (rise and overshoot weigh "
                     "most). Tells whether this config is better or worse than the previous one — the delta "
                     "compares to the pass before. Read it alongside the plots, not instead: a score is no "
                     "substitute for stick feel.",
        "guide_vsag": "⚙️ For <b>comparable</b> passes: enable <code>vbat_sag_compensation</code> (and/or fly at "
                      "a similar battery level). Otherwise motor authority varies between flights and shifts the "
                      "curves and margins even with no tune change.",
        "overlay_hint": "pills at the top-right of each axis: click a pass to hide/show its curves",
        "pill_off": "hidden",
        "tmap_howto": "How to read — each row = a throttle slice (idle at the bottom, full throttle at the top), "
                      "colour = gyro noise power at that frequency. A vertical line that <b>climbs in frequency as "
                      "throttle rises</b> = a motor harmonic; a <b>fixed-frequency</b> line at any throttle = a "
                      "frame/prop resonance.",
        "tmap_lo": "low noise", "tmap_hi": "high",
        "mapex_h": "Example — what a BAD map looks like",
        "mapex_cap": "A line that CLIMBS in frequency with throttle = a motor harmonic (handled by the RPM filter / "
                     "dyn_notch). A FIXED-frequency vertical line = a frame/prop resonance (notch). A good map: a low, "
                     "uniform floor with no sharp line.",
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
        "coh_cap": "per-frequency measurement reliability (greyed if &lt; {gate})",
        "margin": "measured margin", "no_xover": "no crossover",
        "pm_gtd": "guaranteed margin", "bandwidth": "bandwidth",
        "step3_h": "History & comparison",
        "step3_single": "Only one pass so far. Re-fly a chirp log after your changes: it will stack up here for "
                        "before/after comparison.",
        "step3_changes": "↳ changes vs previous pass:",
        "evo_h": "Per-axis indicator evolution",
        "evo_cap": "One tile per axis × indicator: passes on the x-axis, value (unit recalled on the "
                   "ordinate) on y. Each indicator has its own colour + pictogram, reused in the tune score. "
                   "Hover a point to read its exact value. The dot is the median; the whisker "
                   "is the inter-sweep min/max range when a pass has several chirps (bare dot otherwise). A gap "
                   "in the line = indicator not measurable on that pass. The 'margin · f(Ms)' tile is the only "
                   "two-curve one: guaranteed margin on the left in ° (solid), f(Ms) frequency on the right in "
                   "Hz (dashed). On Ms, the green band (1.3–2) is the healthy target zone, red (>2) the nervous, "
                   "low-robustness zone.",
        "cmp_h": "Settings comparison",
        "cmp_none": "Identical PID + filter settings across all passes — curve differences come from the "
                    "flight (battery, throttle, noise), not the tune.",
        "glossary_h": "Glossary",
        "w_filt": "filtering", "w_pid": "the PIDs", "w_phase": "phase",
        "w_pm": "stability margin", "w_res": "resonances",
        "leg_gyro": "gyro lpf", "leg_dterm": "dterm lpf", "leg_notch": "dyn_notch range",
        "leg_xover": "0 dB crossover", "leg_fms": "f(Ms) — sensitivity peak",
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
  .banner {{ position:relative; border-radius:10px; padding:16px 20px 14px; margin-bottom:18px; overflow:hidden;
     background:linear-gradient(120deg,#141d2a 0%,#1b2a3e 48%,#21344a 100%); border:1px solid #2c4a68; }}
  .banner::before {{ content:''; position:absolute; inset:0; pointer-events:none; opacity:.12;
     background:radial-gradient(circle at 88% 25%, #6fd0ff 0, transparent 45%); }}
  .banner-main {{ display:flex; align-items:center; gap:14px; }}
  .banner-icon {{ font-size:30px; line-height:1; color:#7fd0ff; text-shadow:0 0 14px rgba(111,208,255,.5); }}
  .banner-title {{ font-size:25px; font-weight:800; letter-spacing:2.5px; color:#eaf2fb; }}
  .banner-sub {{ font-size:12.5px; color:#9bb4cc; margin-top:1px; }}
  .banner-tags {{ margin-top:11px; }}
  .chip {{ display:inline-block; font:600 11px system-ui; letter-spacing:.4px; color:#cfe6ff; margin-right:6px;
     background:#13314e; border:1px solid #2f567d; border-radius:11px; padding:2px 10px; }}
  .banner-file {{ position:absolute; right:18px; bottom:13px; color:#8aa0b8; font-size:12px; }}
  h3 {{ font-size: 13px; color:#8893a5; margin:14px 0 4px; text-transform:uppercase; letter-spacing:.5px; }}
  .axis {{ border:1px solid #2a2f3a; border-radius:8px; padding:12px 14px; margin-bottom:18px; background:#171b22; position:relative; }}
  .passpills {{ position:absolute; top:11px; right:13px; display:flex; gap:5px; flex-wrap:wrap; justify-content:flex-end; max-width:58%; }}
  .pillbtn {{ font:600 11px system-ui; background:#0d1016; border:1.5px solid; border-radius:11px; padding:1px 9px; cursor:pointer; }}
  .pillbtn.off {{ background:transparent; border-style:dashed; text-decoration:line-through; }}
  summary.collh {{ list-style:none; cursor:pointer; font-size:13px; color:#8893a5; text-transform:uppercase;
     letter-spacing:.5px; font-weight:600; margin:14px 0 4px; }}
  summary.collh::-webkit-details-marker {{ display:none; }}
  summary.collh::before {{ content:'▸ '; color:#8893a5; }}
  details[open] > summary.collh::before {{ content:'▾ '; }}
  summary.collh2 {{ list-style:none; cursor:pointer; font-size:15px; font-weight:600; color:#9ecbff; margin:0 0 4px; }}
  summary.collh2::-webkit-details-marker {{ display:none; }}
  summary.collh2::before {{ content:'▸ '; }}
  details[open] > summary.collh2::before {{ content:'▾ '; }}
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
  .score {{ background:#141c26; border:1px solid #28425c; }}
  .scoreband {{ display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; margin:2px 0 8px; }}
  .scorebig {{ font-size:40px; font-weight:700; color:#e6eaf2; line-height:1; }}
  .scoremax {{ font-size:16px; font-weight:400; color:#8893a5; }}
  .scoregrade {{ font-size:26px; font-weight:700; color:#9ecbff; }}
  .scoredelta {{ font-size:14px; font-weight:600; }}
  table.scoretab {{ border-collapse:collapse; font-size:12px; margin:4px 0; }}
  table.scoretab td {{ padding:2px 10px 2px 0; color:#c2cad6; vertical-align:top; }}
  .scoreall {{ margin:2px 0 4px; font-size:12.5px; }} .scoreall span {{ font-weight:600; }}
  .stepnum {{ display:inline-block; min-width:20px; height:20px; line-height:20px; text-align:center;
             border-radius:50%; background:#28425c; color:#cfe3ff; font-weight:600; margin-right:6px; }}
  .term {{ border-bottom:1px dotted #6b7689; cursor:help; position:relative; }}
  .term:hover::after {{ content:attr(data-tip); position:absolute; left:0; top:1.5em; z-index:20;
     width:340px; white-space:normal; background:#0b0e13; color:#e6eaf2; border:1px solid #3a4150;
     border-radius:6px; padding:9px 11px; font:12px/1.55 system-ui; box-shadow:0 6px 18px rgba(0,0,0,.55); }}
  /* pass labels carry data-pass; the rich coloured config tooltip (#htip) is shown by JS on hover */
  .passtip {{ cursor:help; }}
  .maptip {{ cursor:help; display:inline-block; width:15px; height:15px; line-height:15px; text-align:center;
     border-radius:50%; background:#28425c; color:#cfe3ff; font:bold 10px system-ui; vertical-align:middle; }}
  #htip {{ position:fixed; z-index:60; pointer-events:none; display:none; max-width:420px;
     background:#0b0e13; border:1px solid #3a5a78; border-radius:7px; padding:10px 13px;
     font:12px/1.65 ui-monospace,Consolas,monospace; box-shadow:0 8px 22px rgba(0,0,0,.6); }}
  .glos dt {{ color:#9ecbff; font-weight:600; margin-top:8px; }}
  .glos dd {{ margin:2px 0 0; color:#c2cad6; }}
  .swatch {{ display:inline-block; width:11px; height:11px; border-radius:2px; margin-right:5px; vertical-align:middle; }}
  .diff {{ color:#ffd479; }}
  table.cmp {{ border-collapse:collapse; font-size:12px; margin-top:8px; }}
  table.cmp th, table.cmp td {{ border:1px solid #2a2f3a; padding:3px 9px; text-align:left; color:#c2cad6; }}
  table.cmp th {{ color:#9ecbff; font-weight:600; }}
  table.cmp td.lbl {{ color:#8893a5; }}
  table.cmp td.chg {{ color:#ffd479; font-weight:600; background:#241f12; }}
  .passleg {{ margin:6px 0 4px; font-size:12px; color:#c2cad6; }}
  .passleg label {{ display:inline-flex; align-items:center; gap:5px; margin:2px 16px 2px 0; cursor:pointer; }}
  .passleg input {{ accent-color:#9ecbff; cursor:pointer; }}
  .howto {{ font-size:12px; color:#aab4c4; margin:4px 0 2px; }}
  .pipe {{ margin:6px 0 10px; line-height:2; }}
  .pipe b {{ display:inline-block; background:#13314e; border:1px solid #2f567d; border-radius:10px;
     padding:1px 9px; font:600 11px system-ui; color:#cfe6ff; white-space:nowrap; }}
  .pipe .arr {{ color:#5a6b82; margin:0 3px; }}
  .ptip {{ position:fixed; z-index:60; pointer-events:none; display:none; background:#0b0e13; color:#e6eaf2;
     border:1px solid #3a5a78; border-radius:5px; padding:3px 7px; font:11px ui-monospace,Consolas,monospace;
     box-shadow:0 4px 12px rgba(0,0,0,.55); }}
  .scalebar {{ display:inline-block; height:10px; width:120px; vertical-align:middle; margin:0 6px;
     border-radius:2px; background:linear-gradient(90deg, rgb(0,120,255), rgb(150,90,170), rgb(255,40,30)); }}
  .langbtn {{ position:fixed; top:16px; right:16px; z-index:30; background:#28425c; color:#cfe3ff;
     border:1px solid #3a5a78; border-radius:6px; padding:5px 12px; cursor:pointer; font:600 12px system-ui; }}
  .twocol {{ display:flex; gap:14px; flex-wrap:wrap; }}
  .twocol > div {{ flex:1 1 380px; }}
</style></head><body>
<button id="langbtn" class="langbtn"></button>
<div id="hdr" class="banner"></div>
<div id="root"></div>
<div id="ptip" class="ptip"></div>
<div id="htip"></div>
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
function drawAxesLin(ctx,h,xmax,ymin,ymax,ylabel,ystep,xminor) {{
  ctx.clearRect(0,0,W,h); ctx.strokeStyle='#2a2f3a'; ctx.fillStyle='#8893a5'; ctx.font='10px sans-serif'; ctx.lineWidth=1;
  if (ystep) {{ for (let yv=ymin; yv<=ymax+1e-9; yv+=ystep) {{ const y=lerp(yv,ymin,ymax,h-22,8);  // fixed 0.25 grid so 1.0 is always a line
      ctx.strokeStyle='#2a2f3a'; ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-12,y); ctx.stroke();
      ctx.fillStyle='#8893a5'; ctx.fillText(yv.toFixed(2), 4, y+3); }} }}
  else for (let k=0;k<=4;k++) {{ const yv=ymin+(ymax-ymin)*k/4, y=lerp(yv,ymin,ymax,h-22,8);
    ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-12,y); ctx.stroke(); ctx.fillText(yv.toFixed(2), 4, y+3); }}
  // faint minor x gridlines (e.g. every 10 ms) so the rise/settle timing can be gauged by eye
  if (xminor) for (let xv=xminor; xv<xmax; xv+=xminor) {{ const x=lerp(xv,0,xmax,PAD,W-12);
    ctx.strokeStyle='#23272f'; ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,h-22); ctx.stroke(); }}
  for (let k=0;k<=5;k++) {{ const xv=xmax*k/5, x=lerp(xv,0,xmax,PAD,W-12);
    ctx.strokeStyle='#3a4150'; ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,h-22); ctx.stroke();
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
// Inter-sweep variability band: shaded min/max envelope (lo..hi) on a log-frequency x-axis.
function plotBand(ctx,h,F,lo,hi,fmin,fmax,ymin,ymax,color) {{
  ctx.beginPath();
  for (let i=0;i<F.length;i++) {{ const x=logx(F[i],fmin,fmax),y=lerp(hi[i],ymin,ymax,h-22,8); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }}
  for (let i=F.length-1;i>=0;i--) {{ const x=logx(F[i],fmin,fmax),y=lerp(lo[i],ymin,ymax,h-22,8); ctx.lineTo(x,y); }}
  ctx.closePath(); ctx.fillStyle=color; ctx.globalAlpha=0.22; ctx.fill(); ctx.globalAlpha=1;
}}
// Same, on the linear time x-axis of the step response.
function plotBandLin(ctx,h,X,lo,hi,xmax,ymin,ymax,color) {{
  ctx.beginPath();
  for (let i=0;i<X.length;i++) {{ const x=lerp(X[i],0,xmax,PAD,W-12),y=lerp(hi[i],ymin,ymax,h-22,8); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }}
  for (let i=X.length-1;i>=0;i--) {{ const x=lerp(X[i],0,xmax,PAD,W-12),y=lerp(lo[i],ymin,ymax,h-22,8); ctx.lineTo(x,y); }}
  ctx.closePath(); ctx.fillStyle=color; ctx.globalAlpha=0.22; ctx.fill(); ctx.globalAlpha=1;
}}
// Zoomed inset (incrustation) in the lower-right of the step canvas: the first transient — x from 0 to
// when the curve comes back to 1 (~20-25 ms), y windowed around 1 (≈0.75–1.25, widened to the data) so
// the overshoot/return shape is legible without cramming the whole settle into the main plot.
function stepInset(ctx,h,sser,d,pcol) {{
  const recross=(t,y)=>{{ let pk=0; for(let i=1;i<y.length;i++) if(y[i]>y[pk]) pk=i;
    if (y[pk]>1.0) {{ for(let i=pk;i<y.length;i++) if(y[i]<=1.0) return t[i]; }}
    for(let i=0;i<y.length;i++) if(y[i]>=0.98) return t[i]; return t[t.length-1]; }};
  const prim=sser.find(o=>o.primary)||sser[sser.length-1];   // window the inset on the reference pass
  let xz=recross(prim.p.step.t_ms,prim.p.step.y)*1.3||25;
  // y-window around 1: start tracking min/max only once the curve nears the target (>=0.7), so the
  // rise from 0 doesn't drag the floor down — we want the overshoot/return detail, not the whole rise.
  let lo=0.75, hi=1.25;
  sser.forEach(o=>{{ const t=o.p.step.t_ms,y=o.p.step.y; let on=false;
    for(let i=0;i<t.length&&t[i]<=xz;i++){{ if(y[i]>=0.7) on=true; if(on){{ lo=Math.min(lo,y[i]); hi=Math.max(hi,y[i]); }} }} }});
  if (d.step.y_hi) for(let i=0;i<d.step.t_ms.length&&d.step.t_ms[i]<=xz;i++) hi=Math.max(hi,d.step.y_hi[i]);
  lo=Math.floor(lo/0.05)*0.05; hi=Math.ceil(hi/0.05)*0.05;
  const iw=(W-PAD-12)*0.40, ih=(h-30)*0.52, x0=W-12-iw-6, y0=h-22-ih-8;
  const xp=t=>x0+(t/xz)*iw, yp=v=>y0+ih-(v-lo)/(hi-lo)*ih;
  ctx.fillStyle='rgba(13,16,22,0.92)'; ctx.strokeStyle='#3a4150'; ctx.lineWidth=1;
  ctx.fillRect(x0,y0,iw,ih); ctx.strokeRect(x0,y0,iw,ih);
  ctx.save(); ctx.beginPath(); ctx.rect(x0,y0,iw,ih); ctx.clip();
  ctx.strokeStyle='#5a6273'; ctx.setLineDash([3,2]); ctx.beginPath(); ctx.moveTo(x0,yp(1)); ctx.lineTo(x0+iw,yp(1)); ctx.stroke(); ctx.setLineDash([]);
  if (d.step.y_lo && !HIDDEN.has(PRIMARY)) {{ ctx.beginPath();
    for(let i=0;i<d.step.t_ms.length&&d.step.t_ms[i]<=xz;i++){{ const x=xp(d.step.t_ms[i]),y=yp(d.step.y_hi[i]); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }}
    for(let i=d.step.t_ms.length-1;i>=0;i--){{ if(d.step.t_ms[i]>xz)continue; ctx.lineTo(xp(d.step.t_ms[i]),yp(d.step.y_lo[i])); }}
    ctx.closePath(); ctx.fillStyle=pcol; ctx.globalAlpha=0.22; ctx.fill(); ctx.globalAlpha=1; }}
  for (const o of sser) {{ ctx.globalAlpha=o.primary?1:0.5; ctx.strokeStyle=PAL[o.i%PAL.length]; ctx.lineWidth=o.primary?2:1.4;
    const t=o.p.step.t_ms,y=o.p.step.y; ctx.beginPath(); let started=false;
    for(let i=0;i<t.length&&t[i]<=xz;i++){{ const x=xp(t[i]),yy=yp(y[i]); started?ctx.lineTo(x,yy):ctx.moveTo(x,yy); started=true; }}
    ctx.stroke(); }}
  ctx.globalAlpha=1; ctx.restore();
  ctx.fillStyle='#9ecbff'; ctx.font='9px sans-serif'; ctx.fillText('zoom 0–'+xz.toFixed(0)+' ms', x0+4, y0+10);
  ctx.fillStyle='#8893a5'; ctx.fillText(hi.toFixed(2), x0+iw-26, y0+10); ctx.fillText(lo.toFixed(2), x0+iw-26, y0+ih-4);
}}
// Small fixed-size canvas for the per-axis evolution sparkline grid (cadre 3).
function mkMini(parent,w,h) {{ const c=document.createElement('canvas'); c.width=w; c.height=h;
  c.style.margin='2px 8px 6px 0'; c.style.display='inline-block'; parent.appendChild(c); return c; }}
// Hover a plotted point (stored in canvas._hpts as {{x,y,t}}) -> show its value in the shared #ptip.
function miniHover(canvas) {{
  canvas.onmousemove=(e)=>{{
    const r=canvas.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top, tip=document.getElementById('ptip');
    let best=null, bd=1e9;
    for (const pt of (canvas._hpts||[])) {{ const dd=(pt.x-mx)*(pt.x-mx)+(pt.y-my)*(pt.y-my); if (dd<bd) {{ bd=dd; best=pt; }} }}
    if (best && bd<169) {{ tip.textContent=best.t; tip.style.display='block'; tip.style.left=(e.clientX+12)+'px'; tip.style.top=(e.clientY+12)+'px'; }}
    else tip.style.display='none';
  }};
  canvas.onmouseleave=()=>{{ document.getElementById('ptip').style.display='none'; }};
}}
// One indicator's evolution across passes: median dot + min/max whisker (when a pass has it),
// a bare dot otherwise (single-sweep pass). Null medians (e.g. no crossover) break the line.
// opts.zones = [{{lo,hi,fill}}] horizontal reference bands; opts.ctx_lo/ctx_hi force the y-range
// to include a context value (so a reference band stays visible even when the data is far from it).
function miniRange(pts,opts) {{
  opts=opts||{{}}; let vals=[]; pts.forEach(p=>{{ if(p.v!=null)vals.push(p.v); if(p.lo!=null)vals.push(p.lo); if(p.hi!=null)vals.push(p.hi); }});
  if(opts.ctx_lo!=null)vals.push(opts.ctx_lo); if(opts.ctx_hi!=null)vals.push(opts.ctx_hi);
  if(!vals.length) return null;
  let ymin=Math.min(...vals), ymax=Math.max(...vals);
  if(ymax-ymin<1e-6) {{ ymax+=1; ymin-=1; }}
  const pad=(ymax-ymin)*0.14; return [ymin-pad, ymax+pad];
}}
function miniSeries(ctx,pts,xpos,ypos,color,dash) {{
  ctx.setLineDash(dash||[]); ctx.strokeStyle=color; ctx.globalAlpha=0.5; ctx.lineWidth=1;
  ctx.beginPath(); let started=false;
  pts.forEach((p,i)=>{{ if(p.v==null){{started=false;return;}} const x=xpos(i),y=ypos(p.v); started?ctx.lineTo(x,y):ctx.moveTo(x,y); started=true; }});
  ctx.stroke(); ctx.globalAlpha=1; ctx.setLineDash([]);
  pts.forEach((p,i)=>{{ const x=xpos(i);
    if(p.lo!=null&&p.hi!=null&&p.hi-p.lo>1e-9) {{ const y0=ypos(p.lo),y1=ypos(p.hi);
      ctx.strokeStyle=color; ctx.lineWidth=1.4; ctx.beginPath(); ctx.moveTo(x,y0); ctx.lineTo(x,y1);
      ctx.moveTo(x-3,y0); ctx.lineTo(x+3,y0); ctx.moveTo(x-3,y1); ctx.lineTo(x+3,y1); ctx.stroke(); }}
    if(p.v!=null) {{ ctx.fillStyle=color; ctx.beginPath(); ctx.arc(x,ypos(p.v),2.6,0,7); ctx.fill(); }} }});
}}
function drawMini(canvas,title,pts,color,opts) {{
  opts=opts||{{}};
  const ctx=canvas.getContext('2d'), cw=canvas.width, ch=canvas.height;
  const L=34, Rr=10, Tt=18, Bb=16, unit=opts.unit||'';
  ctx.clearRect(0,0,cw,ch); ctx.font='10px sans-serif';
  ctx.fillStyle=color; ctx.fillText(title,4,12);   // title in the indicator colour (shared identity)
  const rg=miniRange(pts,opts);
  if(!rg) {{ ctx.fillStyle='#5a6273'; ctx.fillText('—',L,ch/2); return; }}
  const [ymin,ymax]=rg, n=pts.length;
  const xpos=i=> n>1 ? L+(cw-L-Rr)*i/(n-1) : (L+cw-Rr)/2;
  const ypos=v=> (ch-Bb)-(v-ymin)/(ymax-ymin)*(ch-Bb-Tt);
  // reference zones (e.g. Ms healthy band) behind everything, clipped to the visible range
  for (const z of (opts.zones||[])) {{ const y1=ypos(Math.min(z.hi,ymax)), y0=ypos(Math.max(z.lo,ymin));
    if(y0>y1){{ ctx.fillStyle=z.fill; ctx.fillRect(L,y1,cw-Rr-L,y0-y1); }} }}
  ctx.strokeStyle='#2a2f3a'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(L,Tt); ctx.lineTo(L,ch-Bb); ctx.lineTo(cw-Rr,ch-Bb); ctx.stroke();
  ctx.fillStyle='#8893a5'; const dec=(ymax-ymin>=10)?0:1;
  ctx.fillText(ymax.toFixed(dec)+unit,2,Tt+7); ctx.fillText(ymin.toFixed(dec)+unit,2,ch-Bb+2);  // unit recalled on the ordinate
  miniSeries(ctx,pts,xpos,ypos,color,opts.dash);
  ctx.fillStyle='#8893a5'; pts.forEach((p,i)=>ctx.fillText(p.n, xpos(i)-3, ch-4));
  canvas._hpts=pts.map((p,i)=> p.v!=null ? {{x:xpos(i), y:ypos(p.v), t:p.v.toFixed(dec)+unit}} : null).filter(Boolean);
  miniHover(canvas);
}}
// Two indicators sharing one tile (independent left/right y-axes): A = left, solid; B = right, dashed.
// Title = the two labels in their own colour, each UNDERLINED with its line style (solid A / dashed B),
// so no "(plein)/(tireté)" words are needed. uA/uB are the units recalled on each ordinate.
function drawMini2(canvas,lA,lB,ptsA,ptsB,colA,colB,uA,uB) {{
  uA=uA||''; uB=uB||'';
  const ctx=canvas.getContext('2d'), cw=canvas.width, ch=canvas.height;
  const L=24, Rr=24, Tt=18, Bb=16;
  ctx.clearRect(0,0,cw,ch); ctx.font='10px sans-serif';
  // label A (solid underline) · label B (dashed underline)
  ctx.fillStyle=colA; ctx.fillText(lA,4,11); const wA=ctx.measureText(lA).width;
  ctx.strokeStyle=colA; ctx.lineWidth=1.4; ctx.beginPath(); ctx.moveTo(4,14); ctx.lineTo(4+wA,14); ctx.stroke();
  ctx.fillStyle='#8893a5'; ctx.fillText(' · ',4+wA,11); const wS=ctx.measureText(' · ').width, xB=4+wA+wS;
  ctx.fillStyle=colB; ctx.fillText(lB,xB,11); const wB=ctx.measureText(lB).width;
  ctx.strokeStyle=colB; ctx.setLineDash([3,2]); ctx.beginPath(); ctx.moveTo(xB,14); ctx.lineTo(xB+wB,14); ctx.stroke(); ctx.setLineDash([]);
  const ra=miniRange(ptsA), rb=miniRange(ptsB);
  if(!ra && !rb) {{ ctx.fillStyle='#5a6273'; ctx.fillText('—',L,ch/2); return; }}
  const n=ptsA.length;
  const xpos=i=> n>1 ? L+(cw-L-Rr)*i/(n-1) : (L+cw-Rr)/2;
  ctx.strokeStyle='#2a2f3a'; ctx.lineWidth=1;
  ctx.beginPath(); ctx.moveTo(L,Tt); ctx.lineTo(L,ch-Bb); ctx.lineTo(cw-Rr,ch-Bb); ctx.stroke();
  const hp=[];
  if(ra) {{ const [aMin,aMax]=ra, yA=v=>(ch-Bb)-(v-aMin)/(aMax-aMin)*(ch-Bb-Tt);
    ctx.fillStyle='#8893a5'; ctx.fillText(aMax.toFixed(0)+uA,0,Tt+7); ctx.fillText(aMin.toFixed(0)+uA,0,ch-Bb+2);
    miniSeries(ctx,ptsA,xpos,yA,colA);
    ptsA.forEach((p,i)=>{{ if(p.v!=null) hp.push({{x:xpos(i), y:yA(p.v), t:p.v.toFixed(0)+uA}}); }}); }}
  if(rb) {{ const [bMin,bMax]=rb, yB=v=>(ch-Bb)-(v-bMin)/(bMax-bMin)*(ch-Bb-Tt);
    ctx.fillStyle='#8893a5'; ctx.fillText(bMax.toFixed(0)+uB,cw-Rr+2,Tt+7); ctx.fillText(bMin.toFixed(0)+uB,cw-Rr+2,ch-Bb+2);
    miniSeries(ctx,ptsB,xpos,yB,colB,[3,2]);
    ptsB.forEach((p,i)=>{{ if(p.v!=null) hp.push({{x:xpos(i), y:yB(p.v), t:p.v.toFixed(0)+uB}}); }}); }}
  ctx.fillStyle='#8893a5'; ptsA.forEach((p,i)=>ctx.fillText(p.n, xpos(i)-3, ch-4));
  canvas._hpts=hp; miniHover(canvas);
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
function filterOverlay(ctx,h,fmin,fmax,fms) {{
  if (CFG.dyn_notch) vband(ctx,h,CFG.dyn_notch.min,CFG.dyn_notch.max,fmin,fmax,'rgba(255,212,121,0.07)');
  if (CFG.gyro_lpf1 && CFG.gyro_lpf1.dyn) {{ vline(ctx,h,CFG.gyro_lpf1.dyn[0],fmin,fmax,'#5a9bd4','gyroLPF'); vline(ctx,h,CFG.gyro_lpf1.dyn[1],fmin,fmax,'#5a9bd4',''); }}
  if (CFG.dterm_lpf1 && CFG.dterm_lpf1.dyn) {{ vline(ctx,h,CFG.dterm_lpf1.dyn[0],fmin,fmax,'#d48fd4','dtermLPF'); vline(ctx,h,CFG.dterm_lpf1.dyn[1],fmin,fmax,'#d48fd4',''); }}
  vline(ctx,h,fms,fmin,fmax,'#ffab40','f(Ms)');
}}
// Frequency where coherence drops below the gate for good (the trusted-band edge): scan for the
// first point past which it stays under GATE for a small window, so a single dip doesn't trip it.
function trustEdge(F,coh) {{
  if (!F || !F.length) return null;
  const n=F.length, win=Math.max(3,Math.floor(n*0.04));
  for (let i=0;i<n-win;i++) {{ let below=true;
    for (let j=i;j<i+win;j++) if (coh[j]>=GATE) {{ below=false; break; }}
    if (below) return F[i]; }}
  return F[n-1];
}}
// Shade the un-trusted (coherence < gate) region and mark the edge — echoed on coh, gain & phase so
// the eye sees the flat gain sits inside the trusted band.
function coherZone(ctx,h,ftrust,fmin,fmax,label) {{
  if (ftrust && ftrust<fmax) vband(ctx,h,ftrust,fmax,fmin,fmax,'rgba(126,138,160,0.11)');
  vline(ctx,h,ftrust,fmin,fmax,'#8a93a5',label||'');
}}
const root=document.getElementById('root');
const single = R.total_passes<=1;
const HIDDEN = new Set();   // pass indices whose overlay curves are hidden (pill toggles, global)

// --- Shared visual identity: one colour + pictogram per INDICATOR and per CONFIG item, reused
// everywhere they are named (tune score, evolution tiles, config tooltip, comparison table) so the
// eye links them at a glance. Filter colours match the Bode overlay (gyro/dterm/notch). ---
const IND={{
  overshoot:{{c:'#ff7a6b',p:'▲'}}, rise:{{c:'#ffc14d',p:'↑'}}, settle:{{c:'#59c2b0',p:'↓'}},
  margin:{{c:'#6fd36f',p:'∠'}}, ms:{{c:'#b58cff',p:'◎'}}, noise:{{c:'#4fa3e0',p:'≈'}}
}};
function citem(lbl) {{
  if (/P\\/I\\/D/.test(lbl)) return {{c:'#9ecbff',p:'⚙'}};
  if (/D_max/.test(lbl))    return {{c:'#ffab40',p:'▲'}};
  if (/gyro/i.test(lbl))    return {{c:'#5a9bd4',p:'∿'}};
  if (/D-term/i.test(lbl))  return {{c:'#d48fd4',p:'∿'}};
  if (/notch/i.test(lbl))   return {{c:'#ffd479',p:'▽'}};
  if (/RPM/i.test(lbl))     return {{c:'#aed581',p:'⟳'}};
  return {{c:'#9ad',p:'·'}};
}}
// A pass's config as coloured+pictogram HTML, for the rich hover tooltip on any pass label. Each
// field shows from→to (underlined) vs the previous pass, so a glance reveals exactly what moved.
function cfgHTML(p) {{
  const fields=cfgFields(p.config||{{}});
  let s='<b style="color:#cfe3ff">'+(LANG==='fr'?'Passe ':'Pass ')+p.n+'</b>'
    +(p.file?' <span style="color:#8893a5">'+p.file+'</span>':'');
  if (!fields.length) return s+'<div style="color:#8893a5">'+(LANG==='fr'?'(config non lue dans ce log)':'(no config parsed)')+'</div>';
  const idx=PASSES.indexOf(p);
  const prev=(idx>0)?Object.fromEntries(cfgFields(PASSES[idx-1].config||{{}})):null;
  if (prev) s+=' <span style="color:#8893a5">— Δ '+(LANG==='fr'?'vs passe ':'vs pass ')+PASSES[idx-1].n+'</span>';
  s+='<div style="margin-top:4px">';
  for (const [lbl,val] of fields) {{
    const ci=citem(lbl), changed=prev && prev[lbl]!=null && prev[lbl]!==val;
    const shown=changed ? ('<u style="color:#ffd479">'+prev[lbl]+' → '+val+'</u>') : val;
    s+='<div style="color:'+ci.c+'">'+ci.p+' '+lbl+' : <span style="color:#e6eaf2">'+shown+'</span></div>';
  }}
  return s+'</div>';
}}

// Teaching example for the throttle×freq map tooltip: a synthetic BAD map drawn with the SAME colour
// formula as the real one — a rising motor harmonic (freq grows with throttle), its 2nd harmonic, and a
// FIXED-frequency frame resonance, over a slightly raised floor; annotations baked in. Memoised data-URI.
let _mockuri=null;
function mockMapURI() {{
  if (_mockuri) return _mockuri;
  const NT=9, NF=64, W0=300, H0=130, cv=document.createElement('canvas'); cv.width=W0; cv.height=H0;
  const ctx=cv.getContext('2d'), ff=i=>20+480*i/(NF-1), g=(x,c,w)=>Math.exp(-0.5*((x-c)/w)**2);
  const M=[]; let lo=1e9, hi=-1e9;
  for (let r=0;r<NT;r++) {{ const t=0.15+0.85*r/(NT-1), row=[];
    for (let i=0;i<NF;i++) {{ const fr=ff(i);
      let v=-33 + 2*Math.sin(i*1.7+r);              // raised, mildly noisy floor
      v+=34*g(fr,110+320*t,17) + 18*g(fr,220+640*t,16) + 30*g(fr,230,11);
      row.push(v); lo=Math.min(lo,v); hi=Math.max(hi,v); }}
    M.push(row); }}
  const Lx=26, cw=(W0-Lx)/NF, chh=(H0-24)/NT;
  for (let r=0;r<NT;r++) for (let i=0;i<NF;i++) {{ const tn=(M[r][i]-lo)/((hi-lo)||1);
    ctx.fillStyle='rgb('+Math.round(255*Math.min(1,tn*1.6))+','+Math.round(120*Math.max(0,1-Math.abs(tn-0.5)*2))+','+Math.round(255*(1-tn))+')';
    ctx.fillRect(Lx+i*cw, 6+(NT-1-r)*chh, cw+1, chh+1); }}
  ctx.fillStyle='#c9d2e0'; ctx.font='8px sans-serif'; ctx.fillText('throttle ↑',1,12); ctx.fillText('freq →',W0-32,H0-2);
  ctx.fillStyle='#fff'; ctx.font='bold 9px sans-serif';
  ctx.fillText('↗ moteur', Lx+NF*cw*0.60, 20); ctx.fillText('│ résonance', Lx+2, H0-14);
  _mockuri=cv.toDataURL('image/png'); return _mockuri;
}}
function mapTipHTML() {{
  return '<b style="color:#cfe3ff">'+T('mapex_h')+'</b>'
    +'<img src="'+mockMapURI()+'" style="display:block;margin:6px 0;border-radius:4px;width:300px">'
    +'<div style="color:#c2cad6;max-width:300px;white-space:normal">'+T('mapex_cap')+'</div>';
}}

// Per-pass show/hide pills, repeated top-right of every axis block. They drive the global HIDDEN
// set, so toggling a pass here hides its overlaid curves across the whole report.
function passPills() {{
  if (single) return null;
  const wrap=el('div','passpills');
  PASSES.forEach((p,i)=>{{
    const off=HIDDEN.has(i), col=PAL[i%PAL.length];
    const b=document.createElement('button');
    b.className='pillbtn passtip'+(off?' off':''); b.textContent='P'+p.n;
    b.dataset.pass=i;
    b.style.borderColor=col; b.style.color=off?'#6b7689':col;
    b.onclick=()=>{{ off?HIDDEN.delete(i):HIDDEN.add(i); render(); }};
    wrap.appendChild(b);
  }});
  return wrap;
}}

function render() {{
  root.innerHTML='';
  W = Math.max(720, Math.min(1760, window.innerWidth - 48));   // responsive: fill the window
  document.getElementById('hdr').innerHTML =
      '<div class="banner-main"><span class="banner-icon">∿</span>'
    + '<div><div class="banner-title">'+T('title')+'</div>'
    + '<div class="banner-sub">'+T('subtitle')+'</div></div></div>'
    + '<div class="banner-tags"><span class="chip">Chirp</span><span class="chip">Analysis</span>'
    + '<span class="chip">Betaflight</span><span class="chip">Tuning</span></div>'
    + '<div class="banner-file">— '+FILE+'</div>';
  document.getElementById('langbtn').textContent = T('lang_btn');

  // ---- Guide ----
  {{
    const g=el('div','axis guide'); let s='<h2>'+T('guide_h')+'</h2>';
    s+='<div class=pipe>'+T('pipe').split(' | ').map(x=>'<b>'+x+'</b>').join('<span class=arr>→</span>')+'</div>';
    s+='<p>'+T('guide_order')
        .replace('{{filt}}',tip('gyro_lpf','<b>'+T('w_filt')+'</b>'))
        .replace('{{pid}}',tip('pid','<b>'+T('w_pid')+'</b>'))
        .replace('{{phase}}',tip('phase',T('w_phase')))
        .replace('{{pm}}',tip('phase_margin',T('w_pm')))
        .replace('{{res}}',tip('resonance',T('w_res')))+'</p>';
    s+='<p>'+(single ? T('guide_single') : T('guide_multi').replace('{{n}}',R.total_passes))+'</p>';
    s+='<p class=meta>'+T('guide_vsag')+'</p>';
    s+='<p class=meta>'+T('guide_add')+'</p>';
    g.innerHTML=s; root.appendChild(g);
  }}

  // ---- TUNE score (composite 0-100 + delta vs previous pass: better/worse after a config change) ----
  if (PRI.tune_score && PRI.tune_score.overall!=null) {{
    const ts=PRI.tune_score;
    const box=el('div','axis score'); let s='<h2>'+T('score_h')+'</h2>';
    let dtxt='';
    const prev=(PRIMARY>0 && PASSES[PRIMARY-1] && PASSES[PRIMARY-1].tune_score) ? PASSES[PRIMARY-1].tune_score : null;
    if (prev && prev.overall!=null) {{
      const dv=Math.round((ts.overall-prev.overall)*10)/10;
      const col=dv>0?'#7ddf7d':(dv<0?'#ff8a80':'#8893a5'), ar=dv>0?'▲':(dv<0?'▼':'=');
      dtxt='<span class=scoredelta style="color:'+col+'">'+ar+' '+(dv>0?'+':'')+dv+' '+T('score_vs')+'</span>';
    }}
    s+='<div class=scoreband><span class=scorebig>'+ts.overall.toFixed(0)+'<span class=scoremax>/100</span></span>'
     + '<span class=scoregrade>'+ts.grade+'</span>'+dtxt+'</div>';
    const SUBL={{overshoot:'overshoot', rise:T('sc_rise'), margin:T('sc_margin'), ms:'Ms', noise:T('sc_noise')}};
    let rows='';
    for (const ax of Object.keys(ts.axes)) {{
      const a=ts.axes[ax];
      const sub=Object.keys(SUBL).filter(k=>a.subs[k]!=null).map(k=>'<span style="color:'+IND[k].c+'">'+IND[k].p+' '+SUBL[k]+' '+a.subs[k]+'</span>').join('  ');
      rows+='<tr><td><b>'+ax+'</b></td><td><b style="color:#9ecbff">'+a.score.toFixed(0)+'</b></td><td>'+sub+'</td></tr>';
    }}
    s+='<table class=scoretab>'+rows+'</table>';
    // every pass's overall score (small), with a star on the best — the comparative view at a glance
    const scored=PASSES.map((p,i)=>({{n:p.n, i:i, v:(p.tune_score&&p.tune_score.overall)}})).filter(o=>o.v!=null);
    if (scored.length>1) {{
      const best=Math.max(...scored.map(o=>o.v));
      const line=scored.map(o=>'<span class="passtip" data-pass="'+o.i+'" style="color:'+PAL[o.i%PAL.length]+'">P'+o.n+' ('+o.v.toFixed(0)+')</span>'
        +(o.v===best?'<span style="color:#ffd479"> ★</span>':'')).join('  ·  ');
      s+='<div class="meta scoreall">'+T('score_all')+' '+line+'</div>';
    }}
    s+='<p class=meta>'+T('score_cap')+'</p>';
    box.innerHTML=s; root.appendChild(box);
  }}

  // ---- Per-axis indicator evolution (right after the score: it shows how each sub-metric moved
  // pass to pass, backing up the single number above) ----
  {{
    // One colour AND one pattern (solid) for every axis — the axes are told apart by their labelled
    // row, not by style. A second pattern (dashed) is used only inside the dual tile to separate its
    // two curves. Hover a point to read its value.
    const sm=(d,k)=>(d.step&&d.step.metrics)?d.step.metrics[k]:null;
    // Ms healthy/danger reference bands (cf. glossary): 1.3–2 = sain, >2 = nerveux/peu robuste.
    const MSZONES=[{{lo:1.3,hi:2.0,fill:'rgba(120,200,120,0.14)'}},{{lo:2.0,hi:9,fill:'rgba(255,120,120,0.12)'}}];
    // each tile carries the shared INDICATOR identity (colour+picto from IND), so a column links to
    // the same-coloured sub-score in the tune note above. Axes (rows) are told apart by their label.
    const INDIC=[
      {{k:'s', key:'overshoot', t:'overshoot', u:'%', g:d=>sm(d,'overshoot_pct'), r:d=>d.overshoot_range}},
      {{k:'s', key:'rise', t:(LANG==='fr'?'montée':'rise'), u:'ms', g:d=>sm(d,'rise_ms'), r:d=>d.rise_range}},
      {{k:'s', key:'settle', t:(LANG==='fr'?'établiss.':'settle'), u:'ms', g:d=>sm(d,'settle_ms'), r:d=>d.settle_range}},
      {{k:'d', uA:'°', uB:'Hz', gA:d=>d.pm_guaranteed_deg, rA:d=>d.pm_guaranteed_range, gB:d=>d.f_ms_hz, rB:d=>d.f_ms_range}},
      {{k:'s', key:'ms', t:'Ms', u:'', g:d=>d.ms, r:d=>d.ms_range, opts:{{ctx_lo:1.0, ctx_hi:2.1, zones:MSZONES}}}},
    ];
    const axesSet=[]; PASSES.forEach(p=>Object.keys(p.axes||{{}}).forEach(a=>{{ if(!axesSet.includes(a)) axesSet.push(a); }}));
    const ord=['roll','pitch','yaw']; axesSet.sort((a,b)=>ord.indexOf(a)-ord.indexOf(b));
    if (axesSet.length) {{
      const box=el('div','axis'); root.appendChild(box);
      box.appendChild(el('h2',null,T('evo_h')));
      box.appendChild(el('div','meta',T('evo_cap')));
      const mw=Math.max(170, Math.min(260, Math.floor((W-30)/3)-10)), mh=128;
      const ptsFor=(axis,g,r)=>PASSES.map(p=>{{ const d=(p.axes||{{}})[axis]; const v=d?g(d):null; const rg=d?r(d):null;
        return {{n:p.n, v:(v==null?null:v), lo:rg?rg[0]:null, hi:rg?rg[1]:null}}; }});
      for (const axis of axesSet) {{
        box.appendChild(el('div','passleg','<b style="border-bottom:2.5px solid #6b7689;padding-bottom:2px">'+axis.toUpperCase()+'</b>'));
        const grid=el('div'); grid.style.lineHeight='0'; box.appendChild(grid);
        for (const ind of INDIC) {{
          if (ind.k==='d') {{   // dual tile: margin (green, solid) + f(Ms) (purple, dashed) — indicator colours
            const A=ptsFor(axis,ind.gA,ind.rA), B=ptsFor(axis,ind.gB,ind.rB);
            if (A.some(p=>p.v!=null)||B.some(p=>p.v!=null)) drawMini2(mkMini(grid,mw,mh), (LANG==='fr'?'marge':'margin'), 'f(Ms)', A, B, IND.margin.c, IND.ms.c, ind.uA, ind.uB);
          }} else {{
            const col=IND[ind.key].c, pts=ptsFor(axis,ind.g,ind.r);
            const o2=Object.assign({{}}, ind.opts||{{}}, {{unit:ind.u||''}});
            if (pts.some(p=>p.v!=null)) drawMini(mkMini(grid,mw,mh), IND[ind.key].p+' '+ind.t, pts, col, o2);
          }}
        }}
      }}
    }}
  }}

  // (the former "current settings" cadre is dropped — the config is now in the pass-label tooltips
  //  and the settings-comparison table below.)

  // ---- Settings comparison (sits where the overview used to: the config diff across passes,
  //      changed cells highlighted; the per-metric evolution is already shown in the tiles above) ----
  if (!single) {{
    const ref=PASSES.map(p=>cfgFields(p.config||{{}})).filter(a=>a.length).slice(-1)[0]||[];
    if (ref.length) {{
      const box=el('div','axis step cmp'); root.appendChild(box);
      box.appendChild(el('h2',null,T('cmp_h')));
      let changedAny=false, t='<table class=cmp><tr><th></th>';
      PASSES.forEach((p,i)=>{{ t+='<th><span class=swatch style="background:'+PAL[i%PAL.length]+'"></span><span class="passtip" data-pass="'+i+'">'+T('pass_word')+' '+p.n+'</span></th>'; }});
      t+='</tr>';
      for (const [lbl] of ref) {{
        const ci=citem(lbl);
        t+='<tr><td class=lbl><span style="color:'+ci.c+'">'+ci.p+'</span> '+lbl+'</td>'; let prev=null;
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

  // ---- Step 1: Filtering ----
  {{
    const box=el('div','axis step'); root.appendChild(box);
    box.appendChild(el('h2',null,'<span class=stepnum>1</span>'+tip('gyro_lpf',T('step1_h'))+' '+T('step1_sub')));
    const tm=PRI.throttle_map;
    if (tm && tm.freqs && tm.freqs.length) {{
      box.appendChild(el('h3',null,tip('throttle_map',T('tmap_h'))+' ('+tm.axis+' gyro · '+(tm.source||'?')+')'
        +' <span class="maptip" title="">?</span>'));
      const rows=tm.levels_db.length, cols=tm.freqs.length;
      // Robust colour scale: anchor to the 10th–98th percentiles, not the absolute min/max. With raw
      // min/max a single quiet cell drags the floor down and the whole map saturates red even when the
      // noise is fairly uniform — a contrast artefact, not "noisy everywhere". Percentiles fix that:
      // a calm map reads blue/green, only genuine hot-spots (top ~2%) go red.
      const flat=tm.levels_db.flat().filter(v=>v!==null).sort((a,b)=>a-b);
      const lo=flat[Math.floor(flat.length*0.10)], hi=flat[Math.floor(flat.length*0.98)];
      const cw=W-PAD-12, chh=22, H2=rows*chh+30;
      const ctx=mkCanvas(box,H2).getContext('2d'); ctx.clearRect(0,0,W,H2);
      for (let r=0;r<rows;r++) for (let c=0;c<cols;c++) {{
        const v=tm.levels_db[r][c]; if (v===null) continue; const tn=Math.max(0,Math.min(1,(v-lo)/((hi-lo)||1)));
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
      box.appendChild(el('div','howto','<span class=meta>'+T('tmap_lo')+'</span><span class=scalebar></span><span class=meta>'+T('tmap_hi')+'</span>'));
      box.appendChild(el('div','howto',T('tmap_howto')));
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
    let s='<details class="coll"><summary class="collh">'+tip('resonance',T('filt_h'))+'</summary><ul class="sugg filt">';
    for (const x of fsug) s+='<li>'+loc(x)+'</li>';
    for (const x of nsug) s+='<li>'+loc(x)+'</li>';
    if (!fsug.length && !nsug.length) s+='<li>—</li>';
    s+='</ul></details>'; box.appendChild(el('div',null,s));
  }}

  // ---- Step 2: PID (Bode + step response, all passes overlaid) ----
  {{
    const head=el('div','axis step pid'); root.appendChild(head);
    head.appendChild(el('h2',null,'<span class=stepnum>2</span>'+tip('pid',T('step2_h'))+' '+T('step2_sub')));
    if (!single) head.appendChild(el('div','meta',T('overlay')+' <i>('+T('overlay_hint')+')</i>'));
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
      let scap=T('spectro_cap').replace('{{sg}}',tip('spectrogram','spectrogramme')).replace('{{ax}}',sg.axis);
      if (sg.n_sweeps) scap+=' '+(LANG==='fr'
        ? 'Médiane de '+sg.n_sweeps+' sweeps (alignés sur le temps relatif) — la crête est plus nette, le bruit inter-sweeps moyenné.'
        : 'Median of '+sg.n_sweeps+' sweeps (aligned on relative time) — sharper ridge, inter-sweep noise averaged out.');
      head.appendChild(el('div','legend',scap));
    }}
  }}
  for (const axis of Object.keys(PRI.axes||{{}})) {{
    const d=PRI.axes[axis]; if(!d||!d.freq) continue;
    const box=el('div','axis'); root.appendChild(box);
    const m=d.phase_margin_deg, fco=d.crossover_hz, mu=d.phase_margin_unc_deg;
    const ms=d.ms, fms=d.f_ms_hz, pmg=d.pm_guaranteed_deg;
    let mtxt;
    if (ms!=null) {{
      // Robust scalars only: Ms, f(Ms) and the guaranteed margin. The 0 dB crossover
      // ("bandwidth") and the measured margin are dropped here — on very damped axes the
      // crossover detection breaks down and reports nonsense (e.g. 2 Hz / 165°). The Bode
      // plots below still carry the full picture.
      mtxt = tip('sensitivity','Ms')+' '+ms.toFixed(2)+' @ '+(fms?fms.toFixed(0):'?')+' Hz'
           + ' · '+tip('phase_margin',T('pm_gtd'))+' ≥'+pmg.toFixed(0)+'°';
    }} else {{
      mtxt = m==null ? T('no_xover') : (tip('phase_margin',T('margin'))+' '+m.toFixed(0)+'°'+(mu?(' ±'+mu.toFixed(0)+'°'):'')+' @ '+(fco?fco.toFixed(0):'?')+' Hz');
    }}
    box.appendChild(el('h2',null,axis.toUpperCase()+' <span class=meta>['+d.band_hz[0]+'–'+d.band_hz[1]+' Hz] — '+mtxt+'</span>'));
    const pills=passPills(); if (pills) box.appendChild(pills);
    const fmin=d.band_hz[0]||1, fmax=d.band_hz[1]||500;
    const ser=PASSES.map((p,i)=>({{p:p.axes&&p.axes[axis], i:i, primary:i===PRIMARY}})).filter(o=>o.p&&o.p.freq&&!HIDDEN.has(o.i));
    const PCOL=PAL[PRIMARY%PAL.length];   // primary pass colour, used for its inter-sweep band

    const wrap=v=>((v%360)+360)%360-360;
    // the trusted-band edge (coherence < gate), read on the primary pass and echoed on every plot
    const ftrust = trustEdge(d.freq, d.coherence);
    const trustLbl = (LANG==='fr'?'zone non fiable':'untrusted zone');

    // 1) Coherence first — it defines where the rest can be trusted; the 0.8 gate edge carries down.
    // The reliability note sits next to the title; the grey zone is labelled in-plot.
    box.appendChild(el('h3',null,tip('coherence',LANG==='fr'?'Cohérence':'Coherence')
      +' <span class="meta" style="text-transform:none;letter-spacing:0;font-weight:400">— '
      +T('coh_cap').replace('{{gate}}',GATE.toFixed(1))+'</span>'));
    let ch=mkCanvas(box,Hh-30).getContext('2d');
    drawAxes(ch,Hh-30,fmin,fmax,0,1,'coh');
    coherZone(ch,Hh-30,ftrust,fmin,fmax,trustLbl);
    hline(ch,Hh-30,GATE,0,1,'#7e8aa0',GATE.toFixed(1));
    if (d.coherence_band && !HIDDEN.has(PRIMARY)) plotBand(ch,Hh-30,d.freq,d.coherence_band[0],d.coherence_band[1],fmin,fmax,0,1,PCOL);
    for (const o of ser) plotLine(ch,Hh-30,o.p.freq,o.p.coherence,o.p.coherence.map(_=>1),fmin,fmax,0,1,PAL[o.i%PAL.length],{{dim:!o.primary, lw:o.primary?2:1.3}});

    // 2) Gain — filter-overlay legend moved up next to the title (the grey untrusted zone is still
    //    echoed from coherence on the plot, but no longer needs its own legend entry).
    const bodeLeg='<span style="text-transform:none;letter-spacing:0;font-weight:400;font-size:11px;margin-left:12px">'
      +'<span style="color:#5a9bd4;margin-right:12px">│ '+tip('gyro_lpf',T('leg_gyro'))+'</span>'
      +'<span style="color:#d48fd4;margin-right:12px">│ '+tip('dterm_lpf',T('leg_dterm'))+'</span>'
      +'<span style="color:#ffd479;margin-right:12px">▮ '+tip('dyn_notch',T('leg_notch'))+'</span>'
      +'<span style="color:#ffab40">│ '+tip('sensitivity',T('leg_fms'))+'</span></span>';
    box.appendChild(el('h3',null,tip('gain',T('bode_h'))+bodeLeg));
    let gAll=[]; ser.forEach(o=>gAll=gAll.concat(o.p.gain_db));
    if (d.gain_band) gAll=gAll.concat(d.gain_band[0],d.gain_band[1]);
    let gmin=Math.min(-12,...gAll), gmax=Math.max(12,...gAll);
    let g=mkCanvas(box,Hh).getContext('2d');
    drawAxes(g,Hh,fmin,fmax,gmin,gmax,'gain dB');
    coherZone(g,Hh,ftrust,fmin,fmax,'');
    filterOverlay(g,Hh,fmin,fmax,fms);
    hline(g,Hh,0,gmin,gmax,'#5a6273','0 dB');
    if (d.gain_band && !HIDDEN.has(PRIMARY)) plotBand(g,Hh,d.freq,d.gain_band[0],d.gain_band[1],fmin,fmax,gmin,gmax,PCOL);
    for (const o of ser) plotLine(g,Hh,o.p.freq,o.p.gain_db,o.p.coherence,fmin,fmax,gmin,gmax,PAL[o.i%PAL.length],{{dim:!o.primary, lw:o.primary?2.2:1.5}});

    // 3) Phase — same trusted-zone overlay.
    box.appendChild(el('h3',null,tip('phase',LANG==='fr'?'Phase':'Phase')));
    let p=mkCanvas(box,Hh).getContext('2d');
    drawAxes(p,Hh,fmin,fmax,-360,0,'phase °');
    coherZone(p,Hh,ftrust,fmin,fmax,'');
    hline(p,Hh,-180,-360,0,'#ff8a80','-180°');
    if (d.phase_band && !HIDDEN.has(PRIMARY)) plotBand(p,Hh,d.freq,d.phase_band[0].map(wrap),d.phase_band[1].map(wrap),fmin,fmax,-360,0,PCOL);
    for (const o of ser) plotLine(p,Hh,o.p.freq,o.p.phase_deg.map(wrap),o.p.coherence,fmin,fmax,-360,0,PAL[o.i%PAL.length],{{dim:!o.primary, lw:o.primary?2.2:1.5}});
    vline(p,Hh,fms,fmin,fmax,'#ffab40','f(Ms)');

    // step response (time domain)
    const sser=ser.filter(o=>o.p.step && o.p.step.t_ms && o.p.step.t_ms.length);
    if (sser.length) {{
      box.appendChild(el('h3',null,tip('step_response',T('step_h'))));
      // Full window on the main plot; y normalised to 0.25 steps so 1.0 is always a gridline.
      let xmax=0, ymax=1.0; sser.forEach(o=>{{ xmax=Math.max(xmax,o.p.step.t_ms[o.p.step.t_ms.length-1]); ymax=Math.max(ymax,...o.p.step.y); }});
      if (d.step.y_hi) ymax=Math.max(ymax,...d.step.y_hi);
      ymax=Math.ceil(ymax/0.25)*0.25;
      let st=mkCanvas(box,Hh).getContext('2d');
      drawAxesLin(st,Hh,xmax,0,ymax,'step',0.25,10);   // minor gridlines every 10 ms
      hline(st,Hh,1,0,ymax,'#5a6273','1.0');
      // rise time is measured 10% → 90% of the final value; show those two thresholds (labels left,
      // away from the lower-right inset) so the "rise X ms" number is self-explanatory.
      st.font='10px sans-serif';
      [[0.1,'10%'],[0.9,'90%']].forEach(([v,lb])=>{{ const y=lerp(v,0,ymax,Hh-22,8);
        st.strokeStyle='#3f4856'; st.setLineDash([2,3]); st.beginPath(); st.moveTo(PAD,y); st.lineTo(W-12,y); st.stroke(); st.setLineDash([]);
        st.fillStyle='#6b7689'; st.fillText(lb, PAD+3, y-2); }});
      if (d.step.y_lo && !HIDDEN.has(PRIMARY)) plotBandLin(st,Hh,d.step.t_ms,d.step.y_lo,d.step.y_hi,xmax,0,ymax,PCOL);
      for (const o of sser) plotLin(st,Hh,o.p.step.t_ms,o.p.step.y,xmax,0,ymax,PAL[o.i%PAL.length],{{dim:!o.primary, lw:o.primary?2.2:1.5}});
      stepInset(st,Hh,sser,d,PCOL);   // zoomed incrustation on the rise/overshoot (lower-right)
      const mt=d.step&&d.step.metrics;
      if (mt) box.appendChild(el('div','legend',T('metrics').replace('{{ov}}',mt.overshoot_pct).replace('{{rise}}',mt.rise_ms==null?'–':mt.rise_ms).replace('{{settle}}',mt.settle_ms==null?'–':mt.settle_ms)));
    }}
    // inter-sweep repeatability: median values are shown above; here is the measured min/max spread
    if (d.n_sweeps) {{
      const rg=a=>a&&a[0]!=null?('['+a[0]+'–'+a[1]+']'):'–';
      const fr='Répétabilité sur '+d.n_sweeps+' sweeps (bande ombrée = étendue min/max inter-sweeps) — overshoot '+rg(d.overshoot_range)+' %, montée '+rg(d.rise_range)+' ms, Ms '+rg(d.ms_range)+', marge '+rg(d.phase_margin_range)+'°.';
      const en='Repeatability over '+d.n_sweeps+' sweeps (shaded band = inter-sweep min/max range) — overshoot '+rg(d.overshoot_range)+' %, rise '+rg(d.rise_range)+' ms, Ms '+rg(d.ms_range)+', margin '+rg(d.phase_margin_range)+'°.';
      box.appendChild(el('div','legend',LANG==='fr'?fr:en));
    }}

    // (per-axis textual diagnosis intentionally omitted here — redundant with the evolution tiles
    // at the top; the observations remain in the text/JSON output for the LLM.)
  }}

  // ---- Glossary ----
  {{
    const order=['chirp','gain','phase','sensitivity','phase_margin','crossover','coherence','resonance',
      'noise_psd','motor_harmonics','gyro_lpf','dterm_lpf','dyn_notch','rpm_filter','dmax','pid','throttle_map','spectrogram','step_response','propwash'];
    const box=el('div','axis'); root.appendChild(box);
    let s='<details class="coll"><summary class="collh2">'+T('glossary_h')+'</summary><dl class=glos>';
    for (const k of order) {{ const g=GL[k]; if (g && (g[LANG]||g.fr)) {{
      const head=(g[LANG]||g.fr).split(/ : | — |: /)[0];
      s+='<dt>'+head+'</dt><dd>'+(g[LANG]||g.fr)+'</dd>'; }} }}
    s+='</dl></details>'; box.innerHTML=s;
  }}
}}
document.getElementById('langbtn').onclick=()=>{{ LANG = (LANG==='fr'?'en':'fr'); render(); }};
let _rt; window.addEventListener('resize', ()=>{{ clearTimeout(_rt); _rt=setTimeout(render, 150); }});
// Cursor-positioned HTML tooltip: pass config on a pass label (.passtip[data-pass]),
// or the good/bad throttle-map teaching example on the '?' badge (.maptip).
document.addEventListener('mousemove', e=>{{
  const ht=document.getElementById('htip');
  const pe=e.target.closest && e.target.closest('.passtip[data-pass]');
  const me=e.target.closest && e.target.closest('.maptip');
  if (pe) ht.innerHTML=cfgHTML(PASSES[+pe.dataset.pass]);
  else if (me) ht.innerHTML=mapTipHTML();
  else {{ ht.style.display='none'; return; }}
  ht.style.display='block';
  ht.style.left=Math.min(e.clientX+14, window.innerWidth-ht.offsetWidth-12)+'px';
  ht.style.top=Math.min(e.clientY+14, window.innerHeight-ht.offsetHeight-12)+'px';
}});
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
        nm = _noise_margin_db(d)
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
        df = bbs.load_dataframe(path, args.session)
        fs = _sample_rate(df)
        new_passes.append(_build_pass(path, df, fs, args))

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
