---
name: betaflight
description: "Use whenever the user mentions Betaflight, FPV drone firmware, flight-controller config, PID/filter/rates tuning, or blackbox log analysis — or shares a Betaflight artifact: a CLI diff/dump (lines like 'diff all', 'set ', 'feature ', 'resource ') or a .bbl/.bfl blackbox log, even when not named as such — or runs a chirp tuning session via Sweep Studio / FPVLogForge (a chirp log ID to analyse, 'analyse log', 'résultat chirp', iterative Bode/step/Ms tuning). Covers flight symptoms (oscillations, wobble, propwash, jello, hot motors, drift), motor/ESC and RX/ELRS/CRSF setup, builds from 3\" to 10\" (cinewhoop, tinywhoop, longrange, X-class), version migrations (4.4 → 4.5 → 4.6), and configuring a new drone from scratch (setup wizard). Trigger even when phrased casually (\"mon drone wobble\", \"PID help\", \"config FC\") — under-triggering is worse than over-triggering."
---

# Betaflight Assistant

This skill helps users configure, tune, troubleshoot, and analyze Betaflight-based FPV drone setups. Betaflight is the dominant open-source firmware for multirotor flight controllers, and configurations are expressed through CLI commands (`set xxx = yyy`) that can be dumped, edited, and replayed.

## When this skill applies

- The user asks anything about Betaflight, even tangentially (Configurator, CLI, PIDs, filters, rates, RX/ELRS/CRSF, ESC protocols, motors, blackbox).
- The user shares or pastes content that looks like Betaflight CLI output (lines starting with `set `, `feature `, `resource `, `serial `, `diff all`, `dump`).
- The user shares a blackbox log file (`.bbl`, `.bfl`, `.txt` with header `H Product:Blackbox flight data recorder`).
- The user describes a flight issue typical of Betaflight tuning: oscillations, propwash, jello, hot motors, drift, yaw spin, failsafes, RX dropouts.
- The user mentions specific Betaflight versions (4.4, 4.5, 4.6) or asks about migrating between them.
- The user wants to configure a new drone from scratch → **launch the setup wizard** (see below).

When in doubt, apply this skill — under-triggering is a worse failure mode than over-triggering here.

> **Wizard hint** — mention to the user that they can say _"configure from scratch"_, _"nouveau drone"_, _"wizard"_, or _"partir de zéro"_ to launch the guided setup wizard.

## Core workflow

> **Script execution rule**: when the user shares a file, **always execute the relevant script** — never analyse the file manually or answer from memory alone. If the code-execution tool is unavailable, tell the user to enable it (Claude Desktop: conversation settings → Analysis tool) before proceeding.

1. **Identify the artifact type** the user is providing:
   - **FC plugged in + MCP server available** → **read live** via the MCP tools (see "MCP integration" below) — takes priority over everything else
   - **CLI diff/dump** (text file or pasted block) → **execute** `python -m scripts.parse_diff` on it
   - **Blackbox log** (`.bbl`/`.bfl`) → **execute** `python -m scripts.analyze_blackbox` on it (full frame decode on demand)
   - **Description of flight behavior** with no file → diagnostic interview
   - **No artifact, generic question** → answer from `references/`

2. **Read the relevant reference file** before answering specific questions:
   - PID/filter/rates questions → `references/pid-tuning.md` (fallback: `pid-tuning.md`)
   - CLI command syntax → `references/cli-commands.md` (fallback: `cli-commands.md`)
   - Specific `set` parameters → `references/parameters.md` (fallback: `parameters.md`)
   - Flight symptoms → `references/troubleshooting.md` (fallback: `troubleshooting.md`)
   - Arming failures / arming flags → `references/arming-flags.md` (fallback: `arming-flags.md`)
   - USB driver / MCU / COM port not found → `references/mcu-usb-drivers.md` (fallback: `mcu-usb-drivers.md`)
   - Version differences → `references/version-changes.md` (fallback: `version-changes.md`)

3. **Diagnose, don't guess.** If symptoms are ambiguous, ask one or two targeted questions (frame size, motor KV, prop, battery, firmware version) before recommending changes.

4. **Recommend changes as CLI snippets** the user can paste directly into Betaflight Configurator's CLI tab. Always wrap them in a code block and end with `save`.

## MCP integration — live FC

When the `betaflight-mcp` server is available, **always prefer a live read over asking for a diff**. Mandatory pattern: read → compute → confirm → write → `save_config`. Never call `save_config` without explicit confirmation — it reboots the FC.

