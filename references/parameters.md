# Betaflight `set` Parameters Reference

Most-used `set` parameters with safe ranges, defaults, and notes. Read this when the user asks about a specific parameter or you need to recommend safe values.

> **Target**: Betaflight 2025.12. Parameter names changed significantly from 4.5.x — see `version-changes.md`.

## Table of contents

1. [PID controller](#pid-controller)
2. [Gyro filters](#gyro-filters)
3. [D-term filters](#d-term-filters)
4. [Dynamic notch](#dynamic-notch)
5. [Rates](#rates)
6. [RC smoothing](#rc-smoothing)
7. [Motor & ESC](#motor--esc)
8. [RPM filter](#rpm-filter)
9. [Failsafe](#failsafe)
10. [OSD & telemetry](#osd--telemetry)

## PID controller

| Parameter | Default | Safe range | Notes |
|-----------|---------|------------|-------|
| `p_pitch` | 47 | 30–70 | Higher = more responsive, oscillation risk |
| `i_pitch` | 84 | 60–120 | Hold under load; too high = bounce-back |
| `d_pitch` | 46 | 20–60 | Base D (straight-line damping) |
| `d_max_pitch` | 60 | ≥ d_pitch | Peak D during fast moves; set = d_pitch to disable dynamic D |
| `f_pitch` | 125 | 100–180 | Feedforward; reduces lag, no oscillation cost |
| `p_roll` | 45 | 30–70 | Often slightly lower than pitch |
| `i_roll` | 80 | 60–120 | |
| `d_roll` | 40 | 20–55 | Base D |
| `d_max_roll` | 55 | ≥ d_roll | Peak D; set = d_roll to disable |
| `f_roll` | 120 | 100–180 | |
| `p_yaw` | 45 | 30–80 | Yaw can take more P |
| `i_yaw` | 90 | 60–150 | |
| `d_yaw` | 0 | 0–15 | Usually 0; small D can help spin recovery |
| `d_max_yaw` | 0 | 0–15 | |
| `d_max_gain` | 37 | 20–60 | How aggressively D rises toward d_max on fast moves |
| `d_max_advance` | 20 | 0–200 | Look-ahead for d_max; set 0 during baseline tuning |
| `anti_gravity_gain` | 3.5 | 2–8 | I boost during fast throttle changes |
| `iterm_relax_cutoff` | 15 | 3–40 | Lower = more relax; see pid-tuning.md for per-build values |

⚠️ In 2025.12: `d_roll` = base D, `d_max_roll` = peak. Reversed vs 4.5.x — do not cross-paste PID diffs.

**Rule of thumb**: tune order: rates → feedforward → P → D → I.

## Gyro filters

| Parameter | Default | Range | Notes |
|-----------|---------|-------|-------|
| `gyro_lpf1_type` | `PT1` | `PT1` `PT2` `PT3` `BIQUAD` | PT2 for Bosch (BMI270) gyros; PT1 elsewhere |
| `gyro_lpf1_static_hz` | 0 | 0–1000 | 0 = disabled; use 0 when RPM filter is active |
| `gyro_lpf1_dyn_min_hz` | 250 | 150–400 | Dynamic LPF floor |
| `gyro_lpf1_dyn_max_hz` | 500 | 300–1000 | Dynamic LPF ceiling |
| `gyro_lpf1_dyn_expo` | 5 | 0–10 | Curve of the dynamic range |
| `gyro_lpf2_type` | `PT1` | `PT1` `PT2` `PT3` | Light anti-aliasing stage |
| `gyro_lpf2_static_hz` | 500 | 0–1000 | 0 = disabled |
| `gyro_notch1_hz` | 0 | 0–900 | Static notch 1 centre; 0 = disabled |
| `gyro_notch1_cutoff` | 0 | 0–900 | Static notch 1 bandwidth |
| `gyro_notch2_hz` | 0 | 0–900 | Static notch 2 centre |
| `gyro_notch2_cutoff` | 0 | 0–900 | Static notch 2 bandwidth |

⚠️ More filtering = more lag = worse handling. Don't over-filter to mask a mechanical problem.

## D-term filters

| Parameter | Default | Range | Notes |
|-----------|---------|-------|-------|
| `dterm_lpf1_type` | `PT1` | `PT1` `PT2` `PT3` `BIQUAD` | |
| `dterm_lpf1_static_hz` | 0 | 0–1000 | 0 = use dynamic |
| `dterm_lpf1_dyn_min_hz` | 75 | 50–150 | D-term filter floor |
| `dterm_lpf1_dyn_max_hz` | 150 | 100–300 | D-term filter ceiling |
| `dterm_lpf1_dyn_expo` | 5 | 0–10 | |
| `dterm_lpf2_type` | `PT1` | `PT1` `PT2` `PT3` | Second D-term filter stage |
| `dterm_lpf2_static_hz` | 150 | 0–500 | 0 = disabled |
| `dterm_notch_hz` | 0 | 0–900 | Static D-term notch centre |
| `dterm_notch_cutoff` | 0 | 0–900 | Static D-term notch bandwidth |
| `yaw_lowpass_hz` | 100 | 0–500 | Yaw-axis lowpass; 0 = disabled |

## Dynamic notch

Tracks noise peaks — complements the RPM filter for frame resonances.

| Parameter | Default | Range | Notes |
|-----------|---------|-------|-------|
| `dyn_notch_count` | 3 | 0–5 | 0 = disabled; 1 often enough with RPM filter |
| `dyn_notch_q` | 300 | 100–1000 | Width; lower = wider = more attenuation |
| `dyn_notch_min_hz` | 100 | 60–300 | Never set below 150 in practice; ≥200 recommended |
| `dyn_notch_max_hz` | 600 | 200–1000 | Ceiling for notch tracking |

For builds without bidirectional DSHOT: `count = 5`, `q = 250`.

## Rates

| Parameter | Default | Notes |
|-----------|---------|-------|
| `rates_type` | `ACTUAL` | `ACTUAL` `BETAFLIGHT` `RACEFLIGHT` `KISS` `QUICK` |
| `roll_rc_rate` | 7 | ACTUAL: center sensitivity (×10 °/s) |
| `roll_expo` | 0 | Curve (0–100) |
| `roll_srate` | 67 | ACTUAL: max rate above center (×10 °/s) |

Mirror for `pitch_*` and `yaw_*`. Default 2025.12: center sensitivity 70, max ~670 °/s.

## RC smoothing

| Parameter | Default | Notes |
|-----------|---------|-------|
| `rc_smoothing` | `ON` | Master switch |
| `rc_smoothing_auto_factor` | 30 | 0–50; lower = sharper (10–20 racing, 40–50 cinematic) |
| `rc_smoothing_setpoint_cutoff` | 0 | 0 = auto |
| `rc_smoothing_feedforward_cutoff` | 0 | 0 = auto |

`feedforward_smooth_factor` also affects smoothness — see pid-tuning.md for link-frequency-specific values.

## Motor & ESC

| Parameter | Default | Notes |
|-----------|---------|-------|
| `motor_pwm_protocol` | `DSHOT600` | DSHOT300 for BMI270 at 3.2 kHz; DSHOT600 for 8 kHz |
| `dshot_bidir` | `OFF` | Required for RPM filter; needs ESC firmware support |
| `dshot_burst` | `ON_UNLESS_CRASH_FLIP` | |
| `motor_poles` | 14 | Verify on your motor — critical for RPM filter accuracy |
| `motor_kv` | 1960 | Used by dynamic idle PID controller |
| `motor_idle` | 5.5 | Idle throttle % (replaces `idle_min_rpm` from 4.5.x) |
| `motor_output_limit` | 100 | Reduce to 80–90 for thermal limiting |
| `thrust_linear` | 0 | 20–40% typical; compensates non-linear thrust curve |
| `min_command` | 1000 | Minimum PWM output |
| `max_throttle` | 2000 | Maximum PWM output |

⚠️ Bidirectional DSHOT requires BLHeli_32 ≥16.7, AM32, or BlueJay.

## RPM filter

Requires `dshot_bidir = ON`. Tracks motor noise precisely.

| Parameter | Default | Notes |
|-----------|---------|-------|
| `rpm_filter_harmonics` | 3 | Number of motor harmonics to filter |
| `rpm_filter_weights` | `100,100,100` | Per-harmonic weight |
| `rpm_filter_q` | 500 | Notch width; higher = narrower |
| `rpm_filter_min_hz` | 100 | Harmonics below this are skipped |

## Failsafe

| Parameter | Default | Notes |
|-----------|---------|-------|
| `failsafe_procedure` | `DROP` | `DROP` `AUTO-LAND` `GPS-RESCUE` |
| `failsafe_delay` | 4 | Tenths of seconds before Stage 2 (4 = 0.4 s) |
| `failsafe_landing_time` | 10 | Tenths of seconds for auto-land before disarm (was `failsafe_off_delay` in 4.5.x) |
| `failsafe_throttle` | 1000 | Throttle value during auto-land / GPS rescue approach |
| `failsafe_recovery_delay` | 20 | Tenths of seconds of clean signal before recovery |
| `failsafe_throttle_low_delay` | 100 | Tenths of seconds at low throttle = disarm |
| `failsafe_stick_threshold` | 30 | Stick movement required for recovery |

**Never disable failsafe.** `GPS-RESCUE` only if GPS is properly configured and home point is set.

## OSD & telemetry

OSD element positions are packed coordinates — always set through Configurator GUI, not CLI:

```
set osd_rssi_pos = 2400   # Packed x/y coordinate — don't edit by hand
```

The `diff` output includes all non-default OSD positions. To reset OSD layout: `defaults nosave` on OSD lines only, or use the GUI reset button.
