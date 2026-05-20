# Betaflight `set` Parameters Reference

Most-used `set` parameters with safe ranges, defaults, and notes. Read this when the user asks about a specific parameter or you need to recommend safe values.

> **Target**: Betaflight 4.5.x. Parameter names changed across versions — see `version-changes.md`.

## Table of contents

1. [PID controller](#pid-controller)
2. [Filters](#filters)
3. [Rates](#rates)
4. [RC smoothing](#rc-smoothing)
5. [Motor & ESC](#motor--esc)
6. [Failsafe](#failsafe)
7. [Dynamic notch (RPM/gyro)](#dynamic-notch)
8. [OSD & telemetry](#osd--telemetry)

## PID controller

| Parameter | Default | Safe range | Notes |
|-----------|---------|------------|-------|
| `p_pitch` | 47 | 30-70 | Higher = more responsive, oscillation risk |
| `i_pitch` | 84 | 60-120 | Hold under load; too high = bounce-back |
| `d_pitch` | 46 | 20-60 | Damping; too high = hot motors |
| `f_pitch` | 125 | 100-180 | Feedforward; reduces lag, no oscillation cost |
| `p_roll` | 45 | 30-70 | Often slightly lower than pitch |
| `i_roll` | 80 | 60-120 |  |
| `d_roll` | 40 | 20-55 |  |
| `f_roll` | 120 | 100-180 |  |
| `p_yaw` | 45 | 30-80 | Yaw can take more P |
| `i_yaw` | 90 | 60-150 |  |
| `d_yaw` | 0 | 0-15 | Usually 0; small D can help spin recovery |

**Rule of thumb**: tune in this order: rates → feedforward → P → D → I.

## Filters

| Parameter | Default | Range | Notes |
|-----------|---------|-------|-------|
| `gyro_lowpass_hz` | 0 (off) | 0-300 | Use dynamic lowpass instead |
| `gyro_lowpass_dyn_min_hz` | 250 | 150-300 | Dynamic gyro lowpass floor |
| `gyro_lowpass_dyn_max_hz` | 500 | 400-700 | Dynamic gyro lowpass ceiling |
| `dterm_lowpass_dyn_min_hz` | 75 | 50-120 | D-term filter floor; lower = less noise, more lag |
| `dterm_lowpass_dyn_max_hz` | 150 | 120-250 | D-term filter ceiling |
| `dyn_notch_count` | 3 | 0-5 | Number of dynamic notches |
| `dyn_notch_q` | 300 | 200-500 | Notch width; lower = wider = more filtering |
| `dyn_notch_min_hz` | 100 | 60-150 | Floor for notch tracking |
| `dyn_notch_max_hz` | 600 | 500-900 | Ceiling for notch tracking |

⚠️ **More filtering = more lag = worse handling.** Don't over-filter to mask a mechanical problem (loose motors, bent props, prop balance).

## Rates

Rates control how stick deflection maps to rotation speed. Format depends on `rates_type`:

```
set rates_type = ACTUAL    # ACTUAL, BETAFLIGHT, RACEFLIGHT, KISS, QUICK
set roll_rc_rate = 7       # Center sensitivity (0-25 × 10 deg/s)
set roll_expo = 50         # Curve aggressiveness (0-100)
set roll_srate = 67        # Max rate (0-100, × 10 deg/s above rc_rate)
```

Mirror for `pitch_*` and `yaw_*`. Default 4.5 rates are intentionally tame — most freestyle pilots use higher.

## RC smoothing

```
set rc_smoothing = ON
set rc_smoothing_auto_factor = 30      # Lower = sharper, higher = smoother (0-50)
set rc_smoothing_setpoint_cutoff = 0   # 0 = auto
set rc_smoothing_feedforward_cutoff = 0  # 0 = auto
```

Default auto values are good for most pilots. Lower auto_factor (10-20) for racing, higher (40-50) for cinematic.

## Motor & ESC

| Parameter | Typical value | Notes |
|-----------|---------------|-------|
| `motor_pwm_protocol` | `DSHOT600` | DSHOT300/600/1200; bidirectional DSHOT for RPM filter |
| `dshot_bidir` | `ON` | Required for RPM filter & dynamic notch tracking |
| `motor_poles` | 14 | 12N14P motors (most common); check spec |
| `idle_min_rpm` | 25 | 20-30 typical; too low = desync risk |
| `motor_output_limit` | 100 | Reduce to 80-90 for thermal limit if needed |

⚠️ Bidirectional DSHOT requires ESC firmware support (BLHeli_32, AM32, BlueJay). Without RPM telemetry, dynamic notch quality degrades.

## Failsafe

```
set failsafe_procedure = DROP      # DROP | LAND | GPS_RESCUE
set failsafe_delay = 4             # Tenths of seconds before action (default 4 = 0.4s)
set failsafe_off_delay = 10        # How long motors stay off after drop (1s)
set failsafe_throttle_low_delay = 100  # Tenths of seconds at low throttle = disarm
```

**Never disable failsafe.** GPS_RESCUE only if GPS is properly configured and home is set.

## Dynamic notch

The dynamic notch filter tracks noise peaks. With RPM filter (`dshot_bidir = ON` + ESC firmware support), it complements rather than replaces RPM filtering.

```
set dyn_notch_count = 3
set dyn_notch_q = 300              # Width: lower=wider=more attenuation
set dyn_notch_min_hz = 100
set dyn_notch_max_hz = 600
```

For builds without bidirectional DSHOT: increase `count` to 5 and lower Q to 250.

## OSD & telemetry

OSD elements are configured by index. Most users set this through Configurator GUI, not CLI. The `diff` output includes lines like:

```
set osd_rssi_pos = 2400
```

Where the value is a packed coordinate. Don't edit by hand — use the GUI.
