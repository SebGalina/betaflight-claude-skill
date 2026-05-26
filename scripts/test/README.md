# Test fixtures

Drop real Betaflight blackbox logs (`*.bbl` / `*.bfl`) in this folder to exercise
the decoder. `python -m scripts.selftest` header-parses every `*.bbl` found here as
a regression check.

These logs are **intentionally git-ignored** (`*.bbl` in `.gitignore`): real logs are
multi-megabyte binaries and would bloat the repository permanently. The self-test
SKIPs this check gracefully when the folder has no logs, so a fresh clone still passes.

The committed, version-controlled fixture is `evals/sample_diff.txt` (a CLI diff),
which `selftest.py` always checks via `parse_diff` and `validate_config`.
