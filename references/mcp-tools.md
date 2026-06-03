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
| `detect_rc_mapping(duration_s)` | Channel classification + TAER/AETR convention guess + ambiguous sticks | Passively detect RC channel mapping |
| `detect_rc_channel_move(baseline, duration_s, threshold)` | Channel with the largest delta from baseline | Identify one axis in the guided mapping protocol |
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

## RC mapping — radio channel detection

Trigger when the user asks to detect or verify RC channel mapping, FC plugged in and MCP server available.

The goal is to determine which physical channel the radio sends each function on (Aileron, Elevator, Rudder, Throttle), then compare against the `rcmap` configured in Betaflight to decide whether a correction is needed.

### Passive mode (starting point)

1. Announce the duration: _"I'll sample your radio for 30 seconds. Move all your sticks through their full range and toggle any switches you want to map."_
2. Call `detect_rc_mapping(duration_s=30)`.
3. Present the result:
   - Throttle confirmed (channel + rest value)
   - Switches detected (2 or 3 positions)
   - Guessed convention (`TAER` / `AETR` / unknown) and ambiguous sticks

If `convention_guess` is `null` **or** `sticks_ambiguous` has ≥ 2 entries → continue into guided mode.

### Guided mode (identification by function)

Ask by **radio function name** (Aileron, Elevator, Rudder), not by physical position — the user knows their controls, not their assumed location.

Sequence for each function in the order Aileron → Elevator → Rudder (Throttle is already known):

1. Call `get_rc` → store as `baseline`.
2. Announce: _"Move your **[Aileron / Elevator / Rudder]** control through its full range. You have 5 seconds."_
3. Call `detect_rc_channel_move(baseline=baseline, duration_s=5, threshold=300)`.
4. If `detected: false` → retry once. After 2 failures: _"No movement detected — check that your radio is transmitting (TX LED on, ELRS/CRSF link established)."_ and offer to restart.
5. If `detected: true` → record `{ "aileron": channel }` (or elevator/rudder depending on the step).
6. Move to the next function.

**Check**: the 4 channels (T/A/E/R) must be distinct. On a duplicate → flag it and offer to rerun.

### Comparison with the Betaflight rcmap

Once the 4 functions are identified, build the detected mapping string (e.g. channel 0=T, 1=A, 2=E, 3=R → `TAER`) and compare it with the current `rcmap` (read via `get_advanced_config` or the CLI `rcmap`).

| Situation | Action |
|-----------|--------|
| Detected mapping = current `rcmap` | ✅ No change needed |
| Detected mapping ≠ current `rcmap` | Propose `set rcmap = XXXX` + `save` |

Generate the CLI snippet only if there is a mismatch, and **only after explicit user confirmation** — `save` reboots the FC.

### Final result presentation

Show a summary table, then offer to correlate the switches with `get_modes` to identify which ones map to ARM, ANGLE, BEEPER, etc.:

```
Channel 0 — Throttle  (rest 1001 µs)        ← detected passively
Channel 1 — Aileron   (identified by move)  ← ch1 in rcmap
Channel 2 — Elevator  (identified by move)  ← ch2 in rcmap
Channel 3 — Rudder    (identified by move)  ← ch3 in rcmap
Channel 4 — aux1      (2-position switch)    ← presumed ARM
Channel 5 — aux2      (3-position switch)

Detected mapping : TAER
Current rcmap    : TAER  ✅ No correction needed
```

### UX rules

- Always capture a fresh `baseline` (`get_rc`) right before each guided step.
- Always announce the duration before calling the tool (`detect_rc_mapping` = 30 s, `detect_rc_channel_move` = 5 s).
- At most 2 attempts per function on `detected: false`, never an infinite loop.
- Never write to the FC without explicit confirmation — this flow is read-only up to the `rcmap` correction.

---

## Rendering analysis curves via a chart MCP server (AntV)

External web tools such as `blackbox.betaflight.com` cannot be driven by MCP. To **display**
the step-response and noise-spectrum curves inline (e.g. in claude.ai web, where
the scripts' matplotlib `--plot` cannot open a window), delegate the rendering to
a generic chart MCP server. Recommended: **AntV `mcp-server-chart`** (free, no API
key, posts data so it handles long FFT series).

Both analysis scripts emit a ready-to-send payload with `--chart`:

```bash
python -m scripts.spectral_analysis <log.bbl> --signal dterm --chart
python -m scripts.step_response  <log.bbl> --bandpass --active-only --chart
```

The payload wraps the AntV `generate_line_chart` arguments (one series per axis
via the `group` field), already downsampled:

```json
{
  "mcp_tool": "generate_line_chart",
  "arguments": {
    "title": "Noise spectrum (dterm) — log.bbl",
    "axisXTitle": "Frequency (Hz)",
    "axisYTitle": "PSD (dB)",
    "data": [ {"time": "47.0", "value": 12.3, "group": "roll"}, … ]
  }
}
```

Workflow: run the script with `--chart`, then call the chart MCP server's
`generate_line_chart` tool with the `arguments` object verbatim; show the
returned image/URL to the user. If no chart MCP is configured, fall back to the
text/JSON report or tell the user to open the log at https://blackbox.betaflight.com.
