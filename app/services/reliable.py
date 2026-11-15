"""Reliability layer — bounded retries, graceful degradation, last resort.

The mission-critical guarantee: every request terminates in a controlled,
user-readable state. ``reliable_call`` wraps a gateway call with bounded
retries and exponential backoff; ``last_resort_answer`` builds an honest
partial response when everything else failed. Neither fabricates results.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class CallResult:
    """Outcome of a reliable gateway call."""

    text: str
    model: str = ""
    provider: str = ""
    attempts: int = 0
    degraded: bool = False
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and self.error is None


def text_of(content: Any) -> str:
    """Extract text from an OpenAI message content field (str or parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for p in content:
            if isinstance(p, dict) and p.get("type") in ("text", "output_text"):
                out.append(str(p.get("text") or ""))
            elif isinstance(p, str):
                out.append(p)
        return "".join(out)
    return ""


def completion_text(result: dict[str, Any]) -> str:
    """Concatenate text across choices of a chat completion result."""
    out = []
    for c in (result or {}).get("choices") or []:
        out.append(text_of((c.get("message") or {}).get("content")))
    return "".join(out)


async def reliable_call(
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    retries: int = 1,
    backoff_base_s: float = 1.5,
    emit: Callable[[dict[str, Any]], None] | None = None,
    label: str = "call",
) -> CallResult:
    """Run ``call()`` with bounded retries + exponential backoff.

    ``call`` is a zero-arg coroutine returning an OpenAI-style completion
    dict (the caller closes over gateway/session/payload). Retries happen
    only on exceptions or empty completions — a valid answer returns
    immediately. Never raises.
    """
    emit = emit or (lambda ev: None)
    last_err: str | None = None
    for attempt in range(1, retries + 2):
        try:
            result = await call()
            text = completion_text(result)
            if text.strip():
                return CallResult(
                    text=text,
                    model=str(result.get("model") or ""),
                    attempts=attempt,
                    usage=result.get("usage") or {},
                )
            last_err = "empty completion"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_err = str(e)[:200]
        if attempt <= retries:
            wait = backoff_base_s * (2 ** (attempt - 1))
            emit({"event": "retry",
                  "text": f"{label} attempt {attempt} failed "
                          f"({last_err or 'empty'}) — retrying in {wait:.0f}s"})
            await asyncio.sleep(wait)
    emit({"event": "fail",
          "text": f"{label} failed after {retries + 1} attempt(s)"})
    return CallResult(text="", attempts=retries + 1,
                      degraded=True, error=last_err)


_INTERNAL_ERROR_MARKERS: tuple[tuple[str, str], ...] = (
    ("database is locked", "The server was busy saving another request. Please try again."),
    ("sqlalchemy", "A temporary storage error interrupted the request. Please try again."),
    ("sqlite3", "A temporary storage error interrupted the request. Please try again."),
    ("traceback", "An internal error interrupted the request. Please try again."),
)


def user_facing_error(err: object) -> str:
    """Plain-language failure text: never leak SQL, stack traces or
    internals to end users (they are logged server-side instead)."""
    text = str(err or "").strip()
    low = text.lower()
    for marker, friendly in _INTERNAL_ERROR_MARKERS:
        if marker in low:
            return friendly
    return text.splitlines()[0][:200] if text else "Unknown error."


def last_resort_answer(
    question: str,
    *,
    failures: list[str] | None = None,
    partials: list[str] | None = None,
    missing: list[str] | None = None,
) -> str:
    """An honest terminal answer when execution could not complete.

    States what was completed, what failed, and what remains unavailable —
    never fabricates content to satisfy the response guarantee.
    """
    parts = [
        "I wasn't able to produce a complete answer to this request.",
        "",
    ]
    if partials:
        parts.append("**What I could determine:**")
        for p in partials[:5]:
            parts.append(f"- {p.strip()[:400]}")
        parts.append("")
    if failures:
        parts.append("**What went wrong:**")
        for f in dict.fromkeys(user_facing_error(f) for f in failures[:5]):
            parts.append(f"- {f}")
        parts.append("")
    if missing:
        parts.append("**What remains unavailable:**")
        for m in missing[:5]:
            parts.append(f"- {m}")
        parts.append("")
    parts.append(
        "**To retry effectively:** rephrase the question, pick a specific "
        "model from the picker instead of `auto`, or try again shortly — "
        "transient provider limits usually clear within a minute."
    )
    return "\n".join(parts)
