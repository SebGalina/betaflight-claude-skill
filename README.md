# betaflight-claude-skill

> **A Claude skill for Betaflight: FPV drone configuration, PID tuning, blackbox log analysis, and troubleshooting.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Betaflight](https://img.shields.io/badge/Betaflight-2025.12-orange.svg)](https://betaflight.com/)
[![Claude Skill](https://img.shields.io/badge/Claude-Agent_Skill-purple.svg)](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)

A [Claude](https://claude.ai) skill that helps you configure, tune, analyze, and troubleshoot FPV drones running Betaflight firmware. It works from the artifacts you already have — CLI dumps, blackbox logs, and plain-language descriptions of how the quad flies — and can also read and write directly to a live flight controller via the MCP server.

## What it does

Once loaded, the skill lets Claude:

- **Diagnose flight issues** from natural-language descriptions (wobbles, hot motors, oscillations, drift, propwash, jello)
- **Resolve arming failures** — decode every arming prevention flag (cause, fix, common pitfalls)
- **Fix connection problems** — MCU/USB driver and COM-port-not-found troubleshooting (Zadig, DFU, ImpulseRC Driver Fixer)
- **Parse and analyze CLI diff/dump files** you share
- **Decode blackbox logs** — full binary frame decode with per-field statistics and CSV export
- **Analyze gyro and motor noise spectra** — Welch PSD of gyroUnfilt vs gyroADC (filter effectiveness) and motor outputs; equivalent to the Blackbox Explorer noise tab
- **Analyze step response** — closed-loop system identification (setpoint → gyro) via Welch cross-spectral method; rise time, overshoot, settling time, coherence
- **Generate paste-ready CLI configs** for common build classes (5" freestyle, 3" cinewhoop, 7" longrange)
- **Guide you through a setup wizard** when configuring a new drone from scratch
- **Read and write a live FC** via the `betaflight-mcp` server (PIDs, filters, rates, ports — without a diff file)
- **Detect RC channel mapping** — passively sample the radio, identify Throttle/Aileron/Elevator/Rudder by function name, compare against Betaflight's `rcmap` and generate a correction if needed
- **Assign switches to flight modes** — flip a switch to the active position, Claude detects the channel and µs value and generates the `aux` command (ARM, BEEPER, ANGLE, etc.)
- **Migrate configurations** across major Betaflight versions (4.4 → 4.5 → 4.6 → 2025.12)
- **Flag deprecated parameters** that would error on import to newer firmware
- **Recommend safe value ranges** for PIDs, filters, rates, and ESC settings
- **Fetch official presets** from `betaflight/firmware-presets` at runtime — always up to date with the community, filterable by firmware version, category, and keywords

**Default tuning target:** Betaflight 2025.12 (4.5.x and 4.4.x differences are documented in `references/version-changes.md`).

## Installation

### Claude Code

```bash
# Per-user (all projects)
git clone https://github.com/SebGalina/betaflight-claude-skill.git ~/.claude/skills/betaflight

# Or per-project
git clone https://github.com/SebGalina/betaflight-claude-skill.git .claude/skills/betaflight
```

`SKILL.md` lives at the repository root, so the cloned folder *is* the skill. Claude triggers it automatically when a request looks Betaflight-related.

### claude.ai (web / mobile / desktop)

1. Download the attached `betaflight-claude-skill-v*.zip` asset from the latest [release](../../releases) (the runtime-only payload, not the "Source code" archive).
2. In Claude: **Settings → Capabilities → Skills → "+ Create skill"** and upload the zip.
3. The skill triggers automatically when relevant.

### Claude API

Upload via the Skills API — see the [official guide](https://platform.claude.com/docs/en/build-with-claude/skills-guide).

## Blackbox log analysis

`scripts/analyze_blackbox.py` parses **all** headers by default and, on demand, fully decodes the binary frame stream (I/P/S/G/H/E frames). The decoder (`scripts/blackbox_decoder.py`) is a faithful pure-Python port of the official [blackbox-log-viewer](https://github.com/betaflight/blackbox-log-viewer) — every field encoding and predictor.

```bash
python -m scripts.analyze_blackbox log.bbl              # headers + build summary (fast, stdlib only)
python -m scripts.analyze_blackbox log.bbl --stats      # decode frames + per-field min/max/mean/std
python -m scripts.analyze_blackbox log.bbl --csv out.csv  # decoded main frames to CSV ('-' for stdout)
python -m scripts.analyze_blackbox log.bbl --json       # full structured output
python -m scripts.analyze_blackbox log.bbl --session N  # pick one of several concatenated logs
```

The `--stats` and `--csv` modes require `numpy` and `pandas`; header parsing and `--json` work with the standard library alone.

## Gyro and motor noise analysis

`scripts/gyro_noise.py` computes the power spectral density (Welch method) of the gyro and motor signals — the equivalent of the noise tab in Blackbox Explorer.

```bash
# Full noise report (gyro + motors, all axes, up to 1 kHz)
python -m scripts.gyro_noise log.bbl

# Render frequency plot — filtered (solid) vs unfiltered (dashed) gyro + motor spectrum
python -m scripts.gyro_noise log.bbl --plot

# Single axis, limit to 500 Hz
python -m scripts.gyro_noise log.bbl --axis roll --max-freq 500

# Export PSD data for external plotting
python -m scripts.gyro_noise log.bbl --csv spectra.csv

# JSON output
python -m scripts.gyro_noise log.bbl --json
```

`gyroUnfilt` is logged by default in Betaflight (controlled by `blackbox_disable_gyrounfilt`, default OFF). When present, the script shows the filter attenuation in dB at the peak noise frequency. If the field is absent the script falls back to the filtered gyro only and warns the user.

## Step response analysis

`scripts/step_response.py` estimates the closed-loop step response (setpoint → gyro) per axis using Welch's cross-spectral density method. Reports rise time, overshoot %, settling time, delay, coherence, and per-axis tuning diagnosis.

```bash
# Any flight log — indicative results
python -m scripts.step_response log.bbl

# Identification flight (full-stick step inputs) — reliable results
python -m scripts.step_response log.bbl --bandpass --active-only

# Render inline figure (step response + coherence, all axes)
python -m scripts.step_response log.bbl --bandpass --active-only --plot

# Single axis, JSON output, or export curves
python -m scripts.step_response log.bbl --bandpass --active-only --axis roll
python -m scripts.step_response log.bbl --bandpass --active-only --json
python -m scripts.step_response log.bbl --bandpass --active-only --csv curves.csv
```

**For reliable results** (coherence > 0.7): fly a dedicated identification session — full stick → neutral → full stick, 3–5 times per axis, no other maneuvers. Then run with `--bandpass --active-only`.

## Noise spectrum / FFT analysis

`scripts/spectral_analysis.py` computes the power spectral density (Welch's method) of the gyro or D-term per axis — PIDToolbox-style — then automatically extracts the frequency peaks and groups them into harmonic series so you know *what* the noise is and *which* filter to reach for.

```bash
# Gyro noise spectrum, all axes (peaks + harmonics + diagnosis)
python -m scripts.spectral_analysis log.bbl

# D-term spectrum — the main noise path to the ESCs
python -m scripts.spectral_analysis log.bbl --signal dterm

# PSD + spectrogram figure, single axis, JSON, or export the spectra
python -m scripts.spectral_analysis log.bbl --plot
python -m scripts.spectral_analysis log.bbl --axis roll
python -m scripts.spectral_analysis log.bbl --json
python -m scripts.spectral_analysis log.bbl --csv spectra.csv
```

Diagnosis: a clean `f0 + 2·f0 + 3·f0` family → **motor noise** (RPM filter); an isolated narrow peak → **frame resonance** (dynamic notch); a raised featureless floor → **broadband** noise (low-pass, never a notch).

Both this and the step-response script accept `--chart`, which emits a `generate_line_chart` payload for the free [AntV `mcp-server-chart`](https://github.com/antvis/mcp-server-chart) MCP server — so the curves render inline where matplotlib `--plot` can't open a window (e.g. claude.ai web). See `references/mcp-tools.md`.

## Wobble mode (EdgeTX stimulus)

`edgetx/wobble.lua` is an EdgeTX custom mixer script that injects a controlled, repeatable disturbance (step or frequency-sweep) on roll/pitch/both, on top of normal stick control. Use it to excite the craft on demand, then measure the response with the step-response and spectral scripts above (inject → log → measure). Setup and safety: `edgetx/README.md`.

## Setup wizard

Say _"configure from scratch"_, _"nouveau drone"_, _"wizard"_, or _"partir de zéro"_ to launch the guided setup wizard. Claude will ask all build info questions in a single grouped message (frame size, motors, props, battery, ESC protocol, RX, flight style), then pick the best preset — preferring the up-to-date official library via `fetch_presets.py`, falling back to the bundled `assets/presets/` stubs offline — and apply it via MCP if the FC is live, or as a copy-paste CLI diff otherwise.

## Official preset library

`scripts/fetch_presets.py` queries `betaflight/firmware-presets` at runtime so the wizard always proposes up-to-date community presets instead of the bundled stubs in `assets/presets/`.

```bash
# List all 2025.12 tune presets
python -m scripts.fetch_presets --category tune

# Filter by keyword
python -m scripts.fetch_presets --category tune --keywords "5inch,freestyle"

# Different firmware version
python -m scripts.fetch_presets --version 4.5 --category rates

# Fetch full CLI content of a specific preset
python -m scripts.fetch_presets --fetch presets/2025.12/tune/defaults.txt

# JSON output (for programmatic use)
python -m scripts.fetch_presets --category tune --json
```

Set `GITHUB_TOKEN` to raise the API rate limit from 60 to 5000 requests/hour. The script uses stdlib only — no extra dependencies.

## MCP — live FC connection

With the `betaflight-mcp` server running, Claude can read and write the FC directly without needing a diff file:

- **Reads**: PIDs, filter config, rates, ports, motor config, sensor data
- **Writes**: any `set` parameter, PID values, rates — always with explicit confirmation before `save_config`
- **RC mapping**: passively samples all RC channels (`detect_rc_mapping`), then identifies each stick axis by function via guided moves (`detect_rc_channel_move`)

Detection is automatic: Claude attempts `list_serial_ports` on startup; if the server responds, it switches to live mode. Otherwise it falls back to offline (diff CLI) mode without blocking.

See `references/mcp-tools.md` for the full tool catalogue, write pattern, and error handling.

## Automated eval runner

`scripts/run_evals.py` runs the skill's test suite against the Claude API. It loads `SKILL.md` + all `references/*.md` as the system prompt, sends each eval prompt to a **skill model** (Sonnet by default), and uses a **judge model** (Haiku) to score pass/fail automatically.

### Setup

```bash
pip install anthropic python-dotenv
# Add your API key to a .env file at the repo root, or export it:
echo "ANTHROPIC_API_KEY=sk-..." > .env
```

### Usage

```bash
python -m scripts.run_evals                   # run all evals
python -m scripts.run_evals --ids 6 7 8       # run specific evals by ID
python -m scripts.run_evals --verbose         # also print full skill responses
python -m scripts.run_evals --model sonnet    # override the skill model
```

### What the evals cover

| ID | Name | What it checks |
|----|------|----------------|
| 1 | diagnose-wobble-from-symptoms | Prefers mechanical causes over PID tuning after a hardware change |
| 2 | generate-cli-config-5inch | Generates a complete, props-off-warned CLI config for a 5" 6S build |
| 3 | analyze-shared-diff | Runs `parse_diff.py`, summarizes build, flags anomalies |
| 4 | migration-4.4-to-4.5 | References `version-changes.md`, lists renamed params, migration workflow |
| 5 | hot-motors-troubleshooting | Ranks D-term as primary suspect, suggests specific CLI changes |
| 6 | wizard-trigger-new-drone | Triggers wizard and groups all build questions in one message |
| 7 | wizard-trigger-from-scratch | Same, also mentions optional MCP connection |
| 8 | wizard-no-trigger-on-pid-question | Does NOT trigger wizard for a PID tuning question |
| 9 | wizard-no-trigger-on-troubleshooting | Does NOT trigger wizard for a troubleshooting question |
| 10 | mcp-live-read-priority | Prefers live MCP read over asking for a diff when FC is plugged in |
| 11 | mcp-write-requires-confirmation | Reads first, shows proposed change, waits for confirmation before writing |
| 12 | safety-no-failsafe-disable | Refuses to disable arming checks; explains why and offers to diagnose instead |

The runner exits with code 0 if all evals pass, 1 if any fail (CI-friendly).

## Repository structure

```
.
├── SKILL.md                  Skill definition + triggering description
├── references/               Docs loaded on demand
│   ├── arming-flags.md       Arming prevention flags: codes, causes, fixes
│   ├── cli-commands.md       Betaflight CLI command reference (2025.12)
│   ├── parameters.md         `set` parameters with safe ranges
│   ├── pid-tuning.md         PID, filter, rates, and step-response tuning guide
│   ├── configuration.md      Configurator tab navigation + all doc URLs
│   ├── troubleshooting.md    Symptom-to-cause map
│   ├── mcu-usb-drivers.md    MCU/USB drivers, DFU, Zadig, COM-port diagnostics
│   ├── version-changes.md    Migration notes between versions
│   ├── mcp-tools.md          MCP tool catalogue, write pattern, RC mapping protocol
│   ├── modes-switches.md     Guided switch assignment flow (ARM, BEEPER, ANGLE…)
│   └── wizard.md             Setup wizard flow, tables, and rules
├── scripts/                  Python tools
│   ├── fetch_presets.py      Fetch + filter official presets from betaflight/firmware-presets
│   ├── gyro_noise.py         Gyro and motor noise spectrum (Welch PSD, equivalent to Blackbox Explorer noise tab)
│   ├── parse_diff.py         Parser for CLI diff/dump output
│   ├── validate_config.py    Config sanity checker
│   ├── analyze_blackbox.py   Blackbox analyzer (CLI entry point)
│   ├── blackbox_decoder.py   Pure-Python blackbox frame decoder
│   ├── blackbox_presenter.py Human-readable scaling + enum decoding
│   ├── step_response.py      Closed-loop step response (Welch cross-spectral method)
│   ├── spectral_analysis.py  Noise spectrum / FFT peaks + harmonics (gyro / D-term)
│   ├── run_evals.py          Automated eval runner (Claude API + judge model)
│   ├── selftest.py           Stdlib-only smoke test for the scripts
│   ├── build_skill_zip.py    Build the runtime-only distributable zip
│   └── test/                 Local blackbox fixtures (git-ignored; see its README)
├── edgetx/                   EdgeTX radio scripts
│   ├── wobble.lua            PID-tuning stimulus generator (step / sweep)
│   └── README.md             Install (Radiomaster Pocket) + flight procedure
├── assets/
│   └── presets/              Starter CLI configs
│       ├── 5inch-freestyle.txt
│       ├── cinewhoop-3inch.txt
│       └── longrange-7inch.txt
├── evals/                    Test cases
│   ├── evals.json            12 eval cases (diagnosis, wizard, MCP, safety)
│   └── sample_diff.txt       Sample CLI diff used by eval #3
└── .github/workflows/        CI: attach the skill zip to each published release
```

## Usage examples

Once installed, just talk to Claude — no explicit invocation needed:

> "My 5-inch quad started wobbling on yaw after I changed props, what should I do?"

> "Generate a baseline CLI config for a 5\" freestyle build, 2207 1750KV motors on 6S, F7 FC, ELRS on UART2."

> "Here's my Betaflight diff, check that it's all consistent." *(attach the file)*

> "I'm moving from Betaflight 4.4 to 4.5 — which parameters should I review?"

> "Analyze this blackbox log and tell me if the motors are running hot." *(attach the .bbl)*

> "Here's my blackbox log from an identification flight — can you run a step response analysis and tell me if my PIDs are well tuned?" *(attach the .bbl)*

> "The step response shows 35% overshoot on roll — what should I change in my PIDs?"

> "J'ai acheté mon premier drone, configure-le de zéro." *(launches the setup wizard)*

> "Mon FC est branché — lis mes PIDs et dis-moi si c'est correct pour un 5\" freestyle." *(live MCP read)*

## Running the scripts manually

```bash
python -m scripts.parse_diff evals/sample_diff.txt
python -m scripts.validate_config evals/sample_diff.txt
python -m scripts.analyze_blackbox your_log.bbl --stats
python -m scripts.gyro_noise your_log.bbl --plot
python -m scripts.step_response your_log.bbl --bandpass --active-only --plot
python -m scripts.selftest                      # stdlib-only smoke test
python -m scripts.run_evals --ids 1 2 3
```

## Limitations

- **No filter response curves.** Theoretical LPF/notch/RPM filter frequency response is not computed. `gyro_noise.py` shows the empirical effect (measured attenuation at peak noise), not the designed curve. For the designed curves use [blackbox.betaflight.com](https://blackbox.betaflight.com) or PIDtoolbox.
- **Defaults to Betaflight 2025.12 conventions.** Older configs may contain deprecated or renamed parameters; the skill flags them but does not auto-migrate.
- **No real-time link without the MCP server.** Without `betaflight-mcp`, the skill works on files and descriptions only. With it, Claude can read and write the FC directly (see the MCP section above).

## Safety

The skill follows strict rules:

- **Never** recommends disabling failsafes or arming checks
- **Always warns** before motor-direction / mapping changes (props-off testing)
- **Flags** suspicious values instead of applying them silently
- **Reminds** that new tunes must be tested in a safe area
- **Never writes to the FC** (MCP mode) without explicit user confirmation before `save_config`

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and PRs welcome.

## License

Apache 2.0 — see [LICENSE.txt](LICENSE.txt).

## Links

- [Betaflight](https://betaflight.com/) — official project
- [Betaflight documentation](https://betaflight.com/docs)
- [blackbox-log-viewer](https://github.com/betaflight/blackbox-log-viewer) — the decoder this skill ports
- [Claude Skills documentation](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)

## Disclaimer

Community project, not affiliated with the Betaflight project or any FC manufacturer. Betaflight is a trademark of its respective owners. This skill relies on publicly documented Betaflight conventions.
