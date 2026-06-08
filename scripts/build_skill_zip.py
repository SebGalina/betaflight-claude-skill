#!/usr/bin/env python3
"""Build the distributable skill zip (runtime payload only).

Produces dist/betaflight-claude-skill-v<version>.zip containing a single top-level
`betaflight/` folder with SKILL.md at its root — ready to upload to claude.ai
(Settings -> Capabilities -> Skills) or via the Skills API.

Dev-only files (evals/, .github/, CONTRIBUTING.md, the eval runner, this build
script, the self-test, test fixtures, caches) are intentionally excluded.

Usage:
    python -m scripts.build_skill_zip                 # version read from CHANGELOG
    python -m scripts.build_skill_zip --version 0.1.17
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIRNAME = "betaflight"

# Files/dirs (relative to repo root) that make up the runtime skill.
INCLUDE_FILES = ["SKILL.md", "requirements.txt", "README.md", "LICENSE.txt"]
INCLUDE_DIRS = ["references", "assets"]

# scripts/ is included selectively: runtime helpers only.
SCRIPT_EXCLUDE = {
    "run_evals.py",        # dev: needs evals/ + anthropic SDK
    "selftest.py",         # dev: smoke test
    "build_skill_zip.py",  # dev: this builder
}

# Vendored compute core: betaflight-chirp-core is the single source of truth
# (separate public repo). The sandbox cannot pip-install, so the package is
# copied into scripts/ at build time, next to chirp_analysis.py (which adds its
# own dir to sys.path and imports `betaflight_chirp_core`). Override the source
# checkout with CORE_REPO_PATH; default is a sibling clone of this repo.
CORE_PKG = "betaflight_chirp_core"
CORE_REPO = Path(os.environ.get("CORE_REPO_PATH", ROOT.parent / "betaflight-chirp-core")).resolve()


def read_version() -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(r"^## \[(\d+\.\d+\.\d+)\]", text, re.MULTILINE)
    if not m:
        sys.exit("Could not read version from CHANGELOG.md")
    return m.group(1)


def collect() -> list[tuple[Path, str]]:
    """Return (source_path, arcname) pairs, arcname rooted at betaflight/."""
    items: list[tuple[Path, str]] = []

    for name in INCLUDE_FILES:
        p = ROOT / name
        if p.exists():
            items.append((p, f"{SKILL_DIRNAME}/{name}"))

    for d in INCLUDE_DIRS:
        for p in sorted((ROOT / d).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                items.append((p, f"{SKILL_DIRNAME}/{p.relative_to(ROOT).as_posix()}"))

    scripts_dir = ROOT / "scripts"
    for p in sorted(scripts_dir.glob("*.py")):
        if p.name in SCRIPT_EXCLUDE:
            continue
        items.append((p, f"{SKILL_DIRNAME}/scripts/{p.name}"))

    # Vendor the compute core into scripts/betaflight_chirp_core/.
    core_pkg_dir = CORE_REPO / CORE_PKG
    if not core_pkg_dir.is_dir():
        sys.exit(f"vendored core not found at {core_pkg_dir}\n"
                 f"clone betaflight-chirp-core next to this repo, or set CORE_REPO_PATH")
    for p in sorted(core_pkg_dir.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            rel = p.relative_to(CORE_REPO).as_posix()  # betaflight_chirp_core/...
            items.append((p, f"{SKILL_DIRNAME}/scripts/{rel}"))

    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", help="override version (default: read from CHANGELOG.md)")
    args = ap.parse_args()

    version = args.version or read_version()
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    out = dist / f"betaflight-claude-skill-v{version}.zip"

    items = collect()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, arc in items:
            zf.write(src, arc)

    print(f"Built {out.relative_to(ROOT)} ({out.stat().st_size:,} bytes, {len(items)} files)")
    for _, arc in items:
        print(f"  {arc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
