#!/usr/bin/env python3
"""
blackbox_decoder.py — Pure-Python decoder for Betaflight blackbox logs.

This is a faithful port of the decoding core of the official
blackbox-log-viewer (src/flightlog_parser.js, datastream.js, decoders.js,
tools.js). It parses the ASCII header of each log section into a structured
config + field definitions, and — on demand — decodes the binary frame stream
(I/P/S/G/H/E frames) by applying the field encodings and predictors to
reconstruct the real flight data.

Public API:
    split_sessions(raw)           -> list[(start, end)] byte ranges, one per log
    FlightLogParser(raw, start, end)
        .parse_header()           -> populate sys_config + frame_defs
        .parse_data()             -> decode frames (fills main_frames, stats...)

For the CLI / analysis layer see analyze_blackbox.py.

No dependency on numpy/pandas here — this module is stdlib-only so it can be
reused anywhere. The analysis layer adds numpy/pandas for stats and CSV.
"""

from __future__ import annotations

import math
import re
import struct
from typing import Callable, Optional

EOF = -1

# ---------------------------------------------------------------------------
# Field predictors
# ---------------------------------------------------------------------------
PREDICTOR_0 = 0
PREDICTOR_PREVIOUS = 1
PREDICTOR_STRAIGHT_LINE = 2
PREDICTOR_AVERAGE_2 = 3
PREDICTOR_MINTHROTTLE = 4
PREDICTOR_MOTOR_0 = 5
PREDICTOR_INC = 6
PREDICTOR_HOME_COORD = 7
PREDICTOR_1500 = 8
PREDICTOR_VBATREF = 9
PREDICTOR_LAST_MAIN_FRAME_TIME = 10
PREDICTOR_MINMOTOR = 11
# Second of a GPS home coord pair — rewritten during header finalisation.
PREDICTOR_HOME_COORD_1 = 256

# ---------------------------------------------------------------------------
# Field encodings
# ---------------------------------------------------------------------------
ENCODING_SIGNED_VB = 0
ENCODING_UNSIGNED_VB = 1
ENCODING_NEG_14BIT = 3
ENCODING_TAG8_8SVB = 6
ENCODING_TAG2_3S32 = 7
ENCODING_TAG8_4S16 = 8
ENCODING_NULL = 9
ENCODING_TAG2_3SVARIABLE = 10

# Flight log events
EVENT_SYNC_BEEP = 0
EVENT_AUTOTUNE_CYCLE_START = 10
EVENT_AUTOTUNE_CYCLE_RESULT = 11
EVENT_AUTOTUNE_TARGETS = 12
EVENT_INFLIGHT_ADJUSTMENT = 13
EVENT_LOGGING_RESUME = 14
EVENT_DISARM = 15
EVENT_GTUNE_CYCLE_RESULT = 20
EVENT_FLIGHT_MODE = 30
EVENT_TWITCH_TEST = 40
EVENT_CUSTOM = 250
EVENT_LOG_END = 255

# Main frame field indices (loopIteration is field 0, time is field 1)
FIELD_INDEX_ITERATION = 0
FIELD_INDEX_TIME = 1

FLIGHT_LOG_MAX_FRAME_LENGTH = 256
MAXIMUM_TIME_JUMP_BETWEEN_FRAMES = 10 * 1_000_000
MAXIMUM_ITERATION_JUMP_BETWEEN_FRAMES = 500 * 10

START_MARKER = b"H Product:Blackbox flight data recorder by Nicholas Sherlock"


# ---------------------------------------------------------------------------
# Bit helpers (mirror tools.js signExtend* and 32-bit semantics)
# ---------------------------------------------------------------------------
def _sign_extend(value: int, bits: int) -> int:
    """Interpret the low `bits` of value as a two's-complement signed int."""
    mask = 1 << (bits - 1)
    value &= (1 << bits) - 1
    return (value ^ mask) - mask


def _to_s32(value: int) -> int:
    return _sign_extend(value, 32)


