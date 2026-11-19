"""Canonical activity-event schema for the agent activity log (redesign).

Every step the agent takes is described by one normalized shape, whether it
came from the research pipeline, the tool loop, the orchestrator, group
chat, or the v2 run ledger::

    {
      "kind":   "think|plan|search|read|write|run|decide|wait|error|result|note",
      "status": "queued|running|done|failed|skipped|waiting",
      "title":  "one plain-language line",
      "detail": "optional longer text / raw payload",
      "actor":  "who emitted it (planner|researcher|tool:web_search|…)",
      "parent": "stable group key this step nests under, or null",
      "group":  true when the event itself opens a group container,
      "duration_ms": null or number,
      "level":  "normal | verbose",      # verbose hidden in high-level view
      "ts":     ISO-8601,
      "meta":   {"stage": "…", "tool": "…", "args": "…", ...},
    }

``normalize(ev)`` maps the loose emit dicts the pipelines produce today
onto this schema; ``enrich(ev)`` merges the normalized fields back into
the event dict so SSE consumers get structure for free while legacy
``text`` fields remain.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Optional

Emit = Callable[[dict[str, Any]], None]

KINDS = (
    "think", "plan", "search", "read", "write", "run",
    "decide", "wait", "error", "result", "usage", "note",
)
STATUSES = ("queued", "running", "done", "failed", "skipped", "waiting",
            "cancelled")

# Per-kind presentation metadata (icon + color key shared with the UI).
KIND_META: dict[str, dict[str, str]] = {
    "think":  {"icon": "◐", "color": "violet", "label": "Thinking"},
    "plan":   {"icon": "≡", "color": "blue",   "label": "Planning"},
    "search": {"icon": "⌕", "color": "cyan",   "label": "Searching"},
    "read":   {"icon": "≣", "color": "teal",   "label": "Reading"},
    "write":  {"icon": "✎", "color": "amber",  "label": "Writing"},
    "run":    {"icon": "▶", "color": "green",  "label": "Running"},
    "decide": {"icon": "↯", "color": "indigo", "label": "Deciding"},
    "wait":   {"icon": "⏸", "color": "gray",   "label": "Waiting"},
    "error":  {"icon": "✕", "color": "red",    "label": "Error"},
    "result": {"icon": "✓", "color": "green",  "label": "Result"},
    "usage":  {"icon": "◈", "color": "gold",   "label": "Model usage"},
    "note":   {"icon": "·", "color": "gray",   "label": "Note"},
}

# Stage name (research pipeline / run state machine) → step kind.
_STAGE_KIND = {
    "route": "decide", "clarifying": "wait", "clarify": "wait",
    "plan": "plan", "planning": "plan", "awaiting_approval": "wait",
    "research": "search", "research_done": "result", "researching": "search",
    "gap": "think", "verify": "run", "verifying": "run",
    "outline": "plan", "outlining": "plan",
    "synthesize": "write", "writing": "write", "editing": "write",
    "building_artifacts": "run", "done": "result", "state": "plan",
    "synth": "write",
}

# Tool name → (kind, verb phrase).
_TOOL_KIND = {
    "web_search": ("search", "Searching the web"),
    "search": ("search", "Searching the web"),
    "exa_search": ("search", "Searching with Exa"),
    "academic_search": ("search", "Searching academic papers"),
    "http_get": ("read", "Fetching a page"),
    "fetch_page": ("read", "Fetching a page"),
    "browse": ("read", "Browsing a page"),
    "read_pdf": ("read", "Reading a PDF"),
    "read_file": ("read", "Reading a file"),
    "calculator": ("run", "Calculating"),
    "run_python": ("run", "Running Python"),
    "get_datetime": ("read", "Checking the time"),
    "generate_image": ("write", "Generating an image"),
    "generate_audio": ("write", "Generating audio"),
}

_ERR_TEXT = re.compile(
    r"^(⚠|error|failed|research step failed|empty reply|fetch error|"
    r"search error|exceeded|timed out)", re.I)
_WAIT_EVENT = {"try", "retry", "wait", "rate_limited"}
_VERBOSE_EVENT = {"routing", "try", "skip", "next", "fail"}


_KEY_ARGS: tuple[str, ...] = ("query", "q", "url", "path", "expression", "code", "prompt", "text")
_KEY_ARG_RE: tuple[re.Pattern[str], ...] = tuple(
    re.compile(r"""(?:^|[,{\s])"?""" + k + r""""?\s*[=:]\s*(['"])(.*?)\1""") for k in _KEY_ARGS
)


