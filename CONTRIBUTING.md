# Contributing to betaflight-skill

Thanks for considering a contribution! This skill is community-driven and aims to stay accurate as Betaflight evolves.

## Ways to contribute

- **Report inaccuracies** — wrong parameter range, deprecated info, missing version note
- **Add presets** — your tuned config for a build class that isn't covered (10", X-class, sub-250g, etc.)
- **Improve references** — clearer explanations, missing edge cases, better symptom mapping
- **Add test cases** — real-world prompts that exposed a gap in the skill
- **Translate** — references are currently in English; FR/DE/ES translations welcome

## Project structure

```
betaflight/
├── SKILL.md             ← Main skill definition (don't touch frontmatter without testing)
├── references/          ← Markdown docs loaded on demand by Claude
├── scripts/             ← Python helpers (parse_diff, validate_config, analyze_blackbox)
├── assets/presets/      ← Starter CLI configs
└── evals/               ← Test cases (evals.json + sample files)
```

## Before submitting a PR

### 1. Test your changes

If you modified a script:

```bash
python scripts/parse_diff.py evals/sample_diff.txt
python scripts/validate_config.py evals/sample_diff.txt
```

If you modified `SKILL.md` or references, run the test prompts in `evals/evals.json` manually against a Claude instance with the skill loaded, and verify the outputs are sensible.

### 2. Keep SKILL.md concise

The body of `SKILL.md` should stay under ~500 lines. Move detail into `references/`. Every line in `SKILL.md` is in Claude's context for every interaction — be ruthless.

### 3. Match Betaflight version conventions

Default target is **Betaflight 4.5.x**. If a contribution is version-specific, say so explicitly. When a parameter name changes across versions, document both in `references/version-changes.md`.

### 4. Safety first

Never add content that recommends:
- Disabling failsafes
- Bypassing arming checks
- Running motors above rated voltage
- Skipping props-off testing for motor mapping/direction changes

These are non-negotiable. PRs that weaken safety guidance will be rejected.

### 5. Stay neutral

This project isn't affiliated with the Betaflight project or any FC manufacturer. Don't promote specific hardware brands in references or presets. Use generic descriptions ("a 2207-class motor", not "[Brand] Specific Motor X").

## Style

- **Imperative voice** in instructions (`"Set dyn_notch_q to..."`, not `"You should set..."`)
- **Code blocks** for all CLI snippets, ending with `save`
- **Comments** in presets explaining what each block does
- **Markdown tables** for parameter references with units and safe ranges
- French or English are both fine for PRs and issues

## Adding a new preset

A good preset:

1. Targets a specific build class (frame size + battery + motor KV range)
2. Has comments explaining each section
3. Ends with `save`
4. Uses 4.5.x parameter names (no deprecated params)
5. Sets a conservative failsafe (`DROP` unless GPS rescue is properly configured)

Save as `assets/presets/<class>-<size>.txt` (e.g. `freestyle-5inch.txt`, `longrange-7inch.txt`).

## Adding a test case

Add to `evals/evals.json`:

```json
{
  "id": <next_id>,
  "name": "short-kebab-case-name",
  "prompt": "What a real user would actually say",
  "expected_output": "What the skill should produce or steer the answer toward",
  "files": ["optional_input_file.txt"]
}
```

If your test needs an input file, drop it in `evals/` and reference it.

## Reporting an issue

Open a GitHub issue with:

- Your Betaflight firmware version
- What you asked Claude
- What you expected
- What you got
- Whether your config was shared (and if so, anonymized if needed)

Issues in French are welcome.

## Code of conduct

Be kind. The FPV community is small and Betaflight is built by volunteers. Same here.
