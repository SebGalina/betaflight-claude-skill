#!/usr/bin/env python3
"""
blackbox_presenter.py — Human-readable presentation for Betaflight logs.

Turns the raw decoded values and raw header config from blackbox_decoder.py
into human-readable units and names — a port of the official log-viewer's
FlightLogFieldPresenter / flightlog_fielddefs plus the Configurator's rate
curve math:

- present_main_field(sys_config, name, value, ...) -> (scaled_value, unit)
    e.g. gyro raw -> °/s, motor -> %, vbat -> V, amperage -> A, eRPM -> rpm
- present_headers(sys_config) -> {param: decoded_name}
    e.g. rates_type 3 -> "ACTUAL", fast_pwm_protocol 7 -> "DSHOT600"
- compute_rates(sys_config) -> per-axis rates in both styles + max °/s

stdlib only.
"""

from __future__ import annotations

import math
from typing import Optional

# ---------------------------------------------------------------------------
# Enum tables (port of flightlog_fielddefs.js)
# ---------------------------------------------------------------------------
RATES_TYPE = ["BETAFLIGHT", "RACEFLIGHT", "KISS", "ACTUAL", "QUICK"]

FAST_PROTOCOL = [
    "PWM", "ONESHOT125", "ONESHOT42", "MULTISHOT", "BRUSHED",
    "DSHOT150", "DSHOT300", "DSHOT600", "DSHOT1200", "PROSHOT1000",
]
_DIGITAL_PROTOCOLS = {"DSHOT150", "DSHOT300", "DSHOT600", "DSHOT1200", "PROSHOT1000"}

SERIALRX_PROVIDER = [
    "SPEK1024", "SPEK2048", "SBUS", "SUMD", "SUMH", "XB-B", "XB-B-RJ01",
    "IBUS", "JETIEXBUS", "CRSF", "SRXL", "CUSTOM", "FPORT", "SRXL2", "GHST",
]

FILTER_TYPE = ["PT1", "BIQUAD", "PT2", "PT3"]
OFF_ON = ["OFF", "ON"]
ANTI_GRAVITY_MODE = ["SMOOTH", "STEP"]
RC_SMOOTHING_DEBUG_AXIS = ["ROLL", "PITCH", "YAW", "THROTTLE"]
SUPER_EXPO_YAW = ["OFF", "ON", "ALWAYS"]
GYRO_TO_USE = ["FIRST", "SECOND", "BOTH"]
FF_AVERAGING = ["OFF", "2_POINT", "3_POINT", "4_POINT"]
SIMPLIFIED_PIDS_MODE = ["OFF", "ON - RP", "ON - RPY"]
THROTTLE_LIMIT_TYPE = ["OFF", "SCALE", "CLIP"]
ITERM_RELAX = ["OFF", "RP", "RPY", "RP_INC", "RPY_INC"]
ITERM_RELAX_TYPE = ["GYRO", "SETPOINT"]
GYRO_LPF = ["OFF", "188HZ", "98HZ", "42HZ", "20HZ", "10HZ", "5HZ", "EXPERIMENTAL"]

BARO_HARDWARE = [
    "AUTO", "NONE", "BMP085", "MS5611", "BMP280", "LPS", "QMP6988",
    "BMP388", "DPS310", "2SMPB_02B",
]

FLIGHT_LOG_FLIGHT_STATE_NAME = [
    "GPS_FIX_HOME", "GPS_FIX", "CALIBRATE_MAG", "SMALL_ANGLE", "FIXED_WING",
]
FLIGHT_LOG_FAILSAFE_PHASE_NAME = ["IDLE", "RX_LOSS_DETECTED", "LANDING", "LANDED"]
FLIGHT_LOG_DISARM_REASON = [
    "ARMING_DISABLED", "FAILSAFE", "THROTTLE_TIMEOUT", "STICKS", "SWITCH",
    "CRASH_PROTECTION", "RUNAWAY_TAKEOFF", "GPS_RESCUE", "SERIAL_IO",
]
FLIGHT_LOG_FEATURES = [
    "RX_PPM", "VBAT", "INFLIGHT_ACC_CAL", "RX_SERIAL", "MOTOR_STOP",
    "SERVO_TILT", "SOFTSERIAL", "GPS", "FAILSAFE", "SONAR", "TELEMETRY",
    "CURRENT_METER", "3D", "RX_PARALLEL_PWM", "RX_MSP", "RSSI_ADC",
    "LED_STRIP", "DISPLAY", "ONESHOT125", "BLACKBOX", "CHANNEL_FORWARDING",
    "TRANSPONDER", "AIRMODE", "SUPEREXPO_RATES",
]

