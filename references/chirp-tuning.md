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

### Logged debug fields — two firmware generations (auto-detected)

The CHIRP debug layout changed between firmware versions, and `chirp_analysis.py` **auto-detects**
which one a log uses.

**Legacy** (early `USE_CHIRP`, e.g. commit `1fc6ad23`):

| field | contents | scaling |
|-------|----------|---------|
| `debug[0]` | excitation phase (sinarg, 0…2π) | `× 5000` |
| `debug[1]` | **active chirp axis**: `0` = roll, `1` = pitch, `2` = yaw, `-1` = inactive | raw |
| `debug[2]` | instantaneous chirp frequency | deci-Hz (`× 10`) |
| `debug[3]` | **raw chirp excitation**, before the phase-comp filter | `× 1000` |

**Current** (BF 2025.12.3-alpha, `db7df6e48`, and later): the CHIRP section logs **only**
`debug[0] = 5000·sinarg`. `debug[1..3]` are gone (all-zero in the log).

So when `debug[1..3]` are empty the tool reconstructs everything from `debug[0]`:
- **excitation** `x = sin(debug[0]/5000)` (or the calibrated `setpoint[i]`, the default — see below);
- **instantaneous sweep frequency** = `d/dt unwrap(debug[0]/5000) / 2π` (replaces `debug[2]`);
- **active axis** = `argmax` of the per-window `setpoint` variance (replaces `debug[1]`; the chirp
  drives one axis at a time, so it dominates that window's energy).

For the FRF input it **defaults to the calibrated `setpoint[i]`** (the actually-injected signal in
deg/s, so the closed-loop gain sits near 0 dB and the phase margin is readable). `--input-col debug3`
forces the legacy reference when present; `--input-col debug0` forces the reconstructed unit sine
(shape only). Output is always `gyroADC[i]`.

> **Phase-margin caveat.** The phase is steep at the 0 dB crossover, so the scalar margin is sensitive
> to small crossover shifts (and to flight conditions: battery sag moves the loop gain when
> `vbat_sag_compensation` is off). The tool reports the margin **with an uncertainty (±)** and
> recommends comparing the overlaid curves / step response for before-after, not the bare number.

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

### Sensitivity peak Ms — the primary stability indicator

`Ms = max|S(f)|`, where `S = 1/(1+L) = 1 − T` is the sensitivity function (`T` is the closed-loop
FRF the chirp measures). It is **the robustness headline and the indicator to prefer** — a single
reliable number, unlike the scalar phase margin. Physically Ms is how much the loop *amplifies*
disturbances at its most fragile frequency `f(Ms)` (the vertical marker on the plots). By Bode's
integral `Ms ≥ 1` always, and it bounds the phase margin from below: `PM ≥ 2·arcsin(1/(2·Ms))`.

Rules of thumb (these are the report's thresholds): **Ms ≲ 1.5 comfortable and damped; ~2
marginal; >2 it rings** (step overshoot climbs, propwash sets in). Lower Ms by restoring margin —
less P/D, or more filtering ahead of the PIDs. **Explain Ms briefly whenever you quote it**, and
quote `f(Ms)` with it so the user knows where the loop is weakest.

### Phase margin — secondary, use with care

Phase falls as frequency rises (delay). The classic **phase margin** is the distance of the phase
from −180° at the 0 dB crossover (≥30° healthy, 15–30° marginal, <15° rings). **Avoid relying on
it.** The phase is steep at the crossover, so the scalar is fragile, and when the **low end of the
Bode is noisy** (low coherence / jagged curve) the reported margin is a likely **false positive**.
Prefer Ms. If a margin must be cited, use the **guaranteed margin derived from Ms**
(`pm_guaranteed_deg`, `PM ≥ 2·arcsin(1/2·Ms)`) anchored at `f(Ms)`, not the bare 0 dB-crossover
number — and compare overlaid curves / step response for before-after rather than the scalar.

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
