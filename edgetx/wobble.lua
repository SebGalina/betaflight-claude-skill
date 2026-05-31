-- wobble.lua  --  EdgeTX custom mixer script (Radiomaster Pocket & others)
--
-- PID-tuning stimulus generator. Injects a controlled disturbance on roll
-- and/or pitch so the response can be measured in Betaflight Blackbox
-- (step-response, filter/resonance analysis, PIDtoolbox, PID-Analyzer...).
--
-- Waveforms:
--   STEP  : bipolar square wave (repeated step input)  -> step-response tests
--   SWEEP : linear frequency chirp (sine)              -> resonance / phase tests
--
-- IMPORTANT: the output is the DISTURBANCE ONLY. You add it on top of your
-- normal stick mixes (CH Aileron += Rwob, CH Elevator += Pwob), so you keep
-- full manual control at all times. Flip the Arm switch off to stop instantly.
--
-- SAFETY: this makes the quad oscillate on purpose. Start with a SMALL
-- amplitude (10-15%), test at safe altitude, props clear of people, finger
-- ready on the disarm. Increase amplitude only if the response is too weak.

-- ===================== USER CONFIG =====================
local STEP_HZ  = 2.0    -- square-wave frequency, steps per second (1.5-3 Hz typical)
local SWEEP_F0 = 0.5    -- chirp start frequency (Hz)
local SWEEP_F1 = 12.0   -- chirp end frequency (Hz)
local SWEEP_T  = 20.0   -- chirp duration (s) before it repeats
local AMP_CAP  = 35     -- hard safety cap on amplitude (% of full stick)
local AMP_DEF  = 15     -- default amplitude (%)
-- =======================================================

local phase = 0.0   -- accumulated chirp phase (rad), kept continuous
local etime = 0.0   -- seconds elapsed since arming
local lastT = 0     -- last getTime() tick (10 ms units)
local armed = false

local function init()
  lastT = getTime()
end

-- Inputs are passed in the order declared in the return table below.
--   arm   : SOURCE  (a switch)  -> >0 = active, <=0 = off
--   axis  : SOURCE  (3-pos sw)  -> low = roll, mid = both, high = pitch
--   mode  : SOURCE  (2-pos sw)  -> <=0 = STEP, >0 = SWEEP
--   amp   : VALUE   (0..AMP_CAP)-> amplitude in % of full stick
local function run(arm, axis, mode, amp)
  local now = getTime()
  local dt  = (now - lastT) / 100.0   -- seconds since last call
  lastT = now
  if dt < 0 or dt > 0.5 then dt = 0 end   -- guard against time jumps / pauses

  -- Arm handling: reset cleanly on the rising edge so each run starts at t=0.
  if arm > 0 then
    if not armed then armed = true; etime = 0.0; phase = 0.0 end
    etime = etime + dt
  else
    armed = false
    return 0, 0
  end

  -- Amplitude -> output scale (channel full range is +/-1024).
  if amp < 0 then amp = 0 end
  if amp > AMP_CAP then amp = AMP_CAP end
  local A = (amp / 100.0) * 1024.0

  -- Waveform value w in [-1, 1].
  local w
  if mode > 0 then
    -- SWEEP: instantaneous freq sweeps F0->F1 over SWEEP_T, then repeats.
    -- Phase is integrated incrementally so the sine stays continuous
    -- (only the frequency resets, no amplitude glitch).
    local f = SWEEP_F0 + (SWEEP_F1 - SWEEP_F0) * ((etime % SWEEP_T) / SWEEP_T)
    phase = phase + 2.0 * math.pi * f * dt
    if phase > 1e6 then phase = phase % (2.0 * math.pi) end  -- keep it bounded
    w = math.sin(phase)
  else
    -- STEP: bipolar square wave at STEP_HZ.
    local half = 0.5 / STEP_HZ
    w = (math.floor(etime / half) % 2 == 0) and 1 or -1
  end

  local out = A * w

  -- Axis routing from the 3-position switch.
  local r, p = 0, 0
  if axis < -300 then
    r = out                 -- switch LOW  -> roll only
  elseif axis > 300 then
    p = out                 -- switch HIGH -> pitch only
  else
    r = out; p = out        -- switch MID  -> both axes
  end

  return r, p
end

return {
  init  = init,
  run   = run,
  input = {
    { "Arm",  SOURCE },
    { "Axis", SOURCE },
    { "Mode", SOURCE },
    { "Amp",  VALUE, 0, AMP_CAP, AMP_DEF },
  },
  output = { "Rwob", "Pwob" },
}
