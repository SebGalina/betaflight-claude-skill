-- wobble.lua  --  EdgeTX MIXER script  (SCRIPTS/MIXES/)
-- v4 — screen-configurable PID stimulus generator
--
-- Configure via the companion wobble_cfg.lua (SCRIPTS/TOOLS/).
-- ONE physical switch required (ARM); a SECOND input (FCArm) is
-- optional and only needed if you want the lockout safety.
--
-- Waveforms:
--   STEP : bipolar square wave  → step-response tests
--   SWEEP: linear sine chirp   → filter / resonance tests
--
-- Axis modes:
--   SEQ  : seq_t s roll, then seq_t s pitch              (2 phases)
--   SEQ+ : seq_t s roll, then pitch, then both           (3 phases)
--   ROLL : roll only
--   PITCH: pitch only
--   BOTH : both simultaneously
--
-- Repeat modes (SEQ / SEQ+ only):
--   ONCE : one full cycle then stops + plays end tone
--   LOOP : repeats until ARM is flipped off
--
-- Safety lockout (requires FCArm input wired):
--   When ON, once a run has happened, the script refuses to re-launch
--   until the FC has been disarmed. A short low buzz signals the block.
--
-- Audio cues:
--   axis switch         : short beep (660 Hz / 150 ms)
--   end of test (ONCE)  : ascending double beep (880 + 1320 Hz)
--   blocked by lockout  : low buzz  (200 Hz double)
--
-- Config is reloaded automatically on every ARM rising edge.

local CFG_FILE = "/SCRIPTS/MIXES/wobble.cfg"
local AMP_CAP  = 35     -- hard safety ceiling regardless of config

-- Defaults (used when no config file exists yet)
local cfg = {
  mode=0, amp=15, step_hz=20, f0=5, f1=12, sweep_t=20,
  axis=0, seq_t=15, repeat_mode=0, safety=1,
}

local phase        = 0.0
local etime        = 0.0
local lastT        = 0
local armed        = false
local test_done    = false   -- set when ONCE cycle completes; cleared on ARM low
local last_phase   = 0       -- last SEQ phase index (0/1/2); for beep detection
local lockout      = false   -- safety lockout (must disarm FC to clear)
local block_beeped = false   -- ensures the lockout buzz plays once per attempt

local function read_cfg()
  local f = io.open(CFG_FILE, "r")
  if not f then return end
  local buf, chunk = "", io.read(f, 512)
  while chunk and #chunk > 0 do buf = buf .. chunk; chunk = io.read(f, 512) end
  io.close(f)
  for line in buf:gmatch("[^\n\r]+") do
    local k, v = line:match("^(%w+)=(%S+)")
    if k and v and cfg[k] ~= nil then cfg[k] = tonumber(v) or cfg[k] end
  end
end

local function init()
  lastT = getTime()
  read_cfg()
end

local function run(arm, fc_arm)
  local now = getTime()
  local dt  = (now - lastT) / 100.0    -- seconds (getTime ticks = 10 ms)
  lastT = now
  if dt < 0 or dt > 0.5 then dt = 0 end

  fc_arm = fc_arm or 0
  local fc_armed = (fc_arm > 0)

  -- FC disarmed → clear the safety lockout
  if not fc_armed then lockout = false end

  if arm > 0 then
    if not armed then
      -- rising edge of wobble switch
      if cfg.safety == 1 and lockout then
        if not block_beeped then
          playTone(200, 300, 100)
          playTone(200, 300,   0)
          block_beeped = true
        end
        return 0, 0
      end
      block_beeped = false
      armed       = true
      etime       = 0.0
      phase       = 0.0
      test_done   = false
      last_phase  = 0
      read_cfg()
    end
    if test_done then return 0, 0 end
    etime = etime + dt
  else
    -- wobble switch off
    if armed and etime > 0.1 and cfg.safety == 1 and fc_armed then
      lockout = true       -- engage lockout: was running, stopped, FC still armed
    end
    armed        = false
    test_done    = false
    block_beeped = false
    return 0, 0
  end

  local amp = math.max(0, math.min(cfg.amp, AMP_CAP))
  local A   = (amp / 100.0) * 1024.0

  -- Waveform -----------------------------------------------------------------
  local w
  if cfg.mode == 1 then
    -- SWEEP: linear chirp F0→F1 over sweep_t, then repeats
    local f0 = cfg.f0 / 10.0
    local st = math.max(cfg.sweep_t, 1)
    local f  = f0 + (cfg.f1 - f0) * ((etime % st) / st)
    phase = phase + 2.0 * math.pi * f * dt
    if phase > 62832 then phase = phase - 62832 end
    w = math.sin(phase)
  else
    -- STEP: bipolar square wave
    local hz = math.max(cfg.step_hz / 10.0, 0.1)
    w = (math.floor(etime * hz * 2) % 2 == 0) and 1.0 or -1.0
  end

  -- Axis routing -------------------------------------------------------------
  local out = A * w
  local r, p = 0, 0
  local ax = cfg.axis
  local n_phases = 0
  if ax == 0 then n_phases = 2 end   -- SEQ  : roll, pitch
  if ax == 1 then n_phases = 3 end   -- SEQ+ : roll, pitch, both

  if n_phases > 0 then
    -- Sequenced mode
    local st    = math.max(cfg.seq_t, 1)
    local cycle = n_phases * st
    local t     = (cfg.repeat_mode == 0) and etime or (etime % cycle)
    local cur_phase = math.floor(t / st)
    if cur_phase >= n_phases then cur_phase = n_phases - 1 end

    -- Beep on phase change
    if cur_phase ~= last_phase then
      last_phase = cur_phase
      playTone(660, 150, 0)
    end

    -- ONCE: stop after one full cycle, play end tone, engage lockout
    if cfg.repeat_mode == 0 and etime >= cycle then
      test_done = true
      if cfg.safety == 1 and fc_armed then lockout = true end
      playTone(880,  300, 50)
      playTone(1320, 600,  0)
      return 0, 0
    end

    if     cur_phase == 0 then r = out
    elseif cur_phase == 1 then p = out
    else                       r = out; p = out   -- phase 2 (SEQ+): both axes
    end
  elseif ax == 2 then       -- ROLL
    r = out
  elseif ax == 3 then       -- PITCH
    p = out
  elseif ax == 4 then       -- BOTH
    r = out; p = out
  end

  return r, p
end

return {
  init   = init,
  run    = run,
  input  = {
    { "Arm",   SOURCE },    -- wobble trigger
    { "FCArm", SOURCE },    -- FC arm state (optional, enables safety lockout)
  },
  output = { "Rwob", "Pwob" },
}
