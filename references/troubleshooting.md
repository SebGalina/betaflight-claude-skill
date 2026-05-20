# Betaflight Troubleshooting Guide

Symptom-to-cause map for diagnosing Betaflight builds. Use this when the user describes a problem without sharing a config or log.

## How to use this guide

1. Find the symptom category below.
2. Walk through the ranked causes (most → least likely).
3. Ask the user 1-2 diagnostic questions if needed.
4. Recommend the safest fix first.

## Table of contents

1. [Won't arm](#wont-arm)
2. [Arms but won't take off / motors don't spin](#arms-but-wont-take-off)
3. [Flips on takeoff](#flips-on-takeoff)
4. [Oscillations / wobbles](#oscillations--wobbles)
5. [Drift / poor angle hold](#drift--poor-angle-hold)
6. [Hot motors / hot ESCs](#hot-motors--hot-escs)
7. [Yaw issues](#yaw-issues)
8. [RX dropouts / failsafe](#rx-dropouts--failsafe)
9. [Video issues (jello, lines)](#video-issues)
10. [Won't flash / brick recovery](#wont-flash--brick-recovery)

## Won't arm

**Diagnostic questions**: What does Configurator show in the Setup tab? Any red bars under "Arming Disable Flags"?

| Cause | Fix |
|-------|-----|
| `MSP` flag | Configurator is connected; disconnect to arm |
| `RXLOSS` | RX not detected — check serial port assignment, RX binding |
| `THROTTLE` | Throttle not at minimum — check stick calibration |
| `ANGLE` | Craft not level — re-level on flat surface, `accelerometer trim` |
| `BOOTGRACE` | Wait 5 seconds after powerup |
| `NOPREARM` | Prearm switch not enabled — assign in Modes tab |
| `BARO`/`GPS` | Sensor required by failsafe mode but not present/calibrated |
| `MOTOR_PROTOCOL` | DSHOT misconfigured — check ESC supports the selected protocol |
| `ARM_SWITCH` | Arm switch was on at power-up — toggle off, then on |

## Arms but won't take off

**Diagnostic questions**: Do all motors spin? Do they spin the right direction? Are props on correctly?

- **No motors spin**: ESC not getting signal. Check `motor_pwm_protocol`, ESC firmware, motor wiring continuity.
- **Some motors spin, others don't**: Bad solder joint, dead ESC, or wrong motor mapping (`resource MOTOR x`).
- **All motors spin, no lift**: Props upside down, props on wrong motors (motor 1 should spin opposite of motor 2, etc.).
- **Motor desync (RPM glitches at full throttle)**: Lower `motor_pwm_protocol` to DSHOT300, raise `idle_min_rpm` to 30.

## Flips on takeoff

Almost always one of:

1. **Wrong motor direction** — verify each motor spins the correct direction (Configurator → Motors tab). Reverse with DSHOT command or by flipping two of three motor wires.
2. **Wrong motor mapping** — motor 1 in software ≠ motor 1 in firmware order. Use Motors tab to identify and remap with `resource MOTOR x`.
3. **Props upside down** — CW prop on CCW motor (or vice versa).
4. **Yaw axis P/D way too high** — rare, but possible after a bad import.

**Always test props-off first** when changing motor mapping or direction.

## Oscillations / wobbles

**Diagnostic questions**: High-frequency shake or low-frequency wobble? When does it happen — hover, hard cornering, descent?

| When | Frequency | Likely cause |
|------|-----------|--------------|
| All the time, hover | Low (slow wobble) | P too low, or loose motors/props |
| Hard cornering | High (rapid shake) | P too high, or filters too open |
| Descent (propwash) | Mid (chop chop chop) | D too low, or D-term filter too tight |
| After a flip stops | Bounce-back | I too high |
| Random twitches | Any | RC noise → check `rc_smoothing`, RX signal |

See `pid-tuning.md` for fixes.

## Drift / poor angle hold

- **Acro mode drift**: I too low. Bump `i_pitch`/`i_roll` by 10-20.
- **Angle mode drift**: Accelerometer calibration. Re-level on flat surface, "Calibrate Accelerometer" in Setup tab.
- **Drift only at high throttle**: Vibration is desensitizing the accelerometer — improve mechanical build.
- **Drift only in wind**: Normal, that's wind. Bump I if it's excessive.

## Hot motors / hot ESCs

Touch motors immediately after a 3-minute flight:
- **Warm** (~40-50°C): normal
- **Hot to touch** (~60-70°C): tuning issue
- **Painful to touch** (>80°C): immediate problem, ground the craft

Causes (ranked):

1. **D too high** — lower by 5-10 increments
2. **D-term filter too open** — lower `dterm_lowpass_dyn_max_hz` by 50
3. **Mechanical vibration** — driving filters to overcompensate; check props, motors, frame
4. **Motors undersized for build weight** — measure thrust-to-weight, should be >4:1 for freestyle
5. **ESC firmware outdated** — BLHeli_S → BlueJay, AM32 fixes many efficiency issues

## Yaw issues

- **Yaw spin-out on punchout**: `i_yaw` too low. Bump by 20-30.
- **Slow yaw response**: `p_yaw` too low or yaw rate too low. Check rates.
- **Yaw bounces back when stopping a turn**: `i_yaw` too high. Lower by 20.
- **Drone yaws when throttling up** ("torque steer"): Motor mount or prop balance issue, sometimes also unequal motor responsiveness.

## RX dropouts / failsafe

**Diagnostic questions**: What RX protocol (ELRS, CRSF, SBUS, FrSky)? Antenna routing? Distance when dropouts happen?

- **ELRS specific**: Check packet rate, switch mode, telemetry ratio. 250Hz+ is overkill for most use — drop to 100Hz for better range.
- **Antenna near carbon**: Carbon absorbs RF. Antennas must protrude away from frame.
- **`serial` port misconfigured**: Verify `serial X 64 ...` (function mask 64 = RX_SERIAL) on the correct UART.
- **Brownout (voltage sag on heavy current)**: Add or replace cap on power leads.

## Video issues

- **Jello in DVR but smooth in goggles**: Soft camera mount, or camera angle on gyro plane — mechanical, not tune.
- **Jello in both**: True vibration. Balance props, check motor bell tightness, look for bent shafts.
- **Lines / static**: VTX power supply issue, often a noisy 5V rail. Move VTX to dedicated regulator.
- **Image cuts out at high throttle**: VTX brownout, undersized cap.

## Won't flash / brick recovery

- **DFU mode not detected**: Hold BOOT button while plugging in USB. Try a different USB cable (data cable, not power-only).
- **Zadig driver issue (Windows)**: Install/reinstall STM32 Bootloader driver via Zadig.
- **Bricked after flash**: Boot to DFU manually (BOOT pad shorted to 3.3V on most boards), flash via Configurator.
- **No CLI access**: Try the impuRC mode or recovery via Configurator's "Manual baudrate" option.

Before flashing, **always**:
1. Back up with `diff all` (save to file)
2. Use the correct target (board-specific, not just "STM32F411")
3. Erase config when flashing across major versions
