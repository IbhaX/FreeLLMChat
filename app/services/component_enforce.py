"""Post-response component enforcement.

Models mostly return plain markdown even when the system prompt demands a
``nova-response`` envelope. This module is the safety net, applied to chat
completions after the gateway returns:

1. **Validate** — does the reply contain a valid envelope with at least one
   component? Trivially short replies (greetings, one-liners) are exempt.
2. **Repair** — ONE automatic repair call ("Rewrite your previous answer
   using the component format. Output only components.") with tools disabled.
3. **Fallback** — if repair still fails, a deterministic markdown→components
   converter wraps headings/bullets/tables/numbers into an envelope so the
   user still gets rendered components instead of raw markdown.

Every outcome is logged as a structured metric line so routed models that
chronically fail components can be identified:

    component_used=true|false repair_used=true|false fallback_used=true|false
    provider=<name> model=<model>
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.response_protocol import (
    RESPONSE_FENCE_TAG,
    is_valid_envelope,
)

log = logging.getLogger("freellm.components")

# Same detection rules as the frontend ResponseClassifier, so backend and
# frontend agree on what counts as a structured reply.
_FENCE_RE = re.compile(r"```nova-response\s*\n([\s\S]*?)\n```")
_FENCE_OPEN_RE = re.compile(r"```nova-response\s*\n([\s\S]*)$")
_JSON_FENCE_RE = re.compile(r"```json\s*\n([\s\S]*?)\n```")
_JSON_FENCE_OPEN_RE = re.compile(r"```json\s*\n([\s\S]*)$")

REPAIR_INSTRUCTION = (
    "Rewrite your previous answer using the component format. "
    "Output only components."
)

# One-line reminder appended as the last message for weaker models.
COMPONENT_REMINDER = (
    f"Reminder: reply with a single ```{RESPONSE_FENCE_TAG} envelope containing "
    "at least one component, unless the answer is one trivial sentence."
)

# Replies at or below this length with no markdown structure are exempt from
# the component requirement (greetings, yes/no answers, one-liners).
_TRIVIAL_MAX_CHARS = 160

_TRIVIAL_STRUCTURE_RE = re.compile(
    r"(^|\n)\s*(#{1,6}\s|[-*+]\s+\S|\d+[.)]\s+\S|\|.+\||\$\$|\`\`\`)",
    re.MULTILINE,
)


def extract_envelope(text: str) -> dict[str, Any] | None:
    """Find and validate a nova-response envelope in model output.

    Mirrors the frontend classifier: fenced ``nova-response`` blocks win,
    plain ``json`` fences are accepted when they hold a valid envelope, and
    unterminated fences are repaired by trimming to the last ``}``.
    """
    if not isinstance(text, str) or not text:
        return None
    for rx in (_FENCE_RE, _JSON_FENCE_RE):
        m = rx.search(text)
        if m:
            env = _try_json(m.group(1))
            if env is not None:
                return env
    for rx in (_FENCE_OPEN_RE, _JSON_FENCE_OPEN_RE):
        m = rx.search(text)
        if not m:
            continue
        inner = m.group(1)
        env = _try_json(inner)
        if env is not None:
            return env
        last = inner.rfind("}")
        if last > 0:
            env = _try_json(inner[: last + 1])
            if env is not None:
                return env
    return None


def _try_json(inner: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(inner.strip())
    except Exception:
        return None
    if not isinstance(obj, dict) or "response_type" not in obj:
        return None
    return obj if is_valid_envelope(obj) else None


def has_renderable_components(text: str) -> bool:
    """True when the reply already carries a valid envelope with components."""
    env = extract_envelope(text)
    if env is None:
        return False
    if env.get("visualization"):
        return True
    comps = env.get("components")
    return isinstance(comps, list) and any(
        isinstance(c, dict) and c.get("type") for c in comps
    )


def is_trivial(text: str) -> bool:
    """True for replies exempt from the component requirement."""
    if not isinstance(text, str):
        return True
    t = text.strip()
    if not t:
        return True
    if len(t) <= _TRIVIAL_MAX_CHARS and not _TRIVIAL_STRUCTURE_RE.search(t):
        return True
    return False


# ---------------------------------------------------------------------------
# Deterministic markdown -> envelope fallback
# ---------------------------------------------------------------------------

_MD_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")
_MD_SEPARATOR_RE = re.compile(r"^\s*\|[\s|:-]+\|\s*$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_STAT_RE = re.compile(r"^\s*(?:[-*+]\s*)?\*\*(.+?)\*\*\s*[:\-–]\s*(.+?)\s*$")


def _parse_md_table(lines: list[str]) -> dict[str, Any] | None:
    """Parse a markdown table block into {columns, rows}."""
    if len(lines) < 2 or not _MD_SEPARATOR_RE.match(lines[1]):
        return None

    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    columns = cells(lines[0])
    rows = [cells(l) for l in lines[2:] if _MD_TABLE_LINE_RE.match(l)]
    rows = [r for r in rows if any(c for c in r)]
    if not columns or not rows:
        return None
    width = len(columns)
    rows = [r + [""] * (width - len(r)) if len(r) < width else r[:width] for r in rows]
    return {"columns": columns, "rows": rows}


def markdown_to_envelope(text: str) -> str | None:
    """Convert markdown into a nova-response fence, deterministically.

    Mappings: markdown tables -> ``table``, bullet runs -> ``list``,
    numbered runs -> ``instructions``, ``**Label**: value`` lines ->
    ``stat_grid``. Headings title the component that follows them; prose
    stays in ``content``. Returns None when no structure could be extracted
    (caller keeps the original text).
    """
    if not isinstance(text, str) or not text.strip():
        return None
    lines = text.strip().split("\n")
    components: list[dict[str, Any]] = []
    prose: list[str] = []
    lead_parts: list[str] = []
    i = 0
    pending_title: str | None = None

    while i < len(lines):
        line = lines[i]
        hm = _HEADING_RE.match(line)
        if hm:
            if pending_title is None and not components:
                lead_parts.append(hm.group(2).strip())
            else:
                pending_title = hm.group(2).strip()
            i += 1
            continue
        if _MD_TABLE_LINE_RE.match(line) and i + 1 < len(lines) and _MD_SEPARATOR_RE.match(lines[i + 1]):
            block = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and _MD_TABLE_LINE_RE.match(lines[i]):
                block.append(lines[i])
                i += 1
            parsed = _parse_md_table(block)
            if parsed:
                data = {"columns": parsed["columns"], "rows": parsed["rows"]}
                if pending_title:
                    data["title"] = pending_title
                    pending_title = None
                components.append({"type": "table", "data": data})
            continue
        if _BULLET_RE.match(line) or _NUMBERED_RE.match(line):
            ordered = bool(_NUMBERED_RE.match(line))
            items: list[str] = []
            while i < len(lines):
                bm = _BULLET_RE.match(lines[i])
                nm = _NUMBERED_RE.match(lines[i])
                if bm:
                    items.append(bm.group(1).strip())
                elif nm:
                    items.append(nm.group(1).strip())
                else:
                    break
                i += 1
            if items:
                data = {"items": items, "ordered": ordered}
                if pending_title:
                    data["title"] = pending_title
                    pending_title = None
                components.append({"type": "list", "data": data})
            continue
        sm = _STAT_RE.match(line)
        if sm and not line.lstrip().startswith("#"):
            stats: list[dict[str, Any]] = []
            while i < len(lines):
                m2 = _STAT_RE.match(lines[i])
                if not m2:
                    break
                stats.append({"label": m2.group(1).strip(), "value": m2.group(2).strip()})
                i += 1
            if stats:
                components.append({
                    "type": "stat_grid",
                    "data": {"title": pending_title or "Key figures", "stats": stats},
                })
                pending_title = None
            continue
        if line.strip():
            prose.append(line.rstrip())
        i += 1

    lead = "\n".join(lead_parts).strip()
    remaining_prose = list(prose)
    if not lead and remaining_prose:
        lead = remaining_prose.pop(0).strip()
    if not components:
        # A substantial prose-only answer still needs a meaningful visual
        # anchor. Preserve the complete answer and surface its direct lead as
        # a compact takeaway card; never fabricate facts or discard prose.
        if len(text.strip()) > _TRIVIAL_MAX_CHARS:
            lead_text = re.sub(r"\s+", " ", text.strip())
            lead_text = re.split(r"(?<=[.!?])\s+", lead_text, maxsplit=1)[0][:360]
            if lead_text:
                components.append({
                    "type": "callout",
                    "data": {
                        "title": "Key takeaway",
                        "message": lead_text,
                        "variant": "info",
                    },
                })
    if not components:
        return None
    # Remaining prose beyond the lead is kept as markdown content after the
    # components so nothing the model said is lost.
    extra_prose = "\n".join(remaining_prose).strip()
    env = {
        "response_type": "general",
        "content": (lead + ("\n\n" + extra_prose if extra_prose else ""))[:2000],
        "components": components,
    }
    return (
        f"```{RESPONSE_FENCE_TAG}\n"
        + json.dumps(env, ensure_ascii=False, indent=None)
        + "\n```"
    )


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------

def _message_content(result: dict[str, Any]) -> str:
    try:
        msg = ((result.get("choices") or [{}])[0]).get("message", {})
        content = msg.get("content")
        return content if isinstance(content, str) else ""
    except Exception:
        return ""


def _set_message_content(result: dict[str, Any], text: str) -> None:
    try:
        msg = ((result.get("choices") or [{}])[0]).get("message", {})
        if isinstance(msg, dict):
            msg["content"] = text
    except Exception:
        pass


async def enforce_components(
    gateway: Any,
    session: Any,
    payload: dict[str, Any],
    result: dict[str, Any],
    *,
    provider: str = "",
    model: str = "",
    attempt_timeout: float | None = None,
) -> tuple[dict[str, Any], dict[str, bool]]:
    """Ensure a chat completion reply uses components.

    Returns ``(result, metrics)`` where metrics has ``component_used``,
    ``repair_used`` and ``fallback_used`` booleans. Never raises: any failure
    leaves the original result untouched.
    """
    metrics = {"component_used": False, "repair_used": False, "fallback_used": False}
    try:
        text = _message_content(result)
        if not text.strip():
            return result, metrics
        if has_renderable_components(text):
            metrics["component_used"] = True
            return result, metrics
        if is_trivial(text):
            return result, metrics

        # ONE renderer-agent call: dedicated component converter on a cheap
        # model, tools disabled, conversation + original answer + instruction.
        from app.services.renderer import convert_to_components

        history = [
            m for m in (payload or {}).get("messages") or []
            if isinstance(m, dict) and m.get("role") in ("user", "assistant")
        ]
        converted = await convert_to_components(
            gateway,
            session,
            text,
            history=history,
            attempt_timeout=attempt_timeout,
        )
        if converted:
            _set_message_content(result, converted)
            metrics["repair_used"] = True
            metrics["component_used"] = True
            return result, metrics

        # Deterministic markdown -> components fallback.
        fenced = markdown_to_envelope(text)
        if fenced:
            _set_message_content(result, fenced)
            metrics["fallback_used"] = True
            metrics["component_used"] = True
        return result, metrics
    except Exception as exc:  # enforcement must never break chat
        log.warning("component enforcement failed: %s", exc)
        return result, metrics
    finally:
        log.info(
            "component_metrics provider=%s model=%s component_used=%s "
            "repair_used=%s fallback_used=%s",
            provider or "?",
            model or "?",
            metrics["component_used"],
            metrics["repair_used"],
            metrics["fallback_used"],
        )
