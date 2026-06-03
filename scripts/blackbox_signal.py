#!/usr/bin/env python3
"""
blackbox_signal.py — shared signal-analysis helpers for the blackbox tools.

`spectral_analysis.py`, `step_response.py` and `chirp_analysis.py` all start
from the same raw material: a decoded blackbox DataFrame, its loop/log rate, and
a flight-activity mask. Those helpers used to be copy-pasted into each script;
they live here so there is a single copy to maintain.

The decode step shells out to `analyze_blackbox.py --csv` (the canonical
pure-Python decoder) rather than re-implementing frame parsing.

Requires: numpy, pandas (the callers already depend on both).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

# Column names in the decoded CSV (blackbox_decoder output).
TIME_COL = "time"
THROTTLE_COL = "rcCommand[3]"


def decode_dataframe(bbl_path: Path, session=None) -> pd.DataFrame:
    """Decode a .bbl/.bfl straight to a DataFrame, in-process.

    Imports the pure-Python `blackbox_decoder` directly instead of shelling out
    to `analyze_blackbox.py --csv` — no subprocess, no temp file, and no
    float round-trip through CSV. The resulting frame is identical to what
    `analyze_blackbox --csv` writes: raw decoded main frames, columns named after
    the log's field definitions (`time`, `gyroADC[0]`, `setpoint[0]`, ...).

    Session selection mirrors analyze_blackbox: `session` is 1-based; when it is
    None the first *decodable* session is returned (a session whose header/data
    fail to parse is skipped).
    """
    sys.path.insert(0, str(Path(__file__).parent))
    import blackbox_decoder as bb  # stdlib-only; safe to import lazily

    raw = Path(bbl_path).read_bytes()
    ranges = bb.split_sessions(raw)
    if not ranges:
        raise ValueError(
            "No blackbox log section markers found — file may be corrupt "
            "or not a Betaflight log"
        )
    if session is not None:
        if session < 1 or session > len(ranges):
            raise ValueError(f"session {session} out of range (1..{len(ranges)})")
        candidates = [ranges[session - 1]]
    else:
        candidates = ranges

    last_err: Exception | None = None
    for start, end in candidates:
        parser = bb.FlightLogParser(raw, start, end)
        try:
            parser.parse_header()
            parser.parse_data()
        except Exception as exc:  # noqa: BLE001 — try the next session
            last_err = exc
            continue
        if parser.main_frames:
            return pd.DataFrame(parser.main_frames, columns=parser.main_field_names)
    raise ValueError(
        f"no decodable session found in {bbl_path}"
        + (f": {last_err}" if last_err else "")
    )


def decode_bbl(bbl_path: Path, session=None) -> Path:
    """Decode a .bbl/.bfl to a temporary CSV via the analyze_blackbox subprocess.

    Kept as the isolated fallback for `load_dataframe` (and for callers that want
    a CSV on disk). Returns the temp CSV path; the caller must unlink it.
    """
    script = Path(__file__).parent / "analyze_blackbox.py"
    tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    tmp.close()
    cmd = [sys.executable, str(script), str(bbl_path), "--csv", tmp.name]
    if session is not None:
        cmd += ["--session", str(session)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"analyze_blackbox failed:\n{r.stderr}")
    return Path(tmp.name)


def load_csv(path: Path) -> pd.DataFrame:
    """Read a decoded CSV (skips the leading '#' comment lines, trims headers)."""
    df = pd.read_csv(path, comment="#")
    df.columns = [c.strip() for c in df.columns]
    return df


def load_dataframe(input_path, session=None) -> pd.DataFrame:
    """Load a decoded CSV, or decode a .bbl/.bfl into a DataFrame.

    For binary logs the fast in-process decoder is used; if it raises for any
    reason we fall back to the proven `analyze_blackbox --csv` subprocess path
    (temp file auto-cleaned), so a decoder edge case never blocks an analysis.
    """
    path = Path(input_path)
    if path.suffix.lower() in (".bbl", ".bfl"):
        try:
            return decode_dataframe(path, session)
        except Exception:  # noqa: BLE001 — isolated subprocess fallback
            tmp = decode_bbl(path, session)
            try:
                return load_csv(tmp)
            finally:
                tmp.unlink(missing_ok=True)
    return load_csv(path)


def sample_rate(df: pd.DataFrame) -> float:
    """Estimate the loop/log rate (Hz) from the time column (microseconds)."""
    dt_us = float(np.median(np.diff(df[TIME_COL].values[:4000])))
    return 1_000_000.0 / dt_us


def active_mask(df: pd.DataFrame, throttle_min: int = 1100) -> np.ndarray:
    """Keep only frames where the craft is actually flying (throttle above idle).

    Noise and closed-loop behaviour are throttle-dependent; idle/disarmed samples
    would dilute the spectrum. Falls back to "keep everything" when throttle is
    absent from the log.
    """
    if THROTTLE_COL in df.columns:
        return df[THROTTLE_COL].values > throttle_min
    return np.ones(len(df), dtype=bool)
