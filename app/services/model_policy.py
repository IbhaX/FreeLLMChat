"""Internal model policy — strong concrete models per role.

Internal pipeline stages (planner, researcher, verifier, synthesizer,
component renderer, expert calls, memory tasks) must not silently land on
the weakest model in the ``auto`` sweep — with local providers enabled,
``auto`` can resolve to a 1.5B model that produces confident fabrication.

Each *role* has an ordered preference list of concrete ``provider/model``
ids. ``auto`` is appended only as the last-resort backstop so a call can
still land somewhere when every preferred provider is cooled or down.

Overrides (comma-separated model ids):
- ``NOVA_MODEL_REASONING`` / ``NOVA_MODEL_GENERAL`` / ``NOVA_MODEL_CODING``
  / ``NOVA_MODEL_FAST`` — per-role preference lists
- ``NOVA_INTERNAL_MODEL`` — one override for all roles
"""

from __future__ import annotations

import os
from typing import Any

_PREFERRED: dict[str, tuple[str, ...]] = {
    # Complex planning, verification, final synthesis.
    "reasoning": (
        "groq/openai/gpt-oss-120b",
        "groq/llama-3.3-70b-versatile",
        "openrouter/openrouter/free",
    ),
    # General internal work (researcher, synthesist, default expert).
    "general": (
        "groq/llama-3.3-70b-versatile",
        "openrouter/openrouter/free",
        "groq/openai/gpt-oss-120b",
    ),
    # Code generation / review / debugging.
    "coding": (
        "groq/qwen/qwen3-32b",
        "groq/openai/gpt-oss-120b",
        "openrouter/openrouter/free",
    ),
    # Cheap formatting / classification / memory tasks.
    "fast": (
        "groq/openai/gpt-oss-20b",
        "gemini/gemini-flash-lite-latest",
        "openrouter/openrouter/free",
    ),
}

_ALIAS_ROLE = {
    "auto:coding": "coding",
    "auto:fast": "fast",
    "auto:reason": "reasoning",
    "auto:reasoning": "reasoning",
    "auto:smart": "reasoning",
}


def is_auto(model: str | None) -> bool:
    return bool(model) and str(model).startswith("auto")


def role_for_alias(model: str | None) -> str:
    return _ALIAS_ROLE.get(str(model or ""), "general")


def preferred_models(role: str) -> list[str]:
    """Ordered concrete model ids for ``role``.

    ``NOVA_MODEL_<ROLE>`` then ``NOVA_INTERNAL_MODEL`` env vars (comma
    separated) take precedence over the built-in preference list.
    """
    env = os.environ.get(f"NOVA_MODEL_{role.upper()}") or os.environ.get(
        "NOVA_INTERNAL_MODEL")
    if env:
        return [m.strip() for m in env.split(",") if m.strip()]
    return list(_PREFERRED.get(role) or _PREFERRED["general"])


def stage_models(requested: str | None, *, role: str = "general") -> list[str]:
    """Ordered model candidates for an internal call.

    - an explicit (non-auto) model choice is honored verbatim;
    - an ``auto*`` alias expands to the matching role's preference list;
    - a bare role name (``general``/``reasoning``/``coding``/``fast``)
      expands the same way;
    - ``auto`` is appended as the last-resort backstop unless the caller
      pinned an explicit model.
    """
    m = str(requested or "")
    if m and not is_auto(m) and m not in _PREFERRED:
        return [m]
    if m in _PREFERRED:
        r = m
    elif is_auto(m):
        r = _ALIAS_ROLE.get(m) or role  # bare "auto" -> caller's role
    else:
        r = role
    return preferred_models(r) + ["auto"]


async def chat_resp(gateway: Any, session: Any,
                    payload: dict[str, Any]) -> dict[str, Any]:
    """``Gateway.chat`` returns ``(resp, provider, key, model)`` — internal
    helpers only need the completion dict."""
    resp, _p, _k, _m = await gateway.chat(session, payload)
    return resp


class SSEDeltaParser:
    """Incremental OpenAI-style SSE → content-delta decoder.

    Chunks may split a multibyte UTF-8 character, an SSE line, or one JSON
    event across many chunks; bytes are decoded incrementally and a line is
    only parsed once complete. Only ``choices[0].delta.content`` is yielded —
    reasoning fields are never forwarded.
    """

    _MAX_PENDING = 65536

    def __init__(self) -> None:
        import codecs
        self._dec = codecs.getincrementaldecoder("utf-8")("replace")
        self._line = ""
        self._pending_json = ""

    def feed(self, chunk: bytes) -> list[str]:
        """Consume raw bytes; return content pieces completed by this chunk."""
        return self._lines(self._dec.decode(chunk))

    def flush(self) -> list[str]:
        """Drain the decoder and any trailing (unterminated) line at EOF."""
        out = self._lines(self._dec.decode(b"", final=True))
        if self._line:
            out.extend(self._consume(self._line))
            self._line = ""
        return out

    def _lines(self, text: str) -> list[str]:
        out: list[str] = []
        self._line += text
        while "\n" in self._line:
            raw, self._line = self._line.split("\n", 1)
            out.extend(self._consume(raw))
        return out

    def _consume(self, raw: str) -> list[str]:
        import json as _json
        line = raw.strip()
        if not line.startswith("data:"):
            return []
        body = line[5:].strip()
        if body == "[DONE]":
            self._pending_json = ""
            return []
        candidate = self._pending_json + body
        try:
            j = _json.loads(candidate)
        except ValueError:
            # One event split over several data lines: keep accumulating,
            # bounded so garbage cannot grow without limit.
            self._pending_json = (candidate
                                  if len(candidate) <= self._MAX_PENDING else "")
            return []
        self._pending_json = ""
        if not isinstance(j, dict):
            return []
        choices = j.get("choices") or [{}]
        first = choices[0] if isinstance(choices, list) and choices else {}
        delta = first.get("delta") if isinstance(first, dict) else None
        piece = delta.get("content") if isinstance(delta, dict) else None
        return [piece] if isinstance(piece, str) and piece else []


