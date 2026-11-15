# Nova v2: Research Redesign

A gap analysis of the current Nova research system and a proposed redesign to bring it level with ChatGPT Deep Research and Manus.

## Summary

Nova's architecture is strong on reliability: fail-open routing, a durable run ledger, and graceful degradation. Research quality, however, is limited by structure rather than polish. The current pipeline is a fixed sequence (plan, workers, gap check, synthesis), while leading research agents run an adaptive loop over real tools and typed evidence. The redesign below addresses that.

## Where Nova falls short of ChatGPT Deep Research and Manus

1. **The plan is fixed up front.** Plan, then workers, then gap check, then synthesis is a pipeline. Deep Research is a loop: it reads, changes its mind, and chases new leads. The current follow-up rounds (1 to 3) are a weak version of that.
2. **The tools are thin.** `web_search` and `http_get` cannot render JS pages, read PDFs well, click, paginate, or run code. Manus's edge is largely its browser and sandbox.
3. **Workers return text, not typed evidence.** Findings are not stored as quote-level, retrievable evidence, so writers cannot cite precisely.
4. **Section writers work in isolation.** Parallel writers with a 900 to 1200 word quota and an expansion retry optimize for length, not insight. The result is redundancy, contradictions between sections, and padding. Length is not the quality bar; synthesis is.
5. **There is no clarify or steer step.** Users cannot approve the plan, answer a scoping question, or redirect mid-run.
6. **Runs probably die with the request.** The ledger survives, but execution may not.
7. **There is no eval harness,** so there is no way to tell whether a change helped.
8. **Web content is untrusted but treated as trusted.** This creates prompt-injection and SSRF risk on `http_get`.
9. **Free-tier models cap the quality ceiling.**

## Nova v2 design

```
User ─► CLARIFIER ─► editable PLAN (user approves/edits)
                         │
                         ▼
              ┌── LEAD RESEARCHER (strong model) ──┐
              │  owns: question tree + budget      │
              │  loop: decide → delegate → read    │
              │        → update → stop?            │
              └──┬──────────┬───────────┬──────────┘
                 ▼          ▼           ▼
            Sub-agents with real loops (own tools, own context)
            web · browser · academic · code sandbox · files
                 │
                 ▼
        EVIDENCE STORE (Postgres + pgvector)
        {claim, quote span, url, date, source score,
         fetched_at, corroborated_by[]}
                 │
                 ▼
   VERIFIER ─► OUTLINER ─► WRITERS (retrieve evidence by id)
                 ─► EDITOR (global dedupe, consistency, citation bind)
                 ─► ARTIFACT BUILDER (report, PDF, charts, xlsx, slides)
```

## The ten upgrades, in priority order

### 1. Adaptive research loop

A lead agent keeps a question tree with open, answered, and contradicted nodes. It spawns sub-agents on demand and stops on coverage or budget, not a fixed round count. This is the biggest single quality gain.

### 2. Evidence store as the backbone

Workers write typed evidence rows: claim, exact quote span, URL, publish date, and retrieval time. Writers retrieve by id, so every citation is bound to a real span. This eliminates hallucinated `[n]` references.

### 3. Real tools

- Headless browser (Playwright) with readability extraction
- PDF and table parsing
- Search aggregation (Brave, Tavily, Exa, or self-hosted SearXNG)
- Academic APIs (OpenAlex, Semantic Scholar, arXiv)
- A sandboxed Python executor for calculations and charts

### 4. Outline-first synthesis

Build the outline from the evidence graph, write sections with shared context, then run an editor pass. Drop the word-count quota and set a coverage and insight target instead.

### 5. Stronger verification

Check each claim against its quote span, require two or more independent sources for key claims, and score sources by domain type, recency, and primary versus secondary. Surface conflicts instead of averaging them.

### 6. Durable jobs

Run research on a queue (Arq or Celery with Redis). Checkpoint each stage so runs can resume. Add reconnectable SSE, cancel, and a mid-run "steer" message.

### 7. Clarify and plan-approval UX

Ask 1 to 3 scoping questions, show the plan, and let the user edit it. ChatGPT does this, and it improves results more than it appears to.

### 8. Model tiering

Use a frontier model for lead, verifier, and editor (BYOK or paid tier), and cheap fast models for workers. Keep the free chain as a fallback, but be honest in the UI about which tier ran.

### 9. Safety layer

Wrap fetched content in an untrusted-data envelope with instruction-stripping, block private IPs in `http_get`, and run code only in a sandbox.

### 10. Evals and tracing

Build a 50 to 100 question benchmark (BrowseComp, GAIA, or DeepResearch Bench style) and score citation precision, coverage, and factuality with an LLM judge. Add per-step token and cost tracing (Langfuse or OpenTelemetry). Without evals, changes are guesses.

## Smaller changes worth making

- Move from SQLite to Postgres with pgvector once there are concurrent runs.
- Add a cross-run source cache with TTL, so repeated fetches are free.
- Add user memory, so past research informs new runs.
- Offer exportable artifacts (PDF, DOCX, XLSX, slides), since Manus users value the deliverable.

## Build order

- **Phase 1 (weeks 1 to 2):** evidence store, browser and PDF tools, durable job queue, eval set.
- **Phase 2 (weeks 3 to 4):** adaptive lead-agent loop, outline-first synthesis with an editor, quote-level verification.
- **Phase 3:** clarify and plan UX, steering, artifacts, and the safety layer.
- **Phase 4:** model tiering, memory, and tuning against the evals.

## Expectations

"100x" is not a real measure here, but Phases 1 and 2 should close most of the perceptible gap with ChatGPT. Manus-level agentic behavior (browsing, files, code) needs Phase 3.

## Next steps

- Expand this into a full design doc with schemas and API contracts.
- Write the Phase 1 code, starting with the evidence store and the adaptive research loop.

