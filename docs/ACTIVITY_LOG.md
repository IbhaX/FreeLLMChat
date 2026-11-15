# Agent Activity Log — v2

The activity log tells the user, in real time, exactly what the agent is
doing. It has two halves: a **normalized event contract** (what the
backend emits and persists) and an **`ActivityLog` component** (how the
web UI renders it). Both sides share one schema.

## 1. Event schema

Every agent step is described by one normalized shape. Emitters may
still produce the legacy loose dicts (`{event, text, stage, agent, …}`);
`services/activity.normalize()` maps them onto this contract at the
`agent_log.tap_emit` choke point, and the JS component mirrors the same
mapping client-side for paths that never touch the tap (plain chat,
group chat).

```jsonc
{
  "id":          "s7",                // step id (client-assigned if absent)
  "seq":         12,                  // ledger sequence (replay only)
  "ts":          "2026-02-21T10:04:31.205Z",
  "kind":        "search",            // step type, see table
  "status":      "running",           // see table
  "title":       "Searching the web: nova v2 benchmarks",
  "detail":      "optional longer text, up to ~2000 chars",
  "actor":       "researcher",        // who emitted it
  "parent":      "research:q2",       // group key this step nests under
  "group":       false,               // true → this step opens a container
  "duration_ms": 1284,
  "level":       "normal",            // "verbose" hidden until toggled
  "meta":        {"tool": "web_search", "args": "nova v2 benchmarks",
                  "result_preview": "…", "stage": "research"}
}
```

**kinds** — `think` ◐ · `plan` ≡ · `search` ⌕ · `read` ≣ · `write` ✎ ·
`run` ▶ · `decide` ↯ · `wait` ⏸ · `error` ✕ · `result` ✓ · `usage` ◈ ·
`note` ·

**statuses** — `queued` ○ · `running` (spinning ring) · `done` ✓ ·
`failed` ✕ · `skipped` — · `waiting` ⏸

### Mapping rules (both implementations)

| Input event | kind | status | level |
|---|---|---|---|
| `stage` (stage=route\|plan\|research\|gap\|verify\|synthesize\|…) | stage map | running (`done` for terminal stages) | normal, `group:true` |
| `tool` (tool=web_search\|http_get\|…) | tool map | done / failed when `ok:false` or result starts `error:` | normal |
| `step_start` / `step_done` / `step_fail` | run / run / error | running / done / failed | normal |
| `agent_start` / `agent_message` | run / result | running / done | normal |
| `research_done`, `research_answer`, `report.ready` | result | done | normal |
| `error`, `degraded`, text starting `⚠`/`error`/`failed`… | error | failed | normal |
| `steer` | decide | done | normal |
| `plan` (detail = sub-question list) | plan | done | normal |
| `try`, `retry`, `wait`, `rate_limited` | wait | waiting | verbose |
| `usage` (model, provider, tokens_*, cost_usd, duration_ms) | usage | done | normal |
| `routing`, `next`, `budget` | decide / note | done | verbose |
| `skip` | decide | skipped | verbose |
| `fail` | error | failed | verbose |
| everything else | note | done | normal |

Explicit status override: `status:"fail|failed|error"` forces `failed`
(and upgrades note/decide → error); `"skip"` forces `skipped`.

## 2. Component structure

`app/static/js/render/ActivityLog.js` exposes
`Nova.ActivityLog.create(host, opts)` → `{ingest, finish, fail, count, stats, destroy}`.

```
.alog
├─ .alog-head      state dot · current-step title · total time (once finished) · ⚙
├─ .alog-dash      pinned live-metrics card (never scrolls with the feed)
├─ .alog-tools     kind filter · status filter · text search · verbose toggle
├─ .alog-steps     scrollable feed (max ~340px)
│   └─ .alog-step  [rail+node] [expander ›] [title+elapsed] [detail] [kids]
└─ .alog-jump      "↓ jump to latest" (shown when scrolled up)
```

- **Rail + node**: vertical line with a circular node per step. Node
  border/icon colored by *kind*; glyph + border show *status* (spinning
  ring while running, ✓ green, ✕ red, ⏸ amber, — dashed). Status is
  never color-only — glyph and `aria-label`/`sr-only` text carry it.
