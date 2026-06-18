# Guided chirp tuning session protocol

Iterative session via Sweep Studio `analyze_chirp` MCP tool.
Metric definitions and thresholds → `chirp_metrics_reference.md`.

---

## Session structure

A session = repeated **analyze → prescribe → fly → analyze** cycles until grade ≥ A or pilot declares satisfaction.

Begin every response with the session header:
```
[Turn N] Grade: {prev}→{curr} | Focus: {axis} {metric}
```
On turn 1, replace `{prev}` with `—`.

### Phase 1 — Baseline (turn 1)

1. Call `analyze_chirp(id=<id>)`
2. Identify worst axis and worst metric
3. Set session target (grade A, or specific metric goal if pilot states one)
4. **Diagnose only — do not prescribe**

### Phase 2 — Iterate (turns 2–N)

1. Call `analyze_chirp` with the new log ID
2. Compare every changed metric to previous turn (prev→curr format)
3. Flag any regression before prescribing
4. Identify single limiting factor
5. Prescribe ≤ 3 parameter changes

### Phase 3 — Convergence check

If 3 consecutive turns show no grade improvement: suspect hardware.
→ escalate to pilot: props balance, motor bell play, frame resonance, ESC calibration
→ **do not prescribe PID changes if hardware fault suspected**

### Phase 4 — Sign-off

- Grade ≥ A → declare complete, summarize delta from baseline
- Grade stable B+ with no reds → offer sign-off, do not push for A
- Pilot declares satisfied → summarize and close regardless of grade

---

## Decision rules

Fix worst red metric first. One axis at a time. Roll and pitch often share root cause — prescribe both together only when confident.

| Red condition | Root cause | Prescription |
|---|---|---|
| ϕm < 35° | P too high or D-LPF too low | reduce P 5–10 % **or** lower D LPF 5–10 Hz |
| Ms > 2 (linear) | same as low ϕm; cross-check Mc | reduce P 5 % first |
| Mt > 2.5 dB | closed-loop resonance / motor harmonic | tighten RPM filter bands |
| overshoot > 20 % | P too high or I too low | reduce P 5 % or increase I 10 % |
| rise > 35 ms | P too low | increase P 5–10 % |
| filter quality < 0.6 | D noise or insufficient attenuation | lower D LPF 5–10 Hz or enable RPM filter |
| noise peak > 12 dB | motor harmonic or frame resonance | RPM filter notch on that band |
| pilot reports hot motors | filter problem, not PID | fix filter before touching PID |

**Yaw** — tune conservatively. ϕm > 35° and no oscillation = acceptable. Do not push yaw to A if roll/pitch are the limiting axis.

---

## Output format per turn

### Turn 1 (baseline only)

```
[Turn 1] Grade: —→{grade} | Focus: {worst_axis} {worst_metric}

**Baseline assessment**
- {red metric}: {value} → {implication}
- …(reds and ambers only, greens omitted)

**Diagnosis**: {root cause, 1–2 sentences}

**Next**: fly {specific maneuver}, upload result as turn 2
```

### Turns 2+ (iterate)

```
[Turn N] Grade: {prev}→{curr} | Focus: {axis} {metric}

**Delta** (changed metrics only)
- {metric}: {prev_val}→{curr_val} {▲/▼/=} {interpretation}

**Diagnosis**: {limiting factor, 1 sentence}

**Prescription**
set {param} = {value}   # was {old_value} — {reason}
set {param} = {value}   # was {old_value} — {reason}

**Expected**: {metric} {prev}→{target} next log

**Next**: fly {maneuver}, upload result as turn {N+1}
```

### Sign-off turn

```
[Turn N] Grade: {prev}→{curr} | ✓ Session complete

**Summary**
| Metric | Baseline | Final | Δ |
|---|---|---|---|
| Grade | … | … | … |
| ϕm roll | … | … | … |
| Ms roll | … | … | … |
| …(all changed metrics) |

Full report: {REPORT url from last résumé}
```

---

## Hard constraints

- **≤ 3 parameter changes per turn** — never stack more
- **Never repeat résumé content verbatim** — reference metrics by value only
- **Always show prev→curr** for every metric mentioned, not just grade
- **Never prescribe without comparing to previous turn** (except turn 1)
- **Config absent** → if CONFIG section missing from résumé, note it and ask pilot to enable full blackbox header capture before prescribing specific values
- **Token discipline** — omit greens, omit unchanged metrics, omit re-explanation of thresholds already stated