Detection: try `list_serial_ports`; if it responds, the server is active. Otherwise fall back to offline mode (CLI diff) without blocking.

→ Full tool catalogue, write pattern, and error handling: `references/mcp-tools.md`

## RC mapping — radio channel detection

Trigger when the user asks to detect or verify RC channel mapping, FC plugged in and MCP server available.

Flow: passive sampling (`detect_rc_mapping`, 30 s) → if the convention stays ambiguous, guided identification by function name (Aileron → Elevator → Rudder via `detect_rc_channel_move`) → comparison with the Betaflight `rcmap` → a `set rcmap` correction proposed **only** when there is a mismatch and **after explicit confirmation** (`save` reboots the FC).

→ Full protocol (passive/guided modes, UX rules, result presentation, rcmap comparison): `references/mcp-tools.md`

## Modes / switches — guided assignment

Trigger on: _"positionne mon inter ARM"_, _"assigne le beeper"_, _"configure mes switches"_, or as a natural follow-up after the RC mapping flow.

Flow: read `get_modes` → ask the user to flip each switch to its active position → detect the channel and µs value → generate the `aux` commands → confirm → `save_config`. Recommended order: ARM first, BEEPER second.

→ Full sequence, range calculation, conflict handling: `references/modes-switches.md`

## Setup wizard (initial configuration)

Trigger **only** on an explicit request to configure from scratch. Typical phrases: "j'ai acheté un drone", "nouveau FC", "wizard", "configure from scratch", "partir de zéro", "premier vol". Do not trigger on one-off tuning or troubleshooting questions.

4-step flow: collect build info (in a single grouped question) → optional MCP connection → preset selection → apply via MCP or export a CLI diff.

**Preset selection**: always try `python -m scripts.fetch_presets --version {bf_version} --category tune --keywords {build_class}` first to offer up-to-date official presets. If the network is unavailable or no preset matches, fall back to `assets/presets/`.

→ Full flow, tables, and rules: `references/wizard.md`

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

When a user shares a CLI diff, run `python -m scripts.parse_diff` on it to get structured output, then:

1. **Summarize the build** — frame size hint, RX protocol, ESC protocol, motor count, firmware version
2. **Flag anomalies** — unusual values, deprecated parameters, common misconfigurations
3. **Suggest improvements** — only changes that have a clear rationale

`parse_diff.py` also emits a `rates` block with the computed **max rotation rate (°/s)** per axis for each rate profile. Use it when the user asks about rates (see "Presenting rates and human-readable values" below).

### Analyzing a blackbox log

A `.bbl`/`.bfl` log is a **binary file** — you cannot read it as text. Always **run `python -m scripts.analyze_blackbox`** to decode it; never try to interpret the raw bytes directly or answer from the filename alone.

`scripts/analyze_blackbox.py` parses **all** log headers by default and can fully decode the binary frame stream on demand (a pure-Python port of the official blackbox-log-viewer decoder). It needs `numpy` + `pandas` for the `--stats` and `--csv` modes.

```
python -m scripts.analyze_blackbox <log.bbl>             # headers + build summary (fast)
python -m scripts.analyze_blackbox <log.bbl> --stats     # decode frames + per-field min/max/mean/std
python -m scripts.analyze_blackbox <log.bbl> --csv out.csv   # decoded main frames to CSV ('-' for stdout)
python -m scripts.analyze_blackbox <log.bbl> --json      # full structured output
python -m scripts.analyze_blackbox <log.bbl> --session N # pick one of several concatenated logs
```

Workflow when a user shares a log:

