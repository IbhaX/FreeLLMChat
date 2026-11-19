"""Isolated browser-fixture server for deterministic UI journeys.

Runs the REAL FastAPI app and routes against a temporary SQLite database.
Only the provider boundary (gateway) and the external tool boundary
(web_search / exa_search / http_get) are scripted. No production switch is
involved: this module patches the boundaries before the app is imported.

Usage:  python -m tests.browser.fixture_app --port 5011 [--db PATH]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

RESEARCH_MARKER = "real-world research, not continuation"

PRIMARY_URL = "https://example.org/primary"
SECONDARY_URL = "https://example.org/secondary"

RESEARCH_ENVELOPE: dict[str, Any] = {
    "response_type": "general",
    "content": ("Two sources compare the options [1][2]. The primary source "
                "describes the baseline, while the secondary source gives "
                "the alternative figures."),
    "components": [{
        "type": "table",
        "columns": ["Option", "Summary"],
        "rows": [["Baseline", "Described by the primary source [1]"],
                 ["Alternative", "Described by the secondary source [2]"]],
    }],
    "sources": [
        {"title": "Primary source", "url": PRIMARY_URL},
        {"title": "Secondary source", "url": SECONDARY_URL},
    ],
}


PLAIN_TABLE_MD = (
    "Two sources compare the options [1][2].\n\n"
    "| Option | Summary |\n|---|---|\n"
    "| Baseline | Described by the primary source [1] |\n"
    "| Alternative | Described by the secondary source [2] |\n\n"
    "The primary source describes the baseline; the secondary source gives "
    "the alternative.")

PLAIN_CODE_MD = (
    "A short sample [1]:\n\n```python\nprint('baseline')\n```\n\n"
    "Source [1] is the primary page.")


def _sse_frame(content: str) -> bytes:
    body = json.dumps({"choices": [{"delta": {"content": content}}]},
                      ensure_ascii=False)
    return f"data: {body}\n\n".encode("utf-8")


class ScriptedGateway:
    """Provider boundary: deterministic replies, optional streaming delay."""

    def __init__(self, chunk_delay: float = 0.12) -> None:
        self.chunk_delay = chunk_delay

    def _reply_for(self, payload: dict[str, Any]) -> str:
        messages = payload.get("messages", [])
        system = " ".join(str(m.get("content", "")) for m in messages
                          if m.get("role") == "system")
        user = next((str(m.get("content", "")) for m in reversed(messages)
                     if m.get("role") == "user"), "").lower()
        if RESEARCH_MARKER in system:
            if "envelope" in user:
                # valid envelope with NO components: sources become a card
                env = dict(RESEARCH_ENVELOPE, components=[])
                return "```nova-response\n" + json.dumps(env) + "\n```"
            if "code" in user:
                return PLAIN_CODE_MD
            # plain markdown (no envelope): exercises deterministic promotion
            return PLAIN_TABLE_MD
        return "Elementary. Tell me the facts, every detail."

    async def chat(self, session: Any, payload: dict[str, Any],
                   on_event: Any = None, **kw: Any) -> tuple[Any, Any, Any, str]:
        text = self._reply_for(payload)
        model = str(payload.get("model") or "fixture/model")
        return ({"choices": [{"message": {"content": text}}]},
                None, None, model)

    async def chat_stream(self, session: Any, payload: dict[str, Any],
                          on_event: Any = None) -> tuple[Any, Any, Any, str]:
        text = self._reply_for(payload)
        model = str(payload.get("model") or "fixture/model")
        step = max(1, len(text) // 14)
        pieces = [text[i:i + step] for i in range(0, len(text), step)]
        delay = self.chunk_delay

        async def gen() -> AsyncIterator[bytes]:
            for piece in pieces:
                await asyncio.sleep(delay)
                yield _sse_frame(piece)
            yield b"data: [DONE]\n\n"

        return gen(), None, None, model

    async def provider_catalog(self, session: Any) -> dict[str, Any]:
        return {"providers": [], "models": []}

    async def close(self) -> None:
        return None


async def _fixture_run_tool(name: str, args: dict[str, Any],
                            original: Any) -> str:
    """Tool boundary: scripted search/fetch results; everything else real."""
    if name == "web_search":
        await asyncio.sleep(0.8)  # slow enough to observe 'searching'
        return ("1. Primary source\n"
                f"   {PRIMARY_URL}\n"
                "   Primary snippet about the baseline option.\n"
                "2. Other page\n"
                "   https://example.org/other\n"
                "   Unrelated snippet.")
    if name == "exa_search":
        await asyncio.sleep(0.4)
        return ("1. Secondary source\n"
                f"   {SECONDARY_URL}\n"
                "   Secondary snippet about the alternative option.")
    if name == "http_get":
        return ("HTTP 200\n<html><head><script>var x=1;</script></head>"
                "<body><article><p>Primary page text about the baseline."
                "</p></article></body></html>")
    return await original(name, args)


DOSSIER_SECTIONS = (
    "## Identity\n\nAlbin Doe is a staff engineer at ExampleCorp [1].\n\n"
    "## Career timeline\n\n"
    + "\n\n".join(
        f"- 20{10 + i:02d}: Dated role {i + 1} at employer {i + 1} "
        f"with citation [{(i % 4) + 1}]."
        for i in range(8))
    + "\n\n## Gaps\n\nChildhood details are not established from the "
    "available public sources.\n\nDOSSIER-SENTINEL-END")

DOSSIER_ENVELOPE = {
    "response_type": "search_results",
    "components": [
        {"type": "person_card",
         "data": {"name": "Albin Doe",
                  "description": "Staff engineer · ExampleCorp",
                  "url": PRIMARY_URL}},
        {"type": "timeline",
         "data": {"title": "Career",
                  "events": [{"date": "2010", "title": "First role",
                              "detail": "Fixture event [1]"}]}},
    ],
    "sources": [
        {"title": "Primary source", "url": PRIMARY_URL},
        {"title": "Secondary source", "url": SECONDARY_URL},
        {"title": "Career record", "url": "https://example.org/career"},
        {"title": "Talk archive", "url": "https://example.org/talks"},
    ],
}


async def _fixture_run_research(
    gateway: Any, session: Any, messages: list[dict[str, Any]],
    *, model: str = "auto", conversation_id: str | None = None,
    depth: str | None = None, on_event: Any = None,
) -> Any:
    """Scripted deep-research boundary for roleplay dossier turns.

    Emits the same public event sequence the real pipeline produces
    (stage → finding per sub-question → synthesis) and returns the long
    markdown report plus a nova-response metadata fence. Asserts the
    caller forced depth='deep'.
    """
    emit = on_event or (lambda ev: None)
    assert depth == "deep", f"dossier turns must force deep depth, got {depth!r}"
    emit({"event": "stage", "stage": "plan", "text": "Planning dossier"})
    await asyncio.sleep(0.3)
    for i, title in enumerate(("Early life", "Career", "Projects"), 1):
        emit({"event": "finding", "result_id": f"find:sq{i}:001",
              "claim": f"{title} finding {i}", "task_id": f"sq:{i}",
              "actor": "researcher", "status": "done",
              "evidence_class": "unverified",
              "title": f"{title} evidence found"})
        await asyncio.sleep(0.15)
    emit({"event": "source_discovered", "title": "Primary source",
          "url": PRIMARY_URL, "result_id": "find:sq1:001",
          "task_id": "sq:1", "status": "done"})
    emit({"event": "answer_section", "section_id": "s:1",
          "title": "Identity", "text": "Albin Doe is a staff engineer [1].",
          "task_id": "synthesis", "status": "running"})
    # Same shape as gateway._usage_event: completed model call accounting.
    emit({"event": "usage", "model": "fixture/deep-model",
          "provider": "fixture", "tokens_in": 4200, "tokens_out": 1800,
          "tokens_total": 6000, "duration_ms": 5400,
          "usage_reported": True, "cost_usd": None, "currency": "USD"})
    report = (DOSSIER_SECTIONS + "\n\n```nova-response\n"
              + json.dumps(DOSSIER_ENVELOPE, ensure_ascii=False) + "\n```")
    return report


def install_boundaries(gateway_cls: type[ScriptedGateway] = ScriptedGateway) -> None:
    """Patch ONLY the provider and tool boundaries of the real app."""
    import app.main as main_mod
    from app.services import roleplay as rp_mod
    from app.services import tools as tools_mod

    main_mod.Gateway = gateway_cls  # lifespan builds app.state.gateway from this

    original_run_tool = tools_mod.run_tool

    async def scripted(name: str, args: dict[str, Any]) -> str:
        return await _fixture_run_tool(name, args, original_run_tool)

    async def fixture_key() -> str:
        return "fixture-exa-key"

    tools_mod.run_tool = scripted
    tools_mod._exa_api_key = fixture_key
    # roleplay.py binds run_research at import time; replace the name it holds.
    rp_mod.run_research = _fixture_run_research


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5011)
    parser.add_argument("--db", default="")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else Path(tempfile.mkdtemp(prefix="nova-fixture-")) / "fixture.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    # Temp fixture only: an empty key disables gateway auth for this process
    # (overrides any .env value; the real database and keys are never used).
    os.environ["GATEWAY_API_KEY"] = ""

    install_boundaries()
    import app.main as main_mod
    print(f"FIXTURE_DB={db_path}", flush=True)
    uvicorn.run(main_mod.app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