async def _close_stream(stream: Any) -> None:
    """Close an async byte iterator if it supports it (bounded, never raises)."""
    import asyncio
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    try:
        await asyncio.wait_for(aclose(), timeout=2.0)
    except BaseException:
        pass


async def stream_completion(
    gateway: Any, session: Any, payload: dict[str, Any],
    *, candidates: list[str] | None = None,
    requested: str | None = None, role: str = "general",
    retries: int = 1, emit: Any | None = None,
    on_delta: Any | None = None, label: str = "",
    timeout_s: float = 120.0,
) -> Any:
    """Streaming completion across ordered explicit model candidates.

    ``timeout_s`` is ONE total budget per candidate attempt (open + drain).
    Content deltas go to ``on_delta`` and accumulate into the returned
    ``CallResult.text``; reasoning is never forwarded. Failover happens only
    BEFORE any output; a mid-stream failure returns the partial result
    degraded instead of replaying another provider into the same answer.
    The stream iterator is closed on every exit path. Gateways without
    ``chat_stream`` use buffered ``gateway.chat`` over the same explicit
    candidates. Never raises — returns the last ``CallResult``.
    """
    import asyncio

    from app.services.reliable import CallResult, completion_text

    cands = list(candidates) if candidates else stage_models(
        requested if requested is not None else payload.get("model"),
        role=role)
    emit = emit or (lambda ev: None)
    chat_stream = getattr(gateway, "chat_stream", None)
    last: Any = None

    if chat_stream is None:
        for cand in cands:
            try:
                resp, _p, _k, _m = await asyncio.wait_for(
                    gateway.chat(session, {**payload, "model": cand,
                                           "stream": False}),
                    timeout=timeout_s)
                text = (completion_text(resp) or "").strip()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last = CallResult(text="", degraded=True,
                                  error=str(e)[:200], model=cand)
                continue
            if text:
                if on_delta is not None:
                    try:
                        on_delta(text)
                    except Exception:
                        pass
                return CallResult(text=text, model=cand, attempts=1)
            last = CallResult(text="", degraded=True,
                              error="empty completion", model=cand)
        return last or CallResult(text="", degraded=True, error="no candidates")

    for cand in cands:
        p = {**payload, "model": cand, "stream": True}
        state: dict[str, Any] = {"acc": "", "got": False, "model": cand}
        stream: Any = None

        async def _attempt() -> None:
            nonlocal stream
            stream, _prov, _key, _model = await chat_stream(
                session, p, on_event=emit)
            if isinstance(_model, str):
                state["model"] = _model
            parser = SSEDeltaParser()
            async for chunk in stream:
                pieces = parser.feed(
                    chunk if isinstance(chunk, bytes) else str(chunk).encode())
                for piece in pieces:
                    state["got"] = True
                    state["acc"] += piece
                    if on_delta is not None:
                        try:
                            on_delta(piece)
                        except Exception:
                            pass
            for piece in parser.flush():
                state["got"] = True
                state["acc"] += piece
                if on_delta is not None:
                    try:
                        on_delta(piece)
                    except Exception:
                        pass

        try:
            await asyncio.wait_for(_attempt(), timeout=timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            acc = str(state["acc"]).strip()
            if state["got"] and acc:
                return CallResult(
                    text=acc, model=str(state["model"]), attempts=1,
                    degraded=True, error=f"stream interrupted: {e}"[:200])
            emit({"event": "note",
                  "text": f"{label or 'stream'} failed ({cand}): {str(e)[:120]}"})
            last = CallResult(text="", degraded=True,
                              error=str(e)[:200], model=cand)
            continue
        finally:
            await _close_stream(stream)

        text = str(state["acc"]).strip()
        if text:
            return CallResult(text=text, model=str(state["model"]), attempts=1)
        last = CallResult(text="", degraded=True,
                          error="empty stream", model=str(state["model"]))
    return last or CallResult(text="", degraded=True, error="no candidates")


async def internal_chat(
    gateway: Any, session: Any, payload: dict[str, Any],
    *, requested: str | None = None, role: str = "general",
    retries: int = 1, emit: Any | None = None, label: str = "",
) -> Any:
    """Reliable completion across the ordered candidate list.

    Strong concrete models are tried first (each with ``retries`` bounded
    retries); ``auto`` is appended as the last-resort backstop unless an
    explicit model was pinned. Returns the last ``CallResult`` — check
    ``.ok`` — never raises."""
    from app.services.reliable import reliable_call

    last = None
    for cand in stage_models(requested, role=role):
        p = {**payload, "model": cand}
        last = await reliable_call(
            lambda p=p: chat_resp(gateway, session, p),
            retries=retries, emit=emit, label=label or f"chat:{cand}")
        if last.ok:
            return last
    return last