# ---------------------------------------------------------------------------
# Data stream (port of datastream.js + decoders.js)
# ---------------------------------------------------------------------------
class DataStream:
    def __init__(self, data: bytes, start: int = 0, end: Optional[int] = None):
        self.data = data
        self.start = start
        self.end = len(data) if end is None else end
        self.pos = start
        self.eof = False

    # --- primitive reads ---------------------------------------------------
    def read_byte(self) -> int:
        if self.pos < self.end:
            b = self.data[self.pos]
            self.pos += 1
            return b
        self.eof = True
        return EOF

    read_u8 = read_byte

    def read_char(self):
        if self.pos < self.end:
            c = self.data[self.pos]
            self.pos += 1
            return chr(c)
        self.eof = True
        return EOF

    def peek_char(self):
        if self.pos < self.end:
            return chr(self.data[self.pos])
        self.eof = True
        return EOF

    def unread_char(self) -> None:
        self.pos -= 1

    def read_s8(self) -> int:
        return _sign_extend(self.read_byte(), 8)

    def read_s16(self) -> int:
        b1 = self.read_byte()
        b2 = self.read_byte()
        return _sign_extend(b1 | (b2 << 8), 16)

    def read_u16(self) -> int:
        b1 = self.read_byte()
        b2 = self.read_byte()
        return b1 | (b2 << 8)

    def read_u32(self) -> int:
        b1 = self.read_byte()
        b2 = self.read_byte()
        b3 = self.read_byte()
        b4 = self.read_byte()
        return (b1 | (b2 << 8) | (b3 << 16) | (b4 << 24)) & 0xFFFFFFFF

    def read_string(self, length: int) -> str:
        chars = []
        for _ in range(length):
            c = self.read_char()
            if c == EOF:
                break
            chars.append(c)
        return "".join(chars)

    # --- variable byte -----------------------------------------------------
    def read_unsigned_vb(self) -> int:
        result = 0
        shift = 0
        for _ in range(5):  # 5 bytes covers a 32-bit quantity
            b = self.read_byte()
            if b == EOF:
                return 0
            result |= (b & 0x7F) << shift
            if b < 128:
                return result & 0xFFFFFFFF
            shift += 7
        return 0  # VB int too long

    def read_signed_vb(self) -> int:
        unsigned = self.read_unsigned_vb()
        # ZigZag decode (Python's arbitrary precision yields the right sign)
        return (unsigned >> 1) ^ -(unsigned & 1)

    # --- tag decoders (port of decoders.js) --------------------------------
    def read_tag2_3s32(self, values: list) -> None:
        lead = self.read_byte()
        selector = lead >> 6
        if selector == 0:  # 2-bit fields
            values[0] = _sign_extend((lead >> 4) & 0x03, 2)
            values[1] = _sign_extend((lead >> 2) & 0x03, 2)
            values[2] = _sign_extend(lead & 0x03, 2)
        elif selector == 1:  # 4-bit fields
            values[0] = _sign_extend(lead & 0x0F, 4)
            lead = self.read_byte()
            values[1] = _sign_extend(lead >> 4, 4)
            values[2] = _sign_extend(lead & 0x0F, 4)
        elif selector == 2:  # 6-bit fields
            values[0] = _sign_extend(lead & 0x3F, 6)
            lead = self.read_byte()
            values[1] = _sign_extend(lead & 0x3F, 6)
            lead = self.read_byte()
            values[2] = _sign_extend(lead & 0x3F, 6)
        else:  # selector == 3: per-field 8/16/24/32-bit
            for i in range(3):
                size_sel = lead & 0x03
                if size_sel == 0:  # 8-bit
                    values[i] = _sign_extend(self.read_byte(), 8)
                elif size_sel == 1:  # 16-bit
                    b1 = self.read_byte()
                    b2 = self.read_byte()
                    values[i] = _sign_extend(b1 | (b2 << 8), 16)
                elif size_sel == 2:  # 24-bit
                    b1 = self.read_byte()
                    b2 = self.read_byte()
                    b3 = self.read_byte()
                    values[i] = _sign_extend(b1 | (b2 << 8) | (b3 << 16), 24)
                else:  # 32-bit
                    b1 = self.read_byte()
                    b2 = self.read_byte()
                    b3 = self.read_byte()
                    b4 = self.read_byte()
                    values[i] = _to_s32(b1 | (b2 << 8) | (b3 << 16) | (b4 << 24))
                lead >>= 2

    def read_tag2_3s_variable(self, values: list) -> None:
        lead = self.read_byte()
        selector = lead >> 6
        if selector == 0:  # 2 bits per field
            values[0] = _sign_extend((lead >> 4) & 0x03, 2)
            values[1] = _sign_extend((lead >> 2) & 0x03, 2)
            values[2] = _sign_extend(lead & 0x03, 2)
        elif selector == 1:  # 5,5,4 bits per field
            values[0] = _sign_extend((lead & 0x3E) >> 1, 5)
            lead2 = self.read_byte()
            values[1] = _sign_extend(((lead & 0x01) << 5) | ((lead2 & 0x0F) >> 4), 5)
            values[2] = _sign_extend(lead2 & 0x0F, 4)
        elif selector == 2:  # 8,7,7 bits per field
            lead2 = self.read_byte()
            values[0] = _sign_extend(((lead & 0x3F) << 2) | ((lead2 & 0xC0) >> 6), 8)
            lead3 = self.read_byte()
            values[1] = _sign_extend(((lead2 & 0x3F) << 1) | ((lead2 & 0x80) >> 7), 7)
            values[2] = _sign_extend(lead3 & 0x7F, 7)
        else:  # selector == 3: per-field 8/16/24/32-bit
            for i in range(3):
                size_sel = lead & 0x03
                if size_sel == 0:
                    values[i] = _sign_extend(self.read_byte(), 8)
                elif size_sel == 1:
                    b1 = self.read_byte()
                    b2 = self.read_byte()
                    values[i] = _sign_extend(b1 | (b2 << 8), 16)
                elif size_sel == 2:
                    b1 = self.read_byte()
                    b2 = self.read_byte()
                    b3 = self.read_byte()
                    values[i] = _sign_extend(b1 | (b2 << 8) | (b3 << 16), 24)
                else:
                    b1 = self.read_byte()
                    b2 = self.read_byte()
                    b3 = self.read_byte()
                    b4 = self.read_byte()
                    values[i] = _to_s32(b1 | (b2 << 8) | (b3 << 16) | (b4 << 24))
                lead >>= 2

    def read_tag8_4s16_v1(self, values: list) -> None:
        selector = self.read_byte()
        i = 0
        while i < 4:
            field = selector & 0x03
            if field == 0:  # zero
                values[i] = 0
            elif field == 1:  # two 4-bit fields
                combined = self.read_byte()
                values[i] = _sign_extend(combined & 0x0F, 4)
                i += 1
                selector >>= 2
                values[i] = _sign_extend(combined >> 4, 4)
            elif field == 2:  # 8-bit
                values[i] = _sign_extend(self.read_byte(), 8)
            else:  # 16-bit
                b1 = self.read_byte()
                b2 = self.read_byte()
                values[i] = _sign_extend(b1 | (b2 << 8), 16)
            selector >>= 2
            i += 1

    def read_tag8_4s16_v2(self, values: list) -> None:
        selector = self.read_byte()
        nibble_index = 0
        buffer = 0
        for i in range(4):
            field = selector & 0x03
            if field == 0:  # zero
                values[i] = 0
            elif field == 1:  # 4-bit
                if nibble_index == 0:
                    buffer = self.read_byte()
                    values[i] = _sign_extend(buffer >> 4, 4)
                    nibble_index = 1
                else:
                    values[i] = _sign_extend(buffer & 0x0F, 4)
                    nibble_index = 0
            elif field == 2:  # 8-bit
                if nibble_index == 0:
                    values[i] = _sign_extend(self.read_byte(), 8)
                else:
                    char1 = (buffer & 0x0F) << 4
                    buffer = self.read_byte()
                    char1 |= buffer >> 4
                    values[i] = _sign_extend(char1, 8)
            else:  # 16-bit
                if nibble_index == 0:
                    char1 = self.read_byte()
                    char2 = self.read_byte()
                    values[i] = _sign_extend((char1 << 8) | char2, 16)
                else:
                    char1 = self.read_byte()
                    char2 = self.read_byte()
                    values[i] = _sign_extend(
                        ((buffer & 0x0F) << 12) | (char1 << 4) | (char2 >> 4), 16
                    )
                    buffer = char2
            selector >>= 2

    def read_tag8_8svb(self, values: list, value_count: int) -> None:
        if value_count == 1:
            values[0] = self.read_signed_vb()
        else:
            header = self.read_byte()
            for i in range(8):
                values[i] = self.read_signed_vb() if (header & 0x01) else 0
                header >>= 1


