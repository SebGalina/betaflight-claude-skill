#!/usr/bin/env python3
"""
parse_diff.py — Parse a Betaflight CLI `diff` or `dump` output into structured data.

Usage:
    python parse_diff.py <path_to_diff.txt>
    cat diff.txt | python parse_diff.py -

Outputs JSON to stdout with:
    - version: firmware version string if detected
    - target: board target if detected
    - features: list of enabled features
    - resources: pin mappings (motor, RX, etc.)
    - serial: serial port configs
    - profiles: list of PID profiles (each with all `set` params)
    - rateprofiles: list of rate profiles
    - global: global `set` params
    - deprecated: list of params likely deprecated (with version note)
    - warnings: list of detected issues
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
import blackbox_presenter as pr  # noqa: E402

# Parameters known to be deprecated in newer versions
DEPRECATED_PARAMS = {
    "d_min_pitch": "Removed in 4.5 — D is now flat",
    "d_min_roll": "Removed in 4.5 — D is now flat",
    "d_min_yaw": "Removed in 4.5 — D is now flat",
    "d_min_boost_gain": "Removed in 4.5",
    "d_min_advance": "Removed in 4.5",
    "dyn_notch_width_percent": "Removed in 4.5 — use dyn_notch_q",
    "dyn_notch_range": "Removed in 4.5 — use dyn_notch_min_hz / dyn_notch_max_hz",
    "iterm_rotation": "Removed in 4.4 — always on",
    "smith_predictor_strength": "Removed in 4.4",
    "smith_predictor_delay": "Removed in 4.4",
    "smith_predictor_filter_hz": "Removed in 4.4",
    "vbat_pid_gain": "Deprecated in 4.6 — automatic now",
}

# Suspicious value ranges (param → (min, max, message))
SUSPICIOUS_RANGES = {
    "p_pitch": (10, 100, "PID P pitch outside typical 30-70 range"),
    "p_roll": (10, 100, "PID P roll outside typical 30-70 range"),
    "i_pitch": (10, 200, "PID I pitch outside typical 60-120 range"),
    "i_roll": (10, 200, "PID I roll outside typical 60-120 range"),
    "d_pitch": (0, 100, "PID D pitch outside typical 20-60 range"),
    "d_roll": (0, 100, "PID D roll outside typical 20-60 range"),
    "dyn_notch_count": (0, 5, "dyn_notch_count outside 0-5"),
    "idle_min_rpm": (10, 50, "idle_min_rpm outside typical 20-30"),
    "motor_output_limit": (50, 100, "motor_output_limit suspiciously low"),
}


def parse(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "version": None,
        "target": None,
        "features": [],
        "resources": [],
        "serial": [],
        "profiles": defaultdict(dict),
        "rateprofiles": defaultdict(dict),
        "global": {},
        "deprecated": [],
        "warnings": [],
    }

    current_profile = None
    current_rateprofile = None
    in_profile_block = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            # Comments may carry useful info
            v = re.search(r"Betaflight.*?(\d+\.\d+\.\d+)", line)
            if v:
                result["version"] = v.group(1)
            t = re.search(r"#\s*target\s*:?\s*(\S+)", line, re.IGNORECASE)
            if t:
                result["target"] = t.group(1)
            continue

        # board_name and manufacturer_id are top-level (not # comments)
        m = re.match(r"board_name\s+(\S+)", line)
        if m:
            result["target"] = m.group(1)
            continue
        m = re.match(r"manufacturer_id\s+(\S+)", line)
        if m:
            # Don't overwrite target if already set
            continue

        # Profile switch
        m = re.match(r"profile\s+(\d+)", line)
        if m:
            current_profile = int(m.group(1))
            current_rateprofile = None
            in_profile_block = True
            continue
        m = re.match(r"rateprofile\s+(\d+)", line)
        if m:
            current_rateprofile = int(m.group(1))
            current_profile = None
            in_profile_block = True
            continue

        # feature
        m = re.match(r"feature\s+(-?\w+)", line)
        if m:
            result["features"].append(m.group(1))
            continue

        # resource
        m = re.match(r"resource\s+(\S+)\s+(\S+)\s+(\S+)", line)
        if m:
            result["resources"].append(
                {"function": m.group(1), "index": m.group(2), "pin": m.group(3)}
            )
            continue

        # serial
        m = re.match(r"serial\s+(.+)", line)
        if m:
            parts = m.group(1).split()
            if len(parts) >= 6:
                result["serial"].append(
                    {
                        "port_id": parts[0],
                        "function_mask": parts[1],
                        "msp_baud": parts[2],
                        "gps_baud": parts[3],
                        "telemetry_baud": parts[4],
                        "blackbox_baud": parts[5],
                    }
                )
            continue

        # set name = value
        m = re.match(r"set\s+(\S+)\s*=\s*(.+)$", line)
        if m:
            name, value = m.group(1), m.group(2).strip()

            # Check deprecated
            if name in DEPRECATED_PARAMS:
                result["deprecated"].append(
                    {"name": name, "value": value, "note": DEPRECATED_PARAMS[name]}
                )

            # Check suspicious ranges
            if name in SUSPICIOUS_RANGES:
                try:
                    v = float(value)
                    lo, hi, msg = SUSPICIOUS_RANGES[name]
                    if v < lo or v > hi:
                        result["warnings"].append(
                            f"{name} = {value}: {msg}"
                        )
                except ValueError:
                    pass

            # Place into correct bucket
            if current_profile is not None:
                result["profiles"][current_profile][name] = value
            elif current_rateprofile is not None:
                result["rateprofiles"][current_rateprofile][name] = value
            else:
                result["global"][name] = value
            continue

    # Convert defaultdicts for JSON serialization
    result["profiles"] = dict(result["profiles"])
    result["rateprofiles"] = dict(result["rateprofiles"])

    # Human-readable rates (max deg/s) per rate profile
    result["rates"] = _rates_from_rateprofiles(result["rateprofiles"])

    # High-level inferences
    if "DSHOT600" in result["global"].get("motor_pwm_protocol", ""):
        pass  # Could add inference logic here
    if result["global"].get("dshot_bidir") != "ON":
        result["warnings"].append(
            "Bidirectional DSHOT not enabled — RPM filter inactive, dynamic notch quality reduced"
        )

    return result


def _rates_from_rateprofiles(rateprofiles: dict) -> dict:
    """Compute human-readable rates (max deg/s) for each rate profile in a diff."""
    out = {}

    def _int(d, key):
        try:
            return int(d[key])
        except (KeyError, ValueError, TypeError):
            return None

    for idx, rp in rateprofiles.items():
        sc = {
            "rates_type": rp.get("rates_type", "BETAFLIGHT"),
            "rc_rates": [_int(rp, f"{a}_rc_rate") for a in ("roll", "pitch", "yaw")],
            "rates": [_int(rp, f"{a}_srate") for a in ("roll", "pitch", "yaw")],
            "rc_expo": [_int(rp, f"{a}_expo") for a in ("roll", "pitch", "yaw")],
            "rate_limits": [
                _int(rp, f"{a}_rate_limit") or 1998 for a in ("roll", "pitch", "yaw")
            ],
        }
        rates = pr.compute_rates(sc)
        if rates:
            out[idx] = rates
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1

    if sys.argv[1] == "-":
        text = sys.stdin.read()
    else:
        with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
            text = f.read()

    result = parse(text)
    json.dump(result, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
