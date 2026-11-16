"""Orchestration engine — plan, delegate, execute, validate, synthesize.

The central coordinator for multi-agent work. Turns a classified request
into an ``ExecutionPlan`` of ``TaskStep``s, runs independent steps in
parallel per dependency level, validates each output, resolves conflicts
in a synthesis pass, and degrades gracefully at every stage — a failed
step never takes the whole request down.

Delegation is bounded: the plan caps steps and experts cannot spawn
further agents (no nested delegation, no loops).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services import experts, skills as skills_mod
from app.services.intent import Complexity, Intent, IntentResult
from app.services.model_policy import internal_chat
from app.services.reliable import CallResult, completion_text, last_resort_answer, reliable_call

MAX_STEPS = 6
STEP_TIMEOUT_S = 180.0
SYNTH_TIMEOUT_S = 120.0
_MAX_UPSTREAM_CHARS = 6000


# ----------------------------------------------------------------- trace

@dataclass
class TraceEvent:
    seq: int
    event: str           # stage | step_start | step_done | step_fail | synth | done
    actor: str           # orchestrator | expert name | synthesizer
    detail: str
    duration_ms: float = 0.0
    status: str = "ok"   # ok | fail | skip
    ts: float = field(default_factory=time.monotonic)


class ExecutionTrace:
    """Structured, serializable execution record — the observability layer."""

    def __init__(self, emit: Any | None = None) -> None:
        self._emit = emit or (lambda ev: None)
        self.events: list[TraceEvent] = []
        self._seq = 0

    def record(self, event: str, actor: str, detail: str,
               duration_ms: float = 0.0, status: str = "ok") -> None:
        self._seq += 1
        ev = TraceEvent(self._seq, event, actor, detail, duration_ms, status)
        self.events.append(ev)
        # Forward as activity events so the chat UI shows live progress.
        self._emit({"event": event, "text": detail,
                    "actor": actor, "status": status})

    def as_dicts(self) -> list[dict[str, Any]]:
        return [
            {"seq": e.seq, "event": e.event, "actor": e.actor,
             "detail": e.detail, "duration_ms": round(e.duration_ms, 1),
             "status": e.status}
            for e in self.events
        ]


# ------------------------------------------------------------------ plan

@dataclass
class TaskStep:
    """One delegated unit of work."""

    id: str
    expert: str                    # registry name
    task: str                      # instruction for the expert
    depends_on: tuple[str, ...] = ()
    model: str = "auto"


@dataclass
class ExecutionPlan:
    steps: list[TaskStep]
    intent: str
    strategy: str                  # template | llm | single
    rationale: str = ""

    def levels(self) -> list[list[TaskStep]]:
        """Dependency levels: each level's steps can run in parallel."""
        done: set[str] = set()
        remaining = list(self.steps)
        out: list[list[TaskStep]] = []
        while remaining:
            lvl = [s for s in remaining
                   if all(d in done for d in s.depends_on)]
            if not lvl:
                lvl = remaining  # cycle — break it, run the rest
            out.append(lvl)
            done.update(s.id for s in lvl)
            remaining = [s for s in remaining if s not in lvl]
        return out


@dataclass
class StepResult:
    step_id: str
    expert: str
    ok: bool
    text: str = ""
    model: str = ""
    duration_ms: float = 0.0
    attempts: int = 0
    error: str | None = None


@dataclass
class OrchestrationResult:
    """The terminal state — always populated, never an exception."""

    answer: str
    intent: str
    strategy: str
    steps: list[StepResult]
    trace: list[dict[str, Any]]
    degraded: bool = False
    model: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------- plan prompts

