# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.8] — 2026-05-22

### Added
- Setup wizard triggered by explicit phrases ("nouveau drone", "configure from scratch", "partir de zéro") — guides the user through build info collection, optional FC connection via MCP, preset selection, and config application or CLI diff export.
- MCP integration section in SKILL.md: full tool catalogue (connection, live reads, real-time telemetry, writes), mandatory read→confirm→write→save pattern, and error handling rules.
- MCP case added to Core workflow as the highest-priority artifact type when the betaflight-mcp server is available.

## [0.1.7] — 2026-05-22

### Changed
- Add safety rule to SKILL.md: never suggest a parameter value outside the Safe range documented in `references/parameters.md` without flagging it, citing the limit, and requiring user confirmation.

## [0.1.0] — 2026-05-21

First public release.

### Added
- Initial skill structure with `SKILL.md`, references, scripts, and presets
- `parse_diff.py` — parser for Betaflight CLI diff/dump output
- `validate_config.py` — sanity-check for CLI dumps (deprecated params, suspicious values)
- `analyze_blackbox.py` — blackbox log analyzer: parses all log headers and, on
  demand, fully decodes the binary frame stream (I/P/S/G/H/E frames) with
  per-field statistics (`--stats`), CSV export (`--csv`), and JSON output
  (`--json`); multi-session aware (`--session`)
- `blackbox_decoder.py` — pure-Python blackbox decoder, a faithful port of the
  official blackbox-log-viewer (encodings + predictors); used by
  `analyze_blackbox.py`
- `blackbox_presenter.py` — human-readable presentation layer: scales raw values
  to physical units (gyro °/s, motor %, vbat V, amperage A, eRPM rpm, PID %),
  decodes enum headers (e.g. `rates_type → ACTUAL`, `fast_pwm_protocol →
  DSHOT600`) with version-aware tables, and computes rates as max rotation rate
  (°/s) per axis via a port of the Configurator rate curve. `analyze_blackbox.py`
  `--stats`/`--json` and `parse_diff.py` now emit human-readable values and a
  `rates` block; raw CSV export is unchanged
- Reference docs:
  - `cli-commands.md` — CLI command reference
  - `parameters.md` — `set` parameter ranges
  - `pid-tuning.md` — PID/filter/rates tuning guide with symptom map
  - `troubleshooting.md` — symptom-to-cause map
  - `version-changes.md` — migration notes between Betaflight versions
- Starter presets:
  - 5" freestyle
  - 3" cinewhoop
  - 7" longrange
- Eval set with 5 test prompts and a sample diff
