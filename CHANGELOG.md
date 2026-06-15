# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.8.2] — 2026-06-15

### Changed
- **Chirp result presentation policy.** New SKILL.md section "Presenting chirp
  analysis results": lead with indicator values (Ms, step metrics, noise margin),
  not the composite score; prefer the **sensitivity peak Ms** as the primary
  stability indicator (with its report thresholds and a short explanation);
  **avoid the scalar phase margin** — likely a false positive when the low end of
  the Bode is noisy — and cite the guaranteed margin from Ms instead; frame the
  score as relative to a single tuning exercise; on multi-pass, show a parameter
  table flagging what moved alongside the per-pass indicators.
- `references/chirp-tuning.md`: added a "Sensitivity peak Ms — the primary
  stability indicator" subsection and demoted phase margin to "secondary, use
  with care" with a hardened false-positive caveat. Docs only.

## [0.8.1] — 2026-06-12

### Changed
- Bump bundled `betaflight-chirp-core` to **0.1.6** and pin it from **PyPI**
  (`betaflight-chirp-core==0.1.6`) instead of `git+https`. Manual runs no longer
  need `git` on PATH; the release workflow vendors the package via
  `pip install --target` from PyPI. Core 0.1.4→0.1.6 is docs/packaging only — the
  public API and report renderer are unchanged, so the skill wrappers are untouched.

## [0.8.0] — 2026-06-12

### Changed
- **Compute core extracted to `betaflight-chirp-core`** (new public package, the
  single source of truth shared with the FPVLogForge backend). The blackbox decode,
  chirp FRF/Bode, step response, spectral noise analysis and the self-contained HTML
  report now live there; `chirp_analysis.py`, `spectral_analysis.py`,
  `step_response.py` and `analyze_blackbox.py` are now thin CLI wrappers over it.
  `build_skill_zip.py` vendors the package into the zip (the sandbox cannot
  pip-install), so behaviour is unchanged — outputs verified byte-for-byte against
  the previous scripts. Bundles `betaflight-chirp-core` **v0.1.4** (shared,
  mountable HTML report renderer assets under `report_assets/`).
- `betaflight-chirp-core` is now declared in `requirements.txt` and `pyproject.toml`
  (pinned `@v0.1.4`) so a plain `git clone` + `pip install -r requirements.txt` /
  `uv run` can run the analysis scripts; `README.md` and `SKILL.md` updated to describe
  the wrapper-over-core architecture (decoder/signal helpers now live in the core).

### Removed
- `blackbox_decoder.py` and `blackbox_signal.py` — replaced by the vendored
  `betaflight_chirp_core` (decoder + signal helpers).

## [0.7.0] — 2026-06-06

### Changed
- Chirp report — **major UI redesign** of the HTML report:
  - the **chirp spectrogram** is now a standalone *measurement sanity-check* cadre
    placed **first** (before Filtering), confirming the sweep actually covered the
    band before anything is read off it.
  - per-axis Bode/step blocks are titled **PID Roll / Pitch / Yaw**; the redundant
    standalone "PID" section header is gone.
  - the **tune note** shows the per-axis detail as a **bordered table** —
    indicator-coloured headers, centred white values.
  - the analysis pipeline is drawn as a **numbered flowchart/sequence diagram**
    (no longer pill-shaped chips that looked like the header tags).
  - every block carries a **gradient accent liseré** (`#ff5b2e → #2dd4ff`).
  - **pictograms** before every section title; tooltip terms are uniformly
    dotted-underlined.
  - the **glossary** is sorted alphanumerically (in the active language) and gains a
    general **"filtering"** entry — the *filtrage* tooltip now explains Betaflight
    filtering as a whole (why and how), not just the gyro lowpass.
  - the multi-pass overlay hint moved next to each axis' pass pills; removed the
    redundant single/multi-pass pin note and the add-a-pass tip.
- Refreshed the live example report (`scripts/full_report.html`) and its screenshot.

## [0.6.1] — 2026-06-04

### Fixed
- Chirp report — throttle × frequency map now uses a **robust percentile (p10–p98)
  colour scale**: a fairly uniform map no longer saturates red (the old absolute
  min/max scale was a contrast artefact, not "noise everywhere").

### Changed
- Chirp report — step response draws faint **10 ms minor gridlines** so rise/settle
  timing can be gauged by eye.
- Chirp report — the phase-margin glossary now states the report uses the
  **guaranteed margin from Ms** (with the f(Ms) marker), not the fragile 0 dB-crossover
  margin.
- Chirp report — per-pass scoreboard shows each score in parentheses.
- Refreshed the live example report (`scripts/full_report.html`) and its screenshot.

## [0.6.0] — 2026-06-04

### Added
- Chirp report — **per-pass config tooltip is now a diff**: on any pass label
  (pills, scoreboard, comparison header) each PID/filter field shows `from → to`
  vs the previous pass, with the changed ones highlighted — what moved, at a glance.
- Chirp report — a **teaching tooltip** on the throttle × frequency map (a "?"
  badge): hover shows a synthetic *bad* map (a rising motor harmonic, a fixed
  frame resonance) drawn with the report's own colour map, to learn to read a
  real one. No image embedded — synthesised in-browser.
