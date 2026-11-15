"""Small talk must not pay for the full component contract.

"hi" used to cost ~4.4k input tokens: the ~3.1k-token component catalog
was injected into every request, plus a separate LLM tool-gate probe. A
greeting can never use a chart or table, so it gets a compact prompt and
skips the probe.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.response_protocol import conversational_system_prompt, structured_system_prompt
from app.services.intent import is_small_talk


@pytest.mark.parametrize("text", [
    "hi", "Hello!", "hey there", "thanks", "thank you so much", "ok", "good morning",
    "how are you?", "bye", "cool, thanks!", "yo", "Hi Nova",
])
def test_small_talk_detected(text: str) -> None:
    assert is_small_talk(text)


@pytest.mark.parametrize("text", [
    "hi, compare python and go for backend services",
    "what is SQLite WAL mode?",
    "thanks — now give me a table of the top 5 GPUs",
    "explain how streaming works",
    "hello can you research the latest LLM benchmarks",
    "",
])
def test_real_requests_are_not_small_talk(text: str) -> None:
    assert not is_small_talk(text)


def test_conversational_prompt_is_far_smaller() -> None:
    small, full = conversational_system_prompt(), structured_system_prompt()
    assert len(small) * 10 < len(full)


async def test_router_skips_llm_probe_for_small_talk(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import orchestrator

    async def boom(*_: Any, **__: Any) -> bool:
        raise AssertionError("tool-gate LLM probe must not run for small talk")

    monkeypatch.setattr("app.services.tools.wants_tools", boom)
    route = await orchestrator.route_message(
        None, None, {"builtin_tools": True, "messages": [{"role": "user", "content": "hi"}]},
        last_user="hi")
    assert route == "direct"


def test_chat_route_injects_compact_prompt_for_hi(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.gateway import Gateway
    from app.main import app

    seen: list[list[dict[str, Any]]] = []

    async def chat(self: Any, session: Any, payload: dict[str, Any],
                   **_: Any) -> tuple[dict[str, Any], Any, Any, str]:
        seen.append(payload["messages"])
        from types import SimpleNamespace
        return ({"choices": [{"message": {"content": "Hello!"}}]},
                SimpleNamespace(name="p"), SimpleNamespace(label="k"), "m")

    async def no_memory(*_: Any, **__: Any) -> str:
        return ""

    monkeypatch.setattr(Gateway, "chat", chat)
    monkeypatch.setattr("app.services.agents.get_memory_text", no_memory)
    monkeypatch.setattr("app.services.astra_memory.ensure_collections", lambda: None)
    monkeypatch.setattr("app.services.astra_memory.store_short", lambda *a, **k: None)
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", json={
            "model": "auto", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    system = "\n".join(m["content"] for m in seen[0] if m["role"] == "system")
    assert "COMPONENT OUTPUT CONTRACT" not in system
    assert len(system) < 1500


def _post_chat(monkeypatch: pytest.MonkeyPatch,
               payload: dict[str, Any]) -> tuple[int, list[list[dict[str, Any]]]]:
    from fastapi.testclient import TestClient

    from app.gateway import Gateway
    from app.main import app

    seen: list[list[dict[str, Any]]] = []

    async def chat(self: Any, session: Any, pl: dict[str, Any],
                   **_: Any) -> tuple[dict[str, Any], Any, Any, str]:
        seen.append(pl["messages"])
        from types import SimpleNamespace
        return ({"choices": [{"message": {"content": "done"}}]},
                SimpleNamespace(name="p"), SimpleNamespace(label="k"), "m")

    async def no_memory(*_: Any, **__: Any) -> str:
        return ""

    monkeypatch.setattr(Gateway, "chat", chat)
    monkeypatch.setattr("app.services.agents.get_memory_text", no_memory)
    monkeypatch.setattr("app.services.astra_memory.ensure_collections", lambda: None)
    monkeypatch.setattr("app.services.astra_memory.store_short", lambda *a, **k: None)
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", json=payload)
    return r.status_code, seen


def _system_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(m["content"] for m in messages if m["role"] == "system")


def test_depth_quick_adds_concise_directive(monkeypatch: pytest.MonkeyPatch) -> None:
    code, seen = _post_chat(monkeypatch, {
        "model": "auto", "depth": "quick",
        "messages": [{"role": "user", "content": "what is a closure?"}]})
    assert code == 200
    assert "QUICK depth" in _system_text(seen[0])


def test_depth_deep_adds_thorough_directive(monkeypatch: pytest.MonkeyPatch) -> None:
    code, seen = _post_chat(monkeypatch, {
        "model": "auto", "depth": "deep",
        "messages": [{"role": "user", "content": "what is a closure?"}]})
    assert code == 200
    assert "DEEP depth" in _system_text(seen[0])


def test_invalid_depth_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    code, seen = _post_chat(monkeypatch, {
        "model": "auto", "depth": "ultra",
        "messages": [{"role": "user", "content": "what is a closure?"}]})
    assert code == 200
    assert "## DEPTH\n" not in _system_text(seen[0])


async def test_quick_depth_skips_tool_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import orchestrator

    async def boom(*_: Any, **__: Any) -> bool:
        raise AssertionError("tool-gate probe must not run at depth=quick")

    monkeypatch.setattr("app.services.tools.wants_tools", boom)
    ir = await orchestrator.classify_request(
        None, None,
        {"builtin_tools": True, "depth": "quick"},
        last_user="what is a closure?")
    assert ir is not None
