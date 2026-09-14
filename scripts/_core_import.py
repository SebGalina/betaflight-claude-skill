#!/usr/bin/env python3
"""Import guard for the compute core — stdlib only, must stay 3.9-parsable.

The analysis CLIs are thin wrappers over `betaflight_chirp_core`. When that
package is missing, the bare `ModuleNotFoundError` points at the wrong problem:
the usual cause is not "forgot to install it" but "this interpreter is too old
to install it at all". The core declares `requires-python >=3.10`, and the
default `python3` on macOS is still 3.9 (Xcode's), where pip finds no candidate
and fails with `No matching distribution found` — which reads as a missing
release on PyPI. `require_core()` says which of the two it actually is.

Nothing here may use syntax newer than 3.9 (no `X | Y` annotations): this module
has to be importable on the very interpreter whose version it is diagnosing.
"""

import importlib.util
import sys

MIN_PYTHON = (3, 10)
CORE_PKG = "betaflight_chirp_core"

_PY_TOO_OLD = """\
error: Python {found} is too old — this skill needs Python {needed} or later.

The compute core `betaflight-chirp-core` declares `requires-python >={needed}`,
so pip cannot install it here: it reports `No matching distribution found`,
which looks like a missing release on PyPI but is really this interpreter.
The scripts themselves also use `X | Y` union type syntax, added in 3.10.

On macOS the default `python3` is 3.9 (the one from Xcode's command line
tools). Install a newer Python from https://www.python.org/downloads/ and
build the venv with it explicitly:

    python3.12 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
"""

_CORE_MISSING = """\
error: the compute core `{pkg}` is not installed (Python {found} is fine).

The analysis scripts are thin CLI wrappers over it. Install it from the repo
root, which pins the version this release was tested against:

    pip install -r requirements.txt

Or, with uv, let it resolve from pyproject.toml on first run:

    uv run python -m scripts.<name> <log.bbl>

Zip / claude.ai users should not see this: the release zip vendors the
package into scripts/. If you are running from a zip, the skill was loaded
without its scripts/ folder — reload the whole skill.
"""


def _fmt(template, **kw):
    found = "{}.{}.{}".format(*sys.version_info[:3])
    needed = "{}.{}".format(*MIN_PYTHON)
    return template.format(found=found, needed=needed, **kw)


def require_python():
    """Exit with an explicit message when the interpreter predates 3.10."""
    if sys.version_info < MIN_PYTHON:
        sys.exit(_fmt(_PY_TOO_OLD))


def require_core():
    """Exit with an explicit message when the core cannot be imported.

    Checks the interpreter first: on an old Python the core is not merely
    absent, it is uninstallable, and saying so is the whole point.
    """
    require_python()
    if importlib.util.find_spec(CORE_PKG) is None:
        sys.exit(_fmt(_CORE_MISSING, pkg=CORE_PKG))


def core_available():
    """True when the core is importable — for callers that skip instead of exit."""
    return sys.version_info >= MIN_PYTHON and importlib.util.find_spec(CORE_PKG) is not None
