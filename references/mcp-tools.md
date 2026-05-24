# MCP Tools — Betaflight FC Live Connection

The Betaflight MCP server (`betaflight-mcp`) exposes MSP tools via FastMCP. When available, **always prefer live reads over asking the user to paste a diff** — it is more reliable and faster.

## Detecting MCP availability

Attempt a call to `list_serial_ports`. If it responds, the server is active. If it fails, fall back to offline mode (CLI diff) without blocking.

## Tool catalogue

### Connection (always first)

| Tool | When to use |
|------|-------------|
| `list_serial_ports` | List available ports before connecting |
| `connect(port, baudrate)` | Open MSP session — call once per conversation |
| `disconnect` | End of session or before switching to another FC |

Propose the detected port to the user; do not connect without their confirmation.

### Reading current state

Call these tools **before** proposing any change — never work blind:

| Tool | Data returned | Use case |
|------|--------------|----------|
| `get_board_info` | Firmware, variant, MCU, API version | Identify the FC, verify BF version |
| `get_fc_status` | Arming flags, cycle time, CPU load | Diagnose arming issues |
| `get_pid_values` | P/I/D per axis (roll, pitch, yaw, level) | Before any PID adjustment |
| `get_rates` | rc_rate, expo, superrate, throttle | Before any rates adjustment |
| `get_filter_config` | Gyro lowpass/notch, Dterm, RPM filter | Diagnose oscillations / noise |
| `get_pid_advanced` | Feedforward, anti-gravity, TPA, iterm relax, D-Max | Advanced tuning |
| `get_advanced_config` | ESC protocol (DSHOT), PID denominators, PWM rate | Verify DSHOT, looptime |
| `get_feature_config` | Active features (AIRMODE, GPS, LED…) | Verify feature config |
| `get_modes` | AUX switches and µs ranges | Diagnose RC modes |
| `get_sensor_config` | Accelerometer, baro, magnetometer | Sensor issues |

### Real-time telemetry

Use for live diagnostics only, not for configuration:

| Tool | Data returned | Use case |
|------|--------------|----------|
| `get_imu_data` | Accelerometer (g), gyro (°/s), magnetometer | Check vibrations on the bench |
| `get_attitude` | Roll, pitch, heading (°) | Verify artificial horizon |
| `get_battery` | Voltage (V), current (A), mAh, RSSI | Battery diagnostic |
| `get_battery_state` | Cells, capacity, state (OK/WARNING/CRITICAL) | Low battery alert |
| `get_rc` | RC channels (µs) | Verify radio reception |
| `snapshot_rc_delta(baseline, threshold)` | Channels that moved beyond threshold | Identify which switch/stick is active |
| `measure_rc_noise(duration_s, channels)` | 95th percentile noise + suggested deadband | Diagnose RC noise with sticks at rest |
| `get_motors` | Motor outputs (µs) | Check motors on the bench (props-off) |

### Writing — mandatory pattern

**Always follow this order, without exception:**

```
1. get_pid_values / get_rates / get_filter_config   ← read current state
2. Calculate new values
3. Present summary to user and ask for explicit confirmation
4. set_pid_values / set_rates                        ← apply after confirmation
5. save_config                                       ← write to EEPROM
```

Never chain `set_*` + `save_config` without an explicit confirmation step. `save_config` reboots the FC.

| Tool | Parameters | Constraints |
|------|-----------|-------------|
| `set_pid_values(axis, p, i, d)` | `axis`: roll/pitch/yaw/level — `p`,`i`,`d`: 0–255 | Check ranges in `references/parameters.md` |
| `set_rates(rc_rate, rc_expo, roll_rate, …)` | All optional — only provided values are updated | Same |
| `save_config` | None | Reboots the FC — warn the user |
| `reboot_fc` | None | Use only when explicitly requested |

## Error handling

- Tool returns `{"error": "..."}` → report to user, do not continue writing.
- `connect` fails → propose another port from `list_serial_ports`, or switch to offline.
- `set_pid_values` returns `{"errors": [...]}` → display validation errors, do not call `save_config`.
- MCP server unavailable → continue in offline mode (CLI diff) without blocking.
