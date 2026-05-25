# Modes & Switches — Guided Assignment

## Purpose

Guide the user through assigning flight modes (ARM, BEEPER, ANGLE, etc.) to physical switches without requiring knowledge of AUX channel numbers, µs ranges, or mode IDs.

## Prerequisites

- FC connected, MCP server active
- RC channels already identified (run RC Mapping flow first if not done)

## Preparation

Call `get_modes` to retrieve the list of available modes and their current IDs. **Never hardcode mode IDs** — they can shift between firmware versions.

## Sequence (repeat for each mode)

1. Call `get_rc` → store as `baseline`.
2. Announce: _"Set your **[ARM / BEEPER / ANGLE / …]** switch to the **active** position. You have 5 seconds."_
3. Call `detect_rc_channel_move(baseline=baseline, duration_s=5, threshold=100)`.
4. If `detected: false` → retry once. After 2 failures: _"No movement detected — check that your radio is transmitting (TX LED on, ELRS/CRSF link established)."_ Offer to restart.
5. If `detected: true` → call `get_rc` to read the exact µs value on the detected channel.
6. Compute the range:
   - Value > 1500 µs → range `1700 2100`
   - Value ≤ 1500 µs → range `900 1300`
7. Compute AUX index: `aux_index = channel - 4` (channels 0–3 are T/A/E/R).
8. Record: `{ mode: "ARM", aux_index: N, range: "1700 2100" }`.

## Priority order

Assign in this order when doing a full switch setup:

1. **ARM** — minimum requirement to fly
2. **BEEPER** — highly recommended (locating a crashed quad)
3. **ANGLE / HORIZON** — if the user flies in assisted mode
4. Additional modes on request (PREARM, TURTLE, BLACKBOX, etc.)

## Generating the commands

After collecting all requested modes, display the full summary **before any write**:

```
# Mode assignment — review before applying
aux 0 <ARM_id>    0 1700 2100 0   # ARM    → AUX1, switch up
aux 1 <BEEPER_id> 1 1700 2100 0   # BEEPER → AUX2, switch up
```

Ask for explicit confirmation, then apply each line and end with `save_config`.

## Conflict check

Before writing, check `get_modes` output for existing assignments on the same AUX channel. Warn the user if a channel is already in use by another mode.

## Rules

- Always read `get_modes` first — do not assume ARM = 0 or BEEPER = 8.
- Always capture a fresh `baseline` (`get_rc`) immediately before each detection step.
- Never write without explicit user confirmation — `save_config` reboots the FC.
- Max 2 attempts per mode on `detected: false`, never loop indefinitely.
