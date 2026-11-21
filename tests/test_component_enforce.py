import asyncio
import json

from app.services.component_enforce import (
    enforce_components,
    extract_envelope,
    has_renderable_components,
    is_trivial,
    markdown_to_envelope,
)

ENV = (
    '```nova-response\n{"response_type":"general","content":"Hi",'
    '"components":[{"type":"list","data":{"items":["a","b"]}}]}\n```'
)
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
    assert types == ["table", "list", "stat_grid", "paragraph"]
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
    # The renderer agent's prompt carries the convert instruction.
    assert "Rewrite your previous answer" in sent["messages"][-1]["content"]
    # The envelope comes back re-serialized through ResponseEnvelope —
    # same components, not necessarily byte-identical text.
    env_out = extract_envelope(r["choices"][0]["message"]["content"])
    assert env_out is not None
    assert env_out["response_type"] == "general"
    assert [c["type"] for c in env_out["components"]] == ["list"]

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
        events = [
            json.loads(line[6:])
            for line in r.text.split("\n")
            if line.startswith("data: {")
        ]
        repl = [
            e["nova"]
            for e in events
            if (e.get("nova") or {}).get("event") == "replace_content"
        ]
        assert repl, r.text
        assert has_renderable_components(repl[0]["content"])
        assert "<tool_call>" not in repl[0]["content"]
        assert r.text.rstrip().endswith("data: [DONE]")
        assert r.text.count("[DONE]") == 1


def test_tool_loop_answer_is_replayed_in_deltas(monkeypatch):
    from fastapi.testclient import TestClient

    import app.services.tools as tools_mod
    from app.api import openai_routes
    from app.main import app

    monkeypatch.setattr(openai_routes, "_REPLAY_DELAY_S", 0)

    async def wants_tools(*a, **kw):
        return True

    async def chat_or_auto(gateway, session, payload, emit=None, **kw):
        return (_res(ENV), None, None, "m")

    class _FS:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    import app.agents.model as model_mod
    monkeypatch.setattr(model_mod, "chat_or_auto", chat_or_auto)
    monkeypatch.setattr(model_mod, "get_session_factory", lambda: _FS)
    monkeypatch.setattr(tools_mod, "wants_tools", wants_tools, raising=False)
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", json={
            "model": "auto", "stream": True, "builtin_tools": True,
            "messages": [{"role": "user", "content": "search the web for x"}],
        })
        events = [
            json.loads(line[6:])
            for line in r.text.split("\n")
            if line.startswith("data: {")
        ]
        deltas = [ch["delta"].get("content") for e in events for ch in e.get("choices", [])
                  if ch.get("delta", {}).get("content")]
        assert len(deltas) > 1, r.text
        assert "".join(deltas) == ENV
        assert r.text.count("[DONE]") == 1


def _person(**over):
    p = {"name": "Muhammad Ali", "description": "American boxer",
         "extract": "Muhammad Ali was an American professional boxer.",
         "thumbnail": "https://upload.wikimedia.org/ali.jpg",
         "url": "https://en.wikipedia.org/wiki/Muhammad_Ali",
         "source": "wikipedia"}
    p.update(over)
    return p


def test_person_question_name():
    from app.services.person import person_question_name

    assert person_question_name("who is muhammad ali?") == "muhammad ali"
    assert person_question_name("Who is Ada Lovelace?") == "ada lovelace"
    assert person_question_name(
        "who is muhammad ali and what did he do") == "muhammad ali"
    assert person_question_name("tell me about nikola tesla") == "nikola tesla"
    assert person_question_name("who are you") is None
    assert person_question_name("who is he") is None
    assert person_question_name("hello there") is None
    assert person_question_name("") is None
    # explicit profile-search asks name their subject
    assert person_question_name(
        "profiles of albin anthony") == "albin anthony"
    assert person_question_name(
        "find all profiles for ada lovelace") == "ada lovelace"
    assert person_question_name(
        "people named nikola tesla") == "nikola tesla"
    # generic "profiles of <thing>" needs >=2 words to count as a name
    assert person_question_name("profiles of the week") is None
    assert person_question_name(
        "give me all the profiles you can find") is None


