# Arming Prevention Flags — Betaflight

Source: https://betaflight.com/docs/wiki/guides/current/Arming-Sequence-And-Safety

Since Betaflight 3.2, the arming prevention system exposes precise flags explaining why the FC refuses to arm. Visible in:
- CLI (`status`)
- OSD (displayed when arming fails)
- Buzzer beep pattern (see below)
- Betaflight Configurator (Status tab)

## Reading flags via CLI

```
status
```

The `Arming disable flags:` line lists all active flags. If absent or empty, the FC is ready to arm.

## Buzzer decoding

Pattern: **5 short beeps** (attention), then long beeps + spaced short beeps.

Formula: `code = (long_beeps × 5) + short_beeps`

Example: 1 long + 2 short = code 7 (`CRASH`)

## Flag table

| Flag | Code | Cause | Fix |
|------|------|-------|-----|
| `NOGYRO` | 1 | Gyroscope not detected at boot | Hardware fault or bad firmware — reflash, check solder joints |
| `FAILSAFE` | 2 | Failsafe currently executing | Wait for failsafe to exit, check RX signal |
| `RXLOSS` / `RX_FAILSAFE` | 3 | Receiver signal missing or invalid | Check radio link, binding, RX UART config |
| `BADRX` / `NOT_DISARMED` | 4 | RX in recovery AND arm switch already ON | Switch arm switch off before powering on |
| `BOXFAILSAFE` | 5 | Failsafe switch active on transmitter | Deactivate the failsafe switch |
| `RUNAWAY` | 6 | Runaway Takeoff Prevention triggered | Disarm, check PIDs and motor config |
| `CRASH` | 7 | Crash Recovery active | Disarm |
| `THROTTLE` | 8 | Throttle above `min_check` at arm time | Lower throttle below `min_check` (default ~1050 µs) |
| `ANGLE` | 9 | FC tilted beyond `small_angle` limit | Place craft flat — default 25°, configurable via `set small_angle` |
| `BOOTGRACE` | 10 | Arm attempted too soon after power-on | Wait `pwr_on_arm_grace` seconds (default 5 s) |
| `NOPREARM` | 11 | Prearm switch configured but not active | Activate prearm switch first |
| `LOAD` | 12 | CPU load too high | Disable features, reduce gyro/PID loop frequency |
| `CALIB` | 13 | Sensor calibration in progress | Wait for calibration to complete |
| `CLI` | 14 | CLI session open | Type `exit` in the CLI |
| `CMS` | 15 | OSD config menu (CMS) open | Exit the CMS menu |
| `OSD` | 16 | OSD menu active | Exit the OSD menu |
| `BST` | 16 | Black Sheep Telemetry disarmed | Refer to BST hardware documentation |
| `MSP` | 17 | Active MSP connection (Betaflight Configurator open) | Disconnect the Configurator |
| `PARALYZE` | 18 | Paralyze mode active (permanent disarm) | Reboot the FC |
| `GPS` | 19 | GPS Rescue configured but insufficient satellites | Wait for GPS fix or disable GPS Rescue |
| `RESCUE_SW` | 20 | GPS Rescue switch active before arming | Deactivate the GPS Rescue switch |
| `RPMFILTER` / `DSHOT_TELEM` | 21 | RPM filter enabled but DSHOT telemetry invalid | Check `dshot_bidir = ON`, BLHeli_32/AM32 firmware, motor wiring |
| `REBOOT_REQD` | 22 | Config change requires reboot | Reboot the FC (`reboot` in CLI or power cycle) |
| `DSHOT_BBANG` | 23 | DSHOT Bitbang failure | Timer conflict — switch to non-Bitbang protocol or check `resource` config |
| `NO_ACC_CAL` | 24 | Accelerometer never calibrated | Calibrate accelerometer (Configurator Setup tab) or disable dependent modes |
| `MOTOR_PROTO` | 25 | Motor/ESC protocol not selected | Choose a protocol (DSHOT300, DSHOT600…) in the Configuration tab |
| `ARMSWITCH` | 26 | Arm switch already in armed position at power-on | Always power on with arm switch in disarmed position |

## Most common flags and their gotchas

### `RXLOSS` (3)
Most frequent flag. Typical causes:
- Binding not done or lost
- Wrong UART config (wrong port number, baud rate)
- `serialrx_provider` does not match actual RX protocol (e.g. CRSF set but receiver is SBUS)
- Range exceeded outdoors

### `MSP` (17)
Betaflight Configurator holds an active MSP connection as long as it is open. **Disconnect the Configurator** (Disconnect button) before trying to arm. Flag clears immediately.

### `RPMFILTER` (21)
Requires all three:
1. `set dshot_bidir = ON`
2. ESC firmware supporting bidirectional telemetry (BLHeli_32 ≥ 32.7, AM32, Bluejay)
3. `motor_poles` correctly set (typically 14 for 2204–2307 motors)

### `ANGLE` (9)
FC refuses to arm if tilted more than 25° by default. Configurable:
```
set small_angle = 180   # disables the angle check (not recommended)
set small_angle = 25    # default
```

### `ARMSWITCH` (26)
Critical safety: if the arm switch is already in the armed position at power-on, the FC blocks arming. Always power on with the arm switch in the disarmed position.

### `DSHOT_BBANG` (23)
Bitbang mode uses DMA timers directly. Common conflicts with:
- LED strip on certain FCs
- Custom `resource` remapping
Fix: disable the LED strip, or switch to hardware-timer DSHOT if available on the FC.