# Part II: Detailed Design Specification

**Status:** Draft v0.1. **Scope:** the research subsystem of Nova (`/v1/research/*`). Chat, roleplay, and knowledge retrieval are unchanged except where noted in the migration section.

## 1. Goals and non-goals

### Goals

- Produce research reports whose every factual claim is bound to a stored, verifiable quote span.
- Replace the fixed pipeline with an adaptive loop that follows leads, resolves contradictions, and stops on coverage or budget.
- Give agents real tools: rendered browsing, PDF and table extraction, academic search, and a code sandbox.
- Make runs durable: they survive client disconnects and worker restarts, and they can be paused, steered, resumed, and cancelled.
- Keep Nova's fail-open philosophy: a failed step degrades the run, it never aborts it silently or returns an empty answer.
- Make quality measurable with an offline eval harness and per-run cost and latency tracing.

### Non-goals (v2)

- Multi-tenant enterprise features such as SSO and audit export.
- Training or fine-tuning models.
- Real-time collaborative editing of reports.
- Replacing the chat, roleplay, or knowledge pipelines.

## 2. Requirements

| Area | Requirement | Target |
| --- | --- | --- |
| Quality | Citation precision (claim supported by cited span) | 95% or higher on the eval set |
| Quality | Coverage of reference-answer facts | 80% or higher on the eval set |
| Latency | Quick depth | under 60 s to final answer |
| Latency | Standard depth | under 6 min |
| Latency | Deep depth | under 25 min, with progress events every 5 s or less |
| Durability | Worker crash mid-run | resume from last checkpoint, no repeated paid calls for completed stages |
| Cost | Per-run budget | hard cap in tokens, tool calls, and wall time, set per depth |
| Safety | Fetched content treated as untrusted | no instruction from a web page may change agent behavior |
| Observability | Every LLM call and tool call traced | 100 percent, with token counts and cost |
| Compatibility | Existing `nova-response` components | still rendered from final state |

## 3. System components

| Component | Responsibility | Tech |
| --- | --- | --- |
| API service | Auth, validation, run CRUD, SSE streaming, rate limits | FastAPI, slowapi |
| Job runner | Executes runs as durable jobs, checkpoints stages | Arq on Redis |
| Clarifier | Produces 0 to 3 scoping questions | Reasoning-tier model |
| Planner | Produces the initial question tree and budget | Reasoning-tier model |
| Lead researcher | Owns the loop: decide, delegate, read, update, stop | Frontier-tier model |
| Sub-agents | Bounded tool loops that return typed evidence | Worker-tier model |
| Tool service | Executes tools with caching, quotas, and safety wrapping | Python modules, Playwright pool, sandbox |
| Evidence store | Sources, quote spans, claims, links, embeddings | Postgres with pgvector |
| Verifier | Checks claims against spans, scores corroboration | Frontier-tier model plus deterministic checks |
| Outliner and writers | Outline from the evidence graph, then section drafting | Frontier or reasoning tier |
| Editor | Global dedupe, consistency, citation binding | Frontier tier |
| Artifact builder | Report to Markdown, PDF, DOCX, XLSX, slides, charts | Python, headless renderer |
| Model router | Role to model selection, failover, cost accounting | extends existing `model_policy` and `gateway` |
| Event bus | Fan-out of run events to SSE and the ledger | Redis streams |
| Trace and cost | Per-call spans, token and cost totals | Langfuse or OpenTelemetry |

### Deployment topology

```
Client ──HTTP/SSE──► API service ──enqueue──► Redis (Arq queue + event streams)
                         │                         │
                         │                         ▼
                         │                  Worker processes (N)
                         │                    ├─ Lead + sub-agent loops
                         │                    ├─ Tool service ─► Browser pool (Playwright)
                         │                    │               ─► Code sandbox (gVisor container)
                         │                    │               ─► External search / academic APIs
                         │                    └─ Model router ─► Providers (frontier / reasoning / worker)
                         ▼
                  Postgres + pgvector  ◄── evidence, runs, events, traces
                  Object storage       ◄── page snapshots, PDFs, artifacts
```

## 4. Run lifecycle

Each run is a state machine. Every transition is persisted before work begins, which is what makes resume possible.

```
created ─► clarifying ─► planning ─► awaiting_approval ─► researching
                                                              │
   ┌──────────────────────────────────────────────────────────┘
   ▼
verifying ─► outlining ─► writing ─► editing ─► building_artifacts ─► done

Any state ─► paused (user) ─► previous state
Any state ─► cancelled (user)
Any state ─► degraded (non-fatal failures recorded; run still reaches done)
Any state ─► failed (only when no usable evidence exists and last-resort responder also fails)
```

| State | Entry condition | Exit condition | Checkpoint written |
| --- | --- | --- | --- |
| `clarifying` | Run created and clarify enabled | Answers received, or skipped by user or timeout | questions and answers |
| `planning` | Clarify done | Plan stored | question tree, budget |
| `awaiting_approval` | Plan stored and approval enabled | User approves, edits, or auto-approve timeout | approved plan version |
| `researching` | Plan approved | Coverage reached, budget spent, or lead decides stop | question tree state, evidence rows |
| `verifying` | Research stopped | All key claims have a verdict | claim verdicts |
| `outlining` | Verification done | Outline stored | outline JSON |
| `writing` | Outline stored | All sections drafted | section drafts |
| `editing` | Sections drafted | Edited report stored | final report and citation map |
| `building_artifacts` | Report stored | Requested exports generated | artifact rows |

## 5. Data model

Postgres 15 or later with the `pgvector` and `pg_trgm` extensions. All ids are UUIDv7 for time-ordered inserts. All tables carry `created_at`; mutable tables carry `updated_at`.

### 5.1 Runs, stages, and events