1. **Run headers first** (default mode) — read off firmware/target/craft, looptime, motor protocol, bidir DSHOT, and the embedded tune red flags.
2. **Decode with `--stats`** if you need actual flight data — gyro/motor/eRPM ranges, accelerometer (Z ≈ acc_1G at hover), throttle/setpoint behaviour, and corrupt-frame counts.
3. **Export with `--csv`** when the user wants the raw decoded series for a spreadsheet or external tool.
4. `analyze_blackbox.py` itself is a **time-domain** analyzer. For **FFT / noise spectra**, run `python -m scripts.spectral_analysis <log.bbl>` (see below); for anything it doesn't cover, point users to https://blackbox.betaflight.com.
5. For **step response analysis**, use `python -m scripts.step_response <log.bbl> --bandpass --active-only` (recommended flags). Closed-loop identification: rise time, overshoot, settling time, coherence. Low coherence on freestyle/racing logs is normal — reliable results need a dedicated identification flight.
6. For **noise / FFT analysis**, use `python -m scripts.spectral_analysis <log.bbl>` (gyro) or `--signal dterm` (the D-term is the main noise path to the ESCs). It computes the Welch PSD per axis, auto-extracts frequency peaks, groups them into **harmonic series**, and diagnoses the source: a clean f0 + 2f0 + 3f0 family → **motor noise** (RPM filter); an isolated narrow peak → **frame resonance** (dynamic notch); a raised featureless floor → **broadband** (low-pass, never a notch). `--plot` adds a spectrogram.
7. For **closed-loop frequency response (Bode) + a full guided tuning report**, when the log was recorded with the chirp generator (`debug_mode = CHIRP`), run `python -m scripts.chirp_analysis <log.bbl> --html report.html`. Pass several logs for a before/after overlay; `--lang en` for English, `--json` for machine output, `--history`/`--no-history` for the accumulated `chirp_history.json`. The self-contained bilingual HTML covers per-axis Bode + step response, gyro-noise PSD (raw vs filtered, motor-harmonic bands), spectrogram, throttle × frequency map, multi-pass overlay, and data-driven observations. Confirm chirp support first with `dump | grep chirp` (the `USE_CHIRP` build feature). **Never enable CHIRP on the ground** (bug betaflight/betaflight#15012). Full details — debug-mapping auto-detection, FRF method, coherence gate, phase margin, report panels: `references/chirp-tuning.md`.

→ Full spectral_analysis invocation: `scripts/spectral_analysis.py --help`
→ Full step_response invocation, signal-quality flags, and the coherence warning to relay to users: `references/pid-tuning.md`

## Guided chirp tuning session (Sweep Studio)

Trigger when the user provides a **Sweep Studio log ID** — phrases like _"analyse log ABC123"_, _"nouveau vol chirp"_, _"résultat : XYZ"_, or any alphanumeric ID associated with a Sweep Studio / FPVLogForge chirp run.

1. Call `mcp__claude_ai_sweep-studio__analyze_chirp(id=<id>)` to retrieve the structured résumé.
2. Follow the iterative session protocol (turn headers, phase 1–4, decision rules, output format): → `references/chirp-session.md`
3. For metric definitions and thresholds (Ms, ϕm, Mt, grade bands, etc.): → `references/chirp_metrics_reference.md`

**Connector required**: `analyze_chirp` is served by the **Sweep Studio MCP connector** (enabled via claude.ai connectors or the Claude Code MCP config) — tool `mcp__claude_ai_sweep-studio__analyze_chirp`. If the tool is **absent**, the connector is not enabled: tell the user to add it, **or** fall back to a local analysis of the raw `.bbl` with `python -m scripts.chirp_analysis <log.bbl> --html report.html` (no connector or remote ID needed — see "Analyzing a blackbox log" step 7).

Session is turn-based: analyze → prescribe → fly → repeat. ≤ 3 parameter changes per turn. Sign-off at grade ≥ A or stable B+ with no reds.

---

### Presenting chirp analysis results

When you receive a chirp analysis result (the `chirp_analysis.py` report or the `analyze_chirp` MCP summary), present it like this:

1. **Lead with the indicator values, not the score.** Open on the measured numbers per axis — sensitivity peak Ms, step overshoot / rise / settling, HF noise margin, resonance peaks. The composite **score is a second-plane footnote**, not the headline.

2. **Sensitivity peak Ms is the primary stability indicator — prefer it.** It is the most reliable single number. Quote `ms` (and the fragile frequency `f_ms_hz`) and **explain it briefly when you use it**: Ms = max|S(f)|, how much the loop amplifies disturbances at its weakest point. Thresholds from the report: **Ms ≲ 1.5 comfortable/damped, ~2 marginal, >2 it rings** (step overshoot climbs, propwash sets in).

3. **Avoid the phase margin as much as possible.** Especially when the **low end of the Bode is noisy** (low coherence / jagged curve near the 0 dB crossover) — the scalar margin there is a likely **false positive**. If a margin must be cited, use the **guaranteed margin from Ms** (`pm_guaranteed_deg`), not the 0 dB-crossover number. Do not build a verdict on the raw phase margin.

4. **Frame the score as relative, never absolute.** State plainly that the composite 0–100 note only has meaning **within the same tuning exercise** (this craft, comparable flights) and is "to read with the plots, not in place of them — a number is no substitute for the stick feel." It is most **telling across multiple passes**, where the delta vs the previous pass shows whether the change helped.

5. **Multi-pass: recall the parameters and highlight what moved.** When several passes exist, show a table of the tuning parameters with the value at each pass and flag the ones that **changed since the last pass**, so the indicator delta is read against the actual change:

   | Param | Pass N-1 | Pass N | Δ |
   |-------|----------|--------|---|
   | `p_pitch` | 47 | 52 | **+5** |
   | `d_pitch` | 38 | 38 | — |

   Pair it with the per-pass indicators (Ms, f_Ms, relative score) so cause (param change) and effect (indicator move) sit side by side. For passes to be comparable, remind the user to enable `vbat_sag_compensation` and/or fly at a similar battery level.

→ Indicator definitions and thresholds (Ms, phase margin caveat, scoring): `references/chirp-tuning.md`

### Presenting rates and human-readable values

Both `analyze_blackbox.py` (`--stats`, `--json`) and `parse_diff.py` already decode raw values into human-readable form — degrees/second, volts, amps, throttle %, rpm, and enum names (e.g. `rates_type 3 → ACTUAL`, `fast_pwm_protocol 7 → DSHOT600`). **Prefer these presented values over raw integers** when answering the user. CSV export stays raw on purpose (for external analysis tools).

When the user asks about **rates**, always present *both* views, because they are the two ways pilots think about rates:

1. The **tune knobs** in the profile's native style (`rates_type`), e.g. ACTUAL → center RC rate, max rate, expo; BETAFLIGHT → RC rate, super rate, expo.
2. The **resulting curve in °/s** — the style-neutral truth that lets any pilot compare: max rotation rate per axis at full stick, plus center sensitivity. This is what the `rates` block reports as `max_dps` / `center_sensitivity_dps`.

Do not ask the user to pick a style — show both. Note that exact numeric conversion *between* rate systems (e.g. Betaflight ↔ Actual knob values) is only approximate; the °/s curve is the unambiguous common ground, so anchor comparisons there. `max_dps` is computed precisely for ACTUAL and BETAFLIGHT; for KISS/QUICK/RACEFLIGHT it is best-effort.

## Safety rules

- **Never recommend disabling failsafes** or critical safety features (arming checks, accelerometer calibration warnings, RX failsafe).
- **Never recommend** running motors above their rated voltage or removing thermal protections.
- **Always warn** before recommending changes to motor direction, ESC protocol, or anything that requires props-off testing.
- **Always remind** the user to test new tunes in a safe area, props-off first when changing motor mapping or direction.
- **Never suggest a parameter value outside the Safe range documented in `references/parameters.md`** without (a) explicitly flagging it as out-of-range, (b) stating the documented limit, and (c) requiring the user to confirm they want to exceed it. If `references/parameters.md` has no Safe range for a parameter, derive bounds from the official Betaflight source range only — never invent bounds.

## Version awareness

Default to **Betaflight 2025.12** conventions unless the user specifies otherwise. Check `references/version-changes.md` (fallback: `version-changes.md`) for differences with 4.5.x and 4.4.x. If the user is on 4.4 or older, suggest upgrading after the diagnostic, not before — old tunes don't translate cleanly across major versions.

## Working with Claude in Chrome on app.betaflight.com

Betaflight Configurator exists in two forms. Only one of them works with Claude in Chrome:

| Version | Claude in Chrome? |
|---------|-------------------|
| **PWA** — `https://app.betaflight.com` (Chrome/Edge/Opera, WebSerial/WebUSB) | ✅ Yes |
| **Desktop Electron** (`.exe` / `.dmg` / `.deb`) | ❌ No — outside the browser |

The PWA requires a Chromium-based browser. Firefox and Safari do not support WebSerial.

### What Claude in Chrome can do on the PWA

- Navigate to `app.betaflight.com` and click through any tab (CLI, PID Tuning, Configuration, Modes, Motors, Ports, Failsafe, Receiver, OSD) — use `references/configuration.md` (fallback: `configuration.md`) to know which tab does what and its documentation URL
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

This skill works in Claude Code and in the claude.ai apps (web / desktop / mobile). Always load the **whole skill, including the `scripts/` folder** (e.g. the release zip) — not just `SKILL.md` — so the tools are present. The blackbox/chirp compute lives in the `betaflight_chirp_core` package, **vendored** into `scripts/betaflight_chirp_core/` of the release zip by `build_skill_zip.py` (the analysis scripts are thin CLI wrappers over it); `blackbox_presenter.py` is a stdlib-only helper module imported by `analyze_blackbox.py` and `parse_diff.py`.

The Python scripts are meant to be **executed** whenever a code-execution environment is available:

- **Claude Code** — scripts run in your local shell with your local Python.
- **claude.ai apps** — scripts run in the code-execution sandbox. This requires code execution / the analysis tool to be enabled for the conversation. Do not refuse to run them here.

Always invoke scripts from the skill root using the module form: `python -m scripts.analyze_blackbox`. If that fails (e.g. `scripts` not found as a package), fall back to `cd scripts && python analyze_blackbox.py` then return to the previous directory.

`analyze_blackbox.py` needs `numpy` + `pandas` only for `--stats` and `--csv`; header parsing and `--json` use the standard library alone. If `numpy`/`pandas` are missing in the sandbox, run header or `--json` mode, or `pip install numpy pandas` first.

## Bundled resources

- `references/chirp-session.md` — Guided chirp tuning session protocol: turn-based workflow (phase 1–4), decision rules table, output format templates, hard constraints
- `references/chirp_metrics_reference.md` — Metric definitions and thresholds for chirp analysis: Ms, ϕm, Mt, grade bands, Bode fields
- `references/arming-flags.md` — All arming prevention flags: codes, causes, fixes, common pitfalls
- `references/mcu-usb-drivers.md` — Common MCUs (STM32, AT32, APM32, GD32), VCP and DFU drivers per OS, Zadig, ImpulseRC Driver Fixer, COM-port-not-found diagnostics
- `references/mcp-tools.md` — Full MCP tool catalogue, write pattern, error handling, RC mapping protocol
- `references/wizard.md` — Detailed initial-configuration wizard flow
- `references/cli-commands.md` — Betaflight CLI command reference
- `references/parameters.md` — Most common `set` parameters with safe ranges
- `references/pid-tuning.md` — PID, filter, and rates tuning guide
- `references/troubleshooting.md` — Symptom-to-cause map
- `references/configuration.md` — Configurator tab navigation: what each tab does, all documentation URLs
- `references/version-changes.md` — Migration notes between major versions
- `scripts/parse_diff.py` — Parser for CLI diff/dump output
- `scripts/betaflight_chirp_core/` — Vendored compute core (pinned **0.1.7**, from PyPI), the single source of truth shared with the FPVLogForge backend: pure-Python `.bbl` decoder (faithful port of the official log-viewer), signal helpers, chirp FRF/Bode + step + spectral analysis, and the self-contained bilingual HTML report renderer (`report_assets/`). The analysis scripts below are thin CLI wrappers over it.
- `scripts/analyze_blackbox.py` — Blackbox log analyzer CLI (thin wrapper over `betaflight_chirp_core`): parses all headers, decodes the full frame stream on demand, per-field stats and CSV export
- `scripts/blackbox_presenter.py` — Human-readable presentation layer: scales raw values to physical units, decodes enum headers, and computes rates in °/s; used by `analyze_blackbox.py` and `parse_diff.py`
- `scripts/validate_config.py` — Sanity-check a CLI dump for common errors
- `scripts/step_response.py` — Closed-loop step response analyser CLI (thin wrapper over `betaflight_chirp_core`): setpoint → gyro via Welch's cross-spectral method; rise time, overshoot, settling time, delay, per-axis diagnosis; `--plot` renders an inline matplotlib figure (step response + coherence curves)
- `scripts/spectral_analysis.py` — Noise spectrum / FFT analyser CLI (thin wrapper over `betaflight_chirp_core`) (gyro or D-term): Welch PSD per axis, automatic peak extraction, harmonic-series grouping (motor noise vs frame resonance vs broadband), source diagnosis; `--plot` renders PSD + spectrogram
- `scripts/chirp_analysis.py` — Closed-loop chirp frequency-response analyser CLI (thin wrapper over `betaflight_chirp_core`) + guided tuning report (`debug_mode = CHIRP`): per-axis Bode (gain dB / phase deg / coherence, diagnosis gated > 0.8) via Welch cross-spectral FRF, auto-detecting the firmware debug mapping (reconstructs from `debug[0]` on current BF; legacy `debug[1..3]` fallback). The `--html` report is a self-contained, bilingual (FR/EN) guided assistant (Filtering → PID → History) with step response, gyro noise PSD (raw vs filtered, eRPM motor-harmonic bands, filter cut-offs, disable candidates), chirp spectrogram, throttle × freq map, multi-pass overlay + settings-comparison table, and deterministic data-driven observations; phase margin carries an uncertainty. `--json` machine-readable; `--history`/`--no-history`/`--lang`. See `references/chirp-tuning.md`
- `assets/presets/` — Starter CLI snippets per build class (3", 5" freestyle, 7" longrange, cinewhoop)