_FLIGHT_MODE_PRE_3_3 = [
    "ARM", "ANGLE", "HORIZON", "BARO", "ANTIGRAVITY", "MAG", "HEADFREE",
    "HEADADJ", "CAMSTAB", "CAMTRIG", "GPSHOME", "GPSHOLD", "PASSTHRU",
    "BEEPER", "LEDMAX", "LEDLOW", "LLIGHTS", "CALIB", "GOV", "OSD",
    "TELEMETRY", "GTUNE", "SONAR", "SERVO1", "SERVO2", "SERVO3", "BLACKBOX",
    "FAILSAFE", "AIRMODE", "3DDISABLE", "FPVANGLEMIX", "BLACKBOXERASE",
    "CAMERA1", "CAMERA2", "CAMERA3", "FLIPOVERAFTERCRASH", "PREARM",
]
_FLIGHT_MODE_POST_3_3 = [
    "ARM", "ANGLE", "HORIZON", "MAG", "BARO", "GPSHOME", "GPSHOLD",
    "HEADFREE", "PASSTHRU", "RANGEFINDER", "FAILSAFE", "GPSRESCUE",
    "ANTIGRAVITY", "HEADADJ", "CAMSTAB", "CAMTRIG", "BEEPER", "LEDMAX",
    "LEDLOW", "LLIGHTS", "CALIB", "GOV", "OSD", "TELEMETRY", "GTUNE",
    "SERVO1", "SERVO2", "SERVO3", "BLACKBOX", "AIRMODE", "3D", "FPVANGLEMIX",
    "BLACKBOXERASE", "CAMERA1", "CAMERA2", "CAMERA3", "FLIPOVERAFTERCRASH",
    "PREARM", "BEEPGPSCOUNT", "VTXPITMODE", "USER1", "USER2", "USER3",
    "USER4", "PIDAUDIO", "ACROTRAINER", "VTXCONTROLDISABLE", "LAUNCHCONTROL",
]
_FLIGHT_MODE_POST_4_5 = [
    "ARM", "ANGLE", "HORIZON", "MAG", "ALTHOLD", "HEADFREE", "CHIRP",
    "PASSTHRU", "FAILSAFE", "POSHOLD", "GPSRESCUE", "ANTIGRAVITY", "HEADADJ",
    "CAMSTAB", "BEEPER", "LEDLOW", "CALIB", "OSD", "TELEMETRY", "SERVO1",
    "SERVO2", "SERVO3", "BLACKBOX", "AIRMODE", "3D", "FPVANGLEMIX",
    "BLACKBOXERASE", "CAMERA1", "CAMERA2", "CAMERA3", "FLIPOVERAFTERCRASH",
    "PREARM", "BEEPGPSCOUNT", "VTXPITMODE", "USER1", "USER2", "USER3",
    "USER4", "PIDAUDIO", "ACROTRAINER", "VTXCONTROLDISABLE", "LAUNCHCONTROL",
]