def pretty_args(targs: str) -> str:
    """``"max_results='5', query='x'"`` → ``"x"`` — the argument a human cares about."""
    for rx in _KEY_ARG_RE:
        m = rx.search(targs or "")
        if m:
            return m.group(2)
    return targs or ""


def _clip(text: Any, n: int = 160) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _tool_fields(ev: dict[str, Any]) -> tuple[str, str, str]:
    """Extract (tool_name, args_preview) from a tool event."""
    name = str(ev.get("tool") or "")
    args = str(ev.get("args") or "")
    text = str(ev.get("text") or "")
    if not name:
        m = re.match(r"^[🔧🛠]\s*([A-Za-z_][\w]*)", text)
        if m:
            name = m.group(1)
            inner = re.search(r"\((.*)\)\s*$", text)
            args = args or (inner.group(1) if inner else "")
    return name, _clip(args, 120)


def normalize(
    ev: dict[str, Any],
    *,
    ts: datetime | None = None,
) -> dict[str, Any]:
    """Map a raw emit dict onto the canonical activity-event shape."""
    event = str(ev.get("event") or ev.get("type") or "note")
    stage = str(ev.get("stage") or "")
    actor = str(ev.get("actor") or ev.get("agent") or "")
    text = str(ev.get("text") or ev.get("detail") or "")
    status_in = str(ev.get("status") or "").lower()

    kind = "note"
    status = "done"
    title = _clip(text or event)
    group = False
    level = "normal"
    meta: dict[str, Any] = {}
    duration_ms = ev.get("duration_ms")

    if stage:
        meta["stage"] = stage
    sub = ev.get("sub")
    parent = ev.get("parent")
    if sub:
        parent = f"{stage or 'research'}:{sub}"

    if event == "stage" or stage and event in ("stage", "state"):
        kind = _STAGE_KIND.get(stage or event, "note")
        status = "done" if stage in ("research_done", "done") else "running"
        group = True
        parent = None
        if ev.get("skipped"):
            status = "skipped"
    elif event == "state":
        kind = "plan"
        status = "running"
        title = _clip(text or f"state → {ev.get('state') or ''}")
        group = True
    elif event == "tool":
        tname, targs = _tool_fields(ev)
        kind, verb = _TOOL_KIND.get(tname, ("run", f"Running {tname or 'tool'}"))
        title = f"{verb}: {_clip(pretty_args(targs), 90)}" if targs else verb
        ok = ev.get("ok")
        out_err = _ERR_TEXT.match(str(ev.get("result") or ""))
        status = "failed" if ok is False or out_err else "done"
        if ev.get("empty") and status == "done":
            status = "skipped"
        actor = actor or f"tool:{tname}"
        meta.update({"tool": tname, "args": targs})
        if ev.get("result"):
            meta["result_preview"] = _clip(ev.get("result"), 400)
        if ev.get("duration_ms") is not None:
            duration_ms = ev.get("duration_ms")
    elif event == "step_start":
        kind, status = "run", "running"
        title = _clip(text or f"{actor} started")
        group = True
        parent = None
    elif event == "step_done":
        kind, status = "run", "done"
        title = _clip(text or f"{actor} finished")
    elif event == "step_fail":
        kind, status = "error", "failed"
        title = _clip(text or f"{actor} failed")
    elif event == "step_queued":
        kind, status = "run", "queued"
        title = _clip(text or f"{actor or 'step'} queued")
        group = True
    elif event == "tool_start":
        tname, targs = _tool_fields(ev)
        kind, verb = _TOOL_KIND.get(tname, ("run", f"Running {tname or 'tool'}"))
        status = "running"
        title = f"{verb}: {_clip(pretty_args(targs), 90)}" if targs else verb
        actor = actor or f"tool:{tname}"
        meta.update({"tool": tname, "args": targs})
    elif event == "handoff":
        kind, status = "decide", "done"
        title = _clip(text or "Handoff")
        for f in ("from_task", "to_task", "target"):
            if ev.get(f):
                meta[f] = ev.get(f)
    elif event == "finding":
        kind, status = "result", "done"
        title = _clip(text or ev.get("claim") or "Finding")
        for f in ("result_id", "evidence_class", "sources", "claim",
                  "sub_question"):
            if ev.get(f) is not None:
                meta[f] = ev.get(f)
    elif event == "source_discovered":
        kind, status = "search", "done"
        src = ev.get("source") if isinstance(ev.get("source"), dict) else ev
        title = _clip(src.get("title") or src.get("url") or "Source found")
        for f in ("id", "url", "snippet", "title"):
            if src.get(f):
                meta[f] = src.get(f)
    elif event == "answer_delta":
        kind, status, level = "write", "running", "verbose"
        title = "Drafting answer…"
    elif event == "answer_section":
        kind, status = "write", "done"
        title = _clip(ev.get("title") or "Section drafted")
        for f in ("section_id", "index"):
            if ev.get(f) is not None:
                meta[f] = ev.get(f)
    elif event == "synth":
        kind, status = "write", "running"
        group = True
    elif event == "agent_start":
        kind, status = "run", "running"
        title = _clip(text or f"{actor} is working")
        group = True
    elif event == "agent_message":
        kind, status = "result", "done"
        title = _clip(text or f"{actor} replied")
    elif event == "research_done":
        kind, status = "result", "done"
        title = _clip(text or "Research complete")
    elif event in ("research_answer", "report.ready"):
        kind, status = "result", "done"
        title = _clip(text or "Report ready")
    elif event == "error":
        kind, status = "error", "failed"
        title = _clip(text or "Something failed")
    elif event == "degraded":
        kind, status = "error", "failed"
        title = _clip(text or "Degraded result")
    elif event == "steer":
        kind, status = "decide", "done"
        title = _clip(text or "User steered the run")
    elif event == "usage":
        kind, status = "usage", "done"
        model = str(ev.get("model") or "")
        total = ev.get("tokens_total")
        if total is None:
            total = int(ev.get("tokens_in") or 0) + int(ev.get("tokens_out") or 0)
        title = _clip(text or (f"{model} · {int(total):,} tokens" if model
                               else f"{int(total):,} tokens"))
        for f in ("model", "provider", "key_label", "tokens_in", "tokens_out",
                  "tokens_total", "cost_usd", "currency"):
            if ev.get(f) is not None:
                meta[f] = ev.get(f)
        meta["tokens_total"] = int(total)
        actor = actor or "model"
    elif event == "plan":
        kind, status = "plan", "done"
        title = _clip(text or "Plan ready")
    elif event == "budget":
        kind, status, level = "note", "done", "verbose"
    elif event in _WAIT_EVENT:
        kind, status, level = "wait", "waiting", "verbose"
    elif event in _VERBOSE_EVENT:
        kind, status, level = "decide", "done", "verbose"
        if event == "fail":
            kind, status = "error", "failed"
        if event == "skip":
            status = "skipped"
    elif _ERR_TEXT.match(text):
        kind, status = "error", "failed"
    else:
        kind, status = "note", "done"

    # Explicit status overrides from structured emitters (execution trace).
    if status_in in ("fail", "failed", "error") and status != "failed":
        status = "failed"
        if kind in ("note", "decide"):
            kind = "error"
    elif status_in == "skip" and status != "skipped":
        status = "skipped"

    raw_detail = ev.get("detail")
    detail = (json.dumps(raw_detail, ensure_ascii=False, indent=2, default=str)
              if isinstance(raw_detail, (dict, list)) else str(raw_detail or ""))
    if not detail and len(text) > 170:
        detail = text[:2000]
    return {
        "kind": kind,
        "status": status,
        "title": title or KIND_META[kind]["label"],
        "detail": detail[:2000],
        "actor": actor,
        "parent": parent,
        "group": group,
        "duration_ms": duration_ms,
        "level": level,
        "ts": (ts or datetime.now(UTC)).isoformat(),
        "meta": meta,
    }


