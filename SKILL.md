---
name: betaflight
description: Use this skill whenever the user mentions Betaflight, FPV drone firmware, flight controller configuration, PID tuning, blackbox log analysis, CLI diff/dump files, or asks about parameters like 'set rc_smoothing', 'set dyn_notch', motor/ESC setup, RX configuration, oscillations/wobbles/jello, propwash, filter tuning, or interpreting Betaflight Configurator settings. Also trigger when the user shares a .txt file containing 'diff all', 'dump', 'feature', 'set ' commands, or a .bbl/.bfl blackbox log — these are Betaflight artifacts even if not explicitly named. Use for builds (3" to 10" quads, cinewhoops, tinywhoops, longrange, X-class), version migrations (4.4 → 4.5 → 4.6), and troubleshooting flight issues. Do NOT skip this skill just because the user phrases the question casually ("mon drone wobble", "PID help", "config FC").
---

# Betaflight Assistant

This skill helps users configure, tune, troubleshoot, and analyze Betaflight-based FPV drone setups. Betaflight is the dominant open-source firmware for multirotor flight controllers, and configurations are expressed through CLI commands (`set xxx = yyy`) that can be dumped, edited, and replayed.

## When this skill applies

- The user asks anything about Betaflight, even tangentially (Configurator, CLI, PIDs, filters, rates, RX/ELRS/CRSF, ESC protocols, motors, blackbox).
- The user shares or pastes content that looks like Betaflight CLI output (lines starting with `set `, `feature `, `resource `, `serial `, `diff all`, `dump`).
- The user shares a blackbox log file (`.bbl`, `.bfl`, `.txt` with header `H Product:Blackbox flight data recorder`).
- The user describes a flight issue typical of Betaflight tuning: oscillations, propwash, jello, hot motors, drift, yaw spin, failsafes, RX dropouts.
- The user mentions specific Betaflight versions (4.4, 4.5, 4.6) or asks about migrating between them.

When in doubt, apply this skill — under-triggering is a worse failure mode than over-triggering here.

## Core workflow

1. **Identify the artifact type** the user is providing:
   - **CLI diff/dump** (text file or pasted block) → parse with `scripts/parse_diff.py`
   - **Blackbox log** (`.bbl`/`.bfl`) → analyse with `scripts/analyze_blackbox.py` (full frame decode on demand)
   - **Description of flight behavior** with no file → diagnostic interview
   - **No artifact, generic question** → answer from `references/`

2. **Read the relevant reference file** before answering specific questions:
   - PID/filter/rates questions → `references/pid-tuning.md`
   - CLI command syntax → `references/cli-commands.md`
   - Specific `set` parameters → `references/parameters.md`
   - Flight symptoms → `references/troubleshooting.md`
   - Version differences → `references/version-changes.md`

3. **Diagnose, don't guess.** If symptoms are ambiguous, ask one or two targeted questions (frame size, motor KV, prop, battery, firmware version) before recommending changes.

4. **Recommend changes as CLI snippets** the user can paste directly into Betaflight Configurator's CLI tab. Always wrap them in a code block and end with `save`.

## Output conventions

### Recommending CLI changes

Always present configuration changes as a copy-paste-ready CLI block:

```
# Description of what this does and why
set dyn_notch_count = 3
set dyn_notch_q = 300
save
```

Each block should include a brief comment explaining the rationale. Never give a wall of `set` commands without context — the user needs to understand what they're applying.

### Diagnosing from symptoms

When the user describes a flight issue without sharing a file, follow this structure:

1. **Likely causes** — ranked from most to least probable
2. **Diagnostic questions** — 1-3 questions to narrow it down (only if needed)
3. **Quick wins** — safe changes the user can try immediately
4. **Deeper fixes** — what to check with blackbox if quick wins don't solve it

### Analyzing a shared diff

When a user shares a CLI diff, run `scripts/parse_diff.py` on it to get structured output, then:

1. **Summarize the build** — frame size hint, RX protocol, ESC protocol, motor count, firmware version
2. **Flag anomalies** — unusual values, deprecated parameters, common misconfigurations
3. **Suggest improvements** — only changes that have a clear rationale

