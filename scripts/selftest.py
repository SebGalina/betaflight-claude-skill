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
from scripts import blackbox_signal as bbs
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

    df = bbs.load_dataframe(log)
    results, _tmap, _noise, _spectro = ca.analyse(df, ca._sample_rate(df), ca.DEFAULT_INPUT_COL)
    ok = _check("axes analysed", len(results) >= 1, f"{len(results)} axes")
    ok &= _check(
        "phase margin read",
        any(a.get("phase_margin_deg") is not None for a in results.values()),
    )
    return ok


def test_analysers_smoke() -> bool:
    """spectral_analysis + step_response on a synthetic signal (no .bbl needed).

    Builds an in-memory decoded DataFrame with a known 220 Hz tone on the gyro
    and a square-wave setpoint, then runs each analyser's `analyse()` directly.
    Exercises the shared bbs helpers (sample rate, activity mask) and the DSP.
    """
    print("spectral_analysis + step_response — synthetic-signal smoke")
    try:
        import numpy as np
        import pandas as pd
        import scipy  # noqa: F401
    except ImportError:
        print("  [SKIP] numpy/pandas/scipy not installed")
        return True

    from scripts import spectral_analysis as sa
    from scripts import step_response as sr

    fs, n, f_tone = 2000.0, 8000, 220.0
    t = np.arange(n) / fs
    rng = np.random.default_rng(0)
    tone = lambda ph: 50 * np.sin(2 * np.pi * f_tone * t + ph) + rng.normal(0, 5, n)
    setp = 200.0 * np.sign(np.sin(2 * np.pi * 3.0 * t))  # 3 Hz square -> step inputs
    df = pd.DataFrame({
        "time": (t * 1e6).astype(np.int64),
        "gyroADC[0]": tone(0.0), "gyroADC[1]": tone(1.0), "gyroADC[2]": tone(2.0),
        "axisD[0]": tone(0.0) / 3, "axisD[1]": tone(1.0) / 3, "axisD[2]": tone(2.0) / 3,
        "setpoint[0]": setp, "setpoint[1]": setp, "setpoint[2]": setp,
        "rcCommand[3]": np.full(n, 1500),
    })

    ok = _check("synthetic sample rate ~2000 Hz", round(bbs.sample_rate(df)) == 2000)

    spec = sa.analyse(df, bbs.sample_rate(df), "gyro", ["roll"])
    peaks = [p["freq_hz"] for p in spec.get("roll", {}).get("peaks", [])]
    ok &= _check("spectral finds the 220 Hz tone",
                 any(abs(f - f_tone) < 10 for f in peaks),
                 f"peaks={[round(f) for f in peaks]}")

    step = sr.analyse(df, bbs.sample_rate(df), ["roll"])
    ok &= _check("step_response returns the roll axis", "roll" in step)
    return ok


def test_decoder_equivalence() -> bool:
    """In-process decode == subprocess CSV decode (regression guard for the fast path).

    Runs only when a local .bbl fixture is present (git-ignored). Asserts the
    in-process `decode_dataframe` yields the same columns and row count as the
    proven `analyze_blackbox --csv` round-trip.
    """
    print("blackbox_signal — in-process vs subprocess decode (scripts/test/*.bbl)")
    try:
        import pandas  # noqa: F401
    except ImportError:
        print("  [SKIP] pandas not installed")
        return True
    logs = sorted(BBL_DIR.glob("*.bbl")) if BBL_DIR.exists() else []
    if not logs:
        print("  [SKIP] no local .bbl fixtures (see scripts/test/README.md)")
        return True
    ok = True
    for log in logs:
        fast = bbs.decode_dataframe(log)
        tmp = bbs.decode_bbl(log)
        try:
            slow = bbs.load_csv(tmp)
        finally:
            tmp.unlink(missing_ok=True)
        ok &= _check(
            f"{log.name}: same columns & row count",
            list(fast.columns) == list(slow.columns) and len(fast) == len(slow),
            f"{len(fast)} vs {len(slow)} rows, {len(fast.columns)} cols",
        )
    return ok


def main() -> int:
    results = [
        test_parse_diff(),
        test_blackbox_headers(),
        test_chirp(),
        test_analysers_smoke(),
        test_decoder_equivalence(),
    ]
    print()
    if all(results):
        print("selftest: all checks passed")
        return 0
    print("selftest: FAILURES above")
    return 1


if __name__ == "__main__":
    sys.exit(main())
