---
name: betaflight
description: Use this skill whenever the user mentions Betaflight, FPV drone firmware, flight controller configuration, PID tuning, blackbox log analysis, CLI diff/dump files, or asks about parameters like 'set rc_smoothing', 'set dyn_notch', motor/ESC setup, RX configuration, oscillations/wobbles/jello, propwash, filter tuning, or interpreting Betaflight Configurator settings. Also trigger when the user shares a .txt file containing 'diff all', 'dump', 'feature', 'set ' commands, or a .bbl/.bfl blackbox log — these are Betaflight artifacts even if not explicitly named. Use for builds (3" to 10" quads, cinewhoops, tinywhoops, longrange, X-class), version migrations (4.4 → 4.5 → 4.6), and troubleshooting flight issues. Do NOT skip this skill just because the user phrases the question casually ("mon drone wobble", "PID help", "config FC"). To configure a new drone from scratch or generate a baseline tune, use the setup wizard: say "configure from scratch", "nouveau drone", "wizard", or "partir de zéro".
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
   - **FC branché + serveur MCP disponible** → **lire en direct** via les tools MCP (voir section "Intégration MCP" ci-dessous) — prioritaire sur tout le reste
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
   - Driver USB / MCU / port COM introuvable → `references/mcu-usb-drivers.md` (fallback: `mcu-usb-drivers.md`)
   - Version differences → `references/version-changes.md` (fallback: `version-changes.md`)

3. **Diagnose, don't guess.** If symptoms are ambiguous, ask one or two targeted questions (frame size, motor KV, prop, battery, firmware version) before recommending changes.

4. **Recommend changes as CLI snippets** the user can paste directly into Betaflight Configurator's CLI tab. Always wrap them in a code block and end with `save`.

## Intégration MCP — FC en direct

Quand le serveur `betaflight-mcp` est disponible, **toujours préférer la lecture live à demander un diff**. Pattern obligatoire : lire → calculer → confirmer → écrire → `save_config`. Ne jamais appeler `save_config` sans confirmation explicite — il redémarre le FC.

Détection : tenter `list_serial_ports` ; si ça répond, le serveur est actif. Sinon, basculer en mode offline (diff CLI) sans bloquer.

→ Catalogue complet des tools, pattern d'écriture et gestion des erreurs : `references/mcp-tools.md`

## Setup wizard (configuration initiale)

Déclencher **uniquement** sur demande explicite de configuration from scratch. Phrases typiques : "j'ai acheté un drone", "nouveau FC", "wizard", "configure from scratch", "partir de zéro", "premier vol". Ne pas déclencher sur les questions de tuning ponctuel ou de dépannage.

Flux en 4 étapes : collecte des infos build (en une seule question groupée) → connexion MCP optionnelle → sélection du preset dans `assets/presets/` → application via MCP ou export diff CLI.

→ Flux complet, tableaux et règles : `references/wizard.md`

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
4. This is a **time-domain** analyzer — it does not do FFT/noise spectra. For that, still point users to https://blackbox.betaflight.com or PIDtoolbox.
5. For **step response analysis**, use `python -m scripts.step_response`.

```
python -m scripts.step_response <log.bbl>                        # text report, all axes
python -m scripts.step_response <log.bbl> --axis roll            # single axis
python -m scripts.step_response <log.bbl> --bandpass --active-only   # best coherence (recommended)
python -m scripts.step_response <log.bbl> --bandpass --active-only --axis yaw
python -m scripts.step_response <log.bbl> --plot                 # render step response + coherence figure
python -m scripts.step_response <log.bbl> --bandpass --active-only --plot  # best quality + figure
python -m scripts.step_response <log.bbl> --json                 # machine-readable
python -m scripts.step_response <log.bbl> --csv curves.csv       # export response curves
python -m scripts.step_response <log.bbl> --nperseg 2048         # larger Welch window
python -m scripts.step_response <decoded.csv>                    # from analyze_blackbox --csv
```