# Fields a producer may set explicitly — enrich must not clobber them with
# normalized guesses (roleplay messages reuse ``kind`` for dialogue kinds,
# and structured emitters pin their own status/title/parent/meta).
_PRESERVE = ("kind", "status", "title", "parent", "meta", "actor",
             "detail", "group", "level", "duration_ms")

# Activity-ish markers: only dicts that look like pipeline events get
# normalized. Roleplay beat/char frames and arbitrary payloads pass through.
_ACTIVITY_EVENTS = frozenset({
    "stage", "state", "tool", "tool_start", "step_start", "step_done",
    "step_fail", "step_queued", "synth", "agent_start", "agent_message",
    "research_done", "research_answer", "report.ready", "error", "degraded",
    "steer", "usage", "plan", "budget", "try", "retry", "wait", "rate_limited",
    "routing", "next", "skip", "fail", "handoff", "finding",
    "source_discovered", "answer_delta", "answer_section",
})

_FINDING_KINDS = {"finding", "source_discovered"}
_ANSWER_KINDS = {"answer_delta", "answer_section"}


_STATUS_ALIAS: dict[str, str] = {
    "ok": "done", "fail": "failed", "error": "failed",
    "skip": "skipped", "cancel": "cancelled",
}


def _channel_for(ev: dict[str, Any]) -> str:
    """Public interaction channel: finding | answer | activity.

    The wire ``event`` decides before the presentation ``kind``: enriched
    answer/finding events carry kind='write'/'note', which must not hide them.
    """
    ch = str(ev.get("channel") or "").lower()
    if ch in ("finding", "answer", "activity"):
        return ch
    event = str(ev.get("event") or ev.get("type") or "")
    if event in _FINDING_KINDS:
        return "finding"
    if event in _ANSWER_KINDS:
        return "answer"
    kind = str(ev.get("kind") or "")
    if kind in _FINDING_KINDS:
        return "finding"
    if kind in _ANSWER_KINDS:
        return "answer"
    return "activity"


