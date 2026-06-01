# Chirp frequency-response tuning (Bode + coherence)

Closed-loop system identification for Betaflight using the built-in **chirp** generator
(`debug_mode = CHIRP`). Where a step response collapses the whole loop into three scalars
(rise time, overshoot, settling), a chirp sweeps the loop across the full band and lets you
read the **frequency response** directly — gain and phase versus frequency — so you can see
*which* frequency a problem lives at and pick the matching cure.

Tooling: `scripts/chirp_analysis.py` turns a chirp log into a per-axis Bode plot
(gain dB / phase deg) with a coherence gate and a throttle×frequency resonance map.

---

## Why chirp instead of step

- **Closed loop, on the right signal.** The chirp is added straight onto `currentPidSetpoint`
  inside `pid.c`, so you excite exactly the signal the controller tracks — not an open-loop
  mixer injection from the radio.
- **Full band in one flight.** A swept sine reaches the hundreds-of-Hz region (filter and
  resonance territory) that a stick-driven step never excites cleanly.
- **Controlled amplitude and linearity.** Amplitude is set per axis in deg/s; the sweep is
  reproducible, so two runs are comparable.
- **A spectrum, not a number.** The result is gain/phase across frequency: a 48 Hz gain bump
  reads as P/D overshoot; a sharp 550 Hz peak reads as a frame resonance to notch; an early
  roll-off reads as over-filtering.

## Plant is non-stationary

The airframe is not one fixed system: motor/frame resonances **migrate with throttle** (RPM
scales with throttle, so the motor order and the structural modes it excites move in frequency).
Think of the plant as a *family* of responses indexed by throttle, not a single curve. That is
why `chirp_analysis.py` also produces a **throttle × frequency resonance map** — a heatmap of the
gyro PSD per throttle slice that shows the "canyon that moves" as you climb the throttle range.

---

## Firmware: `debug_mode = CHIRP`

`CHIRP` is a **compile-time feature** (`USE_CHIRP`). It is included in most modern unified
targets, but confirm before you rely on it:

```
get debug_mode            # CHIRP must appear in the allowed value list
dump | grep chirp         # shows the chirp_* parameters if the feature is built in
```

If `chirp_*` parameters are absent, your firmware was built without `USE_CHIRP` — reflash a
build that includes it.

### Logged debug fields (from `src/main/flight/pid.c`, `#ifdef USE_CHIRP`)

| field | contents | scaling |
|-------|----------|---------|
| `debug[0]` | excitation phase (sinarg, 0…2π) — lets you reconstruct the pure sine offline | `× 5000` |
| `debug[1]` | **active chirp axis**: `0` = roll, `1` = pitch, `2` = yaw, `-1` = inactive | raw |
| `debug[2]` | instantaneous chirp frequency | deci-Hz (`× 10`) |
| `debug[3]` | **raw chirp excitation**, before the phase-comp filter — the cross-correlation reference | `× 1000` |

`chirp_analysis.py` uses `gyroADC[i]` as the output and **`debug[3]` as the input** by default,
segments the log per axis using **`debug[1]`**, and bounds the analysis band to the frequencies
actually swept using **`debug[2]`**.

> **Lead-lag caveat.** `debug[3]` is the excitation *before* the phase-comp (lead-lag) shaping
> filter and before the per-axis amplitude gain. The measured FRF therefore includes that known
> lead-lag. This is fine for gain shape, resonance frequencies and the throttle map; for a precise
> phase-margin reading, keep the lead-lag in mind (or compensate it from `chirp_lag_freq_hz` /
> `chirp_lead_freq_hz`).

### Why the excitation is lead-lag shaped

A rate-controlled closed loop behaves like a differentiator from setpoint to pidSum at low
frequencies (up to ~30 Hz). A raw chirp would put little energy where it matters and too much
elsewhere. The firmware pre-shapes the excitation with a lead-lag (phase-comp) filter
(`chirp_lag_freq_hz` = pole, `chirp_lead_freq_hz` = zero) so the injected energy is flatter
through the plant and the estimate is well-conditioned across the band.

### `chirp_*` CLI parameters (defaults from firmware)