# ---------------------------------------------------------------------------
# Header parsing helpers
# ---------------------------------------------------------------------------
def _parse_int_list(value: str) -> list:
    out = []
    for part in value.split(","):
        part = part.strip()
        try:
            out.append(int(part))
        except ValueError:
            try:
                out.append(float(part))
            except ValueError:
                out.append(part)
    return out


def _maybe_number(value: str):
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def _hex_to_float(s: str) -> float:
    try:
        return struct.unpack("<f", struct.pack("<I", int(s, 16) & 0xFFFFFFFF))[0]
    except (ValueError, struct.error):
        return 0.0


# Legacy header name -> canonical sys_config key (port of translationValues).
# Lets the parser accept old/new firmware header spellings transparently.
_TRANSLATION = {
    "acc_limit_yaw": "yawRateAccelLimit",
    "accel_limit": "rateAccelLimit",
    "acc_limit": "rateAccelLimit",
    "anti_gravity_thresh": "anti_gravity_threshold",
    "currentSensor": "currentMeter",
    "d_notch_cut": "dterm_notch_cutoff",
    "d_setpoint_weight": "dtermSetpointWeight",
    "dterm_lowpass_hz": "dterm_lpf_hz",
    "dterm_lowpass_dyn_hz": "dterm_lpf_dyn_hz",
    "dterm_lowpass2_hz": "dterm_lpf2_hz",
    "dterm_lpf1_type": "dterm_filter_type",
    "dterm_lpf1_static_hz": "dterm_lpf_hz",
    "dterm_lpf1_dyn_hz": "dterm_lpf_dyn_hz",
    "dterm_lpf1_dyn_expo": "dterm_lpf_dyn_expo",
    "dterm_lpf2_type": "dterm_filter2_type",
    "dterm_lpf2_static_hz": "dterm_lpf2_hz",
    "dterm_setpoint_weight": "dtermSetpointWeight",
    "digital_idle_value": "digitalIdleOffset",
    "dshot_idle_value": "digitalIdleOffset",
    "dyn_idle_min_rpm": "dynamic_idle_min_rpm",
    "feedforward_transition": "ff_transition",
    "feedforward_averaging": "ff_averaging",
    "feedforward_smooth_factor": "ff_smooth_factor",
    "feedforward_jitter_factor": "ff_jitter_factor",
    "feedforward_boost": "ff_boost",
    "feedforward_max_rate_limit": "ff_max_rate_limit",
    "feedforward_weight": "dtermSetpointWeight",
    "gyro_hardware_lpf": "gyro_lpf",
    "gyro_lowpass": "gyro_lowpass_hz",
    "gyro_lowpass_type": "gyro_soft_type",
    "gyro_lowpass2_type": "gyro_soft2_type",
    "gyro_lpf1_type": "gyro_soft_type",
    "gyro_lpf1_static_hz": "gyro_lowpass_hz",
    "gyro_lpf1_dyn_hz": "gyro_lowpass_dyn_hz",
    "gyro_lpf1_dyn_expo": "gyro_lowpass_dyn_expo",
    "gyro_lpf2_type": "gyro_soft2_type",
    "gyro_lpf2_static_hz": "gyro_lowpass2_hz",
    "gyro.scale": "gyro_scale",
    "iterm_windup": "itermWindupPointPercent",
    "motor_pwm_protocol": "fast_pwm_protocol",
    "pid_at_min_throttle": "pidAtMinThrottle",
    "pidsum_limit": "pidSumLimit",
    "pidsum_limit_yaw": "pidSumLimitYaw",
    "rc_expo_yaw": "rcYawExpo",
    "rc_interp": "rc_interpolation",
    "rc_interp_int": "rc_interpolation_interval",
    "rc_rate": "rc_rates",
    "rc_rate_yaw": "rcYawRate",
    "rc_yaw_expo": "rcYawExpo",
    "rcExpo": "rc_expo",
    "rcRate": "rc_rates",
    "rpm_filter_harmonics": "gyro_rpm_notch_harmonics",
    "rpm_filter_q": "gyro_rpm_notch_q",
    "rpm_filter_min_hz": "gyro_rpm_notch_min",
    "rpm_filter_lpf_hz": "rpm_notch_lpf",
    "thr_expo": "thrExpo",
    "thr_mid": "thrMid",
    "dynThrPID": "tpa_rate",
    "use_unsynced_pwm": "unsynced_fast_pwm",
    "vbat_scale": "vbatscale",
    "vbat_pid_gain": "vbat_pid_compensation",
    "yaw_accel_limit": "yawRateAccelLimit",
    "yaw_lowpass_hz": "yaw_lpf_hz",
}


