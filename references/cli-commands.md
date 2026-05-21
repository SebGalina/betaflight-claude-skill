# Betaflight CLI Commands Reference

Reference for the Betaflight Command Line Interface. Read this file when the user asks about CLI syntax, what a specific command does, or how to inspect/modify configuration via CLI.

> **Target version**: Betaflight 2025.12. See `version-changes.md` for differences vs 4.5.x.

## Table of contents

1. [Inspection commands](#inspection-commands)
2. [Modification commands](#modification-commands)
3. [diff and dump syntax](#diff-and-dump-syntax)
4. [Profiles](#profiles)
5. [Resource & pin mapping](#resource--pin-mapping)
6. [Feature flags](#feature-flags)
7. [Serial ports](#serial-ports)
8. [Motor & ESC](#motor--esc)
9. [Mixer & servo](#mixer--servo)
10. [RC & receiver](#rc--receiver)
11. [Persistence & reboot](#persistence--reboot)
12. [Reset & defaults](#reset--defaults)
13. [Other commands](#other-commands)
14. [Common gotchas](#common-gotchas)

## Inspection commands

| Command | Purpose |
|---------|---------|
| `status` | Show FC status, sensors, uptime, CPU load |
| `version` | Firmware version, target, build date |
| `tasks` | Real-time task table with CPU usage |
| `get <name>` | Show value of a parameter (supports wildcards: `get pid_*`) |
| `dump` | Dump all non-default config as CLI commands |
| `dump all` | Dump full config including all profiles |
| `diff` | Show only differences from defaults (most useful for sharing) |
| `diff all` | Show diff across all profiles and rateprofiles |
| `resource show all` | Show all pin mappings |
| `dshot_telemetry_info` | Display DSHOT telemetry stats |
| `rc_smoothing_info` | Show RC smoothing computed settings |
| `gyroregisters` | Dump raw gyro config registers |
| `flash_info` | Show flash chip info |
| `vtx_info` | Dump VTX power configuration |
| `mcu_id` | Show microcontroller ID |
| `help [search]` | List available commands (optionally filtered) |

**The `diff all` output is the canonical sharable representation of a config.**

## Modification commands

```
set <name> = <value>
```

After any `set`, the change is in RAM only. Use `save` to persist and reboot.

To list all params matching a pattern: `get pid_` (no trailing wildcard needed).

### Batching changes

```
batch start
set foo = 1
set bar = 2
batch end
```

Use `batch` to group `set` commands; they are validated and applied atomically.

## diff and dump syntax

```
diff [master|profile|rates|hardware|all] {defaults|bare}
dump [master|profile|rates|hardware|all] {defaults|bare}
```

Scope:
- `master` – Global settings only
- `profile` – Active PID profile
- `rates` – Active rate profile
- `hardware` – Board/hardware config
- `all` – All scopes, all profiles

Flags:
- `defaults` – Include default values for comparison
- `bare` – Minimal output (no comments)

## Profiles

6 PID profiles (0–5) and 6 rate profiles (0–5):

```
profile [0-5]       # Switch PID profile
rateprofile [0-5]   # Switch rate profile
```

PIDs, filters, and TPA are scoped per PID profile. RC rates and expo are scoped per rate profile.

## Resource & pin mapping

```
resource MOTOR 1 A03          # Map motor 1 to pin A03
resource MOTOR 1 NONE         # Unmap motor 1
resource list                 # Show all available resources
resource show all             # Show current mappings
timer <pin> [af<n>|none]      # Timer assignment for a pin
dma <device> <index> [<option>|none]  # DMA channel assignment
```

⚠️ Pin changes require a reboot and can brick a board if wrong. Always document original mapping before changing.

## Feature flags

```
feature                   # List enabled features
feature MOTOR_STOP        # Enable a feature
feature -MOTOR_STOP       # Disable a feature (note the minus)
```

Common features: `RX_SERIAL`, `MOTOR_STOP`, `TELEMETRY`, `OSD`. `AIRMODE` is always on in 2025.12, no longer a toggle.

## Serial ports

```
serial <port_id> <function_mask> <msp_baud> <gps_baud> <telemetry_baud> <blackbox_baud>
```

Function masks (bitwise): MSP=1, GPS=2, RX_SERIAL=64, BLACKBOX=128, TELEMETRY_*=various.

Example for ELRS on UART2:
```
serial 1 64 115200 57600 0 115200
```

For serial passthrough (e.g. configuring a VTX or GPS):
```
serialpassthrough <id1> [<baud1>] [<mode1>] [none|<dtr pinio>|reset] [<id2>] [<baud2>] [<mode2>]
```

## Motor & ESC

```
motor <index> [<value>]   # Read or drive a motor (value 1000–2000); omit value to read
dshotprog <index> <cmd>   # Send DSHOT command to ESC
escprog <mode> <index>    # ESC passthrough (modes: sk/bl/ki/cc)
```

## Mixer & servo

```
mixer list                # List available mixers
mixer <name>              # Select a mixer
mmix                      # Define custom motor mix
servo                     # Configure servos
smix <rule> <servo> <source> <rate> <speed> <min> <max> <box>  # Custom servo mixer
smix reset                # Reset servo mixer rules
```

## RC & receiver

```
aux <index> <mode> <channel> <start> <end> <logic>  # AUX switch → mode mapping
map [<map>]               # Show/set RC channel order (e.g. AETR1234)
rxrange                   # Configure RX endpoint ranges
rxfail                    # Set per-channel failsafe values
adjrange <index> <unused> <range_ch> <start> <end> <function> <select_ch>  # In-flight adjustments
bind_rx                   # Initiate RX binding (SRXL2, CRSF, SPI)
```

## Persistence & reboot

| Command | Effect |
|---------|--------|
| `save` | Write config to flash, reboot |
| `save noreboot` | Write config without rebooting |
| `exit` | Discard RAM changes, reboot |
| `exit noreboot` | Discard RAM changes without rebooting |
| `bl [rom]` | Reboot to bootloader |
| `msc [<tz_offset>]` | Reboot to USB mass-storage mode (blackbox access) |

## Reset & defaults

```
defaults           # Wipe everything, reset to firmware defaults, reboot
defaults nosave    # Reset without rebooting
```

⚠️ `defaults` erases the user's entire tune. Always back up with `diff all` first.

## Other commands

```
simplified_tuning apply|disable   # Apply or remove simplified tuning presets
flash_erase                        # Erase flash (deletes all blackbox logs)
flash_scan                         # Scan flash for errors
gpspassthrough                     # GPS serial passthrough for u-center
led                                # LED strip configuration
color / mode_color                 # LED color and mode configuration
beacon list|<[-]name>              # DSHOT beacon configuration
beeper list|<[-]name>              # Beeper tone configuration
vtx <index> <aux_ch> <band> <ch> <power> <start> <end>  # VTX AUX-based control
vtxtable <band> <name> <letter> [FACTORY|CUSTOM] <freq...>  # Define VTX frequency table
play_sound [<index>]               # Play a beeper sound
board_name [<name>]                # Show/set board name
manufacturer_id [<id>]            # Show/set manufacturer ID
signature [<sig>]                  # Show/set firmware signature
```

## Common gotchas

- `set name = value` — spaces around `=` are required
- Parameters are scoped: PIDs/filters → `profile`, rates/expo → `rateprofile`, everything else → master
- **Never paste a `diff`/`dump` into a different firmware version** — parameter names and valid ranges change between releases and will silently corrupt the config
- **4.5 → 2025.12 D naming change**: in 4.5 `d_roll` = D_max and `d_min_roll` = base D; in 2025.12 `d_roll` = base D and `d_max_roll` = peak. Do not cross-paste PID diffs
- `failsafe_off_delay` was renamed `failsafe_landing_time` in 2025.12; GPS rescue variables were also redesigned
- After flashing a new major version, old `diff` dumps may contain deprecated parameters that throw errors — `parse_diff.py` flags these