def test_profile_followup_name():
    from app.services.person import profile_followup_name

    msgs = [
        {"role": "user", "content": "who is albin anthony?"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "give me all the profiles you can find"},
    ]
    assert profile_followup_name(msgs[-1]["content"], msgs) == "albin anthony"
    assert profile_followup_name(
        "can you provide more context?", msgs) == "albin anthony"
    # non-profile follow-ups never look backwards
    assert profile_followup_name("what is the weather", msgs) is None
    # profile ask with no person in history finds nothing
    lonely = [{"role": "user", "content": "how do I cook pasta"}]
    assert profile_followup_name("show me the profiles", lonely) is None


def test_person_card_floor_wraps_plain_reply(monkeypatch):
    async def fake_lookup(name):
        return _person()

    async def no_people(name):
        return []

    monkeypatch.setattr("app.services.person.lookup_person", fake_lookup)
    monkeypatch.setattr("app.services.person.lookup_people", no_people)
    payload = {"messages": [{"role": "user",
                             "content": "who is muhammad ali?"}]}
    reply = "Muhammad Ali was a legendary boxer and activist."
    r, m = asyncio.run(enforce_components(
        FakeGateway(reply), None, payload, _res(reply)))
    env = extract_envelope(r["choices"][0]["message"]["content"])
    assert env is not None and m["component_used"]
    card = env["components"][0]
    assert card["type"] == "person_card"
    assert card["data"]["name"] == "Muhammad Ali"
    assert card["data"]["url"] == "https://en.wikipedia.org/wiki/Muhammad_Ali"
    assert "legendary boxer" in env["content"]


def test_person_card_floor_leads_envelope(monkeypatch):
    async def fake_lookup(name):
        return _person(name=name.title())

    async def no_people(name):
        return []

    monkeypatch.setattr("app.services.person.lookup_person", fake_lookup)
    monkeypatch.setattr("app.services.person.lookup_people", no_people)
    payload = {"messages": [{"role": "user", "content": "who is ada lovelace?"}]}
    r, _ = asyncio.run(enforce_components(
        FakeGateway(ENV), None, payload, _res(ENV)))
    env = extract_envelope(r["choices"][0]["message"]["content"])
    assert env["components"][0]["type"] == "person_card"
    assert env["components"][1]["type"] == "list"


def test_person_card_floor_merges_missing_fields(monkeypatch):
    async def fake_lookup(name):
        return _person()

    async def no_people(name):
        return []

    monkeypatch.setattr("app.services.person.lookup_person", fake_lookup)
    monkeypatch.setattr("app.services.person.lookup_people", no_people)
    thin = (
        '```nova-response\n{"response_type":"general","content":"Bio",'
        '"components":[{"type":"person_card","data":{"name":"Muhammad Ali"}}'
        ',{"type":"paragraph","data":{"text":"boxer"}}]}\n```'
    )
    payload = {"messages": [{"role": "user",
                             "content": "who is muhammad ali?"}]}
    r, _ = asyncio.run(enforce_components(
        FakeGateway(thin), None, payload, _res(thin)))
    env = extract_envelope(r["choices"][0]["message"]["content"])
    cards = [c for c in env["components"] if c["type"] == "person_card"]
    assert len(cards) == 1
    assert cards[0]["data"]["thumbnail"].endswith("ali.jpg")
    assert cards[0]["data"]["extract"].startswith("Muhammad Ali was")


def test_people_carousel_when_no_notable(monkeypatch):
    async def no_person(name):
        return None

    async def fake_people(name):
        return [{"name": "Albin Anthony", "description": "GitHub user",
                 "source": "github", "url": "https://github.com/albin",
                 "facts": [{"label": "Username", "value": "@albin"}]},
                {"name": "Albin A.", "source": "github",
                 "url": "https://github.com/albina"}]

    monkeypatch.setattr("app.services.person.lookup_person", no_person)
    monkeypatch.setattr("app.services.person.lookup_people", fake_people)
    payload = {"messages": [{"role": "user",
                             "content": "who is albin anthony?"}]}
    reply = "Albin Anthony is a software engineer."
    r, m = asyncio.run(enforce_components(
        FakeGateway(reply), None, payload, _res(reply)))
    env = extract_envelope(r["choices"][0]["message"]["content"])
    assert env is not None
    comp = env["components"][0]
    assert comp["type"] == "person_carousel"
    assert len(comp["data"]["people"]) == 2
    assert comp["data"]["people"][0]["name"] == "Albin Anthony"