**Signal quality flags** (improve coherence on noisy logs):
- `--bandpass` — Butterworth 4th-order 5–80 Hz before Welch; removes DC drift and motor-frequency noise
- `--active-only` — keeps only frames around fast stick inputs; drops hovering noise and propwash
- `--nperseg N` — override Welch window size (default: auto ~64 ms, power of 2)

**Always use `--bandpass --active-only`** for identification flights with deliberate step inputs. On a typical log this raises coherence from ~0.1 to 0.65–0.80+.

**Step response — coherence warning**: the script reports a coherence value per axis (5–80 Hz band). Coherence < 0.5 on a freestyle or racing log is **normal and expected** — it means the gyro is driven by many things other than the setpoint (vibrations, propwash, non-linear PID terms like D_max and anti-gravity). The metrics are still indicative but not precise. For reliable coherence (> 0.7) the user needs a **dedicated identification flight**: deliberate full-stick → neutral → full-stick inputs, repeated 3–5 times per axis, with no other maneuvers. Always mention this when presenting step response results from a freestyle or racing log.

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

This skill works in Claude Code and in the claude.ai apps (web / desktop / mobile). Always load the **whole skill, including the `scripts/` folder** (e.g. the release zip) — not just `SKILL.md` — so the tools are present. `blackbox_decoder.py` and `blackbox_presenter.py` are stdlib-only helper modules imported by the other scripts.

The Python scripts are meant to be **executed** whenever a code-execution environment is available:

- **Claude Code** — scripts run in your local shell with your local Python.
- **claude.ai apps** — scripts run in the code-execution sandbox. This requires code execution / the analysis tool to be enabled for the conversation. Do not refuse to run them here.

Always invoke scripts from the skill root using the module form: `python -m scripts.analyze_blackbox`. If that fails (e.g. `scripts` not found as a package), fall back to `cd scripts && python analyze_blackbox.py` then return to the previous directory.

`analyze_blackbox.py` needs `numpy` + `pandas` only for `--stats` and `--csv`; header parsing and `--json` use the standard library alone. If `numpy`/`pandas` are missing in the sandbox, run header or `--json` mode, or `pip install numpy pandas` first.

## Bundled resources

- `references/arming-flags.md` — Tous les arming prevention flags : codes, causes, solutions, pièges courants
- `references/mcu-usb-drivers.md` — MCU courants (STM32, AT32, APM32, GD32), drivers VCP et DFU par OS, Zadig, ImpulseRC Driver Fixer, diagnostic port COM introuvable
- `references/mcp-tools.md` — Catalogue complet des tools MCP, pattern écriture, gestion erreurs
- `references/wizard.md` — Flux détaillé du wizard de configuration initiale
- `references/cli-commands.md` — Betaflight CLI command reference
- `references/parameters.md` — Most common `set` parameters with safe ranges
- `references/pid-tuning.md` — PID, filter, and rates tuning guide
- `references/troubleshooting.md` — Symptom-to-cause map
- `references/configuration.md` — Configurator tab navigation: what each tab does, all documentation URLs
- `references/version-changes.md` — Migration notes between major versions
- `scripts/parse_diff.py` — Parser for CLI diff/dump output
- `scripts/analyze_blackbox.py` — Blackbox log analyzer: parses all headers, decodes the full frame stream on demand, per-field stats and CSV export (CLI entry point)
- `scripts/blackbox_decoder.py` — Pure-Python blackbox decoder (faithful port of the official log-viewer); used by `analyze_blackbox.py`
- `scripts/blackbox_presenter.py` — Human-readable presentation layer: scales raw values to physical units, decodes enum headers, and computes rates in °/s; used by `analyze_blackbox.py` and `parse_diff.py`
- `scripts/validate_config.py` — Sanity-check a CLI dump for common errors
- `scripts/step_response.py` — Closed-loop step response analyser (setpoint → gyro) using Welch's cross-spectral method; rise time, overshoot, settling time, delay, per-axis diagnosis; `--plot` renders an inline matplotlib figure (step response + coherence curves)
- `assets/presets/` — Starter CLI snippets per build class (3", 5" freestyle, 7" longrange, cinewhoop)