- **Groups**: `group:true` events (stage transitions, `step_start`,
  `agent_start`) open a container; following leaf steps nest inside via
  `parent` key or the "currently open running group" heuristic. When a
  new group opens, the previous running group auto-resolves to done.
- **Progressive detail**: one-line title by default; the `›` expander
  reveals the step's *next level* — the plan, queries, claims, tool
  input/output — never timestamps. A `plan` event's detail is also
  surfaced on its enclosing "Planning…" group. Groups stay open while
  running and auto-collapse when done (unless the user toggled them).
- **Tool titles** show the one argument a human cares about
  (`query`, `url`, `path`, …) via `pretty_args` / `prettyArgs`; the raw
  argument string stays in the expander.
- **Live metrics card** (`.alog-dash`): pinned under the header and
  updated on every event + a 500 ms tick — elapsed (pulsing while live),
  steps done/total/failed, tool calls/failed, model calls, total tokens
  (hover: in/out), cumulative cost, latest model · provider (+N others,
  hover lists each ×count), and average latency with in/out totals.
  `usage` events (from `Gateway.chat` / `chat_stream` `_usage_event`)
  feed this card and are demoted to `verbose` rows, so the timeline isn't
  interleaved with per-call cards. Cost appears when `provider.pricing`
  is configured (`input_per_mtok`/`output_per_mtok` + `model_overrides`),
  otherwise `—`. `api.stats()` returns the same aggregates.
- **Live**: running steps show a live `m:ss` timer (500 ms tick); new
  steps animate in; `prefers-reduced-motion` disables animations.
- **Auto-scroll**: follows new steps until the user scrolls up >28px;
  then the jump pill appears.
- **Accessibility**: `role="log"` + `aria-label` on the feed; each step
  has `aria-label` "<Kind>: <title>" and an sr-only status word.
- **Lifecycle dedupe**: `ingest` with an `id` already seen updates the
  step in place (queued → running → done transitions), otherwise a new
  step is appended — safe for retries and replays.

## 3. Where events flow

- **Live SSE** (`/v1/chat/completions`, `/v1/agents/{id}/chat`,
  `/v1/groupchat/stream`, `/v1/research/stream`): each `{"freellm": ev}`
  data-frame is `ingest`ed. Events passing through `agent_log.tap_emit`
  arrive already enriched (`kind/status/title/…` merged into `ev`);
  the component normalizes the rest client-side.
- **v2 run events** (`GET /v1/research2/runs/{id}/events`): SSE with
  `id:` set to the row `seq`; reconnect with `Last-Event-ID: <seq>` to
  replay without duplicates. Payloads already carry normalized fields
  (`_v1_event_to_run_event` → `activity.enrich`).
- **Replay**: `GET /v1/agents/runs/{id}` returns `events[]` via
  `agent_log.list_events` (normalized, `id = "<run>:<seq>"`); feed them
  to `ingest` in order, then `finish()`.

## 4. Ordering, retries, long-running steps

- **Ordering**: SSE frames arrive in emit order; the ledger assigns a
  monotonic `seq` per run (per-run lock serializes the max-seq read).
  Replay delivers `seq`-ordered events; dedupe by `id`/`seq`.
- **Retries**: emitted as their own events (`retry` → waiting, `fail` →
  failed, `next` → decide) rather than mutating a prior step — the
  timeline keeps the history instead of silently rewriting it.
- **Long-running steps**: a `running` step stays running until the next
  group opens, an explicit terminal event arrives, or `finish()` is
  called — the UI ticks its elapsed timer meanwhile. On stream end
  `finish()` resolves all lingering running/waiting steps.
- **Persistence**: `agent_events` stores `kind`, `title`, `parent`,
  `level`, `payload` (raw dict) alongside legacy columns — migration
  `0006_activity_events` + `init_db` column adds.
- **Safety**: activity writes are best-effort and swallow errors
  (unchanged invariant — observability must never break the agent).
"""
