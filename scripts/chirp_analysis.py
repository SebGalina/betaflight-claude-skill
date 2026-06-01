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

Firmware debug-field mapping (src/main/flight/pid.c, `#ifdef USE_CHIRP`):
    debug[0] = 5000 * sinarg          phase of the excitation (0..2pi), for sine reconstruction
    debug[1] = active chirp axis      0=roll, 1=pitch, 2=yaw, -1=inactive
    debug[2] = 10 * fchirp            instantaneous chirp frequency in deci-Hz
    debug[3] = 1000 * chirp           raw chirp excitation (pre phase-comp) — cross-correlation reference

We use gyroADC[i] as the output y and (by default) debug[3] as the input x — the
firmware-labelled reference signal. Note debug[3] is taken *before* the lead-lag
(phase-comp) shaping filter and the per-axis amplitude gain, so the measured FRF
includes that known lead-lag; it is fine for diagnostic gain shape, resonance
frequencies and the throttle map, and is flagged for fine phase-margin reading.
Use --input-col setpoint to fall back to setpoint[i] when no debug channel is present.

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
CHIRP_AXIS_COL = "debug[1]"     # 0=roll 1=pitch 2=yaw -1=inactive
CHIRP_FREQ_COL = "debug[2]"     # deci-Hz
DEFAULT_INPUT_COL = "debug[3]"  # raw chirp excitation x1000 (cross-correlation reference)

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

def _resolve_input_col(df: pd.DataFrame, requested: str, axis_idx: int) -> str | None:
    """Map the --input-col request to an actual column for this axis.

    "setpoint" / "debug[N]" / explicit names are accepted. Falls back to
    setpoint[i] if the requested debug channel is missing (chirp adds onto setpoint).
    """
    if requested == "setpoint":
        col = SETPOINT_COL.format(axis_idx)
        return col if col in df.columns else None
    if requested in df.columns:
        return requested
    # requested debug channel absent -> fall back to setpoint
    fallback = SETPOINT_COL.format(axis_idx)
    return fallback if fallback in df.columns else None


def _axis_mask(df: pd.DataFrame, axis_idx: int) -> np.ndarray:
    """Rows where the chirp is exciting this axis.

    Prefers the firmware axis flag debug[1]; if absent, falls back to a
    setpoint-energy heuristic (rows where this axis carries most of the motion).
    """
    if CHIRP_AXIS_COL in df.columns:
        return df[CHIRP_AXIS_COL].to_numpy() == axis_idx
    # Fallback: no debug[1] -> use the whole flying window for every axis.
    if THROTTLE_COL in df.columns:
        return df[THROTTLE_COL].to_numpy() > THROTTLE_IDLE
    return np.ones(len(df), dtype=bool)


def _swept_band(df: pd.DataFrame, mask: np.ndarray, fmin: float, fmax: float) -> tuple[float, float]:
    """Restrict the band to the frequencies actually swept, from debug[2] (deci-Hz)."""
    if CHIRP_FREQ_COL in df.columns and mask.any():
        f = df.loc[mask, CHIRP_FREQ_COL].to_numpy(float) / 10.0
        f = f[f > 0]
        if f.size:
            return max(fmin, float(np.min(f))), min(fmax, float(np.max(f)))
    return fmin, fmax


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


def _diagnose(peaks, phase_margin, fmin, fmax) -> list[str]:
    hints = []
    fco, margin = phase_margin
    for p in peaks[:3]:
        f = p["freq_hz"]
        if f < 80:
            hints.append(
                f"Gain bump at {f:.0f} Hz (+{p['prominence_db']:.0f} dB) -> closed-loop "
                f"overshoot; this is the P/D region — back off P (or add D) if it exceeds ~3 dB."
            )
        else:
            hints.append(
                f"Sharp gain peak at {f:.0f} Hz (+{p['prominence_db']:.0f} dB) -> resonance; "
                f"target it with a dynamic/static notch, not by changing PID gains."
            )
    if margin is not None:
        if margin <= 0:
            verdict = "UNSTABLE — phase past -180 deg while gain >= 0 dB"
        elif margin >= 30:
            verdict = "healthy"
        elif margin >= 15:
            verdict = "marginal"
        else:
            verdict = "low"
        hints.append(
            f"Phase margin ~{margin:.0f} deg at the {fco:.0f} Hz 0 dB crossover ({verdict}). "
            f"Below ~30 deg the loop rings; reduce gains or add filtering."
        )
    else:
        hints.append(
            "No 0 dB gain crossover inside the coherent band — either the loop stays below "
            "0 dB (conservative tune) or coherence is too low to read the margin."
        )
    if not peaks:
        hints.append("Gain is flat in the coherent band — no overshoot bump or resonance stands out.")
    return hints