def split_sessions(raw: bytes) -> list[tuple[int, int]]:
    """Return (start, end) byte ranges, one per concatenated log section."""
    starts = []
    idx = raw.find(START_MARKER)
    while idx != -1:
        starts.append(idx)
        idx = raw.find(START_MARKER, idx + 1)
    if not starts:
        return []
    ranges = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(raw)
        ranges.append((start, end))
    return ranges


# ---------------------------------------------------------------------------
# Flight log parser
# ---------------------------------------------------------------------------
class FlightLogParser:
    """Parses a single log section. Call parse_header() then parse_data()."""

    def __init__(self, raw: bytes, start: int = 0, end: Optional[int] = None):
        self.stream = DataStream(raw, start, end)

        # frame_defs[marker] = {name, name_to_index, count, signed, predictor, encoding}
        self.frame_defs: dict[str, dict] = {}
        self.sys_config: dict = {
            "frameIntervalI": 32,
            "frameIntervalPNum": 1,
            "frameIntervalPDenom": 1,
            "minthrottle": 1150,
            "maxthrottle": 1850,
            "vbatref": 4095,
            "vbatscale": 110,
            "vbatmincellvoltage": 330,
            "vbatwarningcellvoltage": 350,
            "vbatmaxcellvoltage": 430,
            "currentMeterOffset": 0,
            "currentMeterScale": 400,
            "gyroScale": 1.0,
            "acc_1G": 2048,
            "motor_poles": 14,
            "motorOutput": [1150, 1850],
            "firmwareType": None,
            "firmwareVersion": None,
        }
        self.unknown_headers: list[dict] = []
        self.data_version: Optional[int] = None

        # Decoded output
        self.main_field_names: list[str] = []
        self.main_frames: list[list] = []  # accepted I + P frames, chronological
        self.gps_frames: list[list] = []
        self.slow_frames: list[list] = []
        self.events: list[dict] = []
        self.stats = {
            "total_bytes": 0,
            "total_corrupt_frames": 0,
            "frame": {},  # marker -> {valid, corrupt, desync, bytes}
        }

        # Decode state
        self._prev = None
        self._prev2 = None
        self._cur_main = None
        self._main_valid = False
        self._last_iteration = -1
        self._last_time = -1
        self._last_skipped = 0

        self._gps_home = [None, None]
        self._gps_home_valid = False
        self._last_gps = None
        self._last_slow = None
        self._last_event: Optional[dict] = None
        self._motor0_index: Optional[int] = None

    # --- header parsing ----------------------------------------------------
    def _ensure_frame_def(self, marker: str) -> dict:
        fd = self.frame_defs.get(marker)
        if fd is None:
            fd = {
                "name": [],
                "name_to_index": {},
                "count": 0,
                "signed": [],
                "predictor": [],
                "encoding": [],
            }
            self.frame_defs[marker] = fd
        return fd

    def _parse_field_definition(self, field_name: str, value: str) -> bool:
        m = re.match(r"^Field (.) (.+)$", field_name)
        if not m:
            return False
        marker, info = m.group(1), m.group(2)
        fd = self._ensure_frame_def(marker)
        if info == "predictor":
            fd["predictor"] = _parse_int_list(value)
        elif info == "encoding":
            fd["encoding"] = _parse_int_list(value)
        elif info == "name":
            names = value.split(",")
            # legacy gyroData -> gyroADC
            names = [re.sub(r"^gyroData", "gyroADC", n) for n in names]
            fd["name"] = names
            fd["count"] = len(names)
            fd["name_to_index"] = {n: i for i, n in enumerate(names)}
            if len(fd["signed"]) < fd["count"]:
                fd["signed"] = fd["signed"] + [0] * (fd["count"] - len(fd["signed"]))
        elif info == "signed":
            fd["signed"] = _parse_int_list(value)
        else:
            return False
        return True

    def _store_header(self, name: str, value: str) -> None:
        # Decoder-critical fields get typed handling; everything else is
        # stored generically so that "all headers" are available.
        if name == "I interval":
            iv = int(value)
            self.sys_config["frameIntervalI"] = max(1, iv)
        elif name == "P interval":
            if "/" in value:
                num, denom = value.split("/", 1)
                self.sys_config["frameIntervalPNum"] = int(num)
                self.sys_config["frameIntervalPDenom"] = int(denom)
            else:
                self.sys_config["frameIntervalPNum"] = 1
                self.sys_config["frameIntervalPDenom"] = int(value)
        elif name == "P denom" or name == "P ratio":
            pass
        elif name == "Data version":
            self.data_version = int(value)
            self.sys_config["dataVersion"] = self.data_version
        elif name == "Firmware revision":
            self._parse_firmware_revision(value)
            self.sys_config[name] = value
        elif name == "minthrottle":
            self.sys_config["minthrottle"] = int(value)
            self.sys_config["motorOutput"][0] = int(value)
        elif name == "maxthrottle":
            self.sys_config["maxthrottle"] = int(value)
            self.sys_config["motorOutput"][1] = int(value)
        elif name == "vbatref":
            self.sys_config["vbatref"] = int(value)
        elif name == "motorOutput":
            vals = _parse_int_list(value)
            if len(vals) >= 2:
                self.sys_config["motorOutput"] = [vals[0], vals[1]]
        elif name == "gyro_scale":
            # Match flightlog.js: store the firmware-style scale so that
            # gyroRawToDegreesPerSecond reproduces degrees/second.
            scale = _hex_to_float(value)
            ftype = self.sys_config.get("firmwareType")
            if ftype in ("betaflight", "raceflight", "butterflight", "cleanflight", "inav"):
                scale *= (math.pi / 180) * 0.000001
            self.sys_config["gyroScale"] = scale
        elif name == "vbatcellvoltage":
            vals = _parse_int_list(value)
            if len(vals) >= 3:
                self.sys_config["vbatmincellvoltage"] = vals[0]
                self.sys_config["vbatwarningcellvoltage"] = vals[1]
                self.sys_config["vbatmaxcellvoltage"] = vals[2]
        elif name == "currentMeter":
            vals = _parse_int_list(value)
            if len(vals) >= 2:
                self.sys_config["currentMeterOffset"] = vals[0]
                self.sys_config["currentMeterScale"] = vals[1]
        else:
            # Generic: keep raw, plus a numeric/csv interpretation when useful.
            if "," in value:
                self.sys_config[name] = _parse_int_list(value)
            else:
                self.sys_config[name] = _maybe_number(value)

    def _parse_firmware_revision(self, value: str) -> None:
        m = re.search(
            r"((?:Beta|Race|Clean|Base|Butter)flight)\s+(\d+)\.(\d+)(?:\.(\d+))?",
            value, re.IGNORECASE,
        )
        if m:
            self.sys_config["firmwareType"] = m.group(1).lower()
            patch = m.group(4) if m.group(4) else "0"
            self.sys_config["firmwareVersion"] = f"{int(m.group(2))}.{int(m.group(3))}.{patch}"
            return
        m = re.search(r"(INAV).* (\d+)\.(\d+)(?:\.(\d+))?", value, re.IGNORECASE)
        if m:
            self.sys_config["firmwareType"] = "inav"
            patch = m.group(4) if m.group(4) else "0"
            self.sys_config["firmwareVersion"] = f"{int(m.group(2))}.{int(m.group(3))}.{patch}"
            return
        self.sys_config["firmwareVersion"] = "0.0.0"

    def _parse_header_line(self) -> None:
        stream = self.stream
        if stream.peek_char() != " ":
            return
        stream.read_char()  # skip leading space
        line_start = stream.pos
        separator = -1
        while stream.pos < line_start + 1024 and stream.pos < stream.end:
            b = stream.data[stream.pos]
            if separator == -1 and b == 0x3A:  # ':'
                separator = stream.pos
            if b == 0x0A or b == 0:  # newline or NUL
                break
            stream.pos += 1
        if stream.pos >= stream.end or stream.data[stream.pos] != 0x0A or separator == -1:
            return
        line_end = stream.pos
        field_name = stream.data[line_start:separator].decode("latin-1")
        field_value = stream.data[separator + 1:line_end].decode("latin-1")
        field_name = _TRANSLATION.get(field_name, field_name)

        if not self._parse_field_definition(field_name, field_value):
            # Not a "Field X ..." line -> a config header
            self._store_header(field_name, field_value)
            if field_name not in self.sys_config and field_name not in (
                "P denom", "P ratio"
            ):
                self.unknown_headers.append({"name": field_name, "value": field_value})

    def parse_header(self) -> None:
        stream = self.stream
        while True:
            command = stream.read_char()
            if command == "H":
                self._parse_header_line()
            elif command == EOF:
                break
            else:
                if command in _FRAME_MARKERS:
                    stream.unread_char()
                    break
                # else skip garbage before the first frame

        # Finalise frame definitions
        if not self._frame_def_complete(self.frame_defs.get("I")):
            raise ValueError("Log is missing required I-frame definitions (header may be corrupt)")
        if "P" not in self.frame_defs:
            raise ValueError("Log is missing required P-frame definitions")

        # P frames inherit field layout from I frames
        i_def = self.frame_defs["I"]
        p_def = self.frame_defs["P"]
        p_def["count"] = i_def["count"]
        p_def["name"] = i_def["name"]
        p_def["name_to_index"] = i_def["name_to_index"]
        p_def["signed"] = i_def["signed"]
        if not self._frame_def_complete(p_def):
            raise ValueError("Log is missing required P-frame definitions")

        self.main_field_names = list(i_def["name"])
        self._motor0_index = i_def["name_to_index"].get("motor[0]")

        # Rewrite the second of each GPS home-coord predictor pair
        g_def = self.frame_defs.get("G")
        if self.frame_defs.get("H") and g_def:
            pred = g_def["predictor"]
            for i in range(1, g_def["count"]):
                if pred[i - 1] == PREDICTOR_HOME_COORD and pred[i] == PREDICTOR_HOME_COORD:
                    pred[i] = PREDICTOR_HOME_COORD_1
            self._gps_home = [
                [0] * self.frame_defs["H"]["count"],
                [0] * self.frame_defs["H"]["count"],
            ]
            self._last_gps = [0] * g_def["count"]
        if self.frame_defs.get("S"):
            self._last_slow = [0] * self.frame_defs["S"]["count"]

    @staticmethod
    def _frame_def_complete(fd: Optional[dict]) -> bool:
        return bool(
            fd
            and fd["count"] > 0
            and len(fd["encoding"]) == fd["count"]
            and len(fd["predictor"]) == fd["count"]
        )

    # --- frame decoding ----------------------------------------------------
    def _apply_prediction(self, field_index, predictor, value, current, previous, previous2):
        if predictor == PREDICTOR_0:
            return value
        if predictor == PREDICTOR_MINTHROTTLE:
            return _to_s32(value) + self.sys_config["minthrottle"]
        if predictor == PREDICTOR_MINMOTOR:
            return _to_s32(value) + int(self.sys_config["motorOutput"][0])
        if predictor == PREDICTOR_1500:
            return value + 1500
        if predictor == PREDICTOR_MOTOR_0:
            if self._motor0_index is None:
                raise ValueError("motor[0] prediction before motor[0] was read")
            return value + current[self._motor0_index]
        if predictor == PREDICTOR_VBATREF:
            return value + self.sys_config["vbatref"]
        if predictor == PREDICTOR_PREVIOUS:
            if previous is None:
                return value
            return value + previous[field_index]
        if predictor == PREDICTOR_STRAIGHT_LINE:
            if previous is None:
                return value
            return value + 2 * previous[field_index] - previous2[field_index]
        if predictor == PREDICTOR_AVERAGE_2:
            if previous is None:
                return value
            # round toward zero like C integer division
            return value + int((previous[field_index] + previous2[field_index]) / 2)
        if predictor == PREDICTOR_HOME_COORD:
            if self._gps_home[1] is None:
                return value
            return value + self._gps_home[1][self.frame_defs["H"]["name_to_index"]["GPS_home[0]"]]
        if predictor == PREDICTOR_HOME_COORD_1:
            if self._gps_home[1] is None:
                return value
            return value + self._gps_home[1][self.frame_defs["H"]["name_to_index"]["GPS_home[1]"]]
        if predictor == PREDICTOR_LAST_MAIN_FRAME_TIME:
            if previous is not None:
                return value + previous[FIELD_INDEX_TIME]
            if self._prev is not None:
                return value + self._prev[FIELD_INDEX_TIME]
            return value
        raise ValueError(f"Unsupported field predictor {predictor}")

    def _parse_frame(self, frame_def, current, previous, previous2, skipped_frames, raw):
        predictor = frame_def["predictor"]
        encoding = frame_def["encoding"]
        count = frame_def["count"]
        values = [0] * 8
        stream = self.stream
        i = 0
        while i < count:
            if predictor[i] == PREDICTOR_INC:
                current[i] = skipped_frames + 1
                if previous is not None:
                    current[i] += previous[i]
                i += 1
                continue

            enc = encoding[i]
            if enc == ENCODING_SIGNED_VB:
                value = stream.read_signed_vb()
            elif enc == ENCODING_UNSIGNED_VB:
                value = stream.read_unsigned_vb()
            elif enc == ENCODING_NEG_14BIT:
                value = -_sign_extend(stream.read_unsigned_vb(), 14)
            elif enc == ENCODING_NULL:
                value = 0
            elif enc == ENCODING_TAG8_4S16:
                if self.data_version is not None and self.data_version < 2:
                    stream.read_tag8_4s16_v1(values)
                else:
                    stream.read_tag8_4s16_v2(values)
                for j in range(4):
                    pred = PREDICTOR_0 if raw else predictor[i]
                    current[i] = self._apply_prediction(i, pred, values[j], current, previous, previous2)
                    i += 1
                continue
            elif enc == ENCODING_TAG2_3S32:
                stream.read_tag2_3s32(values)
                for j in range(3):
                    pred = PREDICTOR_0 if raw else predictor[i]
                    current[i] = self._apply_prediction(i, pred, values[j], current, previous, previous2)
                    i += 1
                continue
            elif enc == ENCODING_TAG2_3SVARIABLE:
                stream.read_tag2_3s_variable(values)
                for j in range(3):
                    pred = PREDICTOR_0 if raw else predictor[i]
                    current[i] = self._apply_prediction(i, pred, values[j], current, previous, previous2)
                    i += 1
                continue
            elif enc == ENCODING_TAG8_8SVB:
                group_count = 1
                for j in range(i + 1, min(i + 8, count)):
                    if encoding[j] != ENCODING_TAG8_8SVB:
                        break
                    group_count += 1
                stream.read_tag8_8svb(values, group_count)
                for j in range(group_count):
                    pred = PREDICTOR_0 if raw else predictor[i]
                    current[i] = self._apply_prediction(i, pred, values[j], current, previous, previous2)
                    i += 1
                continue
            else:
                raise ValueError(f"Unsupported field encoding {enc} for field #{i}")

            pred = PREDICTOR_0 if raw else predictor[i]
            current[i] = self._apply_prediction(i, pred, value, current, previous, previous2)
            i += 1

    # --- intentionally skipped frame accounting ----------------------------
    def _should_have_frame(self, frame_index: int) -> bool:
        sc = self.sys_config
        return (
            (frame_index % sc["frameIntervalI"] + sc["frameIntervalPNum"] - 1)
            % sc["frameIntervalPDenom"]
        ) < sc["frameIntervalPNum"]

    def _count_skipped_frames(self) -> int:
        if self._last_iteration == -1:
            return 0
        count = 0
        idx = self._last_iteration + 1
        while not self._should_have_frame(idx):
            count += 1
            idx += 1
        return count

    # --- per-frame-type parse + complete -----------------------------------
    def _parse_intraframe(self, raw):
        count = self.frame_defs["I"]["count"]
        self._cur_main = [0] * count
        self._parse_frame(self.frame_defs["I"], self._cur_main, self._prev, None, 0, raw)

    def _complete_intraframe(self, marker, frame_start, frame_end, raw):
        cur = self._cur_main
        accept = True
        if not raw and self._last_iteration != -1:
            it = cur[FIELD_INDEX_ITERATION]
            tm = cur[FIELD_INDEX_TIME]
            accept = (
                it >= self._last_iteration
                and it < self._last_iteration + MAXIMUM_ITERATION_JUMP_BETWEEN_FRAMES
                and tm >= self._last_time
                and tm < self._last_time + MAXIMUM_TIME_JUMP_BETWEEN_FRAMES
            )
        if accept:
            self._last_iteration = cur[FIELD_INDEX_ITERATION]
            self._last_time = cur[FIELD_INDEX_TIME]
            self._main_valid = True
            self.main_frames.append(cur)
            self._prev = cur
            self._prev2 = cur
        else:
            self._main_valid = False
            self._prev = None
            self._prev2 = None
        return self._main_valid

    def _parse_interframe(self, raw):
        count = self.frame_defs["P"]["count"]
        self._last_skipped = self._count_skipped_frames()
        self._cur_main = [0] * count
        self._parse_frame(
            self.frame_defs["P"], self._cur_main, self._prev, self._prev2, self._last_skipped, raw
        )

    def _complete_interframe(self, marker, frame_start, frame_end, raw):
        cur = self._cur_main
        if self._main_valid and not raw:
            if (
                cur[FIELD_INDEX_TIME] > self._last_time + MAXIMUM_TIME_JUMP_BETWEEN_FRAMES
                or cur[FIELD_INDEX_ITERATION]
                > self._last_iteration + MAXIMUM_ITERATION_JUMP_BETWEEN_FRAMES
            ):
                self._main_valid = False
        if self._main_valid:
            self._last_iteration = cur[FIELD_INDEX_ITERATION]
            self._last_time = cur[FIELD_INDEX_TIME]
            self.main_frames.append(cur)
            self._prev2 = self._prev
            self._prev = cur
        return self._main_valid

    def _parse_gps_frame(self, raw):
        if self.frame_defs.get("G"):
            self._parse_frame(self.frame_defs["G"], self._last_gps, None, None, 0, raw)

    def _complete_gps_frame(self, marker, frame_start, frame_end, raw):
        if self._gps_home_valid:
            self.gps_frames.append(list(self._last_gps))
        return self._gps_home_valid

    def _parse_gps_home_frame(self, raw):
        if self.frame_defs.get("H"):
            self._parse_frame(self.frame_defs["H"], self._gps_home[0], None, None, 0, raw)

    def _complete_gps_home_frame(self, marker, frame_start, frame_end, raw):
        self._gps_home[1] = list(self._gps_home[0])
        self._gps_home_valid = True
        return True

    def _parse_slow_frame(self, raw):
        if self.frame_defs.get("S"):
            self._parse_frame(self.frame_defs["S"], self._last_slow, None, None, 0, raw)

    def _complete_slow_frame(self, marker, frame_start, frame_end, raw):
        self.slow_frames.append(list(self._last_slow))
        return True

    def _parse_event_frame(self, raw):
        stream = self.stream
        event_type = stream.read_byte()
        event = {"event": event_type, "data": {}}
        self._last_event = event

        if event_type == EVENT_SYNC_BEEP:
            event["data"]["time"] = stream.read_unsigned_vb()
        elif event_type == EVENT_FLIGHT_MODE:
            event["data"]["newFlags"] = stream.read_unsigned_vb()
            event["data"]["lastFlags"] = stream.read_unsigned_vb()
        elif event_type == EVENT_DISARM:
            event["data"]["reason"] = stream.read_unsigned_vb()
        elif event_type == EVENT_LOGGING_RESUME:
            event["data"]["logIteration"] = stream.read_unsigned_vb()
            event["data"]["currentTime"] = stream.read_unsigned_vb()
        elif event_type == EVENT_LOG_END:
            end_message = stream.read_string(len("End of log\0"))
            if end_message == "End of log\0":
                stream.end = stream.pos  # stop reading, log is done
            else:
                self._last_event = None
        else:
            # Unknown / unsupported event payload: don't trust the byte stream
            self._last_event = None

    def _complete_event_frame(self, marker, frame_start, frame_end, raw):
        if self._last_event is None:
            return False
        if self._last_event["event"] == EVENT_LOGGING_RESUME:
            self._last_iteration = self._last_event["data"]["logIteration"]
            self._last_time = self._last_event["data"]["currentTime"]
        self.events.append(self._last_event)
        return True

    # --- main loop (port of parseLogData) ----------------------------------
    def parse_data(self, raw: bool = False) -> None:
        stream = self.stream
        # Position at first frame: header parse left us just before it.
        frame_parsers = {
            "I": (self._parse_intraframe, self._complete_intraframe),
            "P": (self._parse_interframe, self._complete_interframe),
            "G": (self._parse_gps_frame, self._complete_gps_frame),
            "H": (self._parse_gps_home_frame, self._complete_gps_home_frame),
            "S": (self._parse_slow_frame, self._complete_slow_frame),
            "E": (self._parse_event_frame, self._complete_event_frame),
        }

        self._main_valid = False
        last_frame_type = None
        last_marker = None
        frame_start = 0
        premature_eof = False
        data_start = stream.pos

        while True:
            command = stream.read_char()

            if last_frame_type is not None:
                last_frame_size = stream.pos - frame_start
                looks_completed = (command in frame_parsers) or (
                    not premature_eof and command == EOF
                )
                fstats = self.stats["frame"].setdefault(
                    last_marker,
                    {"valid": 0, "corrupt": 0, "desync": 0, "bytes": 0},
                )
                if last_frame_size <= FLIGHT_LOG_MAX_FRAME_LENGTH and looks_completed:
                    complete_fn = last_frame_type[1]
                    accepted = complete_fn(last_marker, frame_start, stream.pos, raw)
                    if accepted:
                        fstats["bytes"] += last_frame_size
                        fstats["valid"] += 1
                    else:
                        fstats["desync"] += 1
                else:
                    self._main_valid = False
                    fstats["corrupt"] += 1
                    self.stats["total_corrupt_frames"] += 1
                    # Resync: restart search just after the corrupt frame start
                    stream.pos = frame_start + 1
                    last_frame_type = None
                    last_marker = None
                    premature_eof = False
                    stream.eof = False
                    continue

            if command == EOF:
                break

            frame_start = stream.pos - 1
            ft = frame_parsers.get(command)
            if ft and (command == "E" or command in self.frame_defs):
                last_frame_type = ft
                last_marker = command
                ft[0](raw)  # parse
                if stream.eof:
                    premature_eof = True
            else:
                self._main_valid = False
                last_frame_type = None
                last_marker = None

        self.stats["total_bytes"] += stream.end - data_start


_FRAME_MARKERS = frozenset({"I", "P", "G", "H", "S", "E"})
