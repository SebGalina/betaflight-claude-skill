# Wobble mode — EdgeTX stimulus generator for PID tuning

Two files work together:

| File | Location on radio | Role |
|------|------------------|------|
| `wobble.lua` | `/SCRIPTS/MIXES/` | Mixer script — generates the disturbance channels |
| `wobble_cfg.lua` | `/SCRIPTS/TOOLS/` | Config tool — on-screen parameter editor |

`wobble.lua` outputs the **disturbance only** — added on top of your normal stick mixes,
so you keep full manual control at all times. Flip the ARM switch off to stop instantly.

Two waveforms:

| Mode  | Signal               | Use for                                            |
|-------|----------------------|----------------------------------------------------|
| STEP  | bipolar square wave  | step-response (overshoot, rise time, settling)     |
| SWEEP | linear sine chirp    | resonances, filter cutoffs, phase margin           |

Five axis modes:

| Axis mode | Behaviour |
|-----------|-----------|
| SEQ  | `Seq T` s roll, then pitch (default 15 + 15 = **30 s**) |
| SEQ+ | `Seq T` s roll, then pitch, then both (default 15 + 15 + 15 = **45 s**) |
| ROLL | roll only |
| PITCH | pitch only |
| BOTH | both axes simultaneously |

Two repeat modes (SEQ / SEQ+ only):

| Repeat | Behaviour |
|--------|-----------|
| ONCE | one full cycle then stops automatically (ARM switch can still cut it early) |
| LOOP | repeats until ARM is flipped off |

Safety lockout (requires the `FCArm` input wired):

| Safety | Behaviour |
|--------|-----------|
| ON | once a run has happened, the script refuses to re-launch until the FC has been disarmed — forces a land + disarm cycle before changing BF params |
| OFF | wobble can be re-triggered at will (legacy behaviour) |

Audio cues:
- **axis switch** (any SEQ/SEQ+ phase change): short beep 660 Hz / 150 ms
- **end of test** (ONCE complete): ascending double beep 880 Hz then 1320 Hz
- **blocked by lockout** (Safety ON, FC still armed): low double buzz 200 Hz

## Install

1. Copy `wobble.lua` to **`/SCRIPTS/MIXES/`** on the radio SD card.
2. Copy `wobble_cfg.lua` to **`/SCRIPTS/TOOLS/`** on the radio SD card.
3. On the radio: **Model → Custom Scripts** → add `wobble`.
4. Assign the inputs:
   - **Arm** → a switch (e.g. SA). `> 0` = wobble active.
   - **FCArm** → the source that reflects your FC arm state — typically the same
     switch / channel you mapped to ARM in Betaflight (e.g. SF, or the AUX channel
     used in Modes). Leave unassigned to disable the safety lockout entirely.
5. **Model → Mixes**: add a line on each axis channel, on top of the existing mix:
   - Aileron channel:  `+= Rwob`  (weight 100, ADD)
   - Elevator channel: `+= Pwob`  (weight 100, ADD)

> If the Custom Scripts page isn't available, your EdgeTX build lacks Lua mixer-script
> support — re-flash with Lua enabled.

## Configure from the radio

Open **SYS → Tools → wobble_cfg** on the radio.

```
WOBBLE CFG
  Mode     STEP          ← STEP or SWEEP
  Axis     SEQ           ← SEQ / SEQ+ / ROLL / PITCH / BOTH
  Repeat   ONCE          ← ONCE (auto-stop) or LOOP   [SEQ* only]
  Safety   ON            ← FC-arm lockout: must disarm to retry
  Amp      15 %          ← amplitude (5–35 %, hard-capped)
  Step Hz  2.0 Hz        ← STEP waveform frequency
  Swp F0   0.5 Hz        ← SWEEP start frequency
  Swp F1   12 Hz         ← SWEEP end frequency
  Swp T    20 s          ← SWEEP chirp duration before repeat
  Seq T    15 s          ← seconds per axis in SEQ / SEQ+ mode
+/- scroll   ENT edit   EXIT save+quit
```

Navigation:

- **`+` / `−`** or encoder — scroll rows (browse) / change value (edit)
- **`[ENTER]`** — enter edit mode on the selected row
- **`[ENTER]`** in edit — confirm the new value
- **`[EXIT]`** in edit — cancel, restore the saved value
- **`[EXIT]`** in browse — **save config and quit**

Config is written to `/SCRIPTS/MIXES/wobble.cfg`. `wobble.lua` picks it up
automatically on every ARM rising edge — no need to reload the model.

## Flight + measurement procedure

1. Betaflight: enable Blackbox, set a decent log rate (≥ 1 kHz for filter work).
2. Configure via wobble_cfg (Amp = 10–15 % to start, Axis = SEQ or SEQ+, Repeat = ONCE).
3. Arm the FC, hover at a safe altitude.
4. **Flip the wobble ARM switch on** — the script runs one full cycle
   (30 s for SEQ, 45 s for SEQ+) and stops itself with the end-of-test tone.
5. **Land + disarm FC** (mandatory when Safety = ON). Change PIDs / filters in
   Betaflight (via the Lua menu through MSP if your link supports it, or USB).
6. Take off again, flip the wobble switch — it picks up the latest config on
   the rising edge. Repeat the iteration.
7. Analyse the log: `python -m scripts.step_response <log.bbl>` or
   `python -m scripts.spectral_analysis <log.bbl>` from this repo, or PIDtoolbox /
   PlasmaTree PID-Analyzer.

## Safety

The quad oscillates on purpose. Start with Amp ≤ 15 %, test at safe altitude above
any people or obstacles, keep a finger on the disarm, and flip ARM off the moment
anything looks wrong. The 35 % `AMP_CAP` ceiling in the mixer script limits severity
even if Amp is maxed in config.

## Limits

- Mixer-script update rate on a small radio is coarse (~50 ms); phase accumulation
  stays continuous, so the waveform is correct, but very high-frequency sweeps are
  stairstepped. Fine for tuning, not lab-grade.
- This is a stimulus tool only — the measurement happens in Blackbox.