def test_people_carousel_empty_candidates_noop(monkeypatch):
    async def no_person(name):
        return None

    async def no_people(name):
        return []

    monkeypatch.setattr("app.services.person.lookup_person", no_person)
    monkeypatch.setattr("app.services.person.lookup_people", no_people)
    payload = {"messages": [{"role": "user",
                             "content": "who is albin anthony?"}]}
    reply = "I could not find a public profile for that name."
    r, _ = asyncio.run(enforce_components(
        FakeGateway(reply), None, payload, _res(reply)))
    assert r["choices"][0]["message"]["content"] == reply


def test_card_and_carousel_for_strong_match(monkeypatch):
    """A notable match gets the dossier; every other public profile
    still gets listed in a carousel right below it."""
    async def fake_lookup(name):
        return _person()

    async def fake_people(name):
        return [{"name": "Muhammad Ali", "source": "wikipedia",
                 "url": "https://en.wikipedia.org/wiki/Muhammad_Ali"},
                {"name": "Muhammad Ali", "source": "github",
                 "url": "https://github.com/muhammadali"},
                {"name": "Muhammad Ali Jr", "source": "linkedin",
                 "url": "https://linkedin.com/in/muhammad-ali-jr"}]

    monkeypatch.setattr("app.services.person.lookup_person", fake_lookup)
    monkeypatch.setattr("app.services.person.lookup_people", fake_people)
    payload = {"messages": [{"role": "user",
                             "content": "who is muhammad ali?"}]}
    reply = "Muhammad Ali was a boxer."
    r, _ = asyncio.run(enforce_components(
        FakeGateway(reply), None, payload, _res(reply)))
    env = extract_envelope(r["choices"][0]["message"]["content"])
    types = [c["type"] for c in env["components"]]
    assert types[0] == "person_card" and types[1] == "person_carousel"
    people = env["components"][1]["data"]["people"]
    names = {p["name"].strip().lower() for p in people}
    assert "muhammad ali" not in names  # the dossier person isn't repeated
    assert len(people) == 1


def test_carousel_replaces_weak_match_card(monkeypatch):
    """A fuzzy wiki match ("Alvin Anthons" for "albin anthony") must not
    become the answer — the carousel of real candidates leads instead."""
    async def wrong_person(name):
        return _person(name="Alvin Anthons")

    async def fake_people(name):
        return [{"name": "Albin Anthony", "source": "github",
                 "url": "https://github.com/albinanthony"}]

    monkeypatch.setattr("app.services.person.lookup_person", wrong_person)
    monkeypatch.setattr("app.services.person.lookup_people", fake_people)
    payload = {"messages": [{"role": "user",
                             "content": "who is albin anthony?"}]}
    reply = "Albin Anthony is a developer."
    r, _ = asyncio.run(enforce_components(
        FakeGateway(reply), None, payload, _res(reply)))
    env = extract_envelope(r["choices"][0]["message"]["content"])
    types = [c["type"] for c in env["components"]]
    assert "person_card" not in types
    assert types[0] == "person_carousel"


def test_profile_followup_uses_history_name(monkeypatch):
    """"give me all the profiles you can find" carries no name — the
    subject is resolved from the earlier who-is turn and the carousel
    of real candidates is injected."""
    async def no_person(name):
        return None

    async def fake_people(name):
        return [{"name": "Albin Anthony", "source": "github",
                 "url": "https://github.com/albinanthony"},
                {"name": "Albin Anthony", "source": "linkedin",
                 "url": "https://linkedin.com/in/albin-anthony"}]

    monkeypatch.setattr("app.services.person.lookup_person", no_person)
    monkeypatch.setattr("app.services.person.lookup_people", fake_people)
    payload = {"messages": [
        {"role": "user", "content": "who is albin anthony?"},
        {"role": "assistant", "content": "He is hard to pin down."},
        {"role": "user",
         "content": "give me all the profiles you can find"},
    ]}
    reply = "- github.com/x\n- linkedin.com/in/y"
    r, _ = asyncio.run(enforce_components(
        FakeGateway(reply), None, payload, _res(reply)))
    env = extract_envelope(r["choices"][0]["message"]["content"])
    assert env is not None
    assert env["components"][0]["type"] == "person_carousel"
    assert len(env["components"][0]["data"]["people"]) == 2