```sql
CREATE TABLE research_runs (
  id            uuid PRIMARY KEY,
  owner_id      uuid NOT NULL REFERENCES users(id),
  question      text NOT NULL,
  depth         text NOT NULL CHECK (depth IN ('quick','standard','deep')),
  state         text NOT NULL,
  options       jsonb NOT NULL DEFAULT '{}',   -- clarify, approval, exports, language, date_range
  budget        jsonb NOT NULL,                -- {max_tokens, max_tool_calls, max_wall_s, max_cost_usd}
  spent         jsonb NOT NULL DEFAULT '{}',   -- running totals against budget
  plan_version  int  NOT NULL DEFAULT 0,
  degraded      boolean NOT NULL DEFAULT false,
  error         text,
  result_ref    uuid,                          -- final report id
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz
);

CREATE TABLE run_stages (
  id          uuid PRIMARY KEY,
  run_id      uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  stage       text NOT NULL,
  status      text NOT NULL CHECK (status IN ('pending','running','done','failed','skipped')),
  attempt     int  NOT NULL DEFAULT 1,
  checkpoint  jsonb,                           -- stage-specific resume data
  started_at  timestamptz,
  finished_at timestamptz,
  UNIQUE (run_id, stage, attempt)
);

CREATE TABLE run_events (
  run_id     uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  seq        bigint NOT NULL,                  -- monotonic per run, used for SSE resume
  ts         timestamptz NOT NULL DEFAULT now(),
  type       text NOT NULL,                    -- see section 13.4
  actor      text NOT NULL,                    -- lead | subagent:<id> | verifier | writer:<section> | system
  payload    jsonb NOT NULL,
  PRIMARY KEY (run_id, seq)
);

CREATE TABLE steer_messages (
  id         uuid PRIMARY KEY,
  run_id     uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  text       text NOT NULL,
  applied_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
```

### 5.2 Question tree

```sql
CREATE TABLE questions (
  id          uuid PRIMARY KEY,
  run_id      uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  parent_id   uuid REFERENCES questions(id),
  text        text NOT NULL,
  aspect      text,                            -- grouping label used by the outliner
  status      text NOT NULL CHECK (status IN ('open','in_progress','answered','contradicted','abandoned')),
  priority    int  NOT NULL DEFAULT 5,         -- 1 highest
  coverage    real NOT NULL DEFAULT 0,         -- 0..1, lead's estimate
  notes       text,                            -- lead's running summary for this node
  created_by  text NOT NULL,                   -- planner | lead | subagent:<id> | user
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX questions_run_status ON questions (run_id, status);
```

### 5.3 Sources, evidence, claims

```sql
CREATE TABLE sources (
  id            uuid PRIMARY KEY,
  url           text NOT NULL,
  canonical_url text NOT NULL,
  domain        text NOT NULL,
  title         text,
  author        text,
  published_at  timestamptz,
  fetched_at    timestamptz NOT NULL,
  content_type  text NOT NULL,                 -- html | pdf | json | other
  content_hash  text NOT NULL,
  snapshot_ref  text,                          -- object storage key for raw snapshot
  text_ref      text,                          -- object storage key for extracted text
  source_class  text NOT NULL,                 -- primary | official | academic | news | reference | blog | forum | unknown
  score         real NOT NULL,                 -- 0..1, see section 9
  score_detail  jsonb NOT NULL,
  UNIQUE (canonical_url, content_hash)
);

CREATE TABLE evidence (
  id           uuid PRIMARY KEY,
  run_id       uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  question_id  uuid REFERENCES questions(id),
  source_id    uuid NOT NULL REFERENCES sources(id),
  quote        text NOT NULL,                  -- exact text from the source
  span_start   int  NOT NULL,                  -- char offsets into extracted text
  span_end     int  NOT NULL,
  context      text,                           -- surrounding sentence(s) for disambiguation
  summary      text NOT NULL,                  -- worker's one-line paraphrase
  embedding    vector(1024),
  extracted_by text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX evidence_run ON evidence (run_id);
CREATE INDEX evidence_embed ON evidence USING hnsw (embedding vector_cosine_ops);

CREATE TABLE claims (
  id            uuid PRIMARY KEY,
  run_id        uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  question_id   uuid REFERENCES questions(id),
  text          text NOT NULL,                 -- atomic, self-contained statement
  is_key        boolean NOT NULL DEFAULT false,
  verdict       text NOT NULL DEFAULT 'unchecked'
                CHECK (verdict IN ('unchecked','supported','partially_supported','unverified','unsupported','contradicted')),
  confidence    real,
  independent_sources int NOT NULL DEFAULT 0,
  verifier_note text,
  verified_at   timestamptz
);

CREATE TABLE claim_evidence (
  claim_id    uuid NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  evidence_id uuid NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
  relation    text NOT NULL CHECK (relation IN ('supports','refutes','context')),
  PRIMARY KEY (claim_id, evidence_id)
);
```

### 5.4 Report and artifacts

```sql
CREATE TABLE reports (
  id          uuid PRIMARY KEY,
  run_id      uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  version     int NOT NULL DEFAULT 1,
  outline     jsonb NOT NULL,
  markdown    text NOT NULL,                   -- final body with [[c:<claim_id>]] markers resolved to [n]
  components  jsonb,                           -- nova-response envelope
  quality     jsonb,                           -- coverage, confidence, conflict count, degraded steps
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE report_citations (
  report_id   uuid NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
  n           int  NOT NULL,                   -- the [n] shown to the reader
  evidence_id uuid NOT NULL REFERENCES evidence(id),
  PRIMARY KEY (report_id, n)
);

CREATE TABLE artifacts (
  id         uuid PRIMARY KEY,
  run_id     uuid NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
  kind       text NOT NULL,                    -- pdf | docx | xlsx | pptx | md | png | csv
  name       text NOT NULL,
  storage_ref text NOT NULL,
  bytes      bigint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
```