# Frame events produced by roleplay/chat SSE plumbing — never normalized
# into activity steps (they carry their own UI semantics).
_NON_ACTIVITY_EVENTS = frozenset({
    "beat", "char_start", "char_delta", "char_end", "char_skip",
    "turn_done", "turn_queue", "sum_delta", "sum_end", "presence",
    "research_answer", "agent_message", "replace_content",
})


def enrich(ev: dict[str, Any]) -> dict[str, Any]:
    """Merge normalized activity fields into ``ev`` and return it.

    SSE consumers get ``kind``/``status``/``title``/``parent`` alongside the
    legacy ``event``/``text`` keys; nothing is removed. Explicit producer
    fields (kind/status/title/parent/meta/…) win over normalized guesses,
    and non-activity payloads (roleplay beats, message dicts) are untouched.
    """
    import contextlib
    event = str(ev.get("event") or ev.get("type") or "")
    looks_activity = (
        (event and event not in _NON_ACTIVITY_EVENTS)
        or bool(ev.get("stage"))
        or bool(ev.get("tool"))
        or ("kind" in ev and "status" in ev and "title" in ev)
    )
    if not looks_activity:
        return ev
    with contextlib.suppress(Exception):
        norm = normalize(ev)
        for k, v in norm.items():
            if k == "status":
                # canonical state only — never keep a raw legacy alias
                raw = str(ev.get("status") or "").lower()
                if raw in STATUSES:
                    continue
                ev[k] = _STATUS_ALIAS.get(raw, v)
                continue
            if k in _PRESERVE and ev.get(k) not in (None, ""):
                continue
            ev[k] = v
        if str(ev.get("status") or "").lower() in _STATUS_ALIAS:
            ev["status"] = _STATUS_ALIAS[str(ev["status"]).lower()]
    return ev


