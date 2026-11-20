"""Agent config must tolerate the shapes the UI and seed data actually store.

Builtin/seeded agents store ``capabilities`` as a plain list of strings and
``tools`` at the top level — ``_tool_names``/``_compile_system_prompt``
crashed on that with ``'list' object has no attribute 'get'`` (group chat
agents all returned the raw error text to users).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.agents import _caps, _compile_system_prompt, _tool_names


def _agent(config: Any, enable_tools: bool = False,
           output_format: str = "text") -> SimpleNamespace:
    return SimpleNamespace(
        name="a", system_prompt="sys", config=config,
        enable_tools=enable_tools, output_format=output_format,
    )


def test_list_capabilities_does_not_crash() -> None:
    a = _agent({"capabilities": ["chart selection", "dashboard design"],
                "tools": []}, enable_tools=False)
    assert _tool_names(a) == []
    assert "sys" in _compile_system_prompt(a)


def test_top_level_tools_list_is_used() -> None:
    a = _agent({"capabilities": ["api design"], "tools": ["web_search"]},
               enable_tools=True)
    assert _tool_names(a) == ["web_search"]


def test_wizard_dict_capabilities_still_work() -> None:
    a = _agent({"capabilities": {"tools_enabled": True,
                                 "tools": ["calculator"]}})
    assert _tool_names(a) == ["calculator"]


def test_enabled_with_no_tool_list_falls_back_to_all() -> None:
    assert _tool_names(_agent({}, enable_tools=True)) == ["*"]


def test_non_dict_config_is_safe() -> None:
    for bad in (None, [], "oops"):
        a = _agent(bad, enable_tools=True)
        assert _tool_names(a) == ["*"]
        assert _compile_system_prompt(a).endswith("sys")


def test_caps_only_returns_dicts() -> None:
    assert _caps({"capabilities": ["a", "b"]}) == {}
    assert _caps({"capabilities": {"tools_enabled": True}}) == {"tools_enabled": True}


def test_null_timeout_falls_back_to_default() -> None:
    """Seed agents store "timeout": null — float(None) used to crash the
    whole agent reply with 'float() argument must be ...'."""
    cfg = _agent({"execution": {"timeout": None}})
    execution = cfg.config.get("execution", {})
    timeout_s = max(1.0, min(float(execution.get("timeout") or 120), 600.0))
    assert timeout_s == 120.0


def test_null_content_does_not_crash_gateway_model() -> None:
    """Providers returning {"content": null} used to crash orchestrator
    replies with 'can only concatenate str (not "NoneType") to str'."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from pydantic_ai.models import ModelRequestParameters

    from app.agents.model import GatewayChatModel

    gw = SimpleNamespace()
    async def chat(session: Any, payload: dict[str, Any],
                       on_event: Any = None) -> tuple:
        return ({"choices": [{"message": {"content": None}}]},
                MagicMock(), MagicMock(), "m")
    gw.chat = chat
    model = GatewayChatModel("auto", gw)
    resp = asyncio.run(
        model.request([], None, ModelRequestParameters()))
    assert resp.parts[0].content == ""


def test_tool_blob_detector() -> None:
    from app.services.agents import _looks_like_tool_blob

    # Bare JSON tool-call blobs leaked as text
    assert _looks_like_tool_blob('{"query": "x", "max_results": 5}')
    assert _looks_like_tool_blob('{"tool": "web_search", "arguments": {"q": "x"}}')
    assert _looks_like_tool_blob(
        '{"tool_calls": [{"name": "web_search", "query": "q", "max_results": 3}]}')
    # Leaked call nested inside a fake json component still counts
    assert _looks_like_tool_blob(
        '{"type": "json", "data": {"tool_calls": [{"name": "web_search"}]}}')
    # Legit JSON that must NOT be flagged
    assert not _looks_like_tool_blob(
        '{"response_type": "markdown", "data": {}}')
    assert not _looks_like_tool_blob(
        '{"type": "callout", "data": {"title": "x"}}')
    assert not _looks_like_tool_blob('{"a": 1}')  # no toolish keys
    assert not _looks_like_tool_blob("plain prose answer")
    assert not _looks_like_tool_blob(
        "{" + ", ".join(f'"k{i}": 1' for i in range(7)) + ', "query": "x"}')  # >6 keys
    assert not _looks_like_tool_blob("")