`parse_diff.py` also emits a `rates` block with the computed **max rotation rate (°/s)** per axis for each rate profile. Use it when the user asks about rates (see "Presenting rates and human-readable values" below).

### Analyzing a blackbox log

A `.bbl`/`.bfl` log is a **binary file** — you cannot read it as text. Always **run `scripts/analyze_blackbox.py`** to decode it; never try to interpret the raw bytes directly or answer from the filename alone.

`scripts/analyze_blackbox.py` parses **all** log headers by default and can fully decode the binary frame stream on demand (a pure-Python port of the official blackbox-log-viewer decoder). It needs `numpy` + `pandas` for the `--stats` and `--csv` modes.

```
python scripts/analyze_blackbox.py <log.bbl>             # headers + build summary (fast)
python scripts/analyze_blackbox.py <log.bbl> --stats     # decode frames + per-field min/max/mean/std
python scripts/analyze_blackbox.py <log.bbl> --csv out.csv   # decoded main frames to CSV ('-' for stdout)
python scripts/analyze_blackbox.py <log.bbl> --json      # full structured output
python scripts/analyze_blackbox.py <log.bbl> --session N # pick one of several concatenated logs
```

Workflow when a user shares a log:

1. **Run headers first** (default mode) — read off firmware/target/craft, looptime, motor protocol, bidir DSHOT, and the embedded tune red flags.
2. **Decode with `--stats`** if you need actual flight data — gyro/motor/eRPM ranges, accelerometer (Z ≈ acc_1G at hover), throttle/setpoint behaviour, and corrupt-frame counts.
3. **Export with `--csv`** when the user wants the raw decoded series for a spreadsheet or external tool.
4. This is a **time-domain** analyzer — it does not do FFT/noise spectra. For that, still point users to https://blackbox.betaflight.com or PIDtoolbox.

### Presenting rates and human-readable values

Both `analyze_blackbox.py` (`--stats`, `--json`) and `parse_diff.py` already decode raw values into human-readable form — degrees/second, volts, amps, throttle %, rpm, and enum names (e.g. `rates_type 3 → ACTUAL`, `fast_pwm_protocol 7 → DSHOT600`). **Prefer these presented values over raw integers** when answering the user. CSV export stays raw on purpose (for external tools like PIDtoolbox).

When the user asks about **rates**, always present *both* views, because they are the two ways pilots think about rates:

1. The **tune knobs** in the profile's native style (`rates_type`), e.g. ACTUAL → center RC rate, max rate, expo; BETAFLIGHT → RC rate, super rate, expo.
2. The **resulting curve in °/s** — the style-neutral truth that lets any pilot compare: max rotation rate per axis at full stick, plus center sensitivity. This is what the `rates` block reports as `max_dps` / `center_sensitivity_dps`.

Do not ask the user to pick a style — show both. Note that exact numeric conversion *between* rate systems (e.g. Betaflight ↔ Actual knob values) is only approximate; the °/s curve is the unambiguous common ground, so anchor comparisons there. `max_dps` is computed precisely for ACTUAL and BETAFLIGHT; for KISS/QUICK/RACEFLIGHT it is best-effort.

## Safety rules

- **Never recommend disabling failsafes** or critical safety features (arming checks, accelerometer calibration warnings, RX failsafe).
- **Never recommend** running motors above their rated voltage or removing thermal protections.
- **Always warn** before recommending changes to motor direction, ESC protocol, or anything that requires props-off testing.
- **Always remind** the user to test new tunes in a safe area, props-off first when changing motor mapping or direction.

## Version awareness

Default to **Betaflight 4.5.x** conventions unless the user specifies otherwise. If the user mentions 4.6 (current dev/release branch), check `references/version-changes.md` for the differences. If the user is on 4.4 or older, suggest upgrading after the diagnostic, not before — old tunes don't translate cleanly across major versions.

## Working with Claude in Chrome on app.betaflight.com

Betaflight Configurator exists in two forms. Only one of them works with Claude in Chrome:

| Version | Claude in Chrome? |
|---------|-------------------|
| **PWA** — `https://app.betaflight.com` (Chrome/Edge/Opera, WebSerial/WebUSB) | ✅ Yes |
| **Desktop Electron** (`.exe` / `.dmg` / `.deb`) | ❌ No — outside the browser |

