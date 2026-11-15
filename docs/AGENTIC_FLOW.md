# Agentic Flow — how a request becomes an answer

Every request travels one of four pipelines, decided by the orchestrator's
routing step. All agentic work is recorded durably in `agent_runs` +
`agent_events` and viewable at `/v1/agents/runs` (or the Agents page).

```
chat request ──► route_message() ──┬─ direct      (plain model call)
                                   ├─ tools       (tool-call loop: web_search, http_get…)
                                   ├─ research    (multi-agent research pipeline)
                                   └─ orchestrate (expert delegation engine)
```

## 1. Routing — `app/services/orchestrator.py::route_message`

- `intent.classify_request` scores the message: intent (chat/lookup/code/
  data/research/mixed), complexity (simple/standard/complex), domains.
- `direct` — greetings and simple chat skip everything.
- `tools` — default workhorse: single model loop with built-in tools.
- `research` — explicit research intent or current-data questions.
- `orchestrate` — complex multi-domain work that benefits from experts.

## 2. Research pipeline — `app/services/research.py::run_research`

```
question ─► classify_depth (quick|standard|deep)
         ─► planner  → sub-questions (one worker each, ≤14 deep)
         ─► researchers (parallel) → web_search + http_get per worker
         ─► follow-up rounds → gap checker → more targeted searches
         ─► verifier   → verdicts per finding (supported/partial/…)
         ─► sectioned synthesis → per-aspect writers (parallel) + summary
         ─► deterministic methodology + uncertainty close
         ─► component renderer (nova-response envelope) → sources block
```
Every `stage`/`note`/`research_done` event is recorded in `agent_events`
(kinded `research`).

## 3. Orchestration engine — `app/services/execution.py::orchestrate`

```
question ─► classify_request (intent + domains)
         ─► plan: template plan (deterministic) or LLM plan (JSON steps)
         ─► dependency levels → parallel batches of expert steps
         ─► per step: expert prompt + skills + tools + model-policy
            candidates → reliable_call (bounded retries, failover)
         ─► validate step outputs; drop empty/failed steps
         ─► synthesizer merges surviving results → final answer
         ─► trace → OrchestrationResult (degraded flag, steps, trace)
```

- **Experts** (`experts.py`): ~21 built-in specialists (research, code,
  data, security, writing, design, product, ops…) seeded into
  `agent_configs` at startup.
- **Skills** (`skills.py` + `custom_skills`): instruction blocks attached
  to expert prompts; custom skills merge into the registry live.
- **Model policy** (`model_policy.py`): internal stages pick concrete
  models by role (reasoning/general/coding/fast) — groq first, openrouter
  second, `auto` only as last-resort backstop.
- **Reliability** (`reliable.py`): bounded retries, exponential backoff,
  last-resort answers — a run always terminates in a controlled state.

## 4. Agent chat — `/v1/agents/{id}/chat`

Single-agent execution against an `AgentConfig` (system prompt, model,
tools, output format). Recorded as kind `agent`; streaming runs finish
the ledger entry when the SSE stream closes.

## 5. Recording model

| Table | Holds |
|---|---|
| `agent_runs` | run id, kind, question, subject, model, status, meta, timing |
| `agent_events` | seq, stage, actor, event, detail, status, duration_ms |

Writes are self-contained sessions that commit immediately — the ledger
survives request timeouts and client disconnects.

## 6. Inspection

- `GET /v1/agents/runs?kind=&limit=` — list recent runs
- `GET /v1/agents/runs/{id}` — full ordered event trace
- Agents page → "Run history" panel