### 5.5 Calls, traces, and caches

```sql
CREATE TABLE llm_calls (
  id          uuid PRIMARY KEY,
  run_id      uuid REFERENCES research_runs(id) ON DELETE CASCADE,
  actor       text NOT NULL,
  role        text NOT NULL,                   -- lead | verifier | editor | writer | worker | planner | clarifier
  provider    text NOT NULL,
  model       text NOT NULL,
  tier        text NOT NULL,                   -- frontier | reasoning | worker | fallback
  tokens_in   int NOT NULL,
  tokens_out  int NOT NULL,
  cost_usd    numeric(10,6) NOT NULL DEFAULT 0,
  latency_ms  int NOT NULL,
  ok          boolean NOT NULL,
  error       text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tool_calls (
  id          uuid PRIMARY KEY,
  run_id      uuid REFERENCES research_runs(id) ON DELETE CASCADE,
  actor       text NOT NULL,
  tool        text NOT NULL,
  args        jsonb NOT NULL,
  ok          boolean NOT NULL,
  cache_hit   boolean NOT NULL DEFAULT false,
  latency_ms  int NOT NULL,
  result_ref  text,                            -- object storage key if large
  error       text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE fetch_cache (
  canonical_url text PRIMARY KEY,
  source_id     uuid REFERENCES sources(id),
  fetched_at    timestamptz NOT NULL,
  ttl_s         int NOT NULL,                  -- by source class: news 1h, reference 7d, academic 30d
  etag          text
);
```

### 5.6 Migration note for existing tables

The existing `research_runs`, `agent_runs`, and `agent_events` tables map as follows: v1 `research_runs` rows are imported into `reports` (with `markdown` populated and citations marked `legacy`), and `agent_events` continues to serve non-research agent kinds. `run_events` replaces `agent_events` for research runs only, because it needs the monotonic `seq` for SSE resume.

## 6. Lead researcher loop

The lead agent is the only component that decides what to research next. It never reads full pages. It reads compact digests produced by sub-agents and the current question tree.

### 6.1 Loop

```python
async def lead_loop(run):
    state = load_state(run)                       # question tree, evidence digest, budget
    while True:
        apply_pending_steers(run, state)          # user messages become tree edits
        if budget_exhausted(run) or coverage_met(state):
            break
        decision = await lead_decide(state)       # frontier model, JSON output (6.2)
        if decision.action == "stop":
            break
        tasks = decision.delegations[: run.budget.max_parallel]
        results = await asyncio.gather(*[run_subagent(t) for t in tasks])
        for r in results:
            persist_evidence(r.evidence)
            update_tree(state, r)                 # status, coverage, new child questions
        checkpoint(run, state)                    # resume point after every round
    return state
```

### 6.2 Lead decision contract

The lead receives the question tree (ids, text, status, coverage, notes), a digest of the top evidence per open question, open contradictions, remaining budget, and unapplied steer messages. It returns:

```json
{
  "action": "delegate",
  "reasoning_summary": "Two sources disagree on 2023 revenue; need a primary filing.",
  "delegations": [
    {
      "question_id": "q_7",
      "agent_type": "web",
      "objective": "Find the company's FY2023 annual report and extract reported revenue.",
      "constraints": {"prefer": ["primary", "official"], "max_tool_calls": 8, "date_after": "2024-01-01"},
      "expected_output": "evidence rows with exact quotes for revenue figures"
    }
  ],
  "tree_edits": [
    {"op": "add_child", "parent_id": "q_7", "text": "Which accounting standard was used?", "priority": 3},
    {"op": "set_status", "question_id": "q_2", "status": "answered", "coverage": 0.9}
  ]
}
```

### 6.3 Stop criteria

The loop stops when any of these hold:

1. All questions with priority 1 to 3 are `answered`, `contradicted` (and the contradiction is documented), or `abandoned` with a reason.
2. Marginal gain is low: the last two rounds added no new supported claims for open questions.
3. Any budget is exhausted: tokens, tool calls, wall time, or cost.
4. The user cancels, or pauses and later resumes.

On budget exhaustion the run proceeds to verification with what exists and sets `degraded=true` with the list of unanswered questions.

### 6.4 Budgets by depth

| Depth | Max rounds | Max parallel sub-agents | Max tool calls | Max wall time | Max cost |
| --- | --- | --- | --- | --- | --- |
| quick | 1 | 2 | 8 | 60 s | $0.05 |
| standard | 4 | 4 | 40 | 6 min | $0.60 |
| deep | 10 | 6 | 150 | 25 min | $3.00 |

Defaults are configurable per plan tier. Costs assume the tiering in section 12 and are caps, not estimates.

### 6.5 Steering

A steer message is stored, and the next loop iteration injects it into the lead's context as a user instruction. The lead may add, reprioritize, or abandon questions in response. Steers never interrupt a running sub-agent; they take effect at the next round boundary, except `cancel`, which cancels immediately.

## 7. Sub-agents

A sub-agent is a bounded tool loop with its own context window. It is not a long-lived process.

### 7.1 Types

| Type | Tools | Typical use |
| --- | --- | --- |
| `web` | `search`, `fetch_page`, `browse` | General web questions, news, company and product facts |
| `academic` | `academic_search`, `read_pdf`, `fetch_page` | Papers, citations, methodology, benchmarks |
| `data` | `fetch_page`, `read_pdf`, `run_python`, `read_file` | Numbers, tables, calculations, charts |
| `file` | `read_file`, `run_python` | User-uploaded documents |
| `verifier` | `fetch_page`, `search` | Resolve a specific contradiction with a primary source |

### 7.2 Input and output contract

