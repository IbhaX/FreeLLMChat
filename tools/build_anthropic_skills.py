"""Generate ``app/data/anthropic_skills.json`` from anthropics/skills.

Fetches https://github.com/anthropics/skills (codeload zip, one request),
parses every ``skills/<dir>/SKILL.md`` frontmatter + body, and writes a
seed bundle consumed by ``app.services.skills.load_packaged_skills``.

``requires_tools`` maps a skill to registered app tools only where web
access genuinely serves the skill's purpose — bundled python scripts are
NOT imported (they carry external deps and are Claude-Code specific).

Usage:  python tools/build_anthropic_skills.py [path-to-zip]
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

ZIP_URL = "https://codeload.github.com/anthropics/skills/zip/refs/heads/main"
_DATA = Path(__file__).resolve().parent.parent / "app" / "data"
OUT = _DATA / "anthropic_skills.json"
FILES_DIR = _DATA / "skill_files"
_MAX_INSTRUCTIONS = 16000
# Resource dirs/files each skill ships — extracted so the model can run
# bundled scripts and read reference docs via python_exec.
_RES_PREFIXES = ("scripts/", "templates/", "resources/", "reference/", "assets/")
_RES_FILES = (".md", ".txt", ".xsd", ".xml", ".json", ".yaml", ".yml")

# App tools that genuinely serve a skill's purpose (registry-validated at
# load time — unknown names are dropped there).
_TOOL_MAP: dict[str, tuple[str, ...]] = {
    "webapp-testing": ("http_get", "python_exec"),
    "mcp-builder": ("web_search", "http_get"),
    "claude-api": ("web_search", "http_get"),
    "doc-coauthoring": ("web_search",),
    "internal-comms": ("web_search",),
    "academy-guide": ("web_search", "http_get"),
    # document / artifact skills run their workflows via python_exec
    "pdf": ("python_exec",),
    "docx": ("python_exec",),
    "pptx": ("python_exec",),
    "xlsx": ("python_exec",),
    "canvas-design": ("python_exec",),
    "algorithmic-art": ("python_exec",),
    "slack-gif-creator": ("python_exec",),
    "web-artifacts-builder": ("python_exec",),
}


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
    n_files = 0
    with zipfile.ZipFile(BytesIO(zip_bytes)) as z:
        for entry in sorted(z.namelist()):
            parts = entry.split("/")
            # skills-main/skills/<dir>/SKILL.md
            if (len(parts) < 4 or parts[1] != "skills" or not parts[-1]):
                continue
            rel = "/".join(parts[3:])
            raw = None
            if parts[3] == "SKILL.md" and len(parts) == 4:
                raw = z.read(entry).decode("utf-8", "replace")
                fm = _frontmatter(raw)
                if fm is None:
                    continue
                name = re.sub(
                    r"[^\w-]", "-", str(fm["name"]).lower()).strip("-")
                if not name or name in seen:
                    continue
                seen.add(name)
                out.append({
                    "name": name,
                    "version": "1.0",
                    "description": str(fm["description"])[:500],
                    "instructions": fm["_body"][:_MAX_INSTRUCTIONS],
                    "requires_tools": list(_TOOL_MAP.get(name, ())),
                    "domains": ["*"],
                    "source": "anthropics/skills",
                    "resources": "skill_files/" + name,
                })
                continue
            # resource file inside a skill dir — extract to skill_files/
            if rel.startswith(_RES_PREFIXES) or rel.endswith(_RES_FILES):
                safe = re.sub(r"[^\w-]", "-", parts[2].lower()).strip("-")
                dest_dir = (FILES_DIR / safe).resolve()
                if str(dest_dir).startswith(str(FILES_DIR.resolve())):
                    dest = dest_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(entry))
                    n_files += 1
    print(f"extracted {n_files} skill resource files -> {FILES_DIR}")
    return out


def main() -> None:
    if len(sys.argv) > 1:
        data = Path(sys.argv[1]).read_bytes()
    else:
        with urllib.request.urlopen(ZIP_URL, timeout=60) as r:
            data = r.read()
    skills = build(data)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(skills, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(skills)} skills -> {OUT}")


if __name__ == "__main__":
    main()
