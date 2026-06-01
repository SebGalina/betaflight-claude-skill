-- wobble_cfg.lua  --  EdgeTX TOOL script  (SCRIPTS/TOOLS/)
-- v2 — configuration UI for wobble.lua
--
-- Install: copy to /SCRIPTS/TOOLS/ on the radio SD card.
-- Open via: SYS > Tools > wobble_cfg
--
-- Navigation (B&W and colour screens):
--   +  / encoder right  → scroll down / increase value
--   −  / encoder left   → scroll up   / decrease value
--   [ENTER]             → enter edit mode on selected row
--   [ENTER] (in edit)   → confirm new value
--   [EXIT]  (in edit)   → cancel change, restore saved value
--   [EXIT]  (browse)    → save config and quit
--
-- Config is saved to /SCRIPTS/MIXES/wobble.cfg.
-- wobble.lua reloads it on every ARM rising edge.

local CFG_FILE = "/SCRIPTS/MIXES/wobble.cfg"

-- ── Formatting helpers ────────────────────────────────────────────────────────
local function fmtMode(v)   return (v == 0) and "STEP" or "SWEEP" end
local function fmtAxis(v)   return ({"SEQ","SEQ+","ROLL","PITCH","BOTH"})[v + 1] or "?" end
local function fmtRepeat(v) return (v == 0) and "ONCE" or "LOOP" end
local function fmtSafety(v) return (v == 0) and "OFF"  or "ON"   end
local function fmtHz10(v)   return string.format("%.1f Hz", v / 10) end
local function fmtHz(v)     return string.format("%d Hz",   v)       end
local function fmtSec(v)    return string.format("%d s",    v)       end
local function fmtPct(v)    return string.format("%d %%",   v)       end

-- ── Parameter table ───────────────────────────────────────────────────────────
-- { label, cfg_key, min, max, step, format_fn }
local PARAMS = {
  { "Mode",    "mode",        0,   1,  1,  fmtMode   },
  { "Axis",    "axis",        0,   4,  1,  fmtAxis   },  -- SEQ/SEQ+/ROLL/PITCH/BOTH
  { "Repeat",  "repeat_mode", 0,   1,  1,  fmtRepeat },  -- ONCE/LOOP (SEQ* only)
  { "Safety",  "safety",      0,   1,  1,  fmtSafety },  -- FC-arm lockout
  { "Amp",     "amp",         5,  35,  1,  fmtPct    },
  { "Step Hz", "step_hz",    10,  50,  5,  fmtHz10   },  -- stored *10: 10=1.0Hz
  { "Swp F0",  "f0",          1,  50,  1,  fmtHz10   },  -- stored *10: 1=0.1Hz
  { "Swp F1",  "f1",          5,  50,  1,  fmtHz     },
  { "Swp T",   "sweep_t",     5,  60,  5,  fmtSec    },
  { "Seq T",   "seq_t",       5,  30,  5,  fmtSec    },  -- seconds per axis in SEQ*
}

-- ── State ─────────────────────────────────────────────────────────────────────
local cfg      = {
  mode=0, amp=15, step_hz=20, f0=5, f1=12, sweep_t=20,
  axis=0, seq_t=15, repeat_mode=0, safety=1,
}
local saved    = {}   -- snapshot before edit (for cancel)
local cursor   = 1    -- selected row (1-based)
local scroll   = 1    -- first visible row index
local editing  = false
local exit_next = false

local VISIBLE  = 5    -- rows shown at once
local LH       = 8    -- line height in pixels
local Y_TITLE  = 0
local Y_DATA   = 9    -- first data row
local Y_HINT   = 57
local X_LABEL  = 2
local X_VALUE  = 70
local X_SCROLL = 126

-- ── File I/O ──────────────────────────────────────────────────────────────────
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

local function write_cfg()
  local f = io.open(CFG_FILE, "w")
  if not f then return end
  for _, p in ipairs(PARAMS) do
    io.write(f, p[2] .. "=" .. tostring(cfg[p[2]]) .. "\n")
  end
  io.close(f)
end

-- ── Init ──────────────────────────────────────────────────────────────────────
local function init()
  read_cfg()
end

-- ── Run (called every ~50 ms by EdgeTX) ───────────────────────────────────────
local function run(event)
  -- Deferred exit: draw "SAVED" for one frame then leave
  if exit_next then return 1 end

  lcd.clear()

  -- Title
  lcd.drawText(X_LABEL, Y_TITLE, "WOBBLE CFG", BOLD)

  -- Scroll window
  if cursor < scroll            then scroll = cursor end
  if cursor >= scroll + VISIBLE then scroll = cursor - VISIBLE + 1 end

  for i = 0, VISIBLE - 1 do
    local idx = scroll + i
    if idx > #PARAMS then break end
    local p   = PARAMS[idx]
    local y   = Y_DATA + i * LH
    local sel = (idx == cursor)

    -- Label: highlighted when selected
    lcd.drawText(X_LABEL, y, p[1], sel and INVERS or 0)

    -- Value: blink when editing
    local vflg = sel and (editing and (BLINK + INVERS) or INVERS) or 0
    lcd.drawText(X_VALUE, y, p[6](cfg[p[2]]), vflg)
  end

  -- Scrollbar (only if list doesn't fit)
  if #PARAMS > VISIBLE then
    local total_h = VISIBLE * LH
    local bar_h   = math.max(2, math.floor(total_h * VISIBLE / #PARAMS))
    local bar_y   = Y_DATA + math.floor(total_h * (scroll - 1) / #PARAMS)
    lcd.drawFilledRectangle(X_SCROLL, bar_y, 2, bar_h)
  end

  -- Bottom hint
  local hint = editing
    and "+/- change   ENT confirm   EXIT cancel"
    or  "+/- scroll   ENT edit   EXIT save+quit"
  lcd.drawText(0, Y_HINT, hint, SMLSIZE)

  -- ── Event handling ──────────────────────────────────────────────────────────
  if editing then
    local p = PARAMS[cursor]
    if event == EVT_PLUS_BREAK or event == EVT_ROT_RIGHT then
      cfg[p[2]] = math.min(cfg[p[2]] + p[5], p[4])
    elseif event == EVT_MINUS_BREAK or event == EVT_ROT_LEFT then
      cfg[p[2]] = math.max(cfg[p[2]] - p[5], p[3])
    elseif event == EVT_ENTER_BREAK then
      editing = false           -- confirm
    elseif event == EVT_EXIT_BREAK then
      cfg = saved               -- cancel: restore snapshot
      editing = false
    end
  else
    if event == EVT_PLUS_BREAK or event == EVT_ROT_RIGHT then
      cursor = math.min(cursor + 1, #PARAMS)
    elseif event == EVT_MINUS_BREAK or event == EVT_ROT_LEFT then
      cursor = math.max(cursor - 1, 1)
    elseif event == EVT_ENTER_BREAK then
      -- snapshot current config before edit so EXIT can cancel
      saved = {}; for k, v in pairs(cfg) do saved[k] = v end
      editing = true
    elseif event == EVT_EXIT_BREAK then
      write_cfg()
      -- show "SAVED" for one frame then exit
      lcd.clear()
      lcd.drawText(X_LABEL, Y_TITLE, "WOBBLE CFG", BOLD)
      lcd.drawText(40, 28, "SAVED", BOLD)
      exit_next = true
    end
  end

  return 0
end

return { init = init, run = run }