```json
// input
{
  "run_id": "…", "agent_id": "sa_12", "question_id": "q_7",
  "objective": "…", "constraints": {…},
  "known_evidence_ids": ["ev_1", "ev_9"],      // so it does not re-find the same facts
  "budget": {"max_tool_calls": 8, "max_tokens": 40000}
}

// output
{
  "agent_id": "sa_12",
  "status": "answered | partial | failed",
  "evidence": [
    {
      "source_url": "https://…",
      "quote": "exact text copied from the page",
      "summary": "one-line paraphrase",
      "relevance": 0.0
    }
  ],
  "candidate_claims": [
    {"text": "atomic claim", "evidence_idx": [0, 2], "is_key": true}
  ],
  "new_questions": [{"text": "…", "priority": 4}],
  "dead_ends": [{"what": "searched X", "why": "no primary source found"}],
  "digest": "≤120 words for the lead"
}
```

### 7.3 Rules enforced by the runtime, not the prompt

- A `quote` must appear verbatim in the extracted text of its source. The runtime locates the span and rejects evidence whose quote cannot be found (after whitespace normalization).
- Tool-call and token budgets are hard limits enforced by the loop runner.
- Sub-agents see tool results wrapped as untrusted data (section 15).
- Context is summarized after every 6 tool calls so long loops do not degrade.

## 8. Tool contracts

All tools are async, return a uniform envelope, and are logged in `tool_calls`.

```json
{ "ok": true, "data": {…}, "error": null, "cache_hit": false, "truncated": false }
```

| Tool | Input | Output | Notes |
| --- | --- | --- | --- |
| `search` | `query`, `num` (≤10), `recency` (\`day | week | month |
| `fetch_page` | `url`, `mode` (\`auto | raw | reader` ),  `max\_chars\` |
| `browse` | `url`, `actions[]` (`click`, `scroll`, `type`, `wait_for`), `extract` (\`reader | selector\`) | same as `fetch_page` plus `screenshot_ref` |
| `read_pdf` | `url` or `file_id`, `pages` (range) | `{source_id, text, tables[]}` | Text layer first, OCR fallback; tables as row arrays |
| `academic_search` | `query`, `year_from`, `fields[]` | list of `{title, authors, year, venue, doi, abstract, pdf_url, citation_count}` | OpenAlex and Semantic Scholar, arXiv fallback |
| `run_python` | `code`, `files[]` | `{stdout, stderr, files[], duration_ms}` | gVisor container, no network, 30 s CPU, 512 MB, only stdlib plus numpy, pandas, matplotlib |
| `read_file` | `file_id`, `pages` or `range` | `{text, tables[]}` | Reads the user's uploaded files and ingested documents |

### 8.1 Caching

`fetch_cache` maps canonical URL to a stored source, with TTL by class: news 1 hour, general 24 hours, reference 7 days, academic 30 days. A cache hit still creates a new `evidence` row for the run when a worker extracts from it, so evidence stays run-scoped while fetching is shared.

### 8.2 Errors

Tool errors are returned in the envelope, never raised into the loop. The envelope's `error.code` is one of `timeout`, `blocked`, `not_found`, `paywalled`, `too_large`, `unsafe_url`, `sandbox_error`, `rate_limited`, or `internal`. Sub-agents are prompted on how to react to each, for example trying a different source after `paywalled`.

## 9. Source scoring

Each source gets a score from 0 to 1, stored with its breakdown so it can be audited.

```
score = 0.40 * authority + 0.25 * primacy + 0.20 * recency_fit + 0.15 * transparency
```

| Factor | How it is computed |
| --- | --- |
| authority | Domain class from a maintained table: government and standards bodies, peer-reviewed venues, major newsrooms, established reference works rank high; unattributed blogs and forums rank low |
| primacy | 1.0 for primary documents (filings, datasets, original papers, official statements), 0.6 for direct reporting, 0.3 for secondary summaries |
| recency\_fit | Decays with age only when the question is time-sensitive, as marked by the planner; otherwise 1.0 |
| transparency | Named author, date, and references present |

The verifier applies a corroboration rule on top: a key claim counts as supported only with at least two independent sources (different domains and not citing each other), or one primary source with score 0.8 or higher.

## 10. Verification

### 10.1 Algorithm

1. **Atomize.** Each candidate claim is checked to be atomic and self-contained. The verifier splits compound claims.
2. **Deterministic check.** For each claim-evidence link, confirm the quote exists in the source text and that any numbers and named entities in the claim appear in the quote or its context.
3. **Entailment check.** A frontier-tier model judges whether the quote supports, partially supports, or fails to support the claim, with a one-line rationale.
4. **Corroboration.** Count independent supporting sources and compare against the rule in section 9.
5. **Contradiction scan.** Embed the claim, find nearest evidence from other sources, and flag pairs where an entailment judgment returns contradiction.
6. **Verdict.** One of `supported`, `partially_supported`, `unverified`, `unsupported`, `contradicted`, plus a confidence value.

### 10.2 What happens to each verdict

| Verdict | In the report |
| --- | --- |
| `supported` | Stated plainly with citations |
| `partially_supported` | Stated with the supported part only, qualifier added |
| `unverified` | Omitted from findings, listed under uncertainty when relevant |
| `unsupported` | Dropped |
| `contradicted` | Presented as a disagreement with both sides cited, never averaged |

If a key claim ends `unverified` or `contradicted` and budget remains, the lead is re-invoked with a targeted `verifier` delegation before the run proceeds.

## 11. Synthesis

### 11.1 Outline

The outliner reads the question tree and verified claims and returns:

```json
{
  "title": "…",
  "thesis": "one-paragraph answer to the user's question",
  "sections": [
    {
      "id": "s2",
      "heading": "…",
      "purpose": "what this section must establish",
      "claim_ids": ["c_4", "c_9", "c_15"],
      "must_cover": ["…"],
      "depends_on": ["s1"]
    }
  ],
  "conflicts": [{"claim_ids": ["c_21", "c_22"], "section": "s4"}],
  "open_questions": ["q_11"]
}
```

### 11.2 Writers

Writers run in dependency order, with sections at the same level in parallel. Each writer receives the thesis, the full outline, the summaries of already-written sections, and only the claims and evidence assigned to its section. It must reference facts through claim markers such as `[[c:c_9]]`, not through free-form numbers. Length is set by evidence density, not a quota: a section with five strong claims should be short.

### 11.3 Editor

The editor reads the whole draft and:

- removes duplicated statements across sections,
- fixes contradictions and inconsistent terminology,
- checks that every marker resolves to a claim with an acceptable verdict,
- rewrites the executive summary last so it matches the body,
- flags any sentence that makes an unmarked factual claim, which is then either bound to a claim or removed.

### 11.4 Citation binding

A deterministic pass replaces `[[c:<claim_id>]]` markers with sequential `[n]`, builds `report_citations` rows (`n` to the best supporting `evidence_id`), and emits the reference list with title, domain, date, and quote preview. A marker pointing at a missing or rejected claim fails the build and sends the section back to its writer once. If it fails again, the sentence is dropped and the run is marked degraded.

### 11.5 Report structure

1. Executive summary
2. Findings, one section per aspect
3. Disagreements and uncertainty
4. Methodology and evidence base (sources by class, date range, what could not be verified)
5. Confidence statement
6. References

## 12. Model routing and tiering

| Role | Tier | Used for |
| --- | --- | --- |
| lead, verifier, editor | frontier | Decisions and judgments where errors compound |
| planner, clarifier, outliner, writers | reasoning | Structured planning and prose |
| sub-agent workers, extraction | worker | High-volume reading and extraction |
| fallback | free chain | Existing groq, openrouter, ollama order |

The router reads a config file mapping role to an ordered candidate list per tier, as it does today with `model_policy`. Two additions:

- **Tier honesty.** Each run records which tier actually served each role. If a frontier role fell back to the free chain, the report's quality block says so and the UI shows a badge.
- **Cost accounting.** Every call writes `cost_usd` from a price table, and the run's `spent` totals feed the budget checks in section 6.4.

BYOK keys, when present, are preferred for frontier and reasoning roles.

## 13. API contracts

All endpoints live under `/v1/research`, use bearer-token auth, and return JSON unless noted. Run ownership is enforced on every route.

### 13.1 Endpoint summary

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v1/research/runs` | Create a run |
| GET | `/v1/research/runs` | List the caller's runs |
| GET | `/v1/research/runs/{id}` | Run status, plan, spend, and result summary |
| GET | `/v1/research/runs/{id}/events` | SSE stream of events, resumable |
| POST | `/v1/research/runs/{id}/answers` | Answer clarifying questions |
| GET | `/v1/research/runs/{id}/plan` | Read the current plan |
| PUT | `/v1/research/runs/{id}/plan` | Edit the plan |
| POST | `/v1/research/runs/{id}/approve` | Approve the plan and start research |
| POST | `/v1/research/runs/{id}/steer` | Send a mid-run instruction |
| POST | `/v1/research/runs/{id}/pause` | Pause at the next safe point |
| POST | `/v1/research/runs/{id}/resume` | Resume a paused or failed run |
| POST | `/v1/research/runs/{id}/cancel` | Cancel immediately |
| GET | `/v1/research/runs/{id}/report` | Final report, citations, quality block |
| GET | `/v1/research/runs/{id}/evidence` | Evidence and claims, filterable |
| GET | `/v1/research/runs/{id}/artifacts` | List exports |
| POST | `/v1/research/runs/{id}/artifacts` | Request an export |
| GET | `/v1/research/artifacts/{artifact_id}` | Download an artifact |
| GET | `/v1/research/runs/{id}/trace` | Full ordered trace with token and cost data |