_PLANNER_PROMPT = """You are the orchestration planner. Decompose the user's
request into the minimum effective set of expert tasks.

Available experts:
{experts}

Rules:
- 1-{max_steps} steps. Do not delegate what a single expert can do alone.
- Each step: a registry expert name, a concrete task, and dependencies on
  prior step ids (empty when it can start immediately).
- Independent steps MUST NOT depend on each other — they run in parallel.
- Put verification (fact_checker or code_reviewer) after the producer step
  when accuracy matters.
- The final step is added by the orchestrator — do not plan synthesis.

Output JSON only:
{{"rationale": "one line", "steps": [{{"id": "s1", "expert": "...", "task": "...",
"depends_on": []}}]}}
"""


def _parse_plan(text: str) -> tuple[list[TaskStep], str]:
    """Tolerant JSON plan extraction + validation against the registry."""
    if not text:
        return [], ""
    fence = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    cand = fence.group(1).strip() if fence else text.strip()
    try:
        obj = json.loads(cand)
    except Exception:
        try:
            obj = json.loads(text[: text.rindex("}") + 1])
        except Exception:
            return [], ""
    if not isinstance(obj, dict) or not isinstance(obj.get("steps"), list):
        return [], ""
    steps: list[TaskStep] = []
    seen: set[str] = set()
    for raw in obj["steps"][:MAX_STEPS]:
        if not isinstance(raw, dict):
            continue
        expert = str(raw.get("expert") or "")
        if experts.get_expert(expert) is None:
            continue
        sid = str(raw.get("id") or f"s{len(steps) + 1}")
        if sid in seen:
            continue
        seen.add(sid)
        deps = tuple(
            str(d) for d in (raw.get("depends_on") or [])
            if str(d) in seen          # only earlier steps can be deps
        )
        task = str(raw.get("task") or "")[:800]
        if not task:
            continue
        steps.append(TaskStep(id=sid, expert=expert, task=task,
                              depends_on=deps))
    return steps, str(obj.get("rationale") or "")


def _template_plan(question: str, ir: IntentResult) -> ExecutionPlan:
    """Deterministic plans for well-understood intents — no LLM needed."""
    q = question.strip()
    if ir.intent is Intent.RESEARCH:
        steps = [
            TaskStep("s1", "deep_researcher",
                     f"Research this thoroughly and report findings with citations:\n{q}"),
            TaskStep("s2", "fact_checker",
                     "Verify the load-bearing claims in the research findings "
                     "you are given. List verdicts only.", ("s1",)),
        ]
        return ExecutionPlan(steps, ir.intent.value, "template",
                             "research → verify")
    if ir.intent is Intent.CODE:
        expert = "debugger" if re.search(r"debug|error|traceback|crash|fail", q, re.I) \
            else "code_reviewer" if re.search(r"review", q, re.I) \
            else "python_developer"
        return ExecutionPlan(
            [TaskStep("s1", expert, q)], ir.intent.value, "template",
            f"single expert: {expert}")
    if ir.intent is Intent.DATA:
        expert = "sql_specialist" if re.search(r"\bsql\b|query|database", q, re.I) \
            else "visualization_expert" if re.search(r"chart|plot|visuali[sz]e", q, re.I) \
            else "data_analyst"
        return ExecutionPlan(
            [TaskStep("s1", expert, q)], ir.intent.value, "template",
            f"single expert: {expert}")
    if ir.intent is Intent.SYSTEM_OP:
        expert = "troubleshooter" if re.search(
            r"down|fail|error|slow|incident|outage|why is", q, re.I) \
            else "security_analyst" if re.search(r"secur|vulnerab|audit", q, re.I) \
            else "devops_engineer"
        return ExecutionPlan(
            [TaskStep("s1", expert, q)], ir.intent.value, "template",
            f"single expert: {expert}")
    if ir.intent is Intent.WRITING:
        expert = "summarizer" if re.search(r"summari[sz]e|tl;?dr|condense", q, re.I) \
            else "documenter" if re.search(r"document|readme|runbook", q, re.I) \
            else "writer"
        return ExecutionPlan(
            [TaskStep("s1", expert, q)], ir.intent.value, "template",
            f"single expert: {expert}")
    # MIXED / COMPLEX: research + domain expert, verify at the end.
    steps = [TaskStep("s1", "deep_researcher",
                      f"Gather the facts needed for this request:\n{q}")]
    domain_expert = "data_analyst" if "data" in ir.domains \
        else "software_architect" if "engineering" in ir.domains \
        else "writer"
    steps.append(TaskStep("s2", domain_expert,
                          f"Using the research findings provided, produce the "
                          f"deliverable for:\n{q}", ("s1",)))
    return ExecutionPlan(steps, ir.intent.value, "template",
                         "research → domain expert")


