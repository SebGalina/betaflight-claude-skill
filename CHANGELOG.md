# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.20] — 2026-05-31

### Added
- `--chart` mode on `scripts/spectral_analysis.py` and `scripts/step_response.py`: emits a ready-to-send `generate_line_chart` payload (one series per axis via `group`, downsampled) for the free AntV `mcp-server-chart` MCP server, so the curves render inline where matplotlib `--plot` cannot open a window (e.g. claude.ai web). Documented in `references/mcp-tools.md`.

## [0.1.19] — 2026-05-31

### Added
- `scripts/spectral_analysis.py` — noise spectrum / FFT analyser (gyro or D-term). Welch PSD per axis, automatic frequency-peak extraction, harmonic-series grouping, and PIDToolbox-style diagnosis (motor harmonics → RPM filter; isolated peak → dynamic notch; broadband floor → low-pass). Reuses the existing decoder (`.bbl`/CSV), with `--signal`, `--axis`, `--session`, `--fmin/--fmax`, `--json`, `--csv`, and a `--plot` PSD + spectrogram figure.
- `edgetx/wobble.lua` + `edgetx/README.md` — EdgeTX custom mixer script that injects a controlled step or frequency-sweep disturbance on roll/pitch/both (adjustable amplitude, switch-armed) on top of normal stick control, to excite the craft for step-response and spectral measurements. Includes Radiomaster Pocket install steps, flight procedure, and safety notes.

### Changed
- SKILL.md and README updated: the blackbox section no longer says "no FFT — use PIDtoolbox"; it now points at `spectral_analysis.py`, and both list the new script and the EdgeTX wobble tool.
- `scripts/build_skill_zip.py` now bundles the `edgetx/` folder into the runtime skill.

## [0.1.18] — 2026-05-26

### Added
- `scripts/build_skill_zip.py` — builds the runtime-only distributable (`dist/betaflight-claude-skill-v<version>.zip`) with a single top-level `betaflight/` folder; excludes dev-only files (evals, CI, eval runner, self-test, build script, fixtures).
- `.github/workflows/release.yml` — on a published release, builds that zip and attaches it as an asset (curated release notes stay manual).

### Changed
- README aligned with current repo state: added "arming failures" and "MCU/USB driver" capabilities; structure tree now lists `arming-flags.md`, `mcu-usb-drivers.md`, `selftest.py`, `build_skill_zip.py`, `scripts/test/`, and `.github/workflows/`; setup-wizard section now reflects the official-preset-first selection; install-from-zip step clarified to point at the attached asset; `selftest` added to the manual-run list.

## [0.1.17] — 2026-05-26

### Added
- `scripts/gyro_noise.py` — gyro and motor noise spectrum analyzer (Welch PSD). Computes power spectral density of `gyroUnfilt` (pre-filter) vs `gyroADC` (post-filter) and motor outputs, reporting peak noise frequency and filter attenuation in dB per axis. Equivalent to the noise tab in Blackbox Explorer. Supports `--plot`, `--axis`, `--max-freq`, `--no-motors`, `--csv`, `--json`, `--session`.
- README: new "Gyro and motor noise analysis" section; `gyro_noise.py` added to repository structure and manual run examples; Limitations updated (filter response curves remain the only gap).
- `analyze_blackbox.py`: summary note now points to `gyro_noise.py --plot` instead of blackbox.betaflight.com.
- `scripts/selftest.py` — stdlib-only smoke test: checks `parse_diff` + `validate_config` against the committed `evals/sample_diff.txt`, and header-parses any `*.bbl` logs present in `scripts/test/` (SKIPs gracefully when absent, so a fresh clone passes).
- `scripts/test/README.md` — documents the (intentionally git-ignored) blackbox fixture convention.
- CONTRIBUTING.md test step now points to `python -m scripts.selftest`; `.gitignore` comment clarifies why `*.bbl` fixtures stay local.

### Changed
- SKILL.md trimmed from 320 to 240 lines (progressive disclosure): the full RC mapping protocol moved to `references/mcp-tools.md` and the step-response flags + coherence warning moved to `references/pid-tuning.md`, leaving concise trigger + pointer sections in SKILL.md.
- Frontmatter `description` shortened from 1003 to 912 chars (the 1024 cap was nearly hit) by removing the wizard sentence that duplicated the body; wizard triggers remain in the `## Setup wizard` section.
- Harmonized SKILL.md and `references/mcp-tools.md` to English for model-facing instructions; French trigger phrases (e.g. `"nouveau drone"`, `"positionne mon inter ARM"`) kept verbatim as recognition cues.

## [0.1.16] — 2026-05-25

### Added
- `scripts/fetch_presets.py` — fetches and filters official presets from `betaflight/firmware-presets` at runtime (stdlib only, optional `GITHUB_TOKEN`). Supports `--version`, `--category`, `--keywords`, `--fetch`, `--json`. Recurses into preset subdirectories.
- Setup wizard in SKILL.md now prefers official presets via `fetch_presets.py` over the bundled `assets/presets/` stubs, falling back to local files when network is unavailable.
- README: new "Official preset library" section with usage examples; `fetch_presets.py` added to repository structure; new "What it does" bullet.

## [0.1.15] — 2026-05-25

### Added
- RC mapping detection flow in SKILL.md: passive 30-second sampling via `detect_rc_mapping`, guided identification of Aileron/Elevator/Rudder by function name via `detect_rc_channel_move`, comparison with Betaflight's `rcmap` setting, and auto-generated correction snippet if needed.
- Guided switch assignment flow in SKILL.md: user flips each switch to its active position, Claude detects the AUX channel and µs value and generates the `aux` CLI command. Covers ARM, BEEPER, ANGLE and any other mode. ARM assigned first, always.
- `references/modes-switches.md` — full sequence, range calculation, conflict check, and UX rules for the switch assignment flow.
- `detect_rc_mapping` and `detect_rc_channel_move` added to the real-time telemetry table in `references/mcp-tools.md`.
- README updated: two new bullets in "What it does", RC mapping mention in the MCP section, `modes-switches.md` in repository structure.

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