_DEBUG_MODE_COMPLETE = [
    "NONE", "CYCLETIME", "BATTERY", "GYRO", "ACCELEROMETER", "MIXER",
    "AIRMODE", "PIDLOOP", "NOTCH", "RC_INTERPOLATION", "VELOCITY",
    "DTERM_FILTER", "ANGLERATE", "ESC_SENSOR", "SCHEDULER", "STACK",
    "ESC_SENSOR_RPM", "ESC_SENSOR_TMP", "ALTITUDE", "FFT", "FFT_TIME",
    "FFT_FREQ", "RX_FRSKY_SPI", "GYRO_RAW", "DUAL_GYRO", "DUAL_GYRO_RAW",
    "DUAL_GYRO_COMBINED", "DUAL_GYRO_DIFF", "MAX7456_SIGNAL",
    "MAX7456_SPICLOCK", "SBUS", "FPORT", "RANGEFINDER", "RANGEFINDER_QUALITY",
    "LIDAR_TF", "ADC_INTERNAL", "RUNAWAY_TAKEOFF", "SDIO", "CURRENT_SENSOR",
    "USB", "SMARTAUDIO", "RTH", "ITERM_RELAX", "ACRO_TRAINER", "RC_SMOOTHING",
    "RX_SIGNAL_LOSS", "RC_SMOOTHING_RATE", "ANTI_GRAVITY", "DYN_LPF",
    "RX_SPECTRUM_SPI", "DSHOT_RPM_TELEMETRY", "RPM_FILTER", "D_MAX",
    "AC_CORRECTION", "AC_ERROR", "DUAL_GYRO_SCALED", "DSHOT_RPM_ERRORS",
    "CRSF_LINK_STATISTICS_UPLINK", "CRSF_LINK_STATISTICS_PWR",
    "CRSF_LINK_STATISTICS_DOWN", "BARO", "GPS_RESCUE_THROTTLE_PID",
    "DYN_IDLE", "FF_LIMIT", "FF_INTERPOLATED", "BLACKBOX_OUTPUT",
    "GYRO_SAMPLE", "RX_TIMING", "D_LPF", "VTX_TRAMP", "GHST", "GHST_MSP",
    "SCHEDULER_DETERMINISM", "TIMING_ACCURACY", "RX_EXPRESSLRS_SPI",
    "RX_EXPRESSLRS_PHASELOCK", "RX_STATE_TIME", "GPS_RESCUE_VELOCITY",
    "GPS_RESCUE_HEADING", "GPS_RESCUE_TRACKING", "GPS_CONNECTION",
    "ATTITUDE", "VTX_MSP", "GPS_DOP", "FAILSAFE", "GYRO_CALIBRATION",
    "ANGLE_MODE", "ANGLE_TARGET", "CURRENT_ANGLE", "DSHOT_TELEMETRY_COUNTS",
    "RPM_LIMIT", "RC_STATS", "MAG_CALIB", "MAG_TASK_RATE", "EZLANDING",
]
_ACC_HARDWARE_COMPLETE = [
    "AUTO", "NONE", "ADXL345", "MPU6050", "MMA8452", "BMA280", "LSM303DLHC",
    "MPU6000", "MPU6500", "MPU9250", "ICM20601", "ICM20602", "ICM20608G",
    "ICM20649", "ICM20689", "ICM42605", "ICM42688P", "BMI160", "BMI270",
    "LSM6DSO", "LSM6DSV16X", "VIRTUAL",
]
_MAG_HARDWARE_COMPLETE = [
    "AUTO", "NONE", "HMC5883", "AK8975", "AK8963", "QMC5883", "LIS3MDL",
    "MAG_MPU925X_AK8963",
]


def _ver_tuple(version: Optional[str]) -> tuple:
    if not version:
        return (0, 0, 0)
    parts = []
    for p in str(version).split(".")[:3]:
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _gte(version: Optional[str], target: str) -> bool:
    return _ver_tuple(version) >= _ver_tuple(target)


def _splice_replace(lst, old, new):
    if old in lst:
        lst[lst.index(old)] = new


def _splice_remove(lst, name):
    if name in lst:
        lst.remove(name)