def test_unverified_model_card_dropped_for_carousel(monkeypatch):
    """A model-emitted person_card with no source URL is a guess, not a
    dossier — when lookup finds no notable person, the real candidates
    lead and the thin card is removed."""
    async def no_person(name):
        return None

    async def fake_people(name):
        return [{"name": "Albin Anthony", "source": "github",
                 "url": "https://github.com/albinanthony"}]

    monkeypatch.setattr("app.services.person.lookup_person", no_person)
    monkeypatch.setattr("app.services.person.lookup_people", fake_people)
    env_in = (
        '```nova-response\n{"response_type":"general","content":"x",'
        '"components":[{"type":"person_card","data":{"name":"Albin '
        'Anthony","extract":"A user of this AI model."}}]}\n```'
    )
    payload = {"messages": [{"role": "user",
                             "content": "who is albin anthony?"}]}
    r, _ = asyncio.run(enforce_components(
        FakeGateway(env_in), None, payload, _res(env_in)))
    env = extract_envelope(r["choices"][0]["message"]["content"])
    types = [c["type"] for c in env["components"]]
    assert types[0] == "person_carousel"
    assert "person_card" not in types


def test_sourced_model_card_kept_above_carousel(monkeypatch):
    """A model card citing a real source stays; the carousel lists the
    other candidates below it."""
    async def no_person(name):
        return None

    async def fake_people(name):
        return [{"name": "Albin Anthons", "source": "wikipedia",
                 "url": "https://en.wikipedia.org/wiki/Albin_Anthons"}]

    monkeypatch.setattr("app.services.person.lookup_person", no_person)
    monkeypatch.setattr("app.services.person.lookup_people", fake_people)
    env_in = (
        '```nova-response\n{"response_type":"general","content":"x",'
        '"components":[{"type":"person_card","data":{"name":"Albin '
        'Anthons","url":"https://en.wikipedia.org/wiki/Albin_Anthons",'
        '"source":"wikipedia"}}]}\n```'
    )
    payload = {"messages": [{"role": "user",
                             "content": "who is albin anthony?"}]}
    r, _ = asyncio.run(enforce_components(
        FakeGateway(env_in), None, payload, _res(env_in)))
    env = extract_envelope(r["choices"][0]["message"]["content"])
    types = [c["type"] for c in env["components"]]
    assert types[:2] == ["person_card", "person_carousel"]


def test_profile_followup_without_history_is_noop(monkeypatch):
    async def no_lookup(name):  # pragma: no cover - must not be called
        raise AssertionError("lookup called with no resolvable name")

    monkeypatch.setattr("app.services.person.lookup_person", no_lookup)
    monkeypatch.setattr("app.services.person.lookup_people", no_lookup)
    payload = {"messages": [
        {"role": "user", "content": "how do I cook pasta"},
        {"role": "assistant", "content": "Boil water."},
        {"role": "user", "content": "show me the profiles"},
    ]}
    reply = "No prior subject to look up."
    r, _ = asyncio.run(enforce_components(
        FakeGateway(reply), None, payload, _res(reply)))
    assert "person_carousel" not in r["choices"][0]["message"]["content"]


def test_person_card_floor_skips_other_questions(monkeypatch):
    async def fake_lookup(name):  # pragma: no cover - must not be called
        raise AssertionError("lookup called for non-person question")

    monkeypatch.setattr("app.services.person.lookup_person", fake_lookup)
    payload = {"messages": [{"role": "user", "content": "hi"}]}
    r, _ = asyncio.run(enforce_components(
        FakeGateway(ENV), None, payload, _res(ENV)))
    assert r["choices"][0]["message"]["content"] == ENV


def test_markdown_fallback_extracts_trailing_sources():
    md = (
        "Intro sentence.\n\n"
        "- point one\n- point two\n\n"
        "---\n**Sources**\n"
        "[1] REST vs gRPC guide — Example Pub · https://example.com/a\n"
        "[2] gRPC docs · https://grpc.io/docs\n"
    )
    fence = markdown_to_envelope(md)
    assert fence is not None
    import json as _j
    import re as _re

    env = _j.loads(_re.search(r"```nova-response\n(.*?)\n```", fence, _re.S).group(1))
    assert len(env["sources"]) == 2
    assert env["sources"][0]["url"] == "https://example.com/a"
    assert env["sources"][0]["title"] == "REST vs gRPC guide"
    # the Sources block is removed from prose — answer ends at content
    assert "Sources" not in env["content"]
    assert "example.com" not in env["content"]