async def build_plan(
    gateway: Any, session: Any, question: str,
    ir: IntentResult, history: list[dict[str, Any]] | None = None,
    emit: Any | None = None, trace: ExecutionTrace | None = None,
) -> ExecutionPlan:
    """Build an execution plan — template first, LLM planner for complex."""
    if ir.complexity is not Complexity.COMPLEX or ir.intent is Intent.CHAT:
        return _template_plan(question, ir)

    # Complex/mixed: ask a model for the plan; template fallback.
    prompt = _PLANNER_PROMPT.format(
        experts=experts.describe_for_planner(), max_steps=MAX_STEPS)
    hist = [
        {"role": m.get("role", "user"), "content": str(m.get("content", ""))[:2000]}
        for m in (history or [])[-6:]
    ]
    res = await internal_chat(
        gateway, session, {
            "messages": hist + [{"role": "system", "content": prompt},
                                {"role": "user", "content": question}],
            "nova_structured": False,
        },
        requested=None, role="reasoning", retries=0,
        emit=emit, label="planning")
    steps, rationale = _parse_plan(res.text)
    if not steps:
        if trace:
            trace.record("stage", "orchestrator",
                         "LLM plan unusable — template plan instead",
                         status="skip")
        return _template_plan(question, ir)
    if trace:
        trace.record("stage", "orchestrator",
                     f"Plan: {len(steps)} step(s) — {rationale or 'llm'}")
    return ExecutionPlan(steps, ir.intent.value, "llm", rationale)


# --------------------------------------------------------------- execute

_SYNTH_PROMPT = """You are the synthesizer for a multi-agent run. Combine the
expert outputs below into one coherent answer to the user's request.

Rules:
- Answer the ORIGINAL question directly first, then support it.
- Merge overlapping findings; resolve conflicts by preferring verified /
  better-sourced statements and saying where experts disagree.
- Drop content from steps marked failed.
- Keep citations [n] and component fences intact.
- Output the final answer as markdown; if an expert produced a
  nova-response block, carry its components/sources through in one
  merged block at the end.
"""


def _validate(step: TaskStep, text: str) -> str | None:
    """Result validation: returns an error string or None."""
    if not text or not text.strip():
        return "empty output"
    if len(text.strip()) < 20:
        return "suspiciously short output"
    expert = experts.get_expert(step.expert)
    if expert and expert.output_contract == "findings_json":
        try:
            json.loads(text[text.index("{"):]) if "{" in text else None
            if "[" in text:
                json.loads(text[text.index("["): text.rindex("]") + 1])
        except Exception:
            return "contract=findings_json but output is not JSON"
    return None


