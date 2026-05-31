# Wobble mode — EdgeTX stimulus generator for PID tuning

`wobble.lua` is an EdgeTX **custom mixer script** that injects a controlled,
repeatable disturbance on roll and/or pitch. You arm it with a switch, fly a few
seconds while Blackbox records, then analyse the response. It complements
`scripts/step_response.py` in this repo (inject → log → measure).

Two waveforms:

| Mode  | Signal               | Use for                                            |
|-------|----------------------|----------------------------------------------------|
| STEP  | bipolar square wave  | step-response (overshoot, rise time, settling)     |
| SWEEP | linear sine chirp    | resonances, filter cutoffs, phase margin           |

The script outputs the **disturbance only** — it is added on top of your normal
stick mixes, so you always keep manual control. Switch Arm off to stop instantly.

## Install (Radiomaster Pocket)

1. Copy `wobble.lua` to **`/SCRIPTS/MIXES/`** on the radio SD card
   (keep the name short; `wobble.lua` is fine).
2. On the radio: **Model → "Custom Scripts"** (the LUA page) → add `wobble`.
3. Assign the 4 inputs to your controls:
   - **Arm**  → a switch (e.g. SA). `>0` = active.
   - **Axis** → a 3-position switch (e.g. SB): **low = roll**, **mid = both**, **high = pitch**.
   - **Mode** → a 2-position switch: **down = STEP**, **up = SWEEP**.
   - **Amp**  → amplitude in % (default 15, capped at 35). Edit it here, or wire
     a pot/slider/GVAR as a source if you prefer in-flight adjustment.
   The script exposes two output sources: **Rwob** and **Pwob**.
4. **Model → Mixes**: add a line on each axis channel, on top of the existing mix:
   - Aileron channel:  `+= Rwob`  (weight 100, ADD)
   - Elevator channel: `+= Pwob`  (weight 100, ADD)

> If the Custom Scripts page isn't available, your build lacks Lua mixer-script
> support — re-flash EdgeTX with Lua enabled.

## Tuning the waveform

Edit the `USER CONFIG` block at the top of the script:

- `STEP_HZ`  — square frequency (1.5–3 Hz typical; lower = cleaner single steps).
- `SWEEP_F0`/`SWEEP_F1`/`SWEEP_T` — chirp range and duration. 0.5→12 Hz over 20 s
  covers the band that matters for a 5″; raise `SWEEP_F1` for small/fast quads.
- `AMP_CAP` — hard safety ceiling on amplitude (% of full stick).

## Flight + measurement procedure

1. Betaflight: enable **Blackbox** logging, debug_mode as needed, decent log rate
   (≥1–2 kHz for filter work). Make sure logging is actually running.
2. Hover at a safe altitude. **Amp = 10–15%** to start.
3. **STEP**: Axis = roll, Mode = step, flip Arm on for ~3–5 s, Arm off. Repeat for
   pitch, then both. Land, grab the log.
4. **SWEEP**: same, but Mode = sweep; hold Arm on for one full `SWEEP_T` cycle.
5. Analyse: feed the `.bbl` to `python -m scripts.analyze_blackbox` /
   `scripts.step_response`, or PIDtoolbox / PlasmaTree PID-Analyzer.

## Safety

The quad oscillates on purpose. Start small, test high and clear of people/objects,
keep a finger on the disarm, and flip Arm off the moment anything looks wrong. The
`AMP_CAP` ceiling limits how violent the injection can get even if Amp is maxed.

## Limits

- Mixer-script update rate on a small radio is coarse; the **phase** stays correct
  but high-frequency sweeps are sampled in steps. Fine for tuning, not lab-grade.
- This is a stimulus tool, not an analyser — the measurement happens in Blackbox.