class InteractionEmitter:
    """Typed emit wrapper giving every pipeline event a stable identity.

    Each call copies the producer dict (never mutates it), stamps ``run_id``
    (one uuid per emitter), a monotonically increasing ``seq``,
    ``event_id = f"{run_id}:{seq}"`` and a public ``channel``
    (activity | finding | answer). Structured activity events are enriched;
    roleplay/beat frames pass through with identity only.
    """

    _interaction_emitter = True

    def __init__(self, inner: Emit, *, run_id: Optional[str] = None) -> None:
        self._inner = inner
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._seq = 0

    def prepare(self, ev: dict[str, Any]) -> dict[str, Any]:
        """Return a stamped, enriched COPY of ``ev`` (producer dict untouched).

        Already-identified events keep their run_id/seq/event_id/channel; only
        missing identity is assigned. ``seq`` never moves backwards for this run.
        """
        out = dict(ev)
        out.setdefault("run_id", self.run_id)
        if out.get("seq") is None:
            self._seq += 1
            out["seq"] = self._seq
        elif out.get("run_id") == self.run_id:
            self._seq = max(self._seq, int(out["seq"]))
        out.setdefault("event_id", f"{out['run_id']}:{out['seq']}")
        out.setdefault("channel", _channel_for(out))
        enrich(out)
        return out

    def __call__(self, ev: dict[str, Any]) -> None:
        self._inner(self.prepare(ev))


def to_agent_event_fields(ev: dict[str, Any]) -> dict[str, Any]:
    """Extract the ledger columns for a normalized event.

    ``payload`` stores the *enriched* dict (raw keys + normalized fields)
    so replay via ``list_events`` is full-fidelity — including ``group``
    and ``parent``, which have no ledger column.
    """
    a = ev if "kind" in ev else normalize(ev)
    payload = {**ev, **{k: a.get(k, ev.get(k)) for k in (
        "kind", "status", "title", "detail", "actor", "parent", "group",
        "duration_ms", "level", "ts", "meta",
        "run_id", "seq", "event_id", "channel", "task_id", "parent_task_id",
        "result_id", "section_id", "from_task", "to_task", "target")
        if a.get(k, ev.get(k)) is not None}}
    # ``a`` is the enriched event when present; identity fields live on ``ev``.
    for k in ("run_id", "seq", "event_id", "channel", "task_id",
              "parent_task_id", "result_id", "section_id", "from_task",
              "to_task", "target"):
        if ev.get(k) is not None:
            payload[k] = ev[k]
    return {
        "stage": str(ev.get("stage") or "")[:32],
        "actor": str(a.get("actor") or ev.get("actor") or "")[:64],
        "event": str(ev.get("event") or ev.get("type") or "")[:32],
        "detail": str(ev.get("text") or ev.get("detail") or "")[:8000],
        "status": str(a.get("status") or "done")[:16],
        "duration_ms": float(a.get("duration_ms") or 0.0),
        "kind": str(a.get("kind") or "note")[:24],
        "title": str(a.get("title") or "")[:512],
        "parent": str(a.get("parent") or "")[:64],
        "level": str(a.get("level") or "normal")[:16],
        "payload": payload,
    }