async def _run_step(
    gateway: Any, session: Any, step: TaskStep,
    upstream: dict[str, StepResult],
    question: str, emit: Any, trace: ExecutionTrace,
) -> StepResult:
    """Run one delegated step with timeout, retry and validation."""
    expert = experts.get_expert(step.expert)
    if expert is None:
        trace.record("step_fail", step.expert, "unknown expert — skipped",
                     status="skip")
        return StepResult(step.id, step.expert, False,
                          error="unknown expert")
    t0 = time.monotonic()
    trace.record("step_start", expert.name,
                 f"{expert.title} — {step.task[:80]}")

    # Working context: the question + upstream results this step depends on.
    ctx_parts: list[str] = []
    for dep in step.depends_on:
        r = upstream.get(dep)
        if r and r.ok:
            ctx_parts.append(
                f"### Output of step {dep} ({r.expert})\n"
                f"{r.text[:_MAX_UPSTREAM_CHARS]}")
        elif r:
            ctx_parts.append(
                f"### Output of step {dep} ({r.expert}) — FAILED: {r.error}")
    ctx = "\n\n".join(ctx_parts)

    system = skills_mod.attach_instructions(expert.system_prompt, expert.skills)
    user = step.task + (f"\n\n## Upstream results\n{ctx}" if ctx else "")
    tool_names = sorted(set(expert.tools)
                        | set(skills_mod.required_tools(expert.skills))
                        ) or None

    async def _call() -> dict[str, Any]:
        from app.services.model_policy import stage_models

        last_err: Exception | None = None
        for cand in stage_models(step.model or expert.model_hint or "auto"):
            try:
                if tool_names:
                    from app.services.tools import run_tool_loop
                    return await run_tool_loop(
                        gateway, session,
                        {"model": cand,
                         "messages": [{"role": "system", "content": system},
                                      {"role": "user", "content": user}],
                         "nova_structured": False},
                        tool_names, emit=emit,
                        max_rounds=expert.max_tool_rounds)
                result, _p, _k, _m = await gateway.chat(
                    session,
                    {"model": cand,
                     "messages": [{"role": "system", "content": system},
                                  {"role": "user", "content": user}],
                     "nova_structured": False})
                return result
            except Exception as e:
                last_err = e
        raise last_err or RuntimeError("no model candidates")

    try:
        res = await asyncio.wait_for(
            reliable_call(_call, retries=1, emit=emit,
                          label=f"expert {expert.name}"),
            timeout=STEP_TIMEOUT_S,
        )
    except (asyncio.TimeoutError, TimeoutError):
        res = CallResult(text="", degraded=True,
                         error=f"exceeded {STEP_TIMEOUT_S:.0f}s budget")

    dur = (time.monotonic() - t0) * 1000
    if not res.ok:
        trace.record("step_fail", expert.name,
                     f"{expert.title} failed: {res.error or 'empty'}",
                     duration_ms=dur, status="fail")
        return StepResult(step.id, step.expert, False, duration_ms=dur,
                          attempts=res.attempts, error=res.error)
    verr = _validate(step, res.text)
    if verr:
        trace.record("step_fail", expert.name,
                     f"{expert.title} output rejected: {verr}",
                     duration_ms=dur, status="fail")
        return StepResult(step.id, step.expert, False, duration_ms=dur,
                          attempts=res.attempts, error=verr)
    trace.record("step_done", expert.name,
                 f"{expert.title} done ({res.model or '?'}, "
                 f"{len(res.text)} chars)",
                 duration_ms=dur)
    return StepResult(step.id, step.expert, True, text=res.text,
                      model=res.model, duration_ms=dur,
                      attempts=res.attempts)


async def _synthesize(
    gateway: Any, session: Any, question: str,
    results: list[StepResult], emit: Any, trace: ExecutionTrace,
) -> CallResult:
    """Final synthesis pass over the successful step outputs."""
    ok_steps = [r for r in results if r.ok]
    if not ok_steps:
        return CallResult(text="", degraded=True, error="no successful steps")
    if len(ok_steps) == 1 and all(r.ok for r in results):
        # Single clean result — pass it through, no extra model call.
        return CallResult(text=ok_steps[0].text,
                          model=ok_steps[0].model, attempts=1)
    bundle = "\n\n".join(
        f"### Step {r.step_id} — {r.expert} ({'ok' if r.ok else 'failed'})\n"
        f"{r.text[:_MAX_UPSTREAM_CHARS]}"
        for r in results
    )
    trace.record("stage", "synthesizer",
                 f"Synthesizing {len(ok_steps)} result(s)")
    return await internal_chat(
        gateway, session, {
            "messages": [
                {"role": "system", "content": _SYNTH_PROMPT},
                {"role": "user", "content":
                    f"Original request:\n{question}\n\nExpert outputs:\n{bundle}"},
            ],
            "nova_structured": True,
        },
        requested=None, role="reasoning", retries=1,
        emit=emit, label="synthesis")


