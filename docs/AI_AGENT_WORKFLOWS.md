# AI Agent Workflows — step-by-step system map

This document maps every workflow an AI agent goes through in Nova,
from the moment a user sends a message to the moment a rendered answer
appears — and what gets recorded along the way.

---

## 0. The big picture

```
USER MESSAGE
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  POST /v1/chat/completions                                  │
│  (or /v1/research/*, /v1/agents/*/chat, /v1/roleplay/*)     │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR ROUTING  (services/orchestrator.py)           │
│                                                             │
│  route_message() → intent.classify_request()                │
│                                                             │
│   ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌─────────────┐  │
│   │ direct   │ │ tools    │ │ research  │ │ orchestrate │  │
│   └──────────┘ └──────────┘ └───────────┘ └─────────────┘  │
└─────────────────────────────────────────────────────────────┘
    │                                                          │
    ▼                                                          ▼
 Every run is recorded:                            expert delegation engine
 agent_runs (header)                               services/execution.py
 agent_events (each step)                          services/experts.py
```

Four pipelines exist. Routing is **fail-open** — if classification
crashes, the request falls back to the safest pipeline and still
answers.

---

## 1. Workflow: DIRECT CHAT

*Used for: greetings, simple questions, small talk.*

| Step | What happens | File |
|---|---|---|
| 1 | Request hits `POST /v1/chat/completions` | `app/api/openai_routes.py` |
| 2 | History trimmed to ≤20 turns (system messages always kept) | same |
| 3 | Structured-output system prompt injected (`nova-response` contract) | `app/response_protocol.py` |
| 4 | `route_message` → `classify_request` → `Intent.CHAT` + `COMPLEXITY.SIMPLE` + no tools needed → `direct` | `orchestrator.py`, `intent.py` |
| 5 | `gateway.chat_stream` → provider chain (groq → openrouter → … → ollama last) | `gateway.py` |
| 6 | Provider returns SSE chunks; each token streamed to the client | `openai_routes.py` |
| 7 | On completion: `_sanitize_final_text` repairs split `nova-response` fences; `enforce_components` validates envelope → renderer repair → deterministic markdown→component fallback | `tools.py`, `component_enforce.py`, `renderer.py` |
| 8 | Usage event recorded (`kind=chat`) for quotas/analytics | `services/usage.py` |
| 9 | Frontend `ResponseClassifier` → `ResponseRenderer` draws components | `app/static/js/render/` |

---

## 2. Workflow: TOOL LOOP

*Used for: questions needing live data — weather, prices, "who is X".*

| Step | What happens |
|---|---|
| 1 | `route_message` returns `tools` (default workhorse) |
| 2 | `run_tool_loop(gateway, session, payload, builtin_tools=True)` starts |
| 3 | Model emits a tool call (`web_search`, `http_get`, …) |
| 4 | `services/tools.py` executes the real tool — HTTP fetch + search, result JSON injected back as a tool message |
| 5 | Loop repeats until the model answers or `max_rounds` hits |
| 6 | Same sanitization + component enforcement as direct |
| 7 | Every tool call is streamed to the UI as a `tool` activity event |

---

## 3. Workflow: DEEP RESEARCH (flagship)

*Used for: "research", "in-depth", "investigate", "compare X vs Y", "what is <broad topic>".*

```
question
  │
  ├─ 1. classify_depth ─► quick | standard | deep
  │     (short lookups → quick; definitions/profiles → standard;
  │      comparisons, "everything about", ≥25 words → deep)
  │
  ├─ 2. PLANNER ─► research plan
  │     sub-questions: standard ≤8, deep ≤14 — one worker per aspect
  │     (e.g. person → identity / education / career / projects /
  │      presence / publications / news / controversies)
  │
  ├─ 3. RESEARCHERS ─► parallel worker pool
  │     each worker: tool loop with web_search + http_get
  │     tool budget: standard 8 calls, deep 12 calls
  │     → structured findings {claim, evidence, source_id,
  │        confidence, verdict}
  │
  ├─ 4. FOLLOW-UP ROUNDS ─► gap checker finds weak spots
  │     standard 1 round, deep up to 3 — targeted re-searches
  │
  ├─ 5. VERIFIER ─► cross-checks claims against sources
  │     verdicts: supported / partially supported / unverified /
  │     unsupported / contradicted
  │
  ├─ 6. SECTIONED SYNTHESIS ─► the 5000-word engine
  │     • 1 parallel section writer PER research aspect
  │       (~900–1200 words each, citations [n])
  │     • 1 executive summary writer
  │     • expansion retry for any section <450 words
  │     • deterministic close: methodology + evidence base,
  │       disagreements/uncertainty, confidence score
  │     → full report assembled (never one thin shot)
  │
  ├─ 7. COMPONENT RENDERER ─► nova-response envelope
  │     person_card / key findings list / timeline /
  │     search_result carousel / callout for conflicts
  │     built deterministically from run state as a floor
  │
  └─ 8. PERSIST ─► research_runs row (question, plan, findings,
        sources, answer, meta) + agent_runs/agent_events ledger
```