def adjust_enums(firmware_type: Optional[str], version: Optional[str]) -> dict:
    """Port of adjustFieldDefsList: version-correct DEBUG_MODE / hardware /
    flight-mode-name lists. Returns a dict of the adjusted lists."""
    debug = list(_DEBUG_MODE_COMPLETE)
    acc = list(_ACC_HARDWARE_COMPLETE)
    mag = list(_MAG_HARDWARE_COMPLETE)

    if firmware_type == "betaflight" and _gte(version, "3.3.0"):
        for name in ("MIXER", "AIRMODE", "VELOCITY", "DTERM_FILTER"):
            _splice_remove(debug, name)
        if _gte(version, "3.4.0"):
            _splice_replace(debug, "GYRO", "GYRO_FILTERED")
            _splice_replace(debug, "NOTCH", "GYRO_SCALED")
        if _gte(version, "4.0.0") and "GYRO_RAW" in debug:
            debug.insert(debug.index("GYRO_RAW"), "RX_SFHSS_SPI")
        if _gte(version, "4.1.0"):
            _splice_remove(debug, "DUAL_GYRO")
            _splice_remove(debug, "DUAL_GYRO_COMBINED")
        if _gte(version, "4.3.0"):
            _splice_replace(debug, "FF_INTERPOLATED", "FEEDFORWARD")
            _splice_replace(debug, "FF_LIMIT", "FEEDFORWARD_LIMIT")
        if _gte(version, "4.5.0"):
            if "LIS3MDL" in mag:
                mag.insert(mag.index("LIS3MDL"), "LIS2MDL")
            mag.append("IST8310")
        if not _gte(version, "2025.12.0"):
            _splice_replace(debug, "D_MAX", "D_MIN")
        if _gte(version, "2025.12.0"):
            for name in ("ADXL345", "MMA8452", "BMA280", "LSM303DLHC"):
                _splice_remove(acc, name)
            if "LSM6DSV16X" in acc:
                acc.insert(acc.index("LSM6DSV16X") + 1, "IIM42653")
            _splice_replace(debug, "GPS_RESCUE_THROTTLE_PID", "AUTOPILOT_ALTITUDE")
            _splice_remove(debug, "GYRO_SCALED")
            if "RANGEFINDER_QUALITY" in debug:
                debug.insert(debug.index("RANGEFINDER_QUALITY") + 1, "OPTICALFLOW")
            debug += ["TPA", "S_TERM", "SPA", "TASK", "GIMBAL", "WING_SETPOINT",
                      "AUTOPILOT_POSITION", "CHIRP", "FLASH_TEST_PRBS",
                      "MAVLINK_TELEMETRY"]
            _splice_replace(debug, "DUAL_GYRO_RAW", "MULTI_GYRO_RAW")
            _splice_replace(debug, "DUAL_GYRO_DIFF", "MULTI_GYRO_DIFF")
            _splice_replace(debug, "DUAL_GYRO_SCALED", "MULTI_GYRO_SCALED")
        if _gte(version, "2026.6.0"):
            debug.append("AUTOPILOT_PID")

        if _gte(version, "2025.12.0"):
            modes = list(_FLIGHT_MODE_POST_4_5)
        else:
            modes = list(_FLIGHT_MODE_POST_3_3)
            if not _gte(version, "3.4.0"):
                _splice_remove(modes, "GPSRESCUE")
            if _gte(version, "3.5.0"):
                for name in ("RANGEFINDER", "CAMTRIG", "LEDMAX", "LLIGHTS", "GOV", "GTUNE"):
                    _splice_remove(modes, name)
            if _gte(version, "4.0.0"):
                for name in ("BARO", "GPSHOME", "GPSHOLD"):
                    _splice_remove(modes, name)
        if _gte(version, "2026.6.0") and "GPSRESCUE" in modes:
            modes.insert(modes.index("GPSRESCUE") + 1, "AUTOPILOT")
    else:
        modes = list(_FLIGHT_MODE_PRE_3_3)
        if firmware_type == "betaflight" and not _gte(version, "3.1.7"):
            _splice_remove(modes, "ANTIGRAVITY")

    return {
        "DEBUG_MODE": debug,
        "ACC_HARDWARE": acc,
        "MAG_HARDWARE": mag,
        "FLIGHT_MODE_NAME": modes,
    }


# ---------------------------------------------------------------------------
# Flag / enum presentation
# ---------------------------------------------------------------------------
def present_flags(flags: int, flag_names: list) -> str:
    out = []
    i = 0
    while flags > 0 and i < len(flag_names):
        if flags & 1:
            out.append(flag_names[i])
        flags >>= 1
        i += 1
    return "|".join(out) if out else "0"


def present_enum(value, enum_names: list):
    try:
        return enum_names[int(value)]
    except (ValueError, TypeError, IndexError):
        return value


# ---------------------------------------------------------------------------
# Scaling helpers (port of FlightLog.prototype.*)
# ---------------------------------------------------------------------------
def is_digital_protocol(sc: dict) -> bool:
    proto = sc.get("fast_pwm_protocol")
    if proto is None or not isinstance(proto, int) or proto >= len(FAST_PROTOCOL):
        return True  # default assume digital (DSHOT) on modern builds
    return FAST_PROTOCOL[proto] in _DIGITAL_PROTOCOLS


def gyro_raw_to_dps(sc: dict, value: float) -> float:
    return (sc.get("gyroScale", 1.0) * 1_000_000) / (math.pi / 180.0) * value


def acc_raw_to_g(sc: dict, value: float) -> float:
    g = sc.get("acc_1G") or 2048
    return value / g