async def orchestrate(
    gateway: Any,
    session: Any,
    question: str,
    *,
    intent: IntentResult | None = None,
    history: list[dict[str, Any]] | None = None,
    model: str = "auto",
    emit: Any | None = None,
) -> OrchestrationResult:
    """Plan → execute → synthesize. Never raises; always returns a result."""
    from app.services.intent import classify

    emit = emit or (lambda ev: None)
    trace = ExecutionTrace(emit)
    run_id = uuid.uuid4().hex[:10]
    ir = intent or classify(question, history)
    trace.record("stage", "orchestrator",
                 f"run {run_id} — {ir.rationale}")

    try:
        plan = await build_plan(gateway, session, question, ir,
                                history, emit, trace)
    except Exception as e:                       # planner must never break it
        trace.record("stage", "orchestrator",
                     f"planning failed ({e}) — template plan", status="fail")
        plan = _template_plan(question, ir)
    for step in plan.steps:
        step.model = step.model if step.model != "auto" else model

    if not plan.steps:
        # Nothing delegable — single generic pass.
        plan = ExecutionPlan(
            [TaskStep("s1", "writer", question)],
            ir.intent.value, "single", "no delegable steps")

    # Execute level by level; steps within a level run concurrently.
    results: dict[str, StepResult] = {}
    for level in plan.levels():
        coros = [
            _run_step(gateway, session, s, results, question, emit, trace)
            for s in level
        ]
        done = await asyncio.gather(*coros, return_exceptions=True)
        for s, r in zip(level, done):
            if isinstance(r, BaseException):
                results[s.id] = StepResult(
                    s.id, s.expert, False,
                    error=f"unhandled: {str(r)[:120]}")
                trace.record("step_fail", s.expert,
                             f"unhandled error: {str(r)[:100]}",
                             status="fail")
            else:
                results[s.id] = r

    ordered = [results[s.id] for s in plan.steps if s.id in results]
    ok = [r for r in ordered if r.ok]

    # Synthesize; on synthesis failure return the best partial output.
    try:
        final = await _synthesize(gateway, session, question,
                                  ordered, emit, trace)
    except Exception as e:
        final = CallResult(text="", degraded=True,
                           error=f"synthesis crashed: {str(e)[:120]}")

    if not final.text.strip() and ok:
        # Layered fallback: best available step output as the answer.
        best = max(ok, key=lambda r: len(r.text))
        final = CallResult(text=best.text, model=best.model, degraded=True,
                           error=final.error or "synthesis failed; "
                                                "returning best partial")

    if not final.text.strip():
        # Last resort: honest partial — what completed, what failed.
        trace.record("done", "orchestrator", "all steps failed — partial response",
                     status="fail")
        return OrchestrationResult(
            answer=last_resort_answer(
                question,
                failures=[f"{r.expert}: {r.error or 'failed'}" for r in ordered],
                partials=[],
                missing=[question[:200]]),
            intent=ir.intent.value, strategy=plan.strategy,
            steps=ordered, trace=trace.as_dicts(), degraded=True,
            meta={"run_id": run_id,
                  "failures": [f"{r.expert}: {r.error or 'failed'}"
                               for r in ordered if not r.ok]})

    degraded = final.degraded or any(not r.ok for r in ordered)
    trace.record("done", "orchestrator",
                 f"done — {len(ok)}/{len(ordered)} steps ok"
                 + (" (degraded)" if degraded else ""))
    return OrchestrationResult(
        answer=final.text,
        intent=ir.intent.value,
        strategy=plan.strategy,
        steps=ordered,
        trace=trace.as_dicts(),
        degraded=degraded,
        model=final.model,
        meta={"run_id": run_id,
              "failures": [f"{r.expert}: {r.error}" for r in ordered
                           if not r.ok]},
    )