# ---------------------------------------------------------------------------
# Throttle x frequency resonance map
# ---------------------------------------------------------------------------

def _throttle_map(df: pd.DataFrame, fs: float, axis_idx: int, fmin: float, fmax: float,
                  nbins: int = THROTTLE_BINS) -> dict:
    """PSD of gyro per throttle slice -> heatmap of how resonances migrate with throttle."""
    gcol = GYRO_COL.format(axis_idx)
    if gcol not in df.columns or THROTTLE_COL not in df.columns:
        return {}
    thr = df[THROTTLE_COL].to_numpy(float)
    flying = thr > THROTTLE_IDLE
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
        "throttle_bins": centers,
        "freqs": [round(float(x), 1) for x in freqs_ref[::step]],
        "levels_db": [row[::step] for row in levels],
    }


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
            nperseg=None) -> dict:
    nyq = fs / 2.0
    fmax = min(fmax, nyq * 0.98)
    if nperseg is None:
        nperseg = _auto_nperseg(fs)

    results: dict = {}
    primary_axis_idx = None
    primary_n = 0
    warned_fallback = False

    for i, axis in enumerate(AXES):
        if axes_filter and axis not in axes_filter:
            continue
        gcol = GYRO_COL.format(i)
        xcol = _resolve_input_col(df, input_col, i)
        if gcol not in df.columns or xcol is None:
            continue
        if (input_col != "setpoint" and input_col not in df.columns
                and xcol == SETPOINT_COL.format(i) and not warned_fallback):
            print(f"Note: input column '{input_col}' not in log; falling back to "
                  f"setpoint[i]. Re-log with debug_mode=CHIRP for the firmware "
                  f"reference signal (cleaner FRF).", file=sys.stderr)
            warned_fallback = True
        mask = _axis_mask(df, i)
        if mask.sum() < 512:
            continue
        if mask.sum() > primary_n:
            primary_n, primary_axis_idx = int(mask.sum()), i

        x = df.loc[mask, xcol].to_numpy(float)
        y = df.loc[mask, gcol].to_numpy(float)
        a_fmin, a_fmax = _swept_band(df, mask, fmin, fmax)

        freqs, gain_db, phase_deg, coh = _frf(x, y, fs, nperseg)
        peaks = _gain_peaks(freqs, gain_db, coh, a_fmin, a_fmax)
        fco, margin = _phase_margin(freqs, gain_db, phase_deg, coh, a_fmin, a_fmax)

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
            "diagnosis": _diagnose(peaks, (fco, margin), a_fmin, a_fmax),
        }

    throttle_map = {}
    if primary_axis_idx is not None:
        throttle_map = _throttle_map(df, fs, primary_axis_idx, fmin, fmax)

    return results, throttle_map


# ---------------------------------------------------------------------------
# Self-contained HTML report (vanilla JS / <canvas>, no external dependencies)
# ---------------------------------------------------------------------------

