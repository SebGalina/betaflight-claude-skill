# Betaflight Version Migration Notes

Differences between major Betaflight versions. Read this when the user is migrating, or when their config contains parameters from a different version than the target.

> **Currently shipping**: 4.5.x stable, 4.6.x in development/release candidate.

## Table of contents

1. [4.4 → 4.5](#44--45)
2. [4.5 → 4.6](#45--46)
3. [General migration workflow](#general-migration-workflow)
4. [Deprecated parameters reference](#deprecated-parameters-reference)

## 4.4 → 4.5

### Renamed parameters

| 4.4 name | 4.5 name | Notes |
|----------|----------|-------|
| `dyn_lpf_dterm_min_hz` | `dterm_lowpass_dyn_min_hz` | Same function |
| `dyn_lpf_dterm_max_hz` | `dterm_lowpass_dyn_max_hz` | Same function |
| `dyn_lpf_gyro_min_hz` | `gyro_lowpass_dyn_min_hz` | Same function |
| `dyn_lpf_gyro_max_hz` | `gyro_lowpass_dyn_max_hz` | Same function |
| `dyn_notch_width_percent` | (removed) | Replaced by `dyn_notch_q` |

### New in 4.5

- **Multi-dynamic-notch refactor** — `dyn_notch_count` and `dyn_notch_q` replace old width/range model
- **Cleaner feedforward pipeline** — `feedforward_*` params with smoother defaults
- **D_Min removed** — D is now flat, with dynamic damping via filters
- **TPA refactor** — `tpa_low_*` and `tpa_breakpoint` semantics tweaked

### Things that break

- Imports from 4.4 will throw `Invalid parameter` on the removed `dyn_notch_width_percent` and `d_min_*` params. The `parse_diff.py` script flags these.
- Old PID values are usable as a starting point but expect to retune slightly. Mostly the response is sharper out of the box in 4.5.

### Recommended workflow

1. Backup old config: `diff all` → save text file
2. Flash 4.5 with **Erase config** checked
3. Apply old config selectively (filters and resources first, then PIDs)
4. Tune from there — don't paste a 4.4 dump wholesale

## 4.5 → 4.6

### Renamed / removed parameters

| 4.5 | 4.6 | Notes |
|-----|-----|-------|
| `vbat_pid_gain` | (deprecated) | Compensation now automatic |
| `iterm_relax_type` | (kept, defaults changed) | Default now `RPY` instead of `RP` |

### New in 4.6

- **Async gyro/PID loop refactor** — better CPU usage on F4 targets
- **OpticalFlow position hold** (experimental) — for boards with sensor support
- **Improved RPM filter scheduling** — lower latency

### Things that break

- Custom OSD warnings may need re-positioning (new warning slots added)
- Targets dropped for some older F3 boards — verify target still exists before flashing

### Recommended workflow

Same as 4.4 → 4.5. Always backup, always erase, always tune fresh.

## General migration workflow

1. **`diff all` > config-vX.txt** — save the full current config
2. Note current versions:
   - Betaflight firmware
   - ESC firmware (BLHeli_S, BlueJay, AM32, BLHeli_32)
   - Configurator version
3. Flash new firmware **with Erase config**
4. Run the `parse_diff.py` script against the old dump to flag deprecated params
5. Replay the cleaned dump section by section:
   - First: `resource` mappings (board-specific, must match)
   - Then: `feature`, `serial`, RX setup
   - Then: PID profiles, rateprofiles
   - Skip: deprecated params flagged in step 4
6. Test bench (motors + props off): arming, motor direction, RX response
7. Test fly with default-ish PIDs before applying old tune
8. Re-tune as needed

## Deprecated parameters reference

The `scripts/validate_config.py` script checks for these. Common ones:

| Parameter | Removed in | Replaced by |
|-----------|-----------|-------------|
| `d_min_pitch` / `_roll` / `_yaw` | 4.5 | (gone — D is flat) |
| `d_min_boost_gain` | 4.5 | (gone) |
| `d_min_advance` | 4.5 | (gone) |
| `dyn_notch_width_percent` | 4.5 | `dyn_notch_q` |
| `dyn_notch_range` | 4.5 | `dyn_notch_min_hz` + `dyn_notch_max_hz` |
| `iterm_rotation` | 4.4 | (always on now) |
| `smith_predictor_*` | 4.4 | (gone, was experimental) |
| `airmode` (as feature) | 4.5 | (always on now) |
| `vbat_pid_gain` | 4.6 | (automatic) |

If a user's config still has these, **strip them before applying** — pasting them into a newer FW throws `Invalid name` errors that abort the rest of the CLI script.