class _FakeResult:
    """Stand-in for pydantic-ai's AgentRunResult."""

    def __init__(self, output: Any) -> None:
        self.output = output

    def all_messages(self) -> list[Any]:
        return []


class _FakeAgent:
    """Stand-in for a pydantic-ai ``Agent`` — returns canned outputs."""

    def __init__(self, outputs: list[Any]) -> None:
        self._outputs = list(outputs)
        self.prompts: list[str] = []

    async def run(self, prompt: str, **kwargs: Any) -> _FakeResult:
        self.prompts.append(prompt)
        return _FakeResult(self._outputs.pop(0))


async def test_run_agent_retries_tool_blob(monkeypatch) -> None:
    """A model emitting a bare JSON call gets one retry; if the retry
    still blobs, the reply is a readable fallback."""
    from app.services import agents as agents_mod

    fake = _FakeAgent(['{"query": "x", "max_results": 5}', "The real answer."])
    monkeypatch.setattr("app.agents.factory.build_agent",
                        lambda *a, **k: fake)

    async def no_memory(*a: Any, **k: Any) -> str:
        return ""
    monkeypatch.setattr(agents_mod, "get_memory_text", no_memory)

    cfg = _agent({"tools": ["web_search"]}, enable_tools=True)
    cfg.model = "m"; cfg.temperature = None; cfg.max_tokens = None
    out = await agents_mod.run_agent(SimpleNamespace(), cfg, "hi", [])
    assert out == "The real answer."
    assert fake.prompts == ["hi", agents_mod._TOOL_BLOB_RETRY]


async def test_run_agent_blob_fallback_when_retry_also_blobs(monkeypatch) -> None:
    from app.services import agents as agents_mod

    fake = _FakeAgent(['{"query": "x"}', '{"query": "y"}'])
    monkeypatch.setattr("app.agents.factory.build_agent",
                        lambda *a, **k: fake)

    async def no_memory(*a: Any, **k: Any) -> str:
        return ""
    monkeypatch.setattr(agents_mod, "get_memory_text", no_memory)

    cfg = _agent({"tools": ["web_search"]}, enable_tools=True)
    cfg.model = "m"; cfg.temperature = None; cfg.max_tokens = None
    out = await agents_mod.run_agent(SimpleNamespace(), cfg, "hi", [])
    assert out == agents_mod._TOOL_BLOB_FALLBACK


class _FakeSession:
    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def commit(self) -> None:
        return None


def _model_cfg() -> SimpleNamespace:
    cfg = _agent({}, output_format="text")
    cfg.model = "configured/model"
    cfg.temperature = None
    cfg.max_tokens = None
    return cfg


@pytest.mark.parametrize("override,expected", [
    ("picked/model", "picked/model"),
    (None, "configured/model"),
])
async def test_stream_agent_model_override(
        monkeypatch: pytest.MonkeyPatch, override: str | None, expected: str) -> None:
    import app.agents.model as model_mod
    from app.services import agents as agents_mod

    seen: list[str] = []

    async def chunks() -> Any:
        yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    class Gateway:
        async def chat_stream(self, session: Any, payload: dict[str, Any],
                              on_event: Any = None) -> Any:
            seen.append(payload["model"])
            return chunks(), None, None, None

    async def no_memory(*a: Any, **k: Any) -> str:
        return ""

    monkeypatch.setattr(agents_mod, "get_memory_text", no_memory)
    monkeypatch.setattr(model_mod, "get_session_factory", lambda: _FakeSession)
    cfg = _model_cfg()
    out = [c async for c in agents_mod.stream_agent(
        Gateway(), cfg, "hi", [], model_override=override)]
    assert any(b'"content": "hi"' in c for c in out)
    assert out[-1] == b"data: [DONE]\n\n"
    assert seen == [expected]
    assert cfg.model == "configured/model"


