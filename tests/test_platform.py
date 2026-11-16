"""Tests for the multi-agent platform layer: intent, registries,
execution engine, reliability guarantees."""

from __future__ import annotations

from app.services import experts, skills
from app.services.execution import (
    ExecutionPlan,
    TaskStep,
    _parse_plan,
    _template_plan,
    orchestrate,
)
from app.services.intent import Complexity, Intent, classify
from app.services.reliable import (
    CallResult,
    last_resort_answer,
    reliable_call,
)

# ---------------------------------------------------------------- intent

def test_intent_greeting_is_simple_chat():
    r = classify("hi")
    assert r.intent is Intent.CHAT and r.complexity is Complexity.SIMPLE


def test_intent_explicit_research():
    r = classify("do a deep dive on solid state batteries")
    assert r.intent is Intent.RESEARCH and r.needs_research


def test_intent_lookup_current_fact():
    r = classify("what is the current price of bitcoin")
    assert r.intent is Intent.LOOKUP and r.needs_tools


def test_intent_code_debug():
    r = classify("why does this python function throw a traceback on None input")
    assert r.intent is Intent.CODE


def test_intent_sql_is_data():
    r = classify("write a sql query that joins users to orders")
    assert r.intent in (Intent.DATA, Intent.MIXED) and "data" in r.domains


def test_intent_multi_domain_is_complex_or_mixed():
    r = classify(
        "Research the three best vector databases, then compare their "
        "pricing and write a short recommendation with a comparison table")
    assert r.complexity is Complexity.COMPLEX


def test_intent_short_ambiguous_flagged():
    r = classify("that thing")
    assert r.ambiguities


def test_intent_never_raises_on_garbage():
    assert classify("").intent is Intent.CHAT


# ------------------------------------------------------------- registries

def test_expert_registry_complete():
    names = [e.name for e in experts.all_experts()]
    for req in ("deep_researcher", "fact_checker", "python_developer",
                "code_reviewer", "debugger", "sql_specialist",
                "data_analyst", "writer", "security_analyst"):
        assert req in names


def test_every_expert_has_contract():
    for e in experts.all_experts():
        assert e.system_prompt and e.summary and e.capabilities
        assert e.domain in ("research", "engineering", "data",
                            "content", "operations")
        d = e.to_agent_dict()
        assert d["config"]["builtin"] is True


def test_find_by_capability():
    hits = experts.find_by_capability("code review")
    assert any(e.name == "code_reviewer" for e in hits)


def test_skills_attach_and_tools():
    e = experts.get_expert("deep_researcher")
    prompt = skills.attach_instructions(e.system_prompt, list(e.skills))
    assert "SKILL:" in prompt
    req = skills.required_tools(list(e.skills))
    assert "web_search" in req


def test_unknown_skill_ignored():
    assert skills.attach_instructions("base", ("nope",)) == "base"


# ----------------------------------------------------------------- plan

def test_parse_plan_validates_registry():
    text = '{"rationale": "r", "steps": [' \
           '{"id": "s1", "expert": "web_researcher", "task": "find x", "depends_on": []},' \
           '{"id": "s2", "expert": "ghost_agent", "task": "bogus", "depends_on": []},' \
           '{"id": "s3", "expert": "fact_checker", "task": "verify", "depends_on": ["s1", "s2"]}]}'
    steps, _ = _parse_plan(text)
    assert [s.id for s in steps] == ["s1", "s3"]
    assert steps[1].depends_on == ("s1",)   # dep on skipped s2 dropped


def test_parse_plan_bad_json_returns_empty():
    assert _parse_plan("not json at all") == ([], "")


def test_plan_levels_parallel():
    plan = ExecutionPlan([
        TaskStep("a", "web_researcher", "x"),
        TaskStep("b", "web_researcher", "y"),
        TaskStep("c", "fact_checker", "z", ("a", "b")),
    ], "research", "template")
    levels = plan.levels()
    assert len(levels) == 2 and len(levels[0]) == 2 and levels[1][0].id == "c"


def test_template_plan_research_has_verifier():
    from app.services.intent import classify
    plan = _template_plan("deep dive into X", classify("deep dive into X"))
    names = [s.expert for s in plan.steps]
    assert "deep_researcher" in names and "fact_checker" in names
    assert plan.steps[-1].depends_on


# ------------------------------------------------------- reliable layer

async def test_reliable_call_retries_then_succeeds():
    calls = {"n": 0}

    async def call():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("boom")
        return {"choices": [{"message": {"content": "ok"}}], "model": "m1"}

    res = await reliable_call(call, retries=2, backoff_base_s=0.01)
    assert res.ok and res.attempts == 2 and res.model == "m1"


async def test_reliable_call_never_raises():
    async def call():
        raise RuntimeError("always fails")

    res = await reliable_call(call, retries=1, backoff_base_s=0.01)
    assert not res.ok and res.degraded and res.error


async def test_reliable_call_empty_is_failure():
    async def call():
        return {"choices": [{"message": {"content": "  "}}]}

    res = await reliable_call(call, retries=0)
    assert not res.ok and res.error == "empty completion"


def test_last_resort_answer_honest():
    a = last_resort_answer("q", failures=["researcher: timeout"],
                           partials=["found that X costs $5"],
                           missing=["live pricing"])
    assert "wasn't able" in a and "timeout" in a and "costs $5" in a
    assert "To retry" in a


