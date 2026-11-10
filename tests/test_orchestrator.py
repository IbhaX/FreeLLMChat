import asyncio

import app.services.orchestrator as orch
import app.services.tools as tools_mod


class FakeGateway:
    async def chat(self, session, payload, **kw):
        return {"choices": [{"message": {"content": "YES"}}]}, None, None, "m"


DEEP_Q = "Compare the economic policies, industrial impacts, and long-term outcomes of Tesla versus Gauss across multiple countries with detailed analysis"


def _route(payload, last_user, monkeypatch_wants=None):
    async def wants_tools(*a, **kw):
        return True if monkeypatch_wants is None else monkeypatch_wants

    orig = tools_mod.wants_tools
    tools_mod.wants_tools = wants_tools
    try:
        return asyncio.run(orch.route_message(
            FakeGateway(), None, payload, last_user=last_user, history=[]))
    finally:
        tools_mod.wants_tools = orig


def test_direct_without_builtin_tools():
    assert _route({"messages": []}, "hello") == "direct"


def test_greeting_skips_tools():
    assert _route({"messages": [], "builtin_tools": True}, "hi", monkeypatch_wants=False) == "direct"


def test_simple_question_uses_tool_loop():
    assert _route({"messages": [], "builtin_tools": True}, "what is the weather in Paris") == "tools"


def test_deep_question_routes_to_research():
    assert _route({"messages": [], "builtin_tools": True}, DEEP_Q) == "research"


def test_routing_failure_falls_back_safely():
    class Boom:
        async def chat(self, *a, **kw):
            raise RuntimeError("down")

    # wants_tools fails open to True inside tools.py; orchestrator itself
    # must still return a valid route even if classification explodes.
    r = asyncio.run(orch.route_message(Boom(), None, {"messages": [], "builtin_tools": True},
                                       last_user="hi", history=[]))
    assert r in ("tools", "research")
