#!/usr/bin/env python3
"""Lightweight smoke test for the bundled scripts — stdlib only, no numpy/pandas.

Run from the skill root:  python -m scripts.selftest

Always tests parse_diff + validate_config against the committed fixture
(evals/sample_diff.txt). If any *.bbl logs are present in scripts/test/, it also
header-parses them as a blackbox-decoder regression check. Those logs are large
binaries kept out of git on purpose (see scripts/test/README.md): the test SKIPs
them gracefully when absent, so a fresh clone still passes.
"""
from __future__ import annotations

import sys
from pathlib import Path

from scripts import analyze_blackbox as ab
from scripts import parse_diff, validate_config

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIFF = ROOT / "evals" / "sample_diff.txt"
BBL_DIR = ROOT / "scripts" / "test"


def _check(label: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {label}{f' — {detail}' if detail else ''}")
    return cond


def test_parse_diff() -> bool:
    print("parse_diff + validate_config (evals/sample_diff.txt)")
    if not SAMPLE_DIFF.exists():
        return _check("fixture present", False, f"missing {SAMPLE_DIFF}")
    parsed = parse_diff.parse(SAMPLE_DIFF.read_text(encoding="utf-8"))
    ok = True
    ok &= _check("version parsed", parsed.get("version") == "4.5.1", parsed.get("version"))
    ok &= _check("target parsed", parsed.get("target") == "SPEEDYBEEF7V3", parsed.get("target"))
    ok &= _check("features parsed", "RX_SERIAL" in parsed.get("features", []))
    errors, warnings, info = validate_config.validate(parsed)
    ok &= _check(
        "validate returns 3 lists",
        all(isinstance(x, list) for x in (errors, warnings, info)),
    )
    return ok


def test_blackbox_headers() -> bool:
    print("blackbox decoder — header parse (scripts/test/*.bbl)")
    logs = sorted(BBL_DIR.glob("*.bbl")) if BBL_DIR.exists() else []
    if not logs:
        print("  [SKIP] no local .bbl fixtures (see scripts/test/README.md)")
        return True
    ok = True
    for log in logs:
        result = ab.analyze(log, decode=False, want_stats=False, session_sel=None)
        sessions = result.get("sessions", [])
        fw = sessions[0].get("sys_config", {}).get("Firmware revision", "") if sessions else ""
        ok &= _check(
            log.name,
            result.get("session_count", 0) >= 1 and "Betaflight" in fw,
            fw or result.get("error", "no session"),
        )
    return ok


def test_chirp() -> bool:
    print("chirp_analysis — FRF pipeline (scripts/test/btfl_chirp.bbl)")
    try:
        import numpy, pandas, scipy  # noqa: F401  (heavy deps; skip cleanly if absent)
    except ImportError:
        print("  [SKIP] numpy/pandas/scipy not installed")
        return True
    log = BBL_DIR / "btfl_chirp.bbl"
    if not log.exists():
        print("  [SKIP] no chirp fixture (scripts/test/btfl_chirp.bbl)")
        return True
    from scripts import chirp_analysis as ca

    tmp = ca._decode_bbl(log)
    try:
        df = ca._load_csv(tmp)
        results, _tmap, _noise, _spectro = ca.analyse(df, ca._sample_rate(df), ca.DEFAULT_INPUT_COL)
    finally:
        tmp.unlink(missing_ok=True)
    ok = _check("axes analysed", len(results) >= 1, f"{len(results)} axes")
    ok &= _check(
        "phase margin read",
        any(a.get("phase_margin_deg") is not None for a in results.values()),
    )
    return ok


def main() -> int:
    results = [test_parse_diff(), test_blackbox_headers(), test_chirp()]
    print()
    if all(results):
        print("selftest: all checks passed")
        return 0
    print("selftest: FAILURES above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
