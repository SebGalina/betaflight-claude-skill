# PID, Filter & Rates Tuning Guide

Use this file when the user asks for tuning advice — PIDs, filters, rates, feedforward. Diagnosis comes first; only recommend changes when the symptom is clear.

> **Target version**: Betaflight 2025.12. Freestyle baseline values are for a 5" quad.

## Table of contents

1. [Tuning order](#tuning-order)
2. [VBat sag compensation](#vbat-sag-compensation)
3. [PID tuning](#pid-tuning)
4. [TPA](#tpa)
5. [Anti-gravity](#anti-gravity)
6. [iterm_relax](#iterm_relax)
7. [Dynamic idle](#dynamic-idle)
8. [Filter tuning](#filter-tuning)
9. [Rates tuning](#rates-tuning)
10. [Feedforward](#feedforward)
11. [RC smoothing](#rc-smoothing)
12. [Freestyle baseline (5")](#freestyle-baseline-5)
13. [Symptom map](#symptom-map)

## Tuning order

For freestyle, the official order is:

1. Mechanical: balanced props, tight motors, no vibration source
2. **VBat sag compensation** (before any PID work — ensures consistent feel throughout battery)
3. **PID**: start with D, then P proportionally; leave I for last
4. **Feedforward**
5. **Dynamic D** (disable for consistency, or tune ceiling for racing)
6. **TPA** (only if high-throttle oscillations remain after PID)
7. **iterm_relax**
8. **Anti-gravity**
9. **Dynamic idle**
10. **RC smoothing**
11. **Filters last** (open up only after PID/FF work; filter lag interacts with PID gain)

For racing/general use, a simpler order works: stock PIDs → rates → FF → P → D → I → filters.

## VBat sag compensation

Compensates motor output for voltage sag, giving consistent throttle feel throughout the battery pack.

```
set vbat_sag_lpf_period = 200   # 20-second averaging window
set vbat_sag_compensation = 60  # Range 40–70 for freestyle; 90 for very consistent feel
```

⚠️ `vbat_sag_compensation = 100` pushes motors hard at low voltage and can damage packs chemically. Stay ≤ 70 for freestyle, ≤ 90 as an upper limit.

## PID tuning

### What each term does

- **P (Proportional)**: how hard the FC corrects toward the setpoint. Too low = soft/mushy; too high = high-frequency oscillation on hard cornering.
- **I (Integral)**: holds the craft at setpoint under sustained load (wind, payload). Too low = drift; too high = bounce-back when stopping a maneuver.
- **D / Derivative**: base damping in smooth flight, opposes change. Too low = bouncy/overshoot; too high = hot motors, propwash worse, noise.
- **D_max**: peak D value applied during fast stick inputs. D rises from Derivative toward D_max when you flip fast, then drops back for straight-line flight. Set `d_max_roll = d_roll` (equal) to disable dynamic D and get a fixed D value (recommended for freestyle consistency).
- **F (Feedforward)**: anticipates stick input, drives output ahead of P. No oscillation cost. Too high = twitchy/overshoot on stick inputs.

In 2025.12: `d_roll` = base D, `d_max_roll` = peak D. This is reversed from 4.5 naming — do not paste PID diffs between versions.

### How to bump

Change one axis (pitch or roll) at a time, ±5 at a time, fly a quick test, compare. Yaw last. Use PID profile switching (3-way AUX) to A/B/C in flight.

## TPA

Throttle PID Attenuation — reduces P and D above a throttle threshold to prevent high-throttle oscillations.

```
set tpa_rate = 0.45       # How much to reduce (0.40–0.50 for freestyle)
set tpa_breakpoint = 1650 # Throttle value where reduction begins (1600–1750)
```

Only apply TPA after PID tuning is done at normal throttle. If oscillations persist above TPA breakpoint, also consider `thrust_linear`:

```
set thrust_linear = 20    # 20–40% is enough; no effect above mid-throttle
```

## Anti-gravity

Temporarily boosts I during rapid throttle changes (punch, powerloop) to prevent attitude shift.

```
set anti_gravity_gain = 3.5   # Default; range 3.5–5 for freestyle
```

Higher values = more I boost during throttle chops. Increase if the craft pitches or rolls during fast throttle transitions.

## iterm_relax

Suppresses I accumulation during fast stick inputs to prevent bounce-back on flip exits.

```
set iterm_relax = RPY                # Apply to all axes
set iterm_relax_type = SETPOINT      # Recommended
set iterm_relax_cutoff = 15          # Lower = more relax. Ranges by build:
                                     #   Racing: 30–40
                                     #   Freestyle 5": 15
                                     #   7"+: 10
                                     #   X-Class: 3–5
```

If bounce-back persists, lower `iterm_relax_cutoff` before touching I.

## Dynamic idle

Maintains minimum motor RPM at zero throttle to prevent desyncs and improve low-throttle response. Requires bidirectional DSHOT.

```
set dshot_bidir = ON
set motor_poles = 14          # Verify against your motor spec (critical for RPM filter)
set dynamic_idle_min_rpm = 35 # Roughly 3000–4000 RPM depending on poles; start at 35
```

⚠️ When using dynamic idle, set `transient_throttle_limit = 0`.

## Filter tuning

Two filter chains: **gyro** (raw sensor) and **D-term** (after D calculation). Both have lowpass and notch options.

### Gyro lowpass

In 2025.12, biquad is removed from the gyro path. Use PT1, PT2, or PT3 types. With RPM filtering active, the gyro LPF can be set very high (or disabled at 0) because the RPM notches handle motor-frequency noise.

```
set gyro_lpf1_static_hz = 0      # Disable static LPF1 when using RPM filter
set gyro_lpf1_dyn_min_hz = 250
set gyro_lpf1_dyn_max_hz = 500
set gyro_lpf2_static_hz = 500    # Light anti-aliasing
```

### D-term lowpass

```
set dterm_lpf1_dyn_min_hz = 100
set dterm_lpf1_dyn_max_hz = 200
```

### Dynamic notch

Tracks frame resonances and motor harmonics not covered by the RPM filter.

```
set dyn_notch_count = 1     # Start with 1 for a clean 5" freestyle build
set dyn_notch_q = 250       # Higher Q = narrower notch
set dyn_notch_min_hz = 200  # Never set below 150; ≥200 recommended
```

If motors run hot or audio shows high-frequency whine, tighten filters (lower max_hz). If craft feels laggy on a clean build, open filters (raise min_hz).

### RPM filter

Requires bidirectional DSHOT. Tracks motor frequencies precisely.

```
set dshot_bidir = ON
set motor_poles = 14              # Check your motor spec — wrong value → wrong filter frequencies
set rpm_filter_min_hz = 80        # Default
set rpm_filter_fade_range_hz = 50 # Default — smooth transition at low throttle
```

⚠️ `motor_poles` is critical. Most 5" motors have 14 magnets; verify on your specific motor.

## Rates tuning

Rates control rotation speed per stick deflection — not a PID issue. Don't tune PIDs to fix a rates problem.

Use `rates_type = ACTUAL` for predictability (default in 2025.12):

| Style | center_sensitivity | max_rate | expo | Max (°/s) |
|-------|--------------------|----------|------|-----------|
| Beginner | 50 | 500 | 0 | ~500 |
| Freestyle | 70 | 700 | 40 | ~700 |
| Racing | 85 | 900 | 30 | ~900 |
| Cinematic | 40 | 400 | 60 | ~400 |

Default in 2025.12: center_sensitivity=70, max=670°/s.

## Feedforward

FF makes the craft follow stick inputs without lag — the FC predicts where the setpoint is going.

```
set feedforward_transition = 0       # 0 = full FF everywhere (general/racing)
                                     # 0.9–1 for freestyle (smooth center, full FF at edge)
set feedforward_averaging = AVG_2    # 2_POINT smoothing of FF signal
set feedforward_smooth_factor = 25   # Starting point; see RC smoothing section for link-specific values
set feedforward_jitter_factor = 7    # Suppress noise from RC link (default)
set feedforward_boost = 15           # Extra kick on stick acceleration
```

For freestyle: set `f_roll`, `f_pitch`, `f_yaw` to 90–100. For general use, bump from stock +20 if response feels laggy.

⚠️ `feedforward_jitter_factor` and `feedforward_smooth_factor` serve overlapping purposes — do not use both at high values simultaneously.

## RC smoothing

Reduces step noise from the RC link. The correct value depends on link frequency.

```
set rc_smoothing = 20                  # Freestyle default; 60–120 for cinematic/gimbal work

# For 250 Hz links (e.g. FrSky, older CRSF):
set feedforward_smooth_factor = 40

# For 500 Hz links (ELRS 500Hz, faster CRSF):
set feedforward_smooth_factor = 65

# For cinematic / HD stabilizer:
set rc_smoothing_setpoint_cutoff = 10
set rc_smoothing_feedforward_cutoff = 10
```

## Freestyle baseline (5")

Starting point for a 5" freestyle build with RPM filter and bidirectional DSHOT:

```
# Sag
set vbat_sag_lpf_period = 200
set vbat_sag_compensation = 60

# PIDs
set p_roll = 65
set i_roll = 95
set d_roll = 45
set d_max_roll = 45        # Equal to d_roll = dynamic D disabled
set p_pitch = 65
set i_pitch = 95
set d_pitch = 45
set d_max_pitch = 45
set p_yaw = 35
set i_yaw = 95
set d_yaw = 0

# FF
set feedforward_roll = 95
set feedforward_pitch = 95
set feedforward_yaw = 95
set feedforward_transition = 95  # 0.9–1 as integer in firmware

# TPA
set tpa_rate = 45
set tpa_breakpoint = 1650

# Anti-gravity
set anti_gravity_gain = 4

# Iterm relax
set iterm_relax = RPY
set iterm_relax_type = SETPOINT
set iterm_relax_cutoff = 15

# Dynamic idle
set dynamic_idle_min_rpm = 35
set transient_throttle_limit = 0

# RC smoothing
set rc_smoothing = 20
```

These are starting points, not final values. Always fly with stock PIDs first and verify the build is mechanically sound.

## Symptom map

| Symptom | Most likely cause |
|---------|-------------------|
| High-frequency shake on hard cornering | P too high → lower P 5 |
| Slow, soft, mushy | P too low or FF too low → raise FF first |
| Hot motors, especially after a flight | D too high or filters too open → tighten D-term filter, lower D 5 |
| Bounce-back when stopping a flip | I too high or `iterm_relax_cutoff` too high → lower relax cutoff first |
| Propwash on descent | D too low, or filters too tight → raise D 5, open D-term filter |
| Twitchy on stick inputs | FF too high → lower F 20 |
| Drift in wind | I too low → raise I 10 |
| Yaw spin-out on punchout | yaw I too low → raise i_yaw 20 |
| Attitude shift during throttle punch | Anti-gravity too low → raise anti_gravity_gain |
| Oscillations only at high throttle | TPA not set → add tpa_rate 0.40–0.50 |
| Desyncs at low throttle | Dynamic idle not configured → enable bidirDSHOT + dynamic_idle_min_rpm |
| Jello in video, FPV looks clean | Soft camera mount — not a tune issue |
| Jello in FPV AND camera | Real vibration → mechanical issue, balance props, check motor bell |

## Step response analysis (`scripts/step_response.py`)

Closed-loop system identification (setpoint → gyro) via Welch's cross-spectral method: rise time, overshoot, settling time, delay, per-axis diagnosis.

```
python -m scripts.step_response <log.bbl>                        # text report, all axes
python -m scripts.step_response <log.bbl> --axis roll            # single axis
python -m scripts.step_response <log.bbl> --bandpass --active-only   # best coherence (recommended)
python -m scripts.step_response <log.bbl> --plot                 # step response + coherence figure
python -m scripts.step_response <log.bbl> --json                 # machine-readable
python -m scripts.step_response <log.bbl> --csv curves.csv       # export response curves
python -m scripts.step_response <log.bbl> --nperseg 2048         # larger Welch window
python -m scripts.step_response <decoded.csv>                    # from analyze_blackbox --csv
```

**Signal quality flags** (improve coherence on noisy logs):
- `--bandpass` — Butterworth 4th-order 5–80 Hz before Welch; removes DC drift and motor-frequency noise
- `--active-only` — keeps only frames around fast stick inputs; drops hovering noise and propwash
- `--nperseg N` — override Welch window size (default: auto ~64 ms, power of 2)

**Always use `--bandpass --active-only`** for identification flights with deliberate step inputs. On a typical log this raises coherence from ~0.1 to 0.65–0.80+.

**Coherence warning**: the script reports a coherence value per axis (5–80 Hz band). Coherence < 0.5 on a freestyle or racing log is **normal and expected** — the gyro is driven by many things other than the setpoint (vibrations, propwash, non-linear PID terms like D_max and anti-gravity). The metrics stay indicative but not precise. For reliable coherence (> 0.7) the user needs a **dedicated identification flight**: deliberate full-stick → neutral → full-stick inputs, repeated 3–5 times per axis, with no other maneuvers. Always mention this when presenting step response results from a freestyle or racing log.
