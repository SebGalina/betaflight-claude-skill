# PID, Filter & Rates Tuning Guide

Use this file when the user asks for tuning advice — PIDs, filters, rates, feedforward. Diagnosis comes first; only recommend changes when the symptom is clear.

## Table of contents

1. [Tuning order](#tuning-order)
2. [PID tuning](#pid-tuning)
3. [Filter tuning](#filter-tuning)
4. [Rates tuning](#rates-tuning)
5. [Feedforward](#feedforward)
6. [Symptom map](#symptom-map)

## Tuning order

The correct order is **rates → feedforward → P → D → I → filters last**. Filters should be opened up (less filtering) only after PID/FF work is done, because filter lag interacts with PID gain.

For a new build the priority is:
1. Get the build mechanically sound (balanced props, tight motors, no vibration source)
2. Use Betaflight stock PIDs as a baseline
3. Set rates to taste (don't tune PIDs to a rates problem)
4. Bump feedforward if response feels laggy
5. Only then touch P/D
6. Open filters last if you want crisper feel and motors are cool

## PID tuning

### What each term does

- **P (Proportional)**: how hard the FC corrects toward the setpoint. Too low = soft/slow; too high = oscillation (high-frequency rapid shake).
- **I (Integral)**: holds the craft at setpoint under sustained load (wind, payload). Too low = drift; too high = bounce-back when stopping a maneuver.
- **D (Derivative)**: damping, opposes change. Too low = bouncy/overshoot; too high = hot motors, propwash worse, noise.
- **F (Feedforward)**: anticipates stick input, drives output ahead of P. No oscillation cost. Too high = twitchy/overshoot on stick inputs.

### How to bump

Change one axis (pitch or roll) at a time, ±5 at a time, fly a quick test, compare. Yaw last.

Tuning tools: PID profile switch on a stick (assign 3 profiles in Configurator → Modes), so you can A/B/C in flight.

## Filter tuning

Two filter chains: **gyro** (raw sensor) and **D-term** (after PID). Both have static and dynamic lowpass options.

**Default 4.5 filters are conservative.** For a clean 5" freestyle build with bidirectional DSHOT:

```
set gyro_lowpass_dyn_min_hz = 250
set gyro_lowpass_dyn_max_hz = 500
set dterm_lowpass_dyn_min_hz = 100
set dterm_lowpass_dyn_max_hz = 200
set dyn_notch_count = 3
set dyn_notch_q = 300
```

If motors run hot or audio shows high-frequency whine on a flight recording, **tighten filters** (lower max_hz). If craft feels laggy and you have a clean build, **open filters** (raise min_hz).

### RPM filter

If your ESC supports bidirectional DSHOT (BLHeli_32 16.7+, BlueJay, AM32), enable:

```
set dshot_bidir = ON
set motor_poles = 14    # Check your motor spec
```

This activates the RPM filter, which tracks motor-frequency noise precisely. Dynamic notch then catches harmonics and frame resonance.

## Rates tuning

Rates control rotation speed per stick deflection. Not a PID issue — don't tune PIDs to fix a rates feel problem.

Recommended `rates_type = ACTUAL` for predictability. Common starting points:

| Style | rc_rate | expo | srate | Max rate (°/s) |
|-------|---------|------|-------|----------------|
| Beginner | 7 | 0 | 67 | ~670 |
| Freestyle | 12 | 40 | 80 | ~800 |
| Racing | 14 | 30 | 90 | ~900 |
| Cinematic | 5 | 50 | 50 | ~500 |

Adjust expo to taste: higher expo = softer center, sharper edges.

## Feedforward

FF makes the craft follow stick inputs without lag — the FC predicts where the setpoint is going.

```
set feedforward_transition = 0           # 0 = full FF everywhere
set feedforward_averaging = AVG_2        # Smoothing of FF signal
set feedforward_smooth_factor = 25       # 0-75, higher = smoother but laggier
set feedforward_jitter_factor = 7        # Suppress jitter from RC noise
set feedforward_boost = 15               # Extra kick on stick acceleration
```

If craft feels laggy with stock PIDs, bump `f_pitch`/`f_roll` by 20 each. FF has near-zero cost — it doesn't cause oscillation like P does.

## Symptom map

| Symptom | Most likely cause |
|---------|-------------------|
| High-frequency shake on hard cornering | P too high → lower P 5 |
| Slow, soft, mushy | P too low or FF too low → raise FF first |
| Hot motors, especially after a flight | D too high or filters too open → tighten D-term filter, lower D 5 |
| Bounce-back when stopping a flip | I too high → lower I 10 |
| Propwash on descent | D too low, or filters too tight → raise D 5, open D-term filter |
| Twitchy on stick inputs | FF too high → lower F 20 |
| Drift in wind | I too low → raise I 10 |
| Yaw spin-out on punchout | yaw I too low → raise i_yaw 20 |
| Jello in video, FPV looks clean | Soft camera mount or pitched-too-far gyro — not a tune issue |
| Jello in FPV AND camera | Real vibration → mechanical issue, balance props, check motor bell tightness |
