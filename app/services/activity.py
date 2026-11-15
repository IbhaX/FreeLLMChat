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
from datetime import UTC, datetime
from typing import Any

KINDS = (
    "think", "plan", "search", "read", "write", "run",
    "decide", "wait", "error", "result", "usage", "note",
)
STATUSES = ("queued", "running", "done", "failed", "skipped", "waiting")

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
        title = f"{verb}: {_clip(targs, 90)}" if targs else verb
        ok = ev.get("ok")
        out_err = _ERR_TEXT.match(str(ev.get("result") or ""))
        status = "failed" if ok is False or out_err else "done"
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


def enrich(ev: dict[str, Any]) -> dict[str, Any]:
    """Merge normalized activity fields into ``ev`` in place and return it.

    SSE consumers get ``kind``/``status``/``title``/``parent`` alongside the
    legacy ``event``/``text`` keys; nothing is removed.
    """
    import contextlib
    with contextlib.suppress(Exception):
        ev.update(normalize(ev))
    return ev


def to_agent_event_fields(ev: dict[str, Any]) -> dict[str, Any]:
    """Extract the ledger columns for a normalized event.

    ``payload`` stores the *enriched* dict (raw keys + normalized fields)
    so replay via ``list_events`` is full-fidelity — including ``group``
    and ``parent``, which have no ledger column.
    """
    a = ev if "kind" in ev else normalize(ev)
    payload = {**ev, **{k: a[k] for k in (
        "kind", "status", "title", "detail", "actor", "parent", "group",
        "duration_ms", "level", "ts", "meta") if a.get(k) is not None}}
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