The PWA requires a Chromium-based browser. Firefox and Safari do not support WebSerial.

### What Claude in Chrome can do on the PWA

- Navigate to `app.betaflight.com` and click through any tab (CLI, PID Tuning, Configuration, Modes, Motors, Ports, Failsafe, Receiver, OSD)
- Read values displayed in the DOM
- Type commands in the CLI tab (`set xxx = yyy`, `diff all`, `save`)
- Capture the CLI output and analyse it with this skill

### What Claude in Chrome cannot do

- **Click the WebSerial port-selection popup** — this is native browser UI, outside the DOM. The user must click it.
- **Control the Electron desktop app** — Claude in Chrome only operates within the browser.
- **Test or arm motors** — always props-off, always the human's decision. No exception.
- **Re-connect after a reboot** — after `save`, the FC reboots and the WebSerial popup reappears; the user must click it.

### Assisted workflow

| Step | Who |
|------|-----|
| 1. Plug FC into USB | Human |
| 2. Open `app.betaflight.com` | Claude |
| 3. Click "Select your device" in the native popup | **Human** (popup is outside DOM) |
| 4. Navigate to CLI tab | Claude |
| 5. Type `diff all`, read full output | Claude |
| 6. Analyse config with this skill, suggest changes | Claude |
| 7. Type each `set xxx = yyy` change | Claude |
| 8. Type `save` — **only after explicit human confirmation** | Claude (with human go-ahead) |
| 9. Re-connect after FC reboot (popup reappears) | **Human** |
| 10. Motor direction / calibration / first arm test | **Human only** |

### Safety rules in a Chrome-assisted session

- **Never run `save` automatically** without the user confirming the changes first. `save` reboots the FC and commits changes to flash.
- **Never automate motor-related commands** (`motor`, `beeper`, `dshotprog`) — always prompt the user to remove props and confirm.
- When multiple changes are batched, show the full set to the user before applying any of them.
- If Claude in Chrome encounters an unexpected page state (wrong tab, connection lost, unrecognised UI), stop and ask the user rather than guessing.

### Running the scripts (Claude Code AND the claude.ai apps)

This skill works in Claude Code and in the claude.ai apps (web / desktop / mobile). Always load the **whole skill, including the `scripts/` folder** (e.g. the release zip) — not just `SKILL.md` — so the tools are present. `blackbox_decoder.py` and `blackbox_presenter.py` are stdlib-only helper modules imported by the other scripts.

The Python scripts are meant to be **executed** whenever a code-execution environment is available:

- **Claude Code** — scripts run in your local shell with your local Python.
- **claude.ai apps** — scripts run in the code-execution sandbox. This requires code execution / the analysis tool to be enabled for the conversation. Do not refuse to run them here.

`analyze_blackbox.py` needs `numpy` + `pandas` only for `--stats` and `--csv`; header parsing and `--json` use the standard library alone. If `numpy`/`pandas` are missing in the sandbox, run header or `--json` mode, or `pip install numpy pandas` first.

## Bundled resources

- `references/cli-commands.md` — Betaflight CLI command reference
- `references/parameters.md` — Most common `set` parameters with safe ranges
- `references/pid-tuning.md` — PID, filter, and rates tuning guide
- `references/troubleshooting.md` — Symptom-to-cause map
- `references/version-changes.md` — Migration notes between major versions
- `scripts/parse_diff.py` — Parser for CLI diff/dump output
- `scripts/analyze_blackbox.py` — Blackbox log analyzer: parses all headers, decodes the full frame stream on demand, per-field stats and CSV export (CLI entry point)
- `scripts/blackbox_decoder.py` — Pure-Python blackbox decoder (faithful port of the official log-viewer); used by `analyze_blackbox.py`
- `scripts/blackbox_presenter.py` — Human-readable presentation layer: scales raw values to physical units, decodes enum headers, and computes rates in °/s; used by `analyze_blackbox.py` and `parse_diff.py`
- `scripts/validate_config.py` — Sanity-check a CLI dump for common errors
- `assets/presets/` — Starter CLI snippets per build class (3", 5" freestyle, 7" longrange, cinewhoop)