### 13.2 Create a run

```json
POST /v1/research/runs
{
  "question": "How do the leading open-weight LLMs compare on long-context accuracy?",
  "depth": "deep",                       // optional; classifier picks if omitted
  "options": {
    "clarify": true,
    "require_plan_approval": true,
    "language": "en",
    "date_range": {"from": "2025-01-01"},
    "exports": ["pdf", "xlsx"],
    "file_ids": ["file_81"]
  },
  "budget_override": {"max_cost_usd": 1.50}
}

201 Created
{
  "id": "run_01J…",
  "state": "clarifying",
  "depth": "deep",
  "budget": {"max_tokens": 1500000, "max_tool_calls": 150, "max_wall_s": 1500, "max_cost_usd": 1.5},
  "events_url": "/v1/research/runs/run_01J…/events"
}
```

### 13.3 Clarify, plan, approve

```json
// clarification questions arrive as an event and via GET /runs/{id}
{
  "state": "clarifying",
  "clarifying_questions": [
    {"id": "cq_1", "text": "Which context lengths matter most?", "options": ["32k", "128k", "1M", "any"]}
  ]
}

POST /runs/{id}/answers
{ "answers": [{"id": "cq_1", "value": "128k"}] }

// plan
GET /runs/{id}/plan
{
  "version": 1,
  "thesis_hint": "…",
  "questions": [
    {"id": "q_1", "text": "…", "aspect": "benchmarks", "priority": 1, "parent_id": null}
  ],
  "agent_types": ["web", "academic", "data"],
  "estimated": {"minutes": 14, "cost_usd": 1.1}
}

PUT /runs/{id}/plan
{ "version": 1, "questions": [ …edited list… ] }       // 409 if version is stale

POST /runs/{id}/approve
{ "version": 2 }
```

### 13.4 SSE events

`GET /runs/{id}/events` returns `text/event-stream`. Each event carries `id: <seq>`. A client reconnects with the `Last-Event-ID` header and receives every event after that sequence number, then continues live. Events are stored in `run_events`, so replay works after worker or server restarts.