# ------------------------------------------------------------- model policy

def test_stage_models_prefers_strong_concrete_first():
    from app.services.model_policy import stage_models
    cands = stage_models("auto")
    assert cands[-1] == "auto"                 # backstop last
    assert all("/" in c for c in cands[:-1])   # concrete provider/model first


def test_stage_models_roles_and_explicit():
    from app.services.model_policy import stage_models
    assert stage_models("auto:coding")[0].startswith("groq/")
    assert stage_models("auto", role="reasoning")[0] == "groq/openai/gpt-oss-120b"
    assert stage_models("ollama/llama3.2:1b") == ["ollama/llama3.2:1b"]


def test_stage_models_env_override(monkeypatch):
    from app.services.model_policy import stage_models
    monkeypatch.setenv("NOVA_INTERNAL_MODEL", "openrouter/x,gemini/y")
    assert stage_models("auto")[:2] == ["openrouter/x", "gemini/y"]


def test_research_stage_roles():
    from app.services.research import _stage_model
    assert _stage_model("planner", "auto")[0] == "groq/openai/gpt-oss-120b"
    assert _stage_model("checker", "auto")[0] == "groq/openai/gpt-oss-20b"
    assert _stage_model("synth", "ollama/x") == ["ollama/x"]  # explicit wins


async def test_internal_chat_falls_through_candidates(session, monkeypatch):
    """First preferred model fails -> next candidate serves."""
    from app.services import model_policy
    from app.services.model_policy import internal_chat

    monkeypatch.setattr(model_policy, "_PREFERRED", model_policy._PREFERRED | {
        "general": ("bad/m1", "good/m2")})

    class GW:
        async def chat(self, s, p):
            if p["model"] == "bad/m1":
                raise RuntimeError("provider down")
            if p["model"] == "auto":
                raise RuntimeError("should not reach auto")
            return ({"choices": [{"message": {"content": "ok"}}],
                     "model": p["model"]}, None, None, p["model"])

    res = await internal_chat(GW(), session, {"messages": []},
                              requested="auto", role="general", retries=0)
    assert res.ok and res.model == "good/m2"


# ------------------------------------------------------- execution engine

class _FakeGateway:
    """Gateway double: scripted completions keyed by prompt substring."""

    def __init__(self, script):
        self.script = script
        self.calls: list[str] = []

    async def chat(self, session, payload, on_event=None,
                   attempt_timeout=None):
        """Matches ``Gateway.chat`` -> (resp, provider, key, model)."""
        text = " ".join(str(m.get("content", ""))
                        for m in payload.get("messages", []))
        self.calls.append(text)
        for pat, resp in self.script.items():
            if pat in text:
                if isinstance(resp, Exception):
                    raise resp
                return ({"choices": [{"message": {"content": resp}}],
                         "model": "fake/m"}, None, None, "fake/m")
        return ({"choices": [{"message": {"content": "generic answer"}}],
                 "model": "fake/m"}, None, None, "fake/m")


async def test_orchestrate_single_expert_happy(session):
    gw = _FakeGateway({"": "well researched answer with sources"})
    res = await orchestrate(gw, session, "debug this code for me")
    assert res.answer and not res.degraded
    assert res.steps and res.steps[0].ok
    assert any(e["event"] == "done" for e in res.trace)


async def test_orchestrate_parallel_levels(session, monkeypatch):
    """Independent steps run concurrently — assert both executed."""
    gw = _FakeGateway({"verify": "verdicts: all supported"})
    res = await orchestrate(
        gw, session, "deep dive into quantum computing")
    kinds = [s.expert for s in res.steps]
    assert "deep_researcher" in kinds and "fact_checker" in kinds
    assert res.answer


async def test_orchestrate_failed_step_degrades_not_dies(session):
    """One step fails -> the other still returns; result is degraded."""
    gw = _FakeGateway({
        "verify": RuntimeError("verifier exploded"),
    })
    res = await orchestrate(
        gw, session, "deep dive into fusion reactors")
    assert res.answer                    # researcher output survived
    assert res.degraded
    assert any(not s.ok for s in res.steps)


async def test_orchestrate_all_fail_last_resort(session):
    gw = _FakeGateway({"": RuntimeError("provider down")})
    res = await orchestrate(gw, session, "deep dive into X")
    assert res.answer                    # honest partial, never empty
    assert res.degraded
    assert "wasn't able" in res.answer
    assert res.meta["failures"]


async def test_orchestrate_bad_llm_plan_falls_back(session, monkeypatch):
    """Unusable planner output -> deterministic template plan."""
    gw = _FakeGateway({"": "garbage not json {["})

    async def _bad_plan(*a, **k):
        return CallResult(text="not a plan {{{", attempts=1)

    monkeypatch.setattr("app.services.execution.reliable_call", _bad_plan)
    from app.services.execution import build_plan
    from app.services.intent import classify

    plan = await build_plan(gw, session, "research then compare options",
                            classify("research then compare options"))
    assert plan.strategy == "template" and plan.steps


async def test_orchestrate_emits_trace_events(session):
    events: list[dict] = []
    gw = _FakeGateway({"": "done answer"})
    await orchestrate(gw, session, "debug this traceback",
                      emit=events.append)
    kinds = {e["event"] for e in events}
    assert "stage" in kinds and "done" in kinds
