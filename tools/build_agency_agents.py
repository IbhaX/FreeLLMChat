"""Generate ``app/data/agency_agents.json`` from the agency-agents repo.

Fetches https://github.com/msitarzewski/agency-agents (codeload zip, one
request), parses every ``<category>/<name>.md`` agent file's YAML
frontmatter + persona body, and writes a compact seed bundle consumed by
``app.services.agency_agents.seed_agency_agents``.

Usage:  python tools/build_agency_agents.py [path-to-zip]
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

ZIP_URL = "https://codeload.github.com/msitarzewski/agency-agents/zip/refs/heads/main"
OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "agency_agents.json"
SKIP_DIRS = {".github", "examples", "integrations"}
_MAX_PROMPT = 12000  # chars — a few files run 20-28KB of examples; cap them


def _frontmatter(raw: str) -> dict[str, Any] | None:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.S)
    if not m:
        return None
    fm: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        kv = re.match(r"^(\w[\w-]*)\s*:\s*(.*)$", line.strip())
        if kv:
            fm[kv.group(1)] = kv.group(2).strip().strip('"').strip("'")
    if not fm.get("name") or not fm.get("description"):
        return None
    fm["_body"] = raw[m.end():].strip()
    return fm


def build(zip_bytes: bytes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    with zipfile.ZipFile(BytesIO(zip_bytes)) as z:
        for entry in sorted(z.namelist()):
            parts = entry.split("/")
            if len(parts) != 3 or not parts[2].endswith(".md"):
                continue
            cat = parts[1]
            if cat in SKIP_DIRS:
                continue
            raw = z.read(entry).decode("utf-8", "replace")
            fm = _frontmatter(raw)
            if fm is None:
                continue
            slug = parts[2][:-3]
            if slug.startswith(cat + "-"):
                slug = slug[len(cat) + 1:]
            slug = re.sub(r"[^\w-]", "-", slug.lower()).strip("-")
            if not slug or slug in seen:
                slug = f"{cat}-{slug}" if slug else cat
                if slug in seen:
                    continue
            seen.add(slug)
            body = fm["_body"][:_MAX_PROMPT]
            out.append({
                "name": slug,
                "display_name": str(fm["name"]),
                "description": str(fm["description"])[:500],
                "emoji": str(fm.get("emoji") or "🤖"),
                "category": cat,
                "vibe": str(fm.get("vibe") or "")[:300],
                "system_prompt": body,
            })
    return out


def main() -> None:
    if len(sys.argv) > 1:
        data = Path(sys.argv[1]).read_bytes()
    else:
        with urllib.request.urlopen(ZIP_URL, timeout=60) as r:
            data = r.read()
    agents = build(data)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(agents, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(agents)} agents -> {OUT}")


if __name__ == "__main__":
    main()