| Event type | Payload (abbreviated) |
| --- | --- |
| `state` | `{state, previous}` |
| `clarify.questions` | `{questions[]}` |
| `plan.ready` | `{version, questions[]}` |
| `lead.decision` | `{round, reasoning_summary, delegations[]}` |
| `subagent.start` | `{agent_id, type, objective, question_id}` |
| `tool` | `{agent_id, tool, args_preview, status, ms}` |
| `evidence.added` | `{evidence_id, source_url, domain, score, summary}` |
| `question.updated` | `{question_id, status, coverage}` |
| `subagent.done` | `{agent_id, status, digest}` |
| `verify.claim` | `{claim_id, verdict, confidence}` |
| `conflict.found` | `{claim_ids[], note}` |
| `outline.ready` | `{sections[]}` |
| `section.done` | `{section_id, words}` |
| `report.ready` | `{report_id}` |
| `artifact.ready` | `{artifact_id, kind}` |
| `budget` | `{spent, remaining}` emitted at most every 5 s |
| `degraded` | `{reason, affected[]}` |
| `done` | `{report_id, degraded, quality}` |
| `error` | `{code, message, recoverable}` |

### 13.5 Steer, pause, resume, cancel

```json
POST /runs/{id}/steer
{ "text": "Focus on models under 20B parameters and skip proprietary ones." }
→ 202 { "steer_id": "…", "applies_at": "next_round" }

POST /runs/{id}/pause   → 202 { "state": "pausing" }
POST /runs/{id}/resume  → 202 { "state": "researching" }
POST /runs/{id}/cancel  → 200 { "state": "cancelled" }
```

Resume of a failed run restarts from the last checkpoint and charges only new spend.

### 13.6 Report

```json
GET /runs/{id}/report
{
  "report_id": "rep_…",
  "title": "…",
  "markdown": "…body with [1] [2] markers…",
  "components": { "response_type": "research_report", "components": [ … ] },   // nova-response envelope
  "citations": [
    {"n": 1, "evidence_id": "ev_…", "title": "…", "url": "https://…", "domain": "…",
     "published_at": "2025-03-02", "quote": "exact span", "source_score": 0.86}
  ],
  "quality": {
    "coverage": 0.88,
    "confidence": 0.81,
    "claims": {"supported": 64, "partially_supported": 6, "contradicted": 3, "dropped": 9},
    "independent_sources": 31,
    "tiers_used": {"lead": "frontier", "worker": "worker"},
    "degraded": false,
    "unanswered_questions": []
  }
}
```

### 13.7 Errors

All errors use one shape:

```json
{ "error": { "code": "budget_exceeded", "message": "…", "details": {…}, "request_id": "…" } }
```

| HTTP | Code | When |
| --- | --- | --- |
| 400 | `invalid_request` | Schema or option validation failed |
| 401 | `unauthorized` | Missing or invalid token |
| 403 | `quota_exceeded` | Plan quota for the day is used |
| 404 | `not_found` | Unknown run, or run owned by someone else |
| 409 | `stale_version` | Plan edit against an old version |
| 409 | `invalid_state` | Action not allowed in the current state |
| 422 | `unsafe_input` | Rejected URL or file |
| 429 | `rate_limited` | Rate limit hit, with `Retry-After` |
| 503 | `no_capacity` | Queue backlog above threshold |

Creating a run is idempotent when the client sends an `Idempotency-Key` header: the same key within 24 hours returns the original run.

### 13.8 Compatibility with chat

When `route_message` selects `research`, it creates a run through the same service and streams the SSE events into the chat as `tool` activity events. The final `report.components` envelope is passed through `enforce_components`, so the existing frontend renderer is unchanged.

## 14. Durable execution

- **Queue.** The API enqueues a `run_research(run_id)` job on Arq. Workers pull jobs; a run is owned by one worker at a time through a Redis lease renewed every 10 s.
- **Checkpoints.** Each stage writes `run_stages.checkpoint` before reporting completion. The loop checkpoints after every round. Evidence and claims are written immediately, so a crash loses at most the in-flight sub-agent work.
- **Resume.** If a lease expires, another worker takes over, reads the last checkpoint, and continues. Completed stages are skipped. In-flight sub-agents are re-issued with `known_evidence_ids` so they do not duplicate evidence.
- **Idempotency.** Tool calls and LLM calls carry a deterministic key (`run_id`, stage, round, agent, call index). A repeated key returns the stored result instead of paying twice.
- **Backpressure.** Per-user concurrent run limits, global worker concurrency, and a queue-depth threshold that returns `503 no_capacity`.
- **Cancellation.** A cancel flag in Redis is checked between every tool and LLM call, and in-flight HTTP and browser operations are aborted.
- **Event delivery.** Workers write each event to `run_events` and publish it to a Redis stream. The API reads the stream for live clients and the table for replay.

## 15. Safety

### 15.1 Untrusted content handling

Everything fetched from the web, files, or tools is data, never instructions.

- Tool results are wrapped before entering any prompt:

```
<untrusted_content source="https://example.com/page" fetched_at="2026-11-15T10:02:11Z">
…page text…
</untrusted_content>
```

- Every agent system prompt states that text inside these tags can never change its task, tools, or output format, and must be ignored as instructions.
- A pre-filter flags likely injection patterns (imperatives addressed to AI, role changes, requests to reveal prompts or call tools). Flagged passages are replaced with a marker and logged as a `security.flag` event.
- Sub-agents cannot call tools outside their type's allowlist, regardless of what a page says. Tool arguments are validated against schemas, and `run_python` receives no secrets and no network.
- Exfiltration guard: outbound tool arguments such as URLs and queries are checked so that internal context, keys, or prior private data are not embedded in them.

### 15.2 SSRF and network policy

