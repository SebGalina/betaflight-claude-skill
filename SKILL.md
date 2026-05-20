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
   - **Blackbox log** (`.bbl`/`.bfl`) → analyse with `scripts/analyze_blackbox.py`
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

## Safety rules

- **Never recommend disabling failsafes** or critical safety features (arming checks, accelerometer calibration warnings, RX failsafe).
- **Never recommend** running motors above their rated voltage or removing thermal protections.
- **Always warn** before recommending changes to motor direction, ESC protocol, or anything that requires props-off testing.
- **Always remind** the user to test new tunes in a safe area, props-off first when changing motor mapping or direction.

## Version awareness

Default to **Betaflight 4.5.x** conventions unless the user specifies otherwise. If the user mentions 4.6 (current dev/release branch), check `references/version-changes.md` for the differences. If the user is on 4.4 or older, suggest upgrading after the diagnostic, not before — old tunes don't translate cleanly across major versions.

## Bundled resources

- `references/cli-commands.md` — Betaflight CLI command reference
- `references/parameters.md` — Most common `set` parameters with safe ranges
- `references/pid-tuning.md` — PID, filter, and rates tuning guide
- `references/troubleshooting.md` — Symptom-to-cause map
- `references/version-changes.md` — Migration notes between major versions
- `scripts/parse_diff.py` — Parser for CLI diff/dump output
- `scripts/analyze_blackbox.py` — Blackbox log analyzer (oscillation/noise detection)
- `scripts/validate_config.py` — Sanity-check a CLI dump for common errors
- `assets/presets/` — Starter CLI snippets per build class (3", 5" freestyle, 7" longrange, cinewhoop)
