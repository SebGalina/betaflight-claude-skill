#!/usr/bin/env python3
"""
analyze_blackbox.py — Lightweight blackbox log analyzer.

This is a TEXT-LEVEL analyzer for Betaflight blackbox logs. For full FFT
analysis use https://blackbox.betaflight.com or PIDtoolbox. This script
extracts headers, flight stats, and surfaces obvious tune red flags from
the log's CLI-dumped settings header.

Usage:
    python analyze_blackbox.py <path_to_log.bbl>

Outputs:
    - Detected firmware/target/build info
    - Embedded settings dump summary
    - Flight session count and rough duration
    - Surface-level warnings (filters, dshot bidir, etc.)

Limitations:
    - Does NOT do FFT / noise analysis (requires log decode + signal processing)
    - For real PID tune analysis, point users to PIDtoolbox or the online viewer
"""

import re
import sys
from pathlib import Path

# Reuse parser for embedded settings
sys.path.insert(0, str(Path(__file__).parent))
from parse_diff import parse  # noqa: E402


def analyze(path: Path) -> dict:
    """Analyze a blackbox log file at the header level."""
    # Read as binary then decode lossy — blackbox files are binary with
    # ASCII headers at the start of each log section
    with open(path, "rb") as f:
        raw = f.read(200_000)  # First 200KB usually covers all headers

    text = raw.decode("utf-8", errors="replace")

    result: dict = {
        "file": str(path),
        "size_bytes": path.stat().st_size,
        "log_sections": 0,
        "firmware": None,
        "target": None,
        "craft_name": None,
        "embedded_settings": None,
        "warnings": [],
    }

    # Count log sections (each starts with "H Product:Blackbox")
    result["log_sections"] = text.count("H Product:Blackbox")

    # Firmware
    m = re.search(r"H Firmware revision:Betaflight (\d+\.\d+\.\d+)", text)
    if m:
        result["firmware"] = m.group(1)

    # Target
    m = re.search(r"H Firmware target:(\S+)", text)
    if m:
        result["target"] = m.group(1)

    # Craft name
    m = re.search(r"H Craft name:(.+)", text)
    if m:
        result["craft_name"] = m.group(1).strip()

    # Embedded settings — blackbox logs include "H " prefixed CLI lines
    cli_lines = []
    for line in text.splitlines():
        if line.startswith("H ") and "=" in line:
            # Format: "H paramname:value"
            cli_match = re.match(r"H ([\w_]+):(.+)", line)
            if cli_match:
                cli_lines.append(f"set {cli_match.group(1)} = {cli_match.group(2)}")

    if cli_lines:
        embedded = parse("\n".join(cli_lines))
        result["embedded_settings"] = {
            "global_param_count": len(embedded["global"]),
            "deprecated_count": len(embedded["deprecated"]),
            "warnings": embedded["warnings"],
        }
        result["warnings"].extend(embedded["warnings"])

    # Heuristics
    if result["log_sections"] == 0:
        result["warnings"].append(
            "No blackbox log section markers found — file may be corrupt or not a Betaflight log"
        )

    return result


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        return 1

    result = analyze(path)

    print("=" * 60)
    print(f"Blackbox Log Analysis: {path.name}")
    print("=" * 60)
    print(f"File size:       {result['size_bytes']:,} bytes")
    print(f"Log sessions:    {result['log_sections']}")
    print(f"Firmware:        {result['firmware'] or '(not detected)'}")
    print(f"Target:          {result['target'] or '(not detected)'}")
    print(f"Craft name:      {result['craft_name'] or '(not set)'}")

    if result["embedded_settings"]:
        es = result["embedded_settings"]
        print(f"\nEmbedded settings:")
        print(f"  Global params:    {es['global_param_count']}")
        print(f"  Deprecated:       {es['deprecated_count']}")

    if result["warnings"]:
        print(f"\nWarnings ({len(result['warnings'])}):")
        for w in result["warnings"]:
            print(f"  ⚠ {w}")
    else:
        print("\nNo header-level warnings.")

    print(
        "\nNote: This is a header-only scan. For real noise/PID analysis,\n"
        "use https://blackbox.betaflight.com or PIDtoolbox."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