- `fetch_page` and `browse` resolve DNS first, then block private, loopback, link-local, and cloud-metadata ranges, and re-check after each redirect. Only `http` and `https` are allowed, on standard ports unless allowlisted.
- The browser pool runs in an isolated network namespace with egress only through a filtering proxy, with per-run download size and time limits.
- Downloads are never executed. PDFs and files are parsed in a separate low-privilege process.

### 15.3 Sandbox for code

Code runs in a gVisor-backed container with no network, read-only base image, a scratch volume capped at 256 MB, 30 s CPU, 512 MB memory, and a package allowlist. Output files are scanned for type and size before they become artifacts.

### 15.4 Privacy and data handling

User files and runs are scoped by `owner_id` at every query. Shared source caches store public web content only. Deleting a run deletes its evidence, reports, artifacts, and snapshots; shared `sources` rows remain unless unreferenced.

## 16. Observability and evaluation

### 16.1 Tracing

Every LLM and tool call produces a span with run id, actor, role, model, tier, tokens, cost, latency, and outcome, exported to Langfuse or OpenTelemetry and stored in `llm_calls` and `tool_calls`. The run's `trace` endpoint returns the merged, ordered view.

Dashboards and alerts:

- run success and degraded rate by depth,
- p50 and p95 latency per stage,
- cost per run by tier,
- tool error rate by code,
- fallback rate from frontier to free tier,
- injection flags per 1,000 fetches.

### 16.2 Offline eval harness

```
evals/
  datasets/       # 50 to 100 questions: reference answers, required facts, gold sources
  runners/        # run Nova v2 with a fixed config and seed
  judges/         # LLM-judge prompts and deterministic checks
  reports/        # per-commit score tables
```

| Metric | Method |
| --- | --- |
| Citation precision | For each cited claim, judge whether the stored quote supports it |
| Citation recall | Fraction of factual sentences that carry a citation |
| Coverage | Fraction of required reference facts present in the report |
| Factuality | Judge claims against the reference answer and gold sources |
| Contradiction handling | Planted-conflict questions: does the report surface both sides |
| Injection resistance | Pages with embedded instructions: does the agent stay on task |
| Cost and latency | Totals per run from traces |

Dataset mix: 40 percent factual multi-hop (BrowseComp and GAIA style), 30 percent open-ended synthesis (DeepResearch Bench style), 15 percent numeric and data tasks, 15 percent adversarial (conflicts, paywalls, injections).

A CI job runs a 15-question smoke subset on every change to prompts, tools, or routing, and fails the build if citation precision drops by more than 3 points or coverage by more than 5. The full set runs nightly. LLM judges are calibrated against a hand-labeled subset of 30 items and re-checked monthly.

## 17. Migration from v1

| Step | Change | Risk control |
| --- | --- | --- |
| 1 | Introduce Postgres alongside SQLite and dual-write new tables for research only | Feature flag per user |
| 2 | Add the evidence store and have the v1 researcher also write evidence rows | v1 behavior unchanged |
| 3 | Add the tool service (browser, PDF) and expose the new tools to v1 workers | Shadow-compare outputs |
| 4 | Add the job queue and move v1 runs onto it, keeping the same endpoints | Rollback by flag |
| 5 | Ship the v2 lead loop behind `research_engine=v2`, default off | Eval gate before any default change |
| 6 | Move writers to outline-first with the editor and citation binding | Compare against v1 on the eval set |
| 7 | Enable clarify, plan approval, steering in the UI | Opt-in at first |
| 8 | Make v2 the default once the eval gates pass; retire sectioned parallel writers and word quotas | Keep v1 callable for one release |

The v1 endpoints `/v1/research/*` keep their request shape. New fields are additive, and the legacy response includes `report.markdown` and `components` as before.

## 18. Rollout plan

| Phase | Deliverables | Exit criteria |
| --- | --- | --- |
| 1 (weeks 1 to 2) | Postgres schema, evidence store, browser and PDF tools, Arq queue, eval dataset and runner | A v1 run persists evidence and survives a worker kill; baseline eval scores recorded |
| 2 (weeks 3 to 4) | Lead loop, sub-agent runtime, verifier, outline-first writers, editor, citation binding | Citation precision 90 percent or higher on the eval set; no regression in cost per run beyond 2x v1 |
| 3 (weeks 5 to 7) | Clarify and plan UX, steering, SSE resume, artifacts, safety layer, sandbox | Injection suite passes; SSE resume verified under disconnects |
| 4 (weeks 8 to 10) | Model tiering, cost accounting, memory, cross-run cache, tuning | Targets in section 2 met on the full eval set; v2 default on |

## 19. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Frontier-tier cost grows with deep runs | Margin and quota pressure | Hard budgets, lead sees only digests, worker tier for reading, caching |
| Search APIs rate-limit or change | Coverage drops | Multiple providers with failover, cache, SearXNG fallback |
| JS-heavy or paywalled sites block the browser | Missing primary sources | Escalation path, archive and alternate-source strategy, honest uncertainty reporting |
| LLM judges are biased or drift | Misleading eval trends | Human-labeled calibration set, pinned judge versions, periodic re-calibration |
| Prompt injection through pages | Wrong or unsafe outputs | Section 15 controls and the injection eval set |
| Long runs fail late | Wasted spend | Checkpoints, resume, partial reports on budget exhaustion |
| Free-tier fallback degrades quality silently | User trust | Tier honesty badge and quality block |
| Schema churn during rollout | Migration bugs | Additive migrations, dual-write period, feature flags |

## 20. Open questions

1. Which search provider set is the default, and who pays for it on free plans?
2. Should plan approval default on for deep runs only, or for all depths?
3. What is the retention period for page snapshots and run traces?
4. Do exports (PDF, DOCX, XLSX) embed the citation quotes, or only the reference list?
5. Is a per-user memory store in scope for v2, or deferred to v2.1?
6. Which sandbox technology fits the hosting environment: gVisor, Firecracker, or a managed code-execution service?