**Model policy:** planner/verifier/synthesizer use the *reasoning* role
(groq `gpt-oss-120b` → `llama-3.3-70b` → openrouter free → auto).
Researchers use the *general* role. `auto` is always the last resort.

---

## 4. Workflow: MULTI-AGENT ORCHESTRATION

*Used for: complex multi-domain tasks — "build X and research Y and write Z".*

```
question + history
  │
  ├─ 1. classify_request ─► IntentResult
  │     intent, complexity, needs_tools, needs_research, domains[]
  │
  ├─ 2. PLAN
  │     • template plan (deterministic, includes verifier step), or
  │     • LLM plan → JSON steps {id, expert, task, depends_on}
  │       unknown experts dropped, dangling deps cleaned
  │
  ├─ 3. DEPENDENCY LEVELS ─► ExecutionPlan.levels()
  │     each level runs in PARALLEL; levels run in order
  │
  ├─ 4. PER-STEP EXECUTION (services/execution.py::_run_step)
  │     for each TaskStep in the level:
  │       a. resolve expert → prompt + skills + tool permissions
  │       b. model-policy candidates (role from expert hints)
  │       c. reliable_call → bounded retries + failover
  │       d. tools attached when the expert's skills need them
  │       e. validate output → StepResult{ok, text, model, ms, err}
  │
  ├─ 5. VALIDATE + AGGREGATE
  │     failed/empty steps marked degraded — never abort the run
  │
  ├─ 6. SYNTHESIZE (reasoning-role model)
  │     merges surviving step results into the final answer
  │
  └─ 7. TERMINAL STATE — OrchestrationResult
        {answer, intent, strategy, steps[], trace[], degraded, meta}
        all-fail path → honest last-resort answer (never empty)
```

**Recorded:** the full `ExecutionTrace` → `agent_events` (one row per
stage/step_start/step_done/step_fail/synth/done) under one `agent_runs`
header of kind `orchestrate`.

---

## 5. Workflow: SINGLE AGENT CHAT

*Used for: `/v1/agents/{id}/chat` — one configured agent.*

| Step | What happens |
|---|---|
| 1 | `AgentConfig` loaded (system prompt, model, tools, output format) |
| 2 | `run_agent` or `stream_agent` builds messages: system + history + user |
| 3 | Tool loop if `enable_tools` — same built-in tool execution |
| 4 | Streaming variant wraps the SSE generator; ledger closes when stream ends |
| 5 | Recorded as `agent_runs` kind `agent` with meta `{agent_id, agent}` |

---

## 6. Workflow: ROLEPLAY ROOM

*Used for: `/v1/roleplay/rooms/{id}/message` — multi-character scenes.*

```
user message
  │
  ├─ 1. user turn appended to room.messages
  ├─ 2. for each character (or the selected speaker):
  │       persona_prompt(character, scenario, other cast)
  │       → identity-locked system prompt (personality, style,
  │         boundaries, example dialogue, scene, cast list)
  │       → internal_chat (general-role model candidates)
  │       → reply appended {speaker, character_id, text}
  ├─ 3. transcript capped at 400 messages / 60 turns sent to model
  └─ 4. persisted on the room row — no separate ledger needed
        (the room IS the record)
```

Each character keeps a distinct voice because every turn is generated
with that character's own system prompt — they cannot collapse into
one generic assistant.

---

## 7. Workflow: KNOWLEDGE RETRIEVAL

*Used for: `/v1/knowledge/search` — answering from your documents.*

| Step | What happens |
|---|---|
| 1 | Upload → `documents.ingest`: extract (txt/md/csv/json/html/pdf/docx) → chunk (~1200 chars, 160 overlap) → optional local embeddings |
| 2 | Search → `documents.search`: lexical TF-IDF always; +cosine when embeddings exist; owner-scoped |
| 3 | Chunks return with document name + score for citation |