def motor_raw_to_pct(sc: dict, value: float) -> float:
    if is_digital_protocol(sc):
        pct = ((value - 48) / (2047 - 48)) * 100
    else:
        lo = sc.get("minthrottle", 1150)
        hi = sc.get("maxthrottle", 1850)
        rng = hi - lo if hi != lo else 1
        pct = ((value - lo) / rng) * 100
    return min(max(pct, 0.0), 100.0)


def num_motors(field_names: list) -> int:
    return sum(1 for j in range(8) if f"motor[{j}]" in field_names)


def num_cells_estimate(sc: dict, field_names: list) -> Optional[int]:
    if "vbatLatest" not in field_names:
        return None
    ref = sc.get("vbatref", 4095)
    if not _gte(sc.get("firmwareVersion"), "3.1.0"):
        ref = vbat_adc_to_mv(sc, ref) / 100
    cell_max = sc.get("vbatmaxcellvoltage", 430)
    for i in range(1, 8):
        if ref < i * cell_max:
            return i
    return 8


def vbat_adc_to_mv(sc: dict, adc: float) -> float:
    return (adc * 33 * 10 * sc.get("vbatscale", 110)) / 0xFFF


def vbat_to_volts(sc: dict, value: float) -> float:
    if sc.get("firmwareType") == "betaflight" and _gte(sc.get("firmwareVersion"), "4.0.0"):
        return value / 100
    if _gte(sc.get("firmwareVersion"), "3.1.0"):
        return value / 10
    return vbat_adc_to_mv(sc, value) / 1000


def amperage_adc_to_mv(sc: dict, adc: float) -> float:
    mv = (adc * 33 * 100) / 4095
    mv -= sc.get("currentMeterOffset", 0)
    return (mv * 10000) / (sc.get("currentMeterScale") or 400)


def amperage_to_amps(sc: dict, value: float) -> float:
    if _gte(sc.get("firmwareVersion"), "3.1.0"):
        return value / 100
    return amperage_adc_to_mv(sc, value) / 1000


_GYRO_FIELDS = {f"{p}[{a}]" for p in ("gyroADC", "gyroUnfilt") for a in range(3)}
_ACC_FIELDS = {f"accSmooth[{a}]" for a in range(3)}
_PID_FIELDS = {
    f"{p}[{a}]" for p in ("axisP", "axisI", "axisD", "axisF", "axisS", "axisSum")
    for a in range(3)
}
_FLAG_FIELDS = {"flightModeFlags", "stateFlags", "failsafePhase"}


def present_main_field(sc: dict, name: str, value, *, num_cells=None,
                       n_motors=None, high_res_scale: int = 1):
    """Return (scaled_value, unit). For flag/enum fields returns (str, "")."""
    if name in _GYRO_FIELDS:
        return gyro_raw_to_dps(sc, value / high_res_scale), "deg/s"
    if name in _ACC_FIELDS:
        return acc_raw_to_g(sc, value), "g"
    if name in _PID_FIELDS:
        return value / 10.0, "%"
    if name.startswith("motor["):
        return motor_raw_to_pct(sc, value), "%"
    if name.startswith("eRPM["):
        poles = sc.get("motor_poles") or 14
        return (value * 200) / poles, "rpm"
    if name == "vbatLatest":
        return vbat_to_volts(sc, value), "V"
    if name == "amperageLatest":
        return amperage_to_amps(sc, value), "A"
    if name == "rcCommand[3]":
        return value / high_res_scale, "us"
    if name.startswith("rcCommand["):
        return value / high_res_scale + 1500, "us"
    if name == "time":
        return value / 1_000_000, "s"
    if name.startswith("heading["):
        return (value / math.pi) * 180, "deg"
    if name == "rssi":
        return (value / 1024) * 100, "%"
    if name.startswith("GPS_coord["):
        return value / 10_000_000, "deg"
    if name == "GPS_altitude":
        return value / 10, "m"
    if name == "GPS_speed":
        return value / 100, "m/s"
    if name == "flightModeFlags":
        return value, ""  # decoded separately when flag names known
    return value, ""


