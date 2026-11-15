"""Regression: conversation-scoped streams must not deadlock SQLite.

The route used to flush a usage row on the request session (taking
SQLite's single write lock) and then hand generation to a *second*,
job-owned session. The request session stayed open — lock held — for the
whole stream, so the first gateway write on the job session (sticky route,
key stats) failed with ``database is locked`` and the user got no answer.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app


def _sse(obj: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(obj)}\n\n".encode()


@pytest.fixture
def fake_stream(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    writes: list[str] = []

    async def chat_stream(
        self: Any, session: AsyncSession, payload: dict[str, Any], **_: Any
    ) -> tuple[AsyncIterator[bytes], Any, Any, str]:
        # Mirror Gateway._sticky_set: a committed write on the job session.
        await session.execute(text("CREATE TABLE IF NOT EXISTS _lock_probe (v TEXT)"))
        await session.execute(text("INSERT INTO _lock_probe VALUES ('ok')"))
        await session.commit()
        writes.append("committed")

        async def gen() -> AsyncIterator[bytes]:
            yield _sse({"choices": [{"delta": {"content": "hello"}}]})
            yield b"data: [DONE]\n\n"

        return (gen(), SimpleNamespace(name="fakeprov", id=0),
                SimpleNamespace(label="k1", id=0), "fake-model")

    from app.gateway import Gateway

    monkeypatch.setattr(Gateway, "chat_stream", chat_stream)
    monkeypatch.setattr("app.services.astra_memory.ensure_collections", lambda: None)
    monkeypatch.setattr("app.services.astra_memory.store_short", lambda *a, **k: None)

    async def no_memory(*_: Any, **__: Any) -> str:
        return ""

    monkeypatch.setattr("app.services.agents.get_memory_text", no_memory)
    return writes


def test_detached_stream_can_write_while_request_session_open(
    fake_stream: list[str],
) -> None:
    body = {
        "model": "auto", "stream": True, "nova_structured": False,
        "conversation_id": "lock-regression",
        "messages": [{"role": "user", "content": "hi"}],
    }
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", json=body)
        frames = r.text
    assert r.status_code == 200
    assert "database is locked" not in frames, frames[:400]
    assert fake_stream == ["committed"]
    assert "hello" in frames


def test_internal_errors_are_never_shown_verbatim() -> None:
    from app.services.reliable import last_resort_answer, user_facing_error

    raw = ("(sqlite3.OperationalError) database is locked\n"
           "[SQL: UPDATE sticky_routes SET provider_name=?]")
    friendly = user_facing_error(raw)
    assert "SQL" not in friendly and "sqlite" not in friendly.lower()
    assert "try again" in friendly
    answer = last_resort_answer("hi", failures=[raw, raw])
    assert "sticky_routes" not in answer
    assert answer.count(friendly) == 1       # duplicates collapsed
    assert user_facing_error("rate limited by groq") == "rate limited by groq"


def test_empty_tool_loop_answer_retries_without_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def route(*_: Any, **__: Any) -> str:
        return "tools"

    async def tool_loop(*_: Any, **__: Any) -> dict[str, Any]:
        return {"choices": [{"message": {"role": "assistant", "content": ""}}]}

    calls: list[dict[str, Any]] = []

    async def chat(self: Any, session: Any, payload: dict[str, Any],
                   **_: Any) -> tuple[dict[str, Any], Any, Any, str]:
        calls.append(payload)
        return ({"choices": [{"message": {"content": "Hi there!"}}]},
                SimpleNamespace(name="p"), SimpleNamespace(label="k"), "m")

    from app.gateway import Gateway

    monkeypatch.setattr("app.services.orchestrator.route_message", route)
    monkeypatch.setattr("app.services.tools.run_tool_loop", tool_loop)
    monkeypatch.setattr(Gateway, "chat", chat)
    monkeypatch.setattr("app.services.astra_memory.ensure_collections", lambda: None)
    monkeypatch.setattr("app.services.astra_memory.store_short", lambda *a, **k: None)

    async def no_memory(*_: Any, **__: Any) -> str:
        return ""

    monkeypatch.setattr("app.services.agents.get_memory_text", no_memory)
    body = {"model": "auto", "stream": True, "nova_structured": False,
            "builtin_tools": ["*"], "conversation_id": "empty-retry",
            "messages": [{"role": "user", "content": "hi"}]}
    with TestClient(app) as c:
        frames = c.post("/v1/chat/completions", json=body).text
    assert "asking again without tools" in frames
    assert "Hi there!" in "".join(
        json.loads(line[5:])["choices"][0]["delta"].get("content", "")
        for line in frames.splitlines()
        if line.startswith("data: {\"choices\"") and json.loads(line[5:])["choices"])
    assert len(calls) == 1 and calls[0]["stream"] is False