@pytest.mark.parametrize("override,expected", [
    ("picked/model", "picked/model"),
    (None, "configured/model"),
])
async def test_run_agent_model_override_with_tools(
        monkeypatch: pytest.MonkeyPatch, override: str | None, expected: str) -> None:
    """The model override must propagate through build_agent →
    GatewayChatModel → the gateway payload."""
    import app.agents.model as model_mod
    from app.services import agents as agents_mod

    seen: list[str] = []

    class Gateway:
        async def chat(self, session: Any, payload: dict[str, Any],
                       on_event: Any = None) -> tuple:
            seen.append(payload["model"])
            return ({"choices": [{"message": {"content": "ok"}}]},
                    None, None, "m")

    async def no_memory(*a: Any, **k: Any) -> str:
        return ""

    monkeypatch.setattr(model_mod, "get_session_factory", lambda: _FakeSession)
    monkeypatch.setattr(agents_mod, "get_memory_text", no_memory)
    cfg = _model_cfg()
    cfg.config = {"tools": ["web_search"]}
    cfg.enable_tools = True
    out = await agents_mod.run_agent(
        Gateway(), cfg, "hi", [], model_override=override)
    assert out == "ok"
    assert seen == [expected]
    assert cfg.model == "configured/model"


@pytest.mark.parametrize("output_format,expect_fence", [
    ("structured", True), ("text", True), ("json", False),
])
def test_maybe_structured_normalizes_bare_envelope(
        output_format: str, expect_fence: bool) -> None:
    from app.services.agents import _maybe_structured

    bare = '{"response_type": "general", "content": "Hello there"}'
    out = _maybe_structured(_agent({}, output_format=output_format), bare)
    if expect_fence:
        assert out.startswith("```nova-response\n")
        assert '"content": "Hello there"' in out
    else:
        assert out == bare
    plain = '{"answer": 42}'
    text_cfg = _agent({}, output_format="text")
    assert _maybe_structured(text_cfg, plain) == plain


def test_passthrough_args_validator_matches_pydanticai_contract() -> None:
    """ToolManager calls validate_json/validate_python with
    allow_partial and context kwargs (pydantic-ai 1.107) — the
    passthrough validator must accept and ignore them."""
    from app.agents.tools import _PassthroughArgsValidator

    v = _PassthroughArgsValidator()
    assert v.validate_json(
        '{"q": "x"}', allow_partial="trailing-strings", context=None
    ) == {"q": "x"}
    assert v.validate_json(b"{}", allow_partial="off") == {}
    obj = {"a": 1}
    assert v.validate_python(obj, allow_partial=True, context=None) is obj


async def test_builtin_agent_edit_survives_reseed_and_reset(session) -> None:
    """Editing a built-in agent marks it customized so seed_experts
    no longer overwrites it on boot; /reset restores the registry def."""
    from sqlalchemy import select

    from app.api.agent_routes import reset_agent, update_agent
    from app.models import AgentConfig
    from app.schemas import AgentUpdate
    from app.services.experts import seed_experts

    await seed_experts(session)
    row = (
        await session.execute(
            select(AgentConfig).where(AgentConfig.name == "deep_researcher")
        )
    ).scalar_one()
    original_prompt = row.system_prompt

    await update_agent(
        row.id, AgentUpdate(system_prompt="custom prompt"), session
    )
    assert row.config["customized"] is True

    await seed_experts(session)
    await session.refresh(row)
    assert row.system_prompt == "custom prompt"

    out = await reset_agent(row.id, session)
    assert out.system_prompt == original_prompt
    assert "customized" not in (out.config or {})


async def test_builtin_model_edit_does_not_detach(session) -> None:
    """Model picks persist through reseed without needing the flag."""
    from sqlalchemy import select

    from app.api.agent_routes import update_agent
    from app.models import AgentConfig
    from app.schemas import AgentUpdate
    from app.services.experts import seed_experts

    await seed_experts(session)
    row = (
        await session.execute(
            select(AgentConfig).where(AgentConfig.name == "fact_checker")
        )
    ).scalar_one()
    await update_agent(row.id, AgentUpdate(model="groq/m1"), session)
    assert "customized" not in (row.config or {})
    await seed_experts(session)
    await session.refresh(row)
    assert row.model == "groq/m1"