# ---------------------------------------------------------------------------
# Header enum decoding
# ---------------------------------------------------------------------------
# Static enum maps (param canonical name -> enum list)
_STATIC_HEADER_ENUMS = {
    "rates_type": RATES_TYPE,
    "fast_pwm_protocol": FAST_PROTOCOL,
    "serialrx_provider": SERIALRX_PROVIDER,
    "dterm_filter_type": FILTER_TYPE,
    "dterm_filter2_type": FILTER_TYPE,
    "gyro_soft_type": FILTER_TYPE,
    "gyro_soft2_type": FILTER_TYPE,
    "gyro_lpf": GYRO_LPF,
    "baro_hardware": BARO_HARDWARE,
    "anti_gravity_mode": ANTI_GRAVITY_MODE,
    "iterm_relax": ITERM_RELAX,
    "iterm_relax_type": ITERM_RELAX_TYPE,
    "rc_smoothing_debug_axis": RC_SMOOTHING_DEBUG_AXIS,
    "gyro_to_use": GYRO_TO_USE,
    "ff_averaging": FF_AVERAGING,
    "simplified_pids_mode": SIMPLIFIED_PIDS_MODE,
    "throttle_limit_type": THROTTLE_LIMIT_TYPE,
}
# Params decoded with version-dependent lists
_DYNAMIC_HEADER_ENUMS = {
    "debug_mode": "DEBUG_MODE",
    "acc_hardware": "ACC_HARDWARE",
    "mag_hardware": "MAG_HARDWARE",
}
# Params that are OFF/ON booleans
_BOOL_HEADER_PARAMS = {
    "dshot_bidir", "use_integrated_yaw", "vbat_pid_compensation",
    "gyro_cal_on_first_arm", "pidAtMinThrottle",
}


def present_headers(sc: dict) -> dict:
    """Return {param: decoded_name} for enum-typed config headers present."""
    adjusted = adjust_enums(sc.get("firmwareType"), sc.get("firmwareVersion"))
    out = {}
    for key, names in _STATIC_HEADER_ENUMS.items():
        if key in sc and isinstance(sc[key], int):
            out[key] = present_enum(sc[key], names)
    for key, listname in _DYNAMIC_HEADER_ENUMS.items():
        if key in sc and isinstance(sc[key], int):
            out[key] = present_enum(sc[key], adjusted[listname])
    for key in _BOOL_HEADER_PARAMS:
        if key in sc and isinstance(sc[key], int) and sc[key] in (0, 1):
            out[key] = OFF_ON[sc[key]]
    return out


# ---------------------------------------------------------------------------
# Rate curve (port of betaflight-configurator src/js/RateCurve.js)
# ---------------------------------------------------------------------------
def _constrain(v, lo, hi):
    return max(lo, min(v, hi))


def _bf_rates(rc_cmd_f, rc_cmd_abs, rate, rc_rate, rc_expo, super_active, limit):
    if rc_rate > 2:
        rc_rate = rc_rate + (rc_rate - 2) * 14.54
    if rc_expo > 0:
        rc_cmd_f = rc_cmd_f * (rc_cmd_abs ** 3) * rc_expo + rc_cmd_f * (1 - rc_expo)
    if super_active:
        rc_factor = 1 / _constrain(1 - rc_cmd_abs * rate, 0.01, 1)
        angular = 200 * rc_rate * rc_cmd_f * rc_factor
    else:
        angular = ((rate * 100 + 27) * rc_cmd_f) / 16 / 4.1
    return _constrain(angular, -1 * limit, limit)


def _actual_rates(rc_cmd_f, rc_cmd_abs, rate, rc_rate, rc_expo):
    expof = rc_cmd_abs * ((rc_cmd_f ** 5) * rc_expo + rc_cmd_f * (1 - rc_expo))
    angular = max(0, rate - rc_rate)
    return rc_cmd_f * rc_rate + angular * expof


def _raceflight_rates(rc_cmd_f, rate, rc_rate, rc_expo):
    angular = (1 + 0.01 * rc_expo * (rc_cmd_f * rc_cmd_f - 1.0)) * rc_cmd_f
    return angular * (rc_rate + abs(angular) * rc_rate * rate * 0.01)


def _kiss_rates(rc_cmd_f, rc_cmd_abs, rate, rc_rate, rc_expo):
    kiss_rpy = 1 - rc_cmd_abs * rate
    kiss_curve = rc_cmd_f * rc_cmd_f
    rc_cmd_f = (rc_cmd_f * kiss_curve * rc_expo + rc_cmd_f * (1 - rc_expo)) * (rc_rate / 10)
    return 2000.0 * (1.0 / kiss_rpy) * rc_cmd_f


