"""
Fetch and filter official Betaflight presets from betaflight/firmware-presets.

Usage:
    python -m scripts.fetch_presets                                         # list all 2025.12 presets
    python -m scripts.fetch_presets --category tune                         # filter by category
    python -m scripts.fetch_presets --category tune --keywords 5inch,freestyle
    python -m scripts.fetch_presets --fetch presets/2025.12/tune/foo.txt    # full CLI content
    python -m scripts.fetch_presets --json                                  # JSON output

Set GITHUB_TOKEN env var to raise the API rate limit (60 → 5000 req/hour).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

REPO = "betaflight/firmware-presets"
BRANCH = "master"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
API_BASE = f"https://api.github.com/repos/{REPO}"

CATEGORIES = ["tune", "rates", "filters", "rc_link", "rc_smoothing",
              "osd", "vtx", "leds", "modes", "other", "bnf"]


def _headers():
    h = {"Accept": "application/vnd.github.v3+json", "User-Agent": "betaflight-claude-skill"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _gh_get(path):
    url = f"{API_BASE}/{path}"
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise RuntimeError("GitHub API rate limit exceeded. Set GITHUB_TOKEN env var to increase the limit.") from e
        raise


def _raw_get(path):
    url = f"{RAW_BASE}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "betaflight-claude-skill"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _list_txt_files(api_path):
    """Return list of {name, path} for all .txt files under api_path, recursing into subdirs."""
    results = []
    entries = _gh_get(f"contents/{api_path}")
    if not isinstance(entries, list):
        return results
    for entry in entries:
        if entry["type"] == "file" and entry["name"].endswith(".txt"):
            results.append({"name": entry["name"], "path": entry["path"]})
        elif entry["type"] == "dir":
            results.extend(_list_txt_files(entry["path"]))
    return results


def _parse_header(content):
    """Parse #$ metadata lines; return dict with title, firmware_version, category,
    status, author, keywords, description (all lists where applicable), and cli."""
    meta = {
        "title": "", "firmware_version": [], "category": "", "status": "",
        "author": "", "keywords": [], "description": [], "path": "",
    }
    cli_lines = []
    header_done = False

    for line in content.splitlines():
        stripped = line.strip()
        if not header_done and stripped.startswith("#$"):
            directive = stripped[2:].strip()
            if ":" in directive:
                key, _, value = directive.partition(":")
                key = key.strip().lower()
                value = value.strip()
                if key == "title":
                    meta["title"] = value
                elif key == "firmware_version":
                    meta["firmware_version"].append(value)
                elif key == "category":
                    meta["category"] = value.upper()
                elif key == "status":
                    meta["status"] = value.upper()
                elif key == "author":
                    meta["author"] = value
                elif key == "keywords":
                    meta["keywords"] = [k.strip() for k in value.split(",") if k.strip()]
                elif key == "description" and value:
                    meta["description"].append(value)
        elif not stripped.startswith("#"):
            header_done = True
            if stripped:
                cli_lines.append(line)
        elif header_done:
            cli_lines.append(line)

    meta["cli"] = "\n".join(cli_lines).strip()
    return meta


def _matches_keywords(meta, keywords):
    if not keywords:
        return True
    search_text = " ".join([
        meta["title"].lower(),
        " ".join(meta["keywords"]).lower(),
        " ".join(meta["description"]).lower(),
        meta["author"].lower(),
    ])
    return any(kw.lower() in search_text for kw in keywords)


def list_presets(version="2025.12", categories=None, keywords=None):
    """Return list of preset metadata dicts matching the given filters."""
    if categories is None:
        try:
            entries = _gh_get(f"contents/presets/{version}")
            categories = [e["name"] for e in entries if e["type"] == "dir"]
        except Exception as exc:
            print(f"Error listing categories for {version}: {exc}", file=sys.stderr)
            return []

    results = []
    for cat in categories:
        try:
            files = _list_txt_files(f"presets/{version}/{cat}")
        except Exception as exc:
            print(f"Error listing presets/{version}/{cat}: {exc}", file=sys.stderr)
            continue
        for f in files:
            try:
                content = _raw_get(f["path"])
                meta = _parse_header(content)
                meta["path"] = f["path"]
                if _matches_keywords(meta, keywords):
                    results.append(meta)
            except Exception as exc:
                print(f"Error fetching {f['path']}: {exc}", file=sys.stderr)

    return results


def fetch_preset(path):
    """Fetch full content and metadata for a specific preset path."""
    content = _raw_get(path)
    meta = _parse_header(content)
    meta["path"] = path
    return meta


def _print_list(presets, version):
    if not presets:
        print("No presets found matching the criteria.")
        return
    print(f"Found {len(presets)} preset(s) for Betaflight {version}:\n")
    for i, p in enumerate(presets, 1):
        versions = ", ".join(p["firmware_version"]) or "—"
        kw = ", ".join(p["keywords"]) if p["keywords"] else "—"
        desc = p["description"][0] if p["description"] else ""
        print(f"{i}. [{p['category']}] {p['title']}")
        print(f"   Author: {p['author'] or '—'}  |  Status: {p['status']}  |  Firmware: {versions}")
        if desc:
            print(f"   {desc}")
        print(f"   Keywords: {kw}")
        print(f"   Path: {p['path']}")
        print()


def _print_preset(meta):
    print(f"# {meta['title']}")
    if meta["author"]:
        print(f"# Author: {meta['author']}  |  Status: {meta['status']}")
    for line in meta["description"]:
        print(f"# {line}")
    if meta["description"]:
        print()
    print(meta["cli"])


def main():
    parser = argparse.ArgumentParser(
        description="Fetch official Betaflight presets from betaflight/firmware-presets"
    )
    parser.add_argument("--version", default="2025.12",
                        help="Firmware version (default: 2025.12)")
    parser.add_argument("--category",
                        help=f"Category: {', '.join(CATEGORIES)}")
    parser.add_argument("--keywords",
                        help="Comma-separated keyword filter (title, description, keywords fields)")
    parser.add_argument("--fetch", metavar="PATH",
                        help="Fetch full CLI content of a specific preset by path")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    args = parser.parse_args()

    if args.fetch:
        meta = fetch_preset(args.fetch)
        if args.json:
            print(json.dumps(meta, indent=2))
        else:
            _print_preset(meta)
        return

    categories = [args.category] if args.category else None
    keywords = [k.strip() for k in args.keywords.split(",")] if args.keywords else None

    try:
        presets = list_presets(args.version, categories, keywords)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        output = [{k: v for k, v in p.items() if k != "cli"} for p in presets]
        print(json.dumps(output, indent=2))
    else:
        _print_list(presets, args.version)


if __name__ == "__main__":
    main()