---

## 8. The recording layer — `services/agent_log.py`

Every agentic execution writes two tables:

| Table | Row per | Contents |
|---|---|---|
| `agent_runs` | one run | kind (`orchestrate`/`research`/`agent`), question, subject, model, status (`running`/`done`/`degraded`/`failed`), meta, start/finish, duration |
| `agent_events` | one step | seq, stage, actor, event, detail, status (`ok`/`fail`/`skip`), duration_ms |

**Design rules:**
- Each write opens its own short session and commits immediately —
  the ledger survives client disconnects and request timeouts.
- Observability is fire-and-forget: recording errors are swallowed,
  never allowed to break the request it describes.
- `tap_emit` mirrors live SSE activity events into the ledger, so the
  trace you see streaming is exactly what gets stored.

**Inspect:**
- `GET /v1/agents/runs?kind=&limit=` — recent runs
- `GET /v1/agents/runs/{id}` — full ordered event trace
- Agents page → **Run history** table → click `trace` for the timeline

---

## 9. Reliability guarantees (every workflow)

1. **Routing never raises** — classification failure → `tools`/`direct`.
2. **Every provider call is bounded** — timeout + retries + candidate
   failover through the model policy list.
3. **Components degrade gracefully** — malformed envelope → renderer
   repair → deterministic component floor → plain markdown.
4. **Empty answers impossible** — `reliable.py` last-resort responder.
5. **Partial failure is honest** — `degraded` flag + named failed steps
   in the trace; verified findings still delivered.
6. **Rate limits + quotas enforced** — slowapi middleware + per-plan
   daily counters in `usage_events`.

---

## 10. High-level design

The platform is a **gateway + agent runtime**, not a chat wrapper:

```
┌────────────────────────────────────────────────────────────┐
│ INTERFACE LAYER                                             │
│ chat.html (conversations, components) · research.html       │
│ roleplay.html · agents.html · skills.html · library.html    │
│ → all consume the same /v1 API surface + SSE activity feed  │
├────────────────────────────────────────────────────────────┤
│ ROUTING LAYER                                               │
│ orchestrator.route_message — one decision:                  │
│ direct | tools | research | orchestrate                     │
├────────────────────────────────────────────────────────────┤
│ AGENT RUNTIME                                               │
│ execution.py  — plan → dependency levels → parallel steps   │
│ research.py  — planner → workers → verifier → sectioned     │
│                synthesis → component envelope               │
│ tools.py     — model↔tool loop                             │
│ agents.py    — single-agent configs                         │
│ roleplay.py  — persona-locked multi-character turns         │
├────────────────────────────────────────────────────────────┤
│ INTELLIGENCE LAYER                                          │
│ intent.py    — classify request (intent/complexity/domains) │
│ experts.py   — specialist registry + prompts + tools        │
│ skills.py    — reusable instruction blocks (built-in+custom)│
│ model_policy — per-role model selection + failover order    │
│ reliable.py  — bounded retries + never-empty answers        │
├────────────────────────────────────────────────────────────┤
│ PROVIDER LAYER                                              │
│ gateway.py — health-aware sequential failover, sticky       │
│ routing, cooldowns; providers/*.py adapters; ollama local    │
├────────────────────────────────────────────────────────────┤
│ PERSISTENCE / OBSERVABILITY                                 │
│ SQLite: providers, keys, conversations, research_runs,      │
│ agent_configs, characters, roleplay_rooms, documents/       │
│ chunks, users, auth_tokens, usage_events, agent_runs/       │
│ agent_events (durable execution ledger)                     │
└────────────────────────────────────────────────────────────┘
```

Design invariants:

- **Fail-open everywhere** — routing, classification, components,
  observability all degrade instead of erroring.
- **Concrete models by role** — internal stages never default to `auto`;
  candidate lists go strong→weak with `auto` last.
- **Everything is recorded** — requests log, usage events, research
  runs, agent run/event ledger, room transcripts.
- **Free-first** — groq → openrouter → other free providers → ollama
  local as the final backstop; BYOK supported throughout.

---

## 11. Agent pipeline in detail

### 11.1 The delegation contract

An "agent" in this system is **not** a process — it is a *prompt +
permissions + model hint*, executed as one LLM call inside the
orchestrator's engine:

```
ExpertDef(name, description, domain, prompt, capabilities, skills,
          tools, model_hint)
        │
        ▼
TaskStep(id, expert, task, depends_on)
        │
        ▼
assembled prompt = expert.prompt
                 + skills.attach_instructions(...)   (skill blocks)
                 + tools schema (if the expert's skills need them)
        │
        ▼
reliable_call → model-policy candidates → gateway.chat
        │
        ▼
StepResult(ok, text, model, attempts, duration_ms, error)
```

### 11.2 Planning strategies

| Strategy | When | How |
|---|---|---|
| `single` | simple/low-confidence intent | one expert, one call — no plan overhead |
| `template` | research/multi-domain but shallow | deterministic step list (researcher → verifier → writer) |
| `llm` | complex multi-domain | reasoning model emits JSON steps; unknown experts and dangling deps are sanitized; a verifier step is appended |

### 11.3 Parallelism model

`ExecutionPlan.levels()` topologically sorts steps into dependency
levels. All steps in a level run via `asyncio.gather` — true
concurrency. A step that depends on another's output only runs in a
later level. Cycles are broken by running remaining steps together.

### 11.4 Failure semantics per step

1. `reliable_call` retries with exponential backoff (bounded).
2. Model-policy candidate list iterates on empty/failed responses.
3. A failed step becomes `StepResult(ok=False)` — the run continues.
4. Synthesis receives only successful outputs; if all fail, the
   last-resort responder answers honestly with the failure summary.
5. `degraded=True` propagates to `meta` and the UI badge.

### 11.5 Verification inside the pipeline

Two verifier layers exist:

- **Research verifier** — checks each finding's claim against its
  cited source content (supported / partially / unverified /
  unsupported / contradicted).
- **Orchestration verifier step** — a template-plan expert that reviews
  sibling steps' outputs for gaps and contradictions before synthesis.

---

## 12. Prompting techniques used

### 12.1 System-prompt layering

Prompts are composed, never overwritten — most specific wins:

```
[1] structured-output contract   (component envelope rules — FIRST,
                                  so provider-agnostic rules aren't
                                  diluted by persona text)
[2] pipeline prompt              (planner/verifier/section writer)
[3] custom user instructions     (settings-store block)
[4] agent/expert prompt          (identity + domain expertise)
[5] skill instruction blocks     (attach_instructions output)
[6] conversation/tool context    (history, tool results)
```

### 12.2 Structured output contract

`response_protocol.structured_system_prompt()` instructs the model to
emit a `nova-response` fenced JSON envelope:

```json
{"response_type": "...", "content": "...",
 "components": [{"type": "...", "data": {...}}],
 "sources": [...], "suggested_prompts": [...]}
```

Enforcement is three-layered:

1. **Prompt** — the contract itself.
2. **Repair** — `component_enforce` tries a dedicated renderer-model
   call to fix malformed envelopes.
3. **Deterministic floor** — markdown→components fallback guarantees
   *something* renders; research adds a state-derived component floor
   (person_card, key-findings list, timeline, search_result carousel,
   conflict callout) built only from verified run state.

### 12.3 Role prompts (research pipeline)

| Role | Prompt technique |
|---|---|
| Planner | JSON-mode output (`response_format: json_object`), strict sub-question schema, aspect decomposition guidance |
| Researcher | tool-loop prompt + harvested-URL context + per-worker focus question |
| Gap checker | "what's missing" framing over current findings |
| Verifier | verdict taxonomy forced (supported/partial/unverified/unsupported/contradicted) |
| Section writer | single-aspect scope, citation `[n]` requirement, ~900–1200 word depth mandate, no invented facts |
| Summary writer | 150–250 word executive summary, lead with the key finding |

### 12.4 Persona prompting (roleplay)

`roleplay.persona_prompt()` locks identity per turn:

- "You are {name}. Stay in character… never mention being an AI"
- structured fields: role, description, background, personality,
  motivations, knowledge domain, speaking style
- `Boundaries (never cross)` — explicit negative constraints
- `Example of your voice` — few-shot dialogue
- scene + cast roster so characters react to *each other*, not generics

### 12.5 Reliability prompting

- `temperature` tuned per role: planners/verifiers 0.2 (deterministic),
  roleplay 0.8 (personality), synthesis default.
- **Never-empty guarantee** — `reliable.py` last-resort responder when
  every candidate fails; honest degradation messages, not apologies.
- **No fabrication clauses** — every research/section prompt states
  "use only the findings provided; never invent facts, numbers, dates
  or sources"; uncertainty is surfaced as a first-class section.

##