| parameter | default | meaning |
|-----------|---------|---------|
| `chirp_lag_freq_hz` | 3 | lead-lag pole (excitation shaping) |
| `chirp_lead_freq_hz` | 30 | lead-lag zero (excitation shaping) |
| `chirp_amplitude_roll` | 230 | roll excitation amplitude, deg/s |
| `chirp_amplitude_pitch` | 230 | pitch excitation amplitude, deg/s |
| `chirp_amplitude_yaw` | 180 | yaw excitation amplitude, deg/s |
| `chirp_frequency_start_deci_hz` | 2 | sweep start, deci-Hz (0.2 Hz) |
| `chirp_frequency_end_deci_hz` | 6000 | sweep end, deci-Hz (600 Hz) |
| `chirp_time_seconds` | 20 | sweep duration per axis |

The chirp is **exponential** (more time spent at low frequencies, where resolution is scarce),
and the generator **cycles roll → pitch → yaw** each time the CHIRP mode flag toggles, so a
single flight identifies all three axes.

---

## Reading the result

Order of operations when tuning from a Bode plot: **fix filters first, then PID.** Filter delay
shows up as phase lag and as the high-frequency roll-off; chasing PID against a bad filter setup
just hides the symptom.

### Coherence first — it is the trust gate

Coherence `C(f) = |Pxy|² / (Pxx·Pyy)` ∈ [0, 1] measures how much of the output is linearly
explained by the input at each frequency. **Only read gain and phase where `C(f) > ~0.8.**
`chirp_analysis.py` greys out the low-coherence regions and gates its diagnosis to the trusted
band. Coherence needs averaging — the analyser uses a Welch window sized for several segments;
a too-short excitation or a noisy axis will show low coherence and an unreadable Bode.

### Gain (dB)

- **Flat ~0 dB plateau** at low frequency → the loop tracks well. Good.
- **Bump of a few dB around 40–60 Hz** → closed-loop overshoot — the P/D region. Back off P
  (or add D) if it exceeds ~3 dB.
- **Sharp narrow peak** (often >150 Hz) → a resonance. Cure it with a dynamic/static notch, not
  by changing PID gains.
- **Roll-off at the top of the band** → the low-pass filters. Expected; too early a roll-off
  means over-filtering (extra delay).

### Phase (deg) and phase margin

Phase falls as frequency rises (delay). The **phase margin** is the distance of the phase from
−180° at the frequency where gain crosses 0 dB. Roughly: **≥30° healthy, 15–30° marginal,
<15° rings.** A low margin means the loop is close to sustained oscillation — reduce gains or
add filtering.

---

## Flight protocol

1. **Set up logging:** `set debug_mode = CHIRP`, pick a sensible blackbox rate, `save`.
2. **Tune the sweep if needed:** the defaults (0.2 → 600 Hz over 20 s/axis, ~230 deg/s) suit a
   5-inch; lower the amplitude for fragile or very small craft.
3. **Assign the CHIRP mode** to a switch (Modes tab).
4. **Fly first, then chirp.** Take off, get to a stable hover or gentle forward flight at a
   representative throttle, *then* flip the CHIRP switch. Keep the sticks centred while it sweeps
   — the chirp rides on top of your setpoint, and pilot input pollutes the identification.
5. Let it run through roll → pitch → yaw, land, disarm, pull the log.

> ⚠️ **Never enable CHIRP on the ground / before takeoff.** A full-amplitude sweep on a
> grounded craft can flip or damage it. See betaflight/betaflight#15012 ("Block CHIRP mode
> activation before takeoff").

---

## Running the analyser

```
# Self-contained HTML report (Bode + coherence + throttle map), opens offline:
python -m scripts.chirp_analysis <log.bbl> --html report.html

# Machine-readable:
python -m scripts.chirp_analysis <log.bbl> --json

# Single axis / custom band / explicit input column:
python -m scripts.chirp_analysis <log.bbl> --axis roll --fmin 1 --fmax 600
python -m scripts.chirp_analysis <log.bbl> --input-col setpoint   # if no debug channel logged
```

The HTML report has no external dependencies (a small vanilla-JS canvas renderer) and stacks,
per axis, **gain / phase / coherence** plus the **throttle × frequency resonance map**.
Low-coherence regions are greyed; the −180° phase line and the 0.8 coherence threshold are marked.
