# betaflight-claude-skill

> **A Claude skill for Betaflight: FPV drone configuration, PID tuning, blackbox log analysis, and troubleshooting.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Betaflight](https://img.shields.io/badge/Betaflight-2025.12-orange.svg)](https://betaflight.com/)
[![Claude Skill](https://img.shields.io/badge/Claude-Agent_Skill-purple.svg)](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)

A [Claude](https://claude.ai) skill that helps you configure, tune, analyze, and troubleshoot FPV drones running Betaflight firmware. It works from the artifacts you already have — CLI dumps, blackbox logs, and plain-language descriptions of how the quad flies.

## ✨ What it does

Once loaded, the skill lets Claude:

- 🔧 **Diagnose flight issues** from natural-language descriptions (wobbles, hot motors, oscillations, drift, propwash, jello)
- 📄 **Parse and analyze CLI diff/dump files** you share
- 📊 **Decode blackbox logs** — full binary frame decode with per-field statistics and CSV export (see below)
- ⚡ **Generate paste-ready CLI configs** for common build classes (5" freestyle, 3" cinewhoop, 7" longrange)
- 🔄 **Migrate configurations** across major Betaflight versions (4.4 → 4.5 → 4.6)
- ⚠️ **Flag deprecated parameters** that would error on import to newer firmware
- 📏 **Recommend safe value ranges** for PIDs, filters, rates, and ESC settings

**Default tuning target:** Betaflight 4.5.x (4.4 and 4.6 differences are documented in the references). The blackbox decoder itself is version-agnostic.

## 🚀 Installation

### Claude Code

```bash
# Per-user (all projects)
git clone https://github.com/SebGalina/betaflight-claude-skill.git ~/.claude/skills/betaflight

# Or per-project
git clone https://github.com/SebGalina/betaflight-claude-skill.git .claude/skills/betaflight
```

`SKILL.md` lives at the repository root, so the cloned folder *is* the skill. Claude triggers it automatically when a request looks Betaflight-related.

### claude.ai (web / mobile / desktop)

1. Download `betaflight-claude-skill-v*.zip` from the latest [release](../../releases).
2. In Claude: **Settings → Capabilities → Skills → "+ Create skill"** and upload the zip.
3. The skill triggers automatically when relevant.

### Claude API

Upload via the Skills API — see the [official guide](https://platform.claude.com/docs/en/build-with-claude/skills-guide).

## 📊 Blackbox log analysis

`scripts/analyze_blackbox.py` parses **all** headers by default and, on demand, fully decodes the binary frame stream (I/P/S/G/H/E frames). The decoder (`scripts/blackbox_decoder.py`) is a faithful pure-Python port of the official [blackbox-log-viewer](https://github.com/betaflight/blackbox-log-viewer) — every field encoding and predictor.

```bash
python scripts/analyze_blackbox.py log.bbl              # headers + build summary (fast, stdlib only)
python scripts/analyze_blackbox.py log.bbl --stats      # decode frames + per-field min/max/mean/std
python scripts/analyze_blackbox.py log.bbl --csv out.csv  # decoded main frames to CSV ('-' for stdout)
python scripts/analyze_blackbox.py log.bbl --json       # full structured output
python scripts/analyze_blackbox.py log.bbl --session N  # pick one of several concatenated logs
```

The `--stats` and `--csv` modes require `numpy` and `pandas`; header parsing and `--json` work with the standard library alone.

This is a **time-domain** analyzer (real decoded values, statistics, export). For FFT / noise spectra, use [blackbox.betaflight.com](https://blackbox.betaflight.com) or PIDtoolbox.

## 📦 Repository structure

```
.
├── SKILL.md                  Skill definition + triggering description
├── references/               Docs loaded on demand
│   ├── cli-commands.md       Betaflight CLI command reference
│   ├── parameters.md         `set` parameters with safe ranges
│   ├── pid-tuning.md         PID, filter, and rates tuning guide
│   ├── troubleshooting.md    Symptom-to-cause map
│   └── version-changes.md    Migration notes between versions
├── scripts/                  Python tools
│   ├── parse_diff.py         Parser for CLI diff/dump output
│   ├── validate_config.py    Config sanity checker
│   ├── analyze_blackbox.py   Blackbox analyzer (CLI entry point)
│   └── blackbox_decoder.py   Pure-Python blackbox frame decoder
├── assets/
│   └── presets/              Starter CLI configs
│       ├── 5inch-freestyle.txt
│       ├── cinewhoop-3inch.txt
│       └── longrange-7inch.txt
└── evals/                    Test cases
    ├── evals.json
    └── sample_diff.txt
```

## 💬 Usage examples

Once installed, just talk to Claude — no explicit invocation needed:

> "My 5-inch quad started wobbling on yaw after I changed props, what should I do?"

> "Generate a baseline CLI config for a 5\" freestyle build, 2207 1750KV motors on 6S, F7 FC, ELRS on UART2."

> "Here's my Betaflight diff, check that it's all consistent." *(attach the file)*

> "I'm moving from Betaflight 4.4 to 4.5 — which parameters should I review?"

> "Analyze this blackbox log and tell me if the motors are running hot." *(attach the .bbl)*

## 🧪 Running the scripts

```bash
python scripts/parse_diff.py evals/sample_diff.txt
python scripts/validate_config.py evals/sample_diff.txt
python scripts/analyze_blackbox.py your_log.bbl --stats
```

The skill's test cases live in `evals/evals.json`.

## ⚠️ Limitations

- **No FFT / noise spectra.** The blackbox analyzer decodes real values and computes time-domain statistics; for frequency analysis use [blackbox.betaflight.com](https://blackbox.betaflight.com) or PIDtoolbox.
- **Defaults to Betaflight 2025.12 conventions.** 4.5.x and older configs may contain deprecated or renamed parameters; the skill flags them but does not auto-migrate.
- **No real-time link to the flight controller.** The skill works on files and descriptions — it does not talk to a USB-connected FC. (Claude in Chrome can drive the `app.betaflight.com` PWA; see `SKILL.md`.)

## 🛡️ Safety

The skill follows strict rules:

- **Never** recommends disabling failsafes or arming checks
- **Always warns** before motor-direction / mapping changes (props-off testing)
- **Flags** suspicious values instead of applying them silently
- **Reminds** that new tunes must be tested in a safe area

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and PRs welcome.

## 📜 License

Apache 2.0 — see [LICENSE.txt](LICENSE.txt).

## 🔗 Links

- [Betaflight](https://betaflight.com/) — official project
- [Betaflight documentation](https://betaflight.com/docs)
- [blackbox-log-viewer](https://github.com/betaflight/blackbox-log-viewer) — the decoder this skill ports
- [Claude Skills documentation](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)

## ⚖️ Disclaimer

Community project, not affiliated with the Betaflight project or any FC manufacturer. Betaflight is a trademark of its respective owners. This skill relies on publicly documented Betaflight conventions.
