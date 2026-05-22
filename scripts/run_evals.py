#!/usr/bin/env python3
"""
Eval runner for the Betaflight skill.

Loads SKILL.md + all references/ as system prompt, runs each eval prompt
against the Claude API, then uses a judge model to score pass/fail.

Usage:
    python -m scripts.run_evals                  # run all evals
    python -m scripts.run_evals --ids 6 7 8      # run specific evals
    python -m scripts.run_evals --verbose        # show full responses
    python -m scripts.run_evals --model sonnet   # override skill model

Requirements:
    pip install anthropic python-dotenv
    ANTHROPIC_API_KEY in environment or .env file at repo root
"""

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path

try:
    import anthropic
except ImportError:
    sys.exit("Missing dependency: pip install anthropic")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass  # python-dotenv optional — fall back to env var

REPO_ROOT   = Path(__file__).parent.parent
EVALS_FILE  = REPO_ROOT / "evals" / "evals.json"
EVALS_DIR   = REPO_ROOT / "evals"
SKILL_FILE  = REPO_ROOT / "SKILL.md"
REFS_DIR    = REPO_ROOT / "references"

SKILL_MODEL = "claude-sonnet-4-6"
JUDGE_MODEL = "claude-haiku-4-5-20251001"

JUDGE_PROMPT = """\
You are an evaluator for an AI skill. Your job is to decide if a response \
satisfies the expected behavior described below.

Expected behavior:
{expected}

Response to evaluate:
{response}

Answer with exactly one of:
PASS — the response clearly satisfies the expected behavior
FAIL — the response does not satisfy the expected behavior

Then on a new line, write one short sentence explaining your verdict.
Do not add anything else."""


def load_skill_context() -> str:
    parts = [SKILL_FILE.read_text()]
    for ref in sorted(REFS_DIR.glob("*.md")):
        parts.append(f"\n\n---\n# {ref.name}\n\n{ref.read_text()}")
    return "\n".join(parts)


def load_evals(ids: list[int] | None) -> list[dict]:
    data = json.loads(EVALS_FILE.read_text())
    evals = data["evals"]
    if ids:
        evals = [e for e in evals if e["id"] in ids]
    return evals


def build_user_message(eval_case: dict) -> str:
    prompt = eval_case["prompt"]
    for filename in eval_case.get("files", []):
        path = EVALS_DIR / filename
        if path.exists():
            prompt += f"\n\n```\n{path.read_text()}\n```"
        else:
            print(f"  [warn] attachment not found: {path}", file=sys.stderr)
    return prompt


def run_skill(client: anthropic.Anthropic, system: str, user_message: str, model: str) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


def judge(client: anthropic.Anthropic, expected: str, response: str) -> tuple[bool, str]:
    prompt = JUDGE_PROMPT.format(expected=expected, response=response)
    result = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=128,
        messages=[{"role": "user", "content": prompt}],
    )
    text = result.content[0].text.strip()
    lines = text.splitlines()
    verdict = lines[0].strip().upper().startswith("PASS")
    reason = lines[1].strip() if len(lines) > 1 else ""
    return verdict, reason


def main():
    parser = argparse.ArgumentParser(description="Run Betaflight skill evals")
    parser.add_argument("--ids",     nargs="+", type=int, help="Eval IDs to run (default: all)")
    parser.add_argument("--verbose", action="store_true",  help="Print full skill responses")
    parser.add_argument("--model",   default=SKILL_MODEL,  help="Model for skill invocation")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ANTHROPIC_API_KEY not set. Add it to your environment or to a .env file.")

    client  = anthropic.Anthropic(api_key=api_key)
    system  = load_skill_context()
    evals   = load_evals(args.ids)

    if not evals:
        sys.exit("No evals matched.")

    print(f"Running {len(evals)} eval(s) — skill model: {args.model}\n")

    passed = failed = 0
    results = []

    for ev in evals:
        name = ev["name"]
        print(f"[{ev['id']:02d}] {name} ... ", end="", flush=True)

        user_msg  = build_user_message(ev)
        response  = run_skill(client, system, user_msg, args.model)
        ok, reason = judge(client, ev["expected_output"], response)

        status = "PASS" if ok else "FAIL"
        print(status)
        if reason:
            print(f"      {reason}")

        if args.verbose:
            print(textwrap.indent(response, "    > "))
            print()

        (passed if ok else failed).__class__  # just to reference
        if ok:
            passed += 1
        else:
            failed += 1

        results.append({"id": ev["id"], "name": name, "pass": ok, "reason": reason})

    print(f"\n{'─' * 40}")
    print(f"Results: {passed} passed, {failed} failed / {len(evals)} total")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