def _quick_rates(rc_cmd_f, rc_cmd_abs, rate, rc_rate, rc_expo):
    rc_rate = rc_rate * 200
    rate = max(rate, rc_rate)
    super_cfg = (rate / rc_rate - 1) / (rate / rc_rate)
    curve = (rc_cmd_abs ** 3) * rc_expo + rc_cmd_abs * (1 - rc_expo)
    angular = 1.0 / (1.0 - curve * super_cfg)
    return rc_cmd_f * rc_rate * angular


def _axis_inputs(sc: dict, rates_type: str, axis: int):
    """Map blackbox header rate fields to (rate, rc_rate, rc_expo) for the
    given rates type. Returns None if the type isn't supported."""
    rc_rates = sc.get("rc_rates") or [None, None, None]
    rates = sc.get("rates") or [None, None, None]
    rc_expo = sc.get("rc_expo") or [None, None, None]
    try:
        rr = rc_rates[axis]
        ra = rates[axis]
        ex = rc_expo[axis]
    except (IndexError, TypeError):
        return None
    if rr is None or ra is None or ex is None:
        return None

    if rates_type == "BETAFLIGHT":
        return ra / 100.0, rr / 100.0, ex / 100.0
    if rates_type == "ACTUAL":
        return ra * 10.0, rr * 10.0, ex / 100.0
    if rates_type == "QUICK":
        return ra * 10.0, rr / 100.0, ex / 100.0
    if rates_type == "RACEFLIGHT":
        return ra * 1.0, rr * 1.0, ex * 1.0
    if rates_type == "KISS":
        return ra / 100.0, rr / 100.0, ex / 100.0
    return None


def _max_dps_for_type(rates_type, rate, rc_rate, rc_expo, limit):
    # Evaluate the curve at full stick (rc_cmd_f = 1.0)
    f, fa = 1.0, 1.0
    if rates_type == "ACTUAL":
        return _actual_rates(f, fa, rate, rc_rate, rc_expo)
    if rates_type == "BETAFLIGHT":
        return _bf_rates(f, fa, rate, rc_rate, rc_expo, True, limit)
    if rates_type == "RACEFLIGHT":
        return _raceflight_rates(f, rate, rc_rate, rc_expo)
    if rates_type == "KISS":
        return _kiss_rates(f, fa, rate, rc_rate, rc_expo)
    if rates_type == "QUICK":
        return _quick_rates(f, fa, rate, rc_rate, rc_expo)
    return None


def compute_rates(sc: dict) -> Optional[dict]:
    """Per-axis rates: native style + computed max °/s for both views."""
    rt_raw = sc.get("rates_type")
    if isinstance(rt_raw, int):
        rates_type = present_enum(rt_raw, RATES_TYPE)
    elif isinstance(rt_raw, str) and rt_raw.upper() in RATES_TYPE:
        rates_type = rt_raw.upper()
    else:
        rates_type = "ACTUAL"
    if not isinstance(rates_type, str):
        rates_type = "ACTUAL"

    axes = ["roll", "pitch", "yaw"]
    result = {"rates_type": rates_type, "axes": {}}
    rate_limits = sc.get("rate_limits") or [1998, 1998, 1998]

    for i, axis in enumerate(axes):
        inputs = _axis_inputs(sc, rates_type, i)
        if inputs is None:
            continue
        rate, rc_rate, rc_expo = inputs
        try:
            limit = rate_limits[i]
        except (IndexError, TypeError):
            limit = 1998
        max_dps = _max_dps_for_type(rates_type, rate, rc_rate, rc_expo, limit)
        entry = {
            "native": {
                "rc_rate": (sc.get("rc_rates") or [None] * 3)[i],
                "super_rate" if rates_type == "BETAFLIGHT" else "rate":
                    (sc.get("rates") or [None] * 3)[i],
                "rc_expo": (sc.get("rc_expo") or [None] * 3)[i],
            },
            "max_dps": round(max_dps, 1) if max_dps is not None else None,
        }
        # Actual-style description = curve endpoints (universal across styles)
        if rates_type == "ACTUAL":
            entry["center_sensitivity_dps"] = round(rc_rate, 1)
        result["axes"][axis] = entry
    return result if result["axes"] else None
