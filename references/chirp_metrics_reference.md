# Chirp Metrics Reference

Interpretation guide for chirp-core output metrics used in tuning sessions.
Source fields: `pass["axes"][ax]`, `pass["tune_score"]`, `pass["noise_spectrum"]`, `pass["filter_quality"]`.

---

## Tune score

| Field | Type | Meaning |
|---|---|---|
| `tune_score.overall` | float 0–100 | Mean of per-axis scores |
| `tune_score.grade` | str A–F | A ≥ 85 · B 75–84 · C 65–74 · D 50–64 · F < 50 |
| `tune_score.axes.{ax}.score` | float 0–100 | Per-axis composite |
| `tune_score.axes.{ax}.subs` | dict | Sub-scores: overshoot / rise / margin / ms / noise |

---

## Bode / frequency response (per axis)

| Field | Unit | Meaning | Green | Amber | Red |
|---|---|---|---|---|---|
| `crossover_hz` (Mc) | Hz | 0 dB gain crossover — proxy for closed-loop bandwidth | 20–60 Hz | 15–20 / 60–80 | < 15 or > 80 |
| `phase_margin_deg` (ϕm) | ° | Stability margin at crossover — higher = more robust | ≥ 45° | 35–45° | < 35° |
| `phase_margin_unc_deg` | ° | ±uncertainty on ϕm (coherence-weighted) | < 10° | 10–20° | > 20° |
| `ms` | ratio | Peak sensitivity max\|S\| — amplification of disturbances (linear, ≥ 1 always) | < 1.5 | 1.5–2 | > 2 |
| `f_ms_hz` | Hz | Frequency of Ms peak | — | — | — |
| `pm_guaranteed_deg` | ° | Conservative phase margin (ϕm − uncertainty) | ≥ 35° | 25–35° | < 25° |
| `mt` | dB | Peak complementary sensitivity max\|T\| — closed-loop resonance / delay robustness | < 1.5 dB | 1.5–2.5 dB | > 2.5 dB |
| `f_mt_hz` | Hz | Frequency of Mt peak (closed-loop resonance) | — | — | — |

**Relationships:**
- High Ms + low ϕm → same root cause (P too high or D-LPF too low)
- High Mt at motor harmonic frequency → RPM filter missing or misconfigured
- High Mc + low ϕm → loop gain too aggressive overall

---

## Step response (per axis)

| Field | Unit | Meaning | Green | Amber | Red |
|---|---|---|---|---|---|
| `step.metrics.overshoot_pct` | % | Normalised peak above setpoint | < 10 % | 10–20 % | > 20 % |
| `step.metrics.rise_ms` | ms | 10 %→90 % rise time | < 25 ms | 25–35 ms | > 35 ms |
| `step.metrics.delay_ms` | ms | Transport delay before response starts | < 5 ms | 5–10 ms | > 10 ms |
| `step.metrics.settle_ms` | ms | Time to stay within ±2 % of setpoint | < 80 ms | 80–150 ms | > 150 ms |
| `step.metrics.peak` | normalised | Absolute peak value (1.0 = no overshoot) | ≤ 1.1 | 1.1–1.2 | > 1.2 |

**Relationships:**
- High overshoot + fast rise → P too high
- High overshoot + slow rise → I wind-up or D too low
- High delay → filter latency (LPF cutoff too low)

---

## Gain resonance peaks (per axis)

Each entry in `axes.{ax}.peaks`:

| Field | Unit | Meaning |
|---|---|---|
| `freq_hz` | Hz | Resonance frequency |
| `gain_db` | dB | Peak gain (above 0 dB baseline) |
| `prominence_db` | dB | Peak height above local baseline |

**Interpretation:**
- Peak > +3 dB at any frequency → risk of oscillation at that frequency
- Cluster near motor harmonic bands (motor_hz × 1,2,3,4) → RPM filter issue
- Peak near Mc → directly reduces ϕm → priority fix

---

## Noise spectrum peaks (all axes merged)

Each entry in `noise_spectrum.axes.{ax}.peaks`:

| Field | Unit | Meaning | Threshold |
|---|---|---|---|
| `freq_hz` | Hz | Noise peak frequency | — |
| `above_floor_db` | dB | Height above broadband noise floor | < 6 dB green · 6–12 amber · > 12 red |
| `resid_db` | dB | Residual after filtering (filtered channel) | < 3 dB good |
| `atten_db` | dB | Raw→filtered attenuation at this frequency (negative = cut) | < −10 dB good |
| `prom_db` | dB | Peak prominence in filtered spectrum | < 3 dB good |

---

## Filter quality (per axis)

| Field | Range | Meaning | Green | Amber | Red |
|---|---|---|---|---|---|
| `score` | 0–1 | Harmonic mean of attenuation × preservation | ≥ 0.8 | 0.6–0.8 | < 0.6 |
| `score_attenuation` | 0–1 | How well high-frequency noise is suppressed | ≥ 0.8 | 0.6–0.8 | < 0.6 |
| `score_preservation` | 0–1 | How well useful signal (< split freq) is preserved | ≥ 0.8 | 0.6–0.8 | < 0.6 |
| `f_split_hz` | Hz | Frequency separating "signal" from "noise" bands | — | — | — |
| `recommendation` | str | Verdict text: "good" / "increase cutoff" / "reduce cutoff" / … | — | — | — |
| `confidence` | str | "high" / "medium" / "low" — based on coherence | — | — | — |

**Common patterns:**
- Low `score_attenuation` → D-LPF cutoff too high, or RPM filter off
- Low `score_preservation` → D-LPF cutoff too low → adds delay → reduces ϕm
- Both low → filter mis-configured end-to-end

---

## Betaflight parameter mapping

| Chirp metric | Betaflight parameter | CLI command |
|---|---|---|
| P gain | `p_roll`, `p_pitch`, `p_yaw` | `set p_roll = <val>` |
| I gain | `i_roll`, `i_pitch`, `i_yaw` | `set i_roll = <val>` |
| D gain | `d_roll`, `d_pitch` | `set d_roll = <val>` |
| D-term LPF cutoff | `dterm_lowpass_hz` | `set dterm_lowpass_hz = <val>` |
| D-term LPF type | `dterm_lowpass_type` | `set dterm_lowpass_type = PT1` |
| RPM filter enable | `rpm_filter_harmonics` | `set rpm_filter_harmonics = 3` (0 = off) |
| RPM filter fade | `rpm_filter_fade_range_hz` | `set rpm_filter_fade_range_hz = 50` |
| Gyro LPF cutoff | `gyro_lowpass_hz` | `set gyro_lowpass_hz = <val>` |
| FF gain | `feedforward_roll` etc. | `set feedforward_roll = <val>` |

Save after changes: `save`

---

## Typical tuning sequence

```
1. Fix filter quality (score_attenuation / score_preservation)
2. Fix noise peaks > 12 dB (RPM filter bands)
3. Fix ϕm / Ms (P gain)
4. Fix overshoot / rise (I gain, secondary P)
5. Fix Mt (RPM filter fine-tune)
6. Grade ≥ A or stable B+ → done
```

Never tune PIDs before filters. Noise feeds back into P/D response and makes PID metrics unstable.
