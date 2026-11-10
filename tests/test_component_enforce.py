import asyncio
import json

from app.services.component_enforce import (
    enforce_components,
    extract_envelope,
    has_renderable_components,
    is_trivial,
    markdown_to_envelope,
)

ENV = '```nova-response\n{"response_type":"general","content":"Hi","components":[{"type":"list","data":{"items":["a","b"]}}]}\n```'
MD = """## Groq vs Gemini

Quick comparison of free tiers.

| Aspect | Groq | Gemini |
|---|---|---|
| RPM | 30 | 15 |
| Context | 128K | 1M |

### Pros
- Fast
- Free

**Requests/day**: 14,400
**Latency**: 0.3s
"""


def _res(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class FakeGateway:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def chat(self, session, payload, **kw):
        self.calls.append(payload)
        if isinstance(self.reply, Exception):
            raise self.reply
        return _res(self.reply), None, None, "m"


def test_extract_and_detect():
    assert extract_envelope(ENV)["response_type"] == "general"
    assert has_renderable_components(ENV)
    assert not has_renderable_components(MD)
    # unterminated fence still detected
    assert has_renderable_components(ENV[:-4])


def test_trivial():
    assert is_trivial("Hello! How can I help?")
    assert not is_trivial("- a\n- b")
    assert not is_trivial(MD)


def test_markdown_fallback_maps_structures():
    fence = markdown_to_envelope(MD)
    env = extract_envelope(fence)
    types = [c["type"] for c in env["components"]]
    assert types == ["table", "list", "stat_grid"]
    table = env["components"][0]["data"]
    assert table["columns"] == ["Aspect", "Groq", "Gemini"]
    assert table["rows"][1] == ["Context", "128K", "1M"]
    assert env["components"][1]["data"]["title"] == "Pros"
    assert env["content"].startswith("Groq vs Gemini")
    assert markdown_to_envelope("just prose, no structure here at all") is None


def test_enforce_passthrough_and_trivial():
    gw = FakeGateway(ENV)
    r, m = asyncio.run(enforce_components(gw, None, {"messages": []}, _res(ENV)))
    assert m["component_used"] and not m["repair_used"] and not gw.calls
    r, m = asyncio.run(enforce_components(gw, None, {"messages": []}, _res("Hi there!")))
    assert not m["component_used"] and not gw.calls


def test_enforce_repair_then_fallback():
    gw = FakeGateway(ENV)
    payload = {"messages": [{"role": "user", "content": "compare"}], "tools": [1], "stream": True}
    r, m = asyncio.run(enforce_components(gw, None, payload, _res(MD)))
    assert m == {"component_used": True, "repair_used": True, "fallback_used": False}
    sent = gw.calls[0]
    assert "tools" not in sent and sent["stream"] is False
    assert sent["messages"][-1]["content"].startswith("Rewrite your previous answer")
    assert r["choices"][0]["message"]["content"] == ENV

    gw = FakeGateway(MD)  # repair still markdown -> deterministic fallback
    r, m = asyncio.run(enforce_components(gw, None, payload, _res(MD)))
    assert m == {"component_used": True, "repair_used": False, "fallback_used": True}
    assert has_renderable_components(r["choices"][0]["message"]["content"])

    gw = FakeGateway(RuntimeError("down"))  # repair error never breaks chat
    r, m = asyncio.run(enforce_components(gw, None, payload, _res(MD)))
    assert m["fallback_used"]


def test_prompt_catalog_covers_registry():
    from app.response_protocol import component_schemas, structured_system_prompt

    p = structured_system_prompt()
    for name in component_schemas():
        assert f"- `{name}`" in p
    assert "MUST" in p and "never" in p.lower()


def test_stream_endpoint_replaces_markdown_with_components(monkeypatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app.main import app

    md_reply = "## Benefits\n- Types\n- Tooling\n- Refactors\n<tool_call>x</tool_call>"

    async def fake_stream():
        for piece in (md_reply[:10], md_reply[10:]):
            chunk = {"choices": [{"index": 0, "delta": {"content": piece}}]}
            yield f"data: {json.dumps(chunk)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    with TestClient(app) as c:
        gw = app.state.gateway

        async def chat_stream(session, payload, on_event=None):
            prov = SimpleNamespace(name="fake")
            return fake_stream(), prov, SimpleNamespace(label="k"), "m1"

        async def chat(session, payload, **kw):  # repair returns markdown again
            return _res(md_reply), None, None, "m1"

        monkeypatch.setattr(gw, "chat_stream", chat_stream)
        monkeypatch.setattr(gw, "chat", chat)
        r = c.post("/v1/chat/completions", json={
            "model": "auto", "stream": True,
            "messages": [{"role": "user", "content": "benefits of TS"}],
        })
        events = [json.loads(l[6:]) for l in r.text.split("\n") if l.startswith("data: {")]
        repl = [e["freellm"] for e in events if (e.get("freellm") or {}).get("event") == "replace_content"]
        assert repl, r.text
        assert has_renderable_components(repl[0]["content"])
        assert "<tool_call>" not in repl[0]["content"]
        assert r.text.rstrip().endswith("data: [DONE]")
        assert r.text.count("[DONE]") == 1