- Chirp report — the step response now draws the **10 % / 90 % rise-time
  thresholds**, so the "rise X ms" figure is self-explanatory.
- Chirp report — the tuning guide opens with a compact horizontal **analysis
  pipeline** (Blackbox → frequency ID → frequency response → margin/crossover →
  simulated step → noise & filtering → scoring → recommendations).

### Changed
- Chirp report — coherence glossary now states the **0.8** reliability gate
  (matching the plot), was an inconsistent ~0.6.
- Chirp report — the margin · f(Ms) evolution tile drops the "(solid)/(dashed)"
  words; each label is underlined with its own line style instead.

## [0.5.0] — 2026-06-04

### Added
- Chirp report — **inter-sweep repeatability**: when a log triggers the chirp
  several times on an axis, each sweep is analysed independently and aggregated
  into a median curve + min/max band on gain/phase/coherence/step, and median ±
  range on the scalars. Single-sweep logs render byte-identically to before. A
  multi-sweep axis spectrogram is the per-cell median (cleaner ridge).
- Chirp report — composite **tune score** (0–100 + letter grade), per axis and
  overall, blending overshoot, rise, guaranteed phase margin, Ms and HF noise
  margin; shown with the delta vs the previous pass and a per-pass scoreboard
  (★ on the best) — a comparative gauge of better/worse than the last config.
- Chirp report — **per-axis indicator evolution** panel (small multiples across
  passes, median dot + inter-sweep whisker), under the score; hover a point for
  its value.
- Chirp report — a shared **visual identity** (colour + pictogram) per indicator
  and per config item, reused across the tune score, evolution tiles, comparison
  table and the per-pass config tooltip.
- Chirp report — a **rich config tooltip** on every pass label (pills, scoreboard,
  comparison header) showing that pass's PID + filters; per-pass **show/hide
  pills** in each axis block header; a zoomed **inset** on the step response.

### Changed
- Chirp report — restyled header banner (CHIRP ANALYZER + keyword chips);
  coherence reliability note moved beside its title with the untrusted zone
  labelled in-plot; Bode filter legend moved beside its title; step y-scale
  normalised to 0.25 steps (1.0 always a gridline); collapsible "Filtering
  leads" and "Glossary"; the standalone "current settings" and "history" cadres
  dropped (config now lives in the tooltips + comparison table).
- Chirp text output — per-axis HF noise margin (peak-aware: folds in the
  resolved resonances) and gain resonances added; the phase-margin line now
  reports the robust guaranteed margin instead of the fragile 0 dB-crossover one.
- README — chirp section documents every invocation mode (batch chronological,
  incremental, language) with a live example report and screenshot.

## [0.4.0] — 2026-06-03

### Removed
- `references/method.md` (1045 lines) — orphaned (referenced nowhere) and in
  French, against the English-only references rule. Pure dead weight.
- An unused French design note under `references/` — only cited from a
  `spectral_analysis.py` comment (never loaded into context), against the
  English-only references rule; the comment now describes the diagnosis
  conventions generically.
- `scripts/gyro_noise.py` — its pre/post-filter gyro PSD is reproduced by the
  `chirp_analysis.py` HTML report (raw vs filtered, motor-harmonic bands, filter
  cut-offs) and its peak/harmonic diagnosis overlaps `spectral_analysis.py`,
  the documented FFT/noise tool. The motor-output PSD was the only unique bit
  and was not referenced anywhere in the skill workflow.

### Added
- `scripts/blackbox_signal.py` `decode_dataframe()` — decodes a `.bbl`/`.bfl`
  to a DataFrame **in-process** via `blackbox_decoder`, with no subprocess, no
  temp file and no CSV float round-trip. `load_dataframe()` now uses it for
  binary logs and falls back to the isolated `analyze_blackbox --csv` subprocess
  only if the in-process decode raises. `spectral_analysis`, `step_response` and
  `chirp_analysis` load through `load_dataframe`, dropping their temp-file dance.
- `selftest.py`: a synthetic-signal smoke for `spectral_analysis` +
  `step_response` (runs without a `.bbl`, when numpy/scipy are present) and an
  in-process-vs-subprocess decode equivalence guard (runs when a `.bbl` fixture
  is present).

### Changed
- Token diet for `SKILL.md` (token-economy guidelines): trimmed the
  always-loaded `description` from ~132 to ~105 words without losing trigger
  coverage, and compressed the inline chirp paragraph into a short pointer to
  `references/chirp-tuning.md` (which already documents the internals).
- Refactor: extracted the helpers that were copy-pasted across the blackbox
  analysers (`_decode_bbl`, `_load_csv`, `_sample_rate`, activity mask) into a
  single shared module `scripts/blackbox_signal.py`. `spectral_analysis`,
  `step_response` and `chirp_analysis` now delegate to it — one copy to maintain,
  no behaviour change. The diverging DSP primitives (Welch window sizing, axis
  segmentation, chart payloads) stay per-script on purpose.

