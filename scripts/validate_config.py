#!/usr/bin/env python3
"""
validate_config.py — Sanity-check a Betaflight CLI dump for common configuration errors.

Usage:
    python validate_config.py <path_to_diff.txt>

Reads a CLI diff/dump and reports:
    - Deprecated parameters (would error on newer firmware)
    - Suspicious value combinations
    - Missing recommended settings
    - Safety concerns (failsafe, etc.)

Exit codes:
    0 — no issues
    1 — warnings only
    2 — errors that would prevent import
"""

import sys
from pathlib import Path

# Reuse the parser
sys.path.insert(0, str(Path(__file__).parent))
from parse_diff import parse  # noqa: E402


def validate(config: dict) -> tuple[list[str], list[str], list[str]]:
    """Return (errors, warnings, info)."""
    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    # 1. Deprecated params would error on import
    for dep in config["deprecated"]:
        errors.append(
            f"DEPRECATED: '{dep['name']} = {dep['value']}' — {dep['note']}"
        )

    # 2. Carry through parser warnings
    warnings.extend(config["warnings"])

    # 3. Failsafe sanity check
    g = config["global"]
    if g.get("failsafe_procedure", "DROP") not in ("DROP", "LAND", "GPS_RESCUE"):
        errors.append(
            f"Invalid failsafe_procedure: {g.get('failsafe_procedure')}"
        )

    if "failsafe_delay" in g:
        try:
            d = int(g["failsafe_delay"])
            if d > 20:
                warnings.append(
                    f"failsafe_delay = {d} (= {d/10}s) is unusually long"
                )
        except ValueError:
            pass

    # 4. Motor protocol sanity
    proto = g.get("motor_pwm_protocol", "")
    if proto and proto not in (
        "DSHOT150",
        "DSHOT300",
        "DSHOT600",
        "DSHOT1200",
        "PROSHOT1000",
        "MULTISHOT",
        "ONESHOT125",
        "BRUSHED",
        "DISABLED",
    ):
        errors.append(f"Unknown motor_pwm_protocol: {proto}")

    # 5. RPM filter requires bidir DSHOT
    if g.get("dshot_bidir") == "ON":
        info.append("✓ Bidirectional DSHOT enabled — RPM filter active")
    else:
        warnings.append(
            "Bidirectional DSHOT not enabled — consider enabling for RPM filter"
        )

    # 6. Motor poles sanity
    if "motor_poles" in g:
        try:
            poles = int(g["motor_poles"])
            if poles not in (12, 14, 16, 18):
                warnings.append(
                    f"motor_poles = {poles} is unusual (typical: 14 for 12N14P)"
                )
        except ValueError:
            pass

    # 7. PID profile sanity — at least one profile defined
    if not config["profiles"]:
        warnings.append("No PID profile data found in dump")
    else:
        info.append(f"✓ {len(config['profiles'])} PID profile(s) detected")

    # 8. RX configuration
    has_rx_serial = any(
        f.lstrip("-") == "RX_SERIAL" and not f.startswith("-")
        for f in config["features"]
    )
    if has_rx_serial:
        info.append("✓ RX_SERIAL feature enabled")
    elif config["features"]:
        warnings.append("RX_SERIAL not in feature list — RX may not work")

    # 9. Version detected?
    if config["version"]:
        info.append(f"✓ Firmware version: {config['version']}")
    else:
        info.append("? Firmware version not detected in header")

    # 10. Target detected?
    if config["target"]:
        info.append(f"✓ Target: {config['target']}")

    return errors, warnings, info


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2

    with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
        text = f.read()

    config = parse(text)
    errors, warnings, info = validate(config)

    print("=" * 60)
    print(f"Betaflight Config Validation: {sys.argv[1]}")
    print("=" * 60)

    if info:
        print("\nInfo:")
        for i in info:
            print(f"  {i}")

    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for w in warnings:
            print(f"  ⚠ {w}")

    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors:
            print(f"  ✗ {e}")

    print()
    if errors:
        print(f"FAIL: {len(errors)} error(s) — would not import cleanly")
        return 2
    if warnings:
        print(f"PASS with {len(warnings)} warning(s)")
        return 1
    print("PASS — no issues detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
