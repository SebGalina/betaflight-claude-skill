# Betaflight CLI Commands Reference

Reference for the Betaflight Command Line Interface. Read this file when the user asks about CLI syntax, what a specific command does, or how to inspect/modify configuration via CLI.

> **Target version**: Betaflight 4.5.x. See `version-changes.md` for differences in 4.4 and 4.6.

## Table of contents

1. [Inspection commands](#inspection-commands)
2. [Modification commands](#modification-commands)
3. [Resource & pin mapping](#resource--pin-mapping)
4. [Feature flags](#feature-flags)
5. [Serial ports](#serial-ports)
6. [Persistence commands](#persistence-commands)
7. [Reset & defaults](#reset--defaults)

## Inspection commands

| Command | Purpose |
|---------|---------|
| `status` | Show FC status, sensors, uptime, CPU load |
| `version` | Firmware version, target, build date |
| `tasks` | Real-time task table with CPU usage |
| `get <name>` | Show value of a parameter (supports wildcards: `get pid_*`) |
| `dump` | Dump all non-default config as CLI commands |
| `dump all` | Dump full config including defaults |
| `diff` | Show only differences from defaults (most useful for sharing) |
| `diff all` | Show diff across all profiles and rateprofiles |
| `resource show all` | Show pin mappings |

**The `diff all` output is the canonical sharable representation of a config.**

## Modification commands

```
set <name> = <value>
```

After any `set`, the change is in RAM only. Use `save` to persist and reboot.

To list all params matching a pattern: `get pid_` (no trailing wildcard needed).

## Resource & pin mapping

```
resource MOTOR 1 A03      # Map motor 1 to pin A03
resource MOTOR 1 NONE     # Unmap motor 1
resource list             # Show all available resources
```

⚠️ Pin changes require a reboot and can brick a board if wrong. Always document original mapping before changing.

## Feature flags

```
feature                   # List enabled features
feature MOTOR_STOP        # Enable a feature
feature -MOTOR_STOP       # Disable a feature (note the minus)
```

Common features: `RX_SERIAL`, `MOTOR_STOP`, `TELEMETRY`, `OSD`, `AIRMODE` (deprecated as toggle in 4.5+, always on).

## Serial ports

```
serial <port_id> <function_mask> <msp_baud> <gps_baud> <telemetry_baud> <blackbox_baud>
```

Function masks (bitwise): MSP=1, GPS=2, RX_SERIAL=64, BLACKBOX=128, TELEMETRY_*=various.

Example for ELRS on UART2:
```
serial 1 64 115200 57600 0 115200
```

## Persistence commands

| Command | Effect |
|---------|--------|
| `save` | Write config to flash, reboot |
| `exit` | Discard changes, reboot |
| `defaults` | Reset to firmware defaults (DESTRUCTIVE) |
| `defaults nosave` | Show defaults without writing |

## Reset & defaults

```
defaults                  # Wipe everything, reset to firmware defaults
defaults show_unsupported # Include unsupported params
```

⚠️ `defaults` erases the user's entire tune. Always back up with `diff all` first.

## Common gotchas

- `set name = value` — the spaces around `=` are required
- Some parameters are profile-scoped (PIDs, filters) and others global. Use `profile 0|1|2` to switch.
- Rates are in their own `rateprofile 0|1|2|3|4|5` scope.
- After flashing a new major version, old `diff` dumps may contain deprecated parameters that throw errors — `parse_diff.py` flags these.
