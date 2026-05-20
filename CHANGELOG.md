# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial skill structure with `SKILL.md`, references, scripts, and presets
- `parse_diff.py` — parser for Betaflight CLI diff/dump output
- `validate_config.py` — sanity-check for CLI dumps (deprecated params, suspicious values)
- `analyze_blackbox.py` — header-level blackbox log inspection
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

## [0.1.0] — TBD

First public release.