def _html_report(output: dict, file_name: str) -> str:
    payload = json.dumps(output)
    # The renderer is intentionally dependency-free: a tiny canvas plotting engine.
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Chirp Bode report — {file_name}</title>
<style>
  body {{ font: 13px/1.4 system-ui, sans-serif; margin: 24px; background:#11141a; color:#dfe3ea; }}
  h1 {{ font-size: 18px; }} h2 {{ font-size: 15px; margin: 24px 0 8px; color:#9ecbff; }}
  .axis {{ border:1px solid #2a2f3a; border-radius:8px; padding:12px; margin-bottom:20px; background:#171b22; }}
  canvas {{ display:block; background:#0d1016; border-radius:4px; margin:6px 0; }}
  .diag {{ color:#c9d2e0; }} .diag li {{ margin:2px 0; }}
  .meta {{ color:#8893a5; font-size:12px; }}
  code {{ color:#ffd479; }}
</style></head><body>
<h1>Chirp frequency-response report <span class="meta">— {file_name}</span></h1>
<div id="root"></div>
<script>
const DATA = {payload};
const W = 880, Hh = 150, PAD = 46;
function mkCanvas(parent, h) {{
  const c = document.createElement('canvas'); c.width = W; c.height = h;
  parent.appendChild(c); return c;
}}
function lerp(v,a,b,A,B) {{ return A + (v-a)*(B-A)/((b-a)||1); }}
function logx(f, fmin, fmax) {{ return lerp(Math.log10(f), Math.log10(fmin), Math.log10(fmax), PAD, W-12); }}
function drawAxes(ctx,h,fmin,fmax,ymin,ymax,ylabel) {{
  ctx.clearRect(0,0,W,h); ctx.strokeStyle='#2a2f3a'; ctx.fillStyle='#8893a5'; ctx.font='10px sans-serif';
  ctx.lineWidth=1;
  // y grid
  for (let k=0;k<=4;k++) {{ const yv=ymin+(ymax-ymin)*k/4; const y=lerp(yv,ymin,ymax,h-22,8);
    ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-12,y); ctx.stroke();
    ctx.fillText(yv.toFixed(ymax-ymin>=10?0:1), 4, y+3); }}
  // x grid (decades)
  const d0=Math.floor(Math.log10(fmin)), d1=Math.ceil(Math.log10(fmax));
  for (let d=d0; d<=d1; d++) for (const m of [1,2,5]) {{
    const f=m*Math.pow(10,d); if (f<fmin||f>fmax) continue; const x=logx(f,fmin,fmax);
    ctx.strokeStyle='#20242e'; ctx.beginPath(); ctx.moveTo(x,8); ctx.lineTo(x,h-22); ctx.stroke();
    ctx.fillStyle='#8893a5'; ctx.fillText(f>=1000?(f/1000)+'k':f, x-6, h-8); }}
  ctx.fillStyle='#9ecbff'; ctx.fillText(ylabel, PAD, 7+0); ctx.save();
}}
function plotLine(ctx,h,F,Y,coh,fmin,fmax,ymin,ymax,color) {{
  for (let i=1;i<F.length;i++) {{
    const trusted = coh[i]>={COHERENCE_GATE} && coh[i-1]>={COHERENCE_GATE};
    ctx.strokeStyle = trusted ? color : 'rgba(120,130,150,0.35)';
    ctx.lineWidth = trusted ? 1.8 : 1;
    ctx.beginPath();
    ctx.moveTo(logx(F[i-1],fmin,fmax), lerp(Y[i-1],ymin,ymax,h-22,8));
    ctx.lineTo(logx(F[i],fmin,fmax),   lerp(Y[i],ymin,ymax,h-22,8));
    ctx.stroke();
  }}
}}
function hline(ctx,h,val,ymin,ymax,fmin,fmax,color,label) {{
  const y=lerp(val,ymin,ymax,h-22,8); ctx.strokeStyle=color; ctx.setLineDash([4,3]);
  ctx.beginPath(); ctx.moveTo(PAD,y); ctx.lineTo(W-12,y); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle=color; ctx.fillText(label, W-70, y-3);
}}
const root=document.getElementById('root');
for (const [axis,d] of Object.entries(DATA.axes)) {{
  const box=document.createElement('div'); box.className='axis';
  const t=document.createElement('h2'); t.textContent=axis.toUpperCase()+
    '  ['+d.band_hz[0]+'–'+d.band_hz[1]+' Hz, input '+d.input_col+', n='+d.n_samples+']';
  box.appendChild(t); root.appendChild(box);
  const F=d.freq, C=d.coherence, fmin=d.band_hz[0]||1, fmax=d.band_hz[1]||1000;
  // gain
  let g=mkCanvas(box,Hh).getContext('2d'); let gmin=Math.min(-12,...d.gain_db), gmax=Math.max(12,...d.gain_db);
  drawAxes(g,Hh,fmin,fmax,gmin,gmax,'gain dB'); hline(g,Hh,0,gmin,gmax,fmin,fmax,'#5a6273','0 dB');
  plotLine(g,Hh,F,d.gain_db,C,fmin,fmax,gmin,gmax,'#4fc3f7');
  // phase
  let p=mkCanvas(box,Hh).getContext('2d');
  drawAxes(p,Hh,fmin,fmax,-360,0,'phase °'); hline(p,Hh,-180,-360,0,fmin,fmax,'#ff8a80','-180°');
  plotLine(p,Hh,F,d.phase_deg.map(v=>((v%360)+360)%360-360),C,fmin,fmax,-360,0,'#ba9cff');
  // coherence
  let ch=mkCanvas(box,Hh-40).getContext('2d');
  drawAxes(ch,Hh-40,fmin,fmax,0,1,'coh'); hline(ch,Hh-40,{COHERENCE_GATE},0,1,fmin,fmax,'#7e8aa0','0.8');
  plotLine(ch,Hh-40,F,C,C.map(_=>1),fmin,fmax,0,1,'#80cbc4');
  // diagnosis
  const ul=document.createElement('ul'); ul.className='diag';
  for (const line of d.diagnosis) {{ const li=document.createElement('li'); li.textContent=line; ul.appendChild(li); }}
  box.appendChild(ul);
}}
// throttle x frequency heatmap
const tm=DATA.throttle_map;
if (tm && tm.freqs && tm.freqs.length) {{
  const box=document.createElement('div'); box.className='axis';
  const t=document.createElement('h2'); t.textContent='Throttle × frequency resonance map ('+tm.axis+' gyro)';
  box.appendChild(t); root.appendChild(box);
  const rows=tm.levels_db.length, cols=tm.freqs.length;
  const flat=tm.levels_db.flat().filter(v=>v!==null);
  const lo=Math.min(...flat), hi=Math.max(...flat);
  const cw=W-PAD-12, chh=22, H2=rows*chh+30;
  const cv=mkCanvas(box,H2); const ctx=cv.getContext('2d');
  ctx.clearRect(0,0,W,H2);
  for (let r=0;r<rows;r++) for (let c=0;c<cols;c++) {{
    const v=tm.levels_db[r][c]; if (v===null) continue;
    const tnorm=(v-lo)/((hi-lo)||1);
    const R=Math.round(255*Math.min(1,tnorm*1.6)), G=Math.round(120*Math.max(0,1-Math.abs(tnorm-0.5)*2)), B=Math.round(255*(1-tnorm));
    ctx.fillStyle='rgb('+R+','+G+','+B+')';
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
}}
</script></body></html>
"""


# ---------------------------------------------------------------------------
# Text summary
# ---------------------------------------------------------------------------

def _print_human(output: dict) -> None:
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
        if d["peaks"]:
            ps = ", ".join(f"{p['freq_hz']:.0f}Hz(+{p['prominence_db']:.0f}dB)" for p in d["peaks"][:5])
            print(f"  Gain peaks       : {ps}")
        else:
            print("  Gain peaks       : none above threshold")
        if d["phase_margin_deg"] is not None:
            print(f"  Phase margin     : {d['phase_margin_deg']:.0f} deg @ {d['crossover_hz']:.0f} Hz")
        else:
            print("  Phase margin     : no 0 dB crossover in coherent band")
        print()
        for hint in d["diagnosis"]:
            print(f"  > {hint}")
        print()
    tm = output.get("throttle_map") or {}
    if tm:
        print(f"Throttle map     : {tm['axis']} gyro, {len(tm['throttle_bins'])} throttle bins "
              f"× {len(tm['freqs'])} freqs (see --html / --json for the heatmap)")


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
    ap.add_argument("input", help=".bbl/.bfl log or decoded CSV from analyze_blackbox --csv")
    ap.add_argument("--axis", choices=AXES, help="Analyse a single axis (default: all excited axes)")
    ap.add_argument("--session", type=int, default=None, metavar="N",
                    help="Session index for multi-session logs")
    ap.add_argument("--input-col", default=DEFAULT_INPUT_COL, metavar="COL",
                    help=f"Excitation input column (default {DEFAULT_INPUT_COL}; "
                         f"'setpoint' uses setpoint[i]; falls back to setpoint[i] if absent)")
    ap.add_argument("--fmin", type=float, default=DEFAULT_FMIN, metavar="HZ",
                    help=f"Lower edge of the analysis band (default {DEFAULT_FMIN:g})")
    ap.add_argument("--fmax", type=float, default=DEFAULT_FMAX, metavar="HZ",
                    help=f"Upper edge of the analysis band (default {DEFAULT_FMAX:g}, clamped to Nyquist)")
    ap.add_argument("--nperseg", type=int, default=None, metavar="N",
                    help="Welch window size in samples (default: auto, ~2 Hz resolution)")
    ap.add_argument("--json", action="store_true", help="Output as JSON")
    ap.add_argument("--html", metavar="OUT", help="Write a self-contained HTML Bode report")
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
        results, throttle_map = analyse(
            df, fs, args.input_col, axes_filter,
            fmin=args.fmin, fmax=args.fmax, nperseg=args.nperseg,
        )
        nyq = fs / 2.0
        output = {
            "sample_rate_hz": round(fs),
            "input_col": args.input_col,
            "band_hz": [args.fmin, round(min(args.fmax, nyq * 0.98), 1)],
            "axes": results,
            "throttle_map": throttle_map,
        }

        if args.html:
            Path(args.html).write_text(_html_report(output, path.name), encoding="utf-8")
            print(f"Report written to {args.html}", file=sys.stderr)
        elif args.json:
            print(json.dumps(output, indent=2))
        else:
            _print_human(output)
    finally:
        if tmp_csv:
            tmp_csv.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