## [0.3.1] — 2026-06-02

### Changed
- Chirp HTML report polish, driven by real multi-pass use:
  - Removed the deterministic per-axis **PID lead** (PID advice is left to the
    model); the measured Bode/step **diagnosis** stays.
  - **Overview** is tied to the latest pass and, for multi-pass, adds an
    "evolution since pass 1" block (config changes + per-axis phase-margin and
    overshoot deltas).
  - **Multi-pass overlay**: passes are a **checkbox list** (show/hide each pass's
    curves) instead of an inline legend.
  - "Current settings" → context-aware **"Initial settings"** (single pass) /
    **"latest pass"** (multi).
  - Tuning guide recommends enabling **`vbat_sag_compensation`** for comparable
    passes.
  - Throttle × frequency map gets a **colour-scale legend** and a **how-to-read**
    caption.

## [0.3.0] — 2026-06-02

### Changed
- `scripts/chirp_analysis.py` evolved from a Bode analyser into a full **guided,
  bilingual (FR/EN, live toggle) tuning assistant**, validated on real
  `debug_mode = CHIRP` logs (BF 2025.12.3-alpha):
  - **Firmware auto-detection** — current BF logs only `debug[0] = 5000·sinarg`;
    the tool reconstructs the excitation, the instantaneous sweep frequency and
    per-axis segmentation (by `setpoint` energy) from it. The legacy `debug[1..3]`
    path is kept as a fallback. FRF input defaults to the calibrated `setpoint[i]`.
  - Report now bundles a per-axis **step response**, a **gyro noise PSD**
    (floor-referenced raw vs filtered, eRPM-derived **motor-harmonic bands**,
    filter cut-off lines, disable-candidate analysis), a **chirp spectrogram**
    (log frequency), a throttle × frequency map (motor-average fallback when
    `rcCommand[3]` isn't logged), a **multi-pass overlay** with an exhaustive
    settings-comparison table, and a guided **Filtering → PID → History** workflow
    with deterministic, data-driven observations read from the log's PID/filter
    header (no LLM at runtime).
  - **Phase margin** is reported with an uncertainty (the scalar is sensitive to
    the crossover; prefer the curves/step for before-after). Responsive full-width
    layout. New flags: `--lang fr|en`, `--history`/`--no-history`, several inputs
    for a before/after overlay.
- `references/chirp-tuning.md` and `SKILL.md` updated for the current debug mapping
  and the richer report; `scripts/selftest.py` adds a (dependency-guarded) chirp
  pipeline smoke test.

## [0.2.0] — 2026-06-01

### Added
- `scripts/chirp_analysis.py` — closed-loop chirp frequency-response analyser for
  logs recorded with Betaflight's chirp generator (`debug_mode = CHIRP`). Computes
  the per-axis FRF `H(f) = Pxy/Pxx` via Welch's cross-spectral method → **Bode**
  (gain dB, phase deg) plus **coherence**, segmenting by the active chirp axis
  (`debug[1]`) and using the logged excitation (`debug[3]`) as the input reference
  (configurable via `--input-col`, with a `setpoint[i]` fallback). Diagnosis is
  gated to where coherence > 0.8; also builds a **throttle × frequency resonance
  map**. `--html` writes a single self-contained report (vanilla-JS `<canvas>`
  renderer, no external dependencies, opens offline); `--json` is machine-readable.
- `references/chirp-tuning.md` — chirp frequency-response tuning method: why chirp
  over step, the `debug_mode = CHIRP` field mapping, the `chirp_*` CLI parameters,
  lead-lag excitation shaping, how to read the Bode/coherence plots, and the flight
  protocol (including: never enable CHIRP on the ground, betaflight/betaflight#15012).

### Changed
- README and SKILL.md: added the chirp frequency-response workflow to the capability
  list and the blackbox analysis steps, with a `get chirp` availability note, and
  dropped the AntV `mcp-server-chart` `--chart` promotion from both (the flag itself
  remains in the code).

## [0.1.20] — 2026-05-31

### Added
- `--chart` mode on `scripts/spectral_analysis.py` and `scripts/step_response.py`: emits a ready-to-send `generate_line_chart` payload (one series per axis via `group`, downsampled) for the free AntV `mcp-server-chart` MCP server, so the curves render inline where matplotlib `--plot` cannot open a window (e.g. claude.ai web). Documented in `references/mcp-tools.md`.

## [0.1.19] — 2026-05-31

### Added
- `scripts/spectral_analysis.py` — noise spectrum / FFT analyser (gyro or D-term). Welch PSD per axis, automatic frequency-peak extraction, harmonic-series grouping, and source diagnosis (motor harmonics → RPM filter; isolated peak → dynamic notch; broadband floor → low-pass). Reuses the existing decoder (`.bbl`/CSV), with `--signal`, `--axis`, `--session`, `--fmin/--fmax`, `--json`, `--csv`, and a `--plot` PSD + spectrogram figure.

### Changed
- SKILL.md and README updated: the blackbox section no longer defers FFT to an external tool; it now points at `spectral_analysis.py`, and both list the new script.

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
