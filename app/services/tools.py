"""Built-in agent tools — a small plugin registry of safe, local functions
exposed to models via OpenAI-style function calling.

Each tool: name, description (sent to model), JSON-schema parameters,
and an async execute(**args) -> str. Register new tools in TOOLS below.
"""

from __future__ import annotations

import ast
import asyncio
import ipaddress
import operator
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from html import unescape
from urllib.parse import unquote, urljoin, urlparse

import httpx


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    execute: Callable[..., Awaitable[str]]
    icon: str = "🔧"

    def openai_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---------- implementations ----------

_SAFE_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
        return _SAFE_OPS[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported expression")


def _check_pow(tree: ast.AST, max_exp: int = 1000) -> None:
    """Reject huge exponents like 9**9**9 that would hang the process."""
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            if isinstance(node.right, ast.Constant) and isinstance(
                node.right.value, (int, float)
            ):
                if abs(node.right.value) > max_exp:
                    raise ValueError(f"exponent too large (>{max_exp})")
            else:
                raise ValueError("non-literal exponents not allowed")


async def _calculator(expression: str) -> str:
    try:
        if len(expression) > 200:
            return "calculation error: expression too long"
        tree = ast.parse(expression, mode="eval")
        _check_pow(tree)
        result = _eval(tree.body)
        if isinstance(result, float) and abs(result) > 1e308:
            return "calculation error: result overflow"
        return f"{expression} = {result}"
    except Exception as e:
        return f"calculation error: {e}"


async def _get_datetime() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.strftime('%A, %B %d, %Y %H:%M:%S')} UTC"


_BLOCKED_HOST_RE = re.compile(
    r"^(localhost|127\.|0\.0\.0\.0|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|169\.254\.|::1|metadata\.google|internal|.*\.local)$",
    re.IGNORECASE,
)


def _ssrf_blocked(url: str) -> bool:
    """True if the URL targets a private/internal address."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return True
    if not host or _BLOCKED_HOST_RE.match(host):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


async def _http_get(url: str, max_chars: int = 4000) -> str:
    if not url.startswith(("http://", "https://")):
        return "error: url must start with http:// or https://"
    if _ssrf_blocked(url):
        return "error: private/internal addresses are not allowed"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as c:
            # Manually follow up to 3 redirects so each hop is re-checked.
            for _hop in range(3):
                r = await c.get(url, headers={"User-Agent": "FreeLLM-Agent/1.0"})
                if r.status_code in (301, 302, 303, 307, 308):
                    url = r.headers.get("location", "")
                    if not url or _ssrf_blocked(urljoin(str(r.url), url)):
                        return "error: redirect to a blocked address"
                    url = urljoin(str(r.url), url)
                    continue
                return f"HTTP {r.status_code}\n{r.text[:max_chars]}"
            return "error: too many redirects"
    except Exception as e:
        return f"fetch error: {e}"


async def _web_search(query: str, max_results: int = 5) -> str:
    """DuckDuckGo lite HTML search — free, no key.

    The instant-answer JSON API only covers a few encyclopedic queries, so we
    scrape the lightweight result page instead: title, snippet and URL per hit.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0"},
            )
        text = r.text
        links = re.findall(
            r"<a[^>]+href=\"([^\"]+)\"[^>]*class='result-link'[^>]*>(.*?)</a>", text
        )
        snippets = re.findall(r"<td class='result-snippet'>(.*?)</td>", text, re.S)

        def _real_url(href: str) -> str:
            # DDG wraps targets as //duckduckgo.com/l/?uddg=<urlencoded>
            m = re.search(r"[?&]uddg=([^&]+)", href)
            return unquote(m.group(1)) if m else href

        def _clean(s: str) -> str:
            return unescape(re.sub(r"<[^>]+>", "", s)).strip()

        parts: list[str] = []
        for i, (href, title) in enumerate(links[:max_results]):
            snip = _clean(snippets[i]) if i < len(snippets) else ""
            entry = f"{i + 1}. {_clean(title)}\n   {_real_url(href)}"
            if snip:
                entry += f"\n   {snip}"
            parts.append(entry)
        return "\n".join(parts) or "No results found."
    except Exception as e:
        return f"search error: {e}"


# ---------- media generation ----------

_MEDIA_DIR = None


def _media_dir() -> Any:
    """``data/media`` under the project root — created lazily."""
    global _MEDIA_DIR
    if _MEDIA_DIR is None:
        from pathlib import Path

        from app.config import get_settings

        p = Path(get_settings().database_url.split("///")[-1]).parent / "media"
        p.mkdir(parents=True, exist_ok=True)
        _MEDIA_DIR = p
    return _MEDIA_DIR


async def _generate_image(prompt: str, width: int = 1024, height: int = 1024) -> str:
    """Free image generation via pollinations.ai — no API key needed.

    The URL generates on first fetch and is CDN-cached afterwards, so we
    return the URL directly instead of downloading the bytes."""
    if not prompt.strip():
        return "error: empty prompt"
    w = max(64, min(int(width or 1024), 1792))
    h = max(64, min(int(height or 1024), 1792))
    from urllib.parse import quote

    url = (
        "https://image.pollinations.ai/prompt/"
        f"{quote(prompt.strip(), safe='')}"
        f"?width={w}&height={h}&model=sana&nologo=true&seed=-1"
    )
    return (
        f"Generated image URL: {url}\n"
        f"Show it to the user with an image_gallery component: "
        f'{{"type": "image_gallery", "data": {{"images": [{{"url": "{url}", "caption": "{prompt[:80]}"}}]}}}}'
    )


_TTS_VOICES = {
    "aria": "en-US-AriaNeural",
    "guy": "en-US-GuyNeural",
    "jenny": "en-US-JennyNeural",
    "sonia": "en-GB-SoniaNeural",
    "prabhat": "en-IN-PrabhatNeural",
    "neerja": "en-IN-NeerjaNeural",
}


async def _generate_audio(text: str, voice: str = "aria") -> str:
    """Free text-to-speech via edge-tts (Microsoft Edge voices, no key).

    Writes an mp3 under data/media served at /media/<file>."""
    text = (text or "").strip()
    if not text:
        return "error: empty text"
    if len(text) > 4000:
        text = text[:4000]
    voice_id = _TTS_VOICES.get((voice or "").lower(), _TTS_VOICES["aria"])
    try:
        import uuid as _uuid

        import edge_tts

        fname = f"{_uuid.uuid4().hex}.mp3"
        out = _media_dir() / fname
        await edge_tts.Communicate(text, voice_id).save(str(out))
        url = f"/media/{fname}"
        return (
            f"Generated audio URL: {url} (voice {voice_id})\n"
            f'Play it with an audio component: {{"type": "audio", "data": {{"url": "{url}", "title": "{text[:60]}"}}}}'
        )
    except ImportError:
        return "error: edge-tts not installed (pip install edge-tts)"
    except Exception as e:
        return f"audio generation error: {e}"


# ---------- registry ----------

TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            name="calculator",
            icon="🧮",
            description="Evaluate a math expression. Use for any arithmetic or numeric calculation.",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "e.g. '(3+4)*2'"}
                },
                "required": ["expression"],
            },
            execute=_calculator,
        ),
        Tool(
            name="get_datetime",
            icon="🕐",
            description="Get the current date and time in UTC.",
            parameters={"type": "object", "properties": {}},
            execute=_get_datetime,
        ),
        Tool(
            name="web_search",
            icon="🌐",
            description="Search the web and return ranked results with titles, URLs and snippets (DuckDuckGo, no key needed).",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "search query"},
                    "max_results": {"type": "integer", "default": 3},
                },
                "required": ["query"],
            },
            execute=_web_search,
        ),
        Tool(
            name="http_get",
            icon="📡",
            description="Fetch a URL over HTTP GET and return status + body (truncated). Use for public APIs and docs.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                    "max_chars": {"type": "integer", "default": 4000},
                },
                "required": ["url"],
            },
            execute=_http_get,
        ),
        Tool(
            name="generate_image",
            icon="🎨",
            description=(
                "Generate an image from a text prompt (free, no key needed). "
                "Returns a URL — show it with an image_gallery component."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "detailed image description"},
                    "width": {"type": "integer", "default": 1024},
                    "height": {"type": "integer", "default": 1024},
                },
                "required": ["prompt"],
            },
            execute=_generate_image,
        ),
        Tool(
            name="generate_audio",
            icon="🔊",
            description=(
                "Turn text into spoken audio (free TTS). Returns an audio "
                "file URL — play it with an audio component."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "text to speak (max ~4000 chars)"},
                    "voice": {
                        "type": "string",
                        "description": "one of: aria, guy, jenny, sonia, prabhat, neerja",
                        "default": "aria",
                    },
                },
                "required": ["text"],
            },
            execute=_generate_audio,
        ),
    ]
}


def list_tools() -> list[dict[str, Any]]:
    return [
        {"name": t.name, "icon": t.icon, "description": t.description}
        for t in TOOLS.values()
    ]


def specs_for(names: list[str] | None) -> list[dict[str, Any]]:
    """OpenAI `tools` array for the requested names ('*'/None => all)."""
    if not names:
        return []
    if names == ["*"]:
        names = list(TOOLS)
    return [TOOLS[n].openai_spec() for n in names if n in TOOLS]


async def run_tool_loop(
    gateway: Any,
    session: Any,
    payload: dict[str, Any],
    names: Any,
    emit: Callable[[dict[str, Any]], None] | None = None,
    max_rounds: int = 5,
    call_timeout: float | None = None,
    tool_output_hook: Callable[[str, str], None] | None = None,
    tool_policy: bool = False,
) -> dict[str, Any]:
    """Server-side tool-call loop over the built-in tools.

    Sends ``payload`` (non-streaming) through the gateway with the requested
    built-in tool specs; executes any tool_calls locally and re-calls until the
    model answers directly (or rounds are exhausted). ``emit`` gets activity
    dicts for each executed call. ``call_timeout`` bounds each provider call.
    Returns the final completion dict. ``tool_policy`` prepends a usage policy
    to the system prompt so the model answers directly when it can instead of
    calling tools speculatively (used for plain chat; researchers/agents keep
    their own prompts).
    """
    import json as _json

    emit = emit or (lambda ev: None)
    if names is True or names in (None, [], "*"):
        names = ["*"]
    specs = specs_for(list(names))
    if not specs:
        result, _p, _k, _m = await gateway.chat(session, payload, on_event=emit)
        return result

    body = dict(payload)
    body["stream"] = False
    body["tools"] = specs
    body["tool_choice"] = "auto"
    messages = [dict(m) for m in (body.get("messages") or [])]
    if tool_policy:
        messages = _with_tool_policy(messages)
    body["messages"] = messages

    result: dict[str, Any] = {}
    # Tool-loop rounds (researchers, agents) need longer per-attempt patience
    # than plain chat: free models routinely take 30-90s once tools are in
    # play. Scale the per-attempt timeout with the round budget, capped at 2m.
    attempt_timeout = min(call_timeout, 120.0) if call_timeout else None
    for _round in range(max_rounds):
        # Re-routing happens every round; after the first, keep only
        # failover/retry events so the activity list doesn't repeat
        # "Routing…/Trying…" for the same provider set.
        round_emit = emit if _round == 0 else _routing_once(emit)
        try:
            result, _p, _k, _m = await asyncio.wait_for(
                gateway.chat(session, body, on_event=round_emit,
                             attempt_timeout=attempt_timeout),
                timeout=call_timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"agent timed out after {call_timeout}s")
        msg = ((result.get("choices") or [{}])[0]).get("message", {})
        if not isinstance(msg.get("content"), str):
            # Some providers return content as a list of parts (text chunks);
            # flatten before it is sent back as the assistant message.
            parts = msg.get("content")
            msg["content"] = "".join(
                str(p.get("text") or "") for p in parts if isinstance(p, dict)
            ) if isinstance(parts, list) else (parts or "")
        calls = msg.get("tool_calls") or []
        if not calls:
            calls = _parse_text_tool_calls(msg.get("content") or "")
            if calls:
                # A provider emitted <tool_call> markup as text instead of
                # structured tool_calls — strip the blob so it never reaches
                # the UI, then execute it like a real call.
                msg["content"] = _strip_text_tool_calls(msg["content"])
        if not calls:
            return _sanitize_result(result)
        messages.append({
            "role": "assistant",
            "content": msg.get("content") or "",
            "tool_calls": calls,
        })
        for tc in calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = _json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            emit({"event": "tool", "text": f"🔧 {name}({_brief_args(args)})"})
            out = await run_tool(name, args)
            if tool_output_hook is not None:
                try:
                    tool_output_hook(name, out)
                except Exception:
                    pass
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": out,
            })
    # Rounds exhausted while the model still wanted tools: without a final
    # answer the last completion has no text and the UI shows "(empty
    # response)". Give it one closing call with tools removed so it must
    # answer with whatever it gathered.
    body.pop("tools", None)
    body.pop("tool_choice", None)
    emit({"event": "note", "text": "research limit reached — answering with what's gathered"})
    # Weak models often respond to the closing call by emitting MORE
    # <tool_call> text blobs (which sanitize would strip to "") instead of
    # answering. Execute those blobs like real calls so the model gets
    # actual results/errors, then ask again — bounded to 3 attempts.
    messages.append({
        "role": "user",
        "content": "Tool use is finished. Answer now with components using "
                   "only the gathered data.",
    })
    result: dict[str, Any] = {}
    for _try in range(3):
        result, _p, _k, _m = await asyncio.wait_for(
            gateway.chat(session, body, on_event=_routing_once(emit),
                         attempt_timeout=attempt_timeout),
            timeout=call_timeout,
        )
        msg = ((result.get("choices") or [{}])[0]).get("message", {})
        content = msg.get("content")
        text = content if isinstance(content, str) else ""
        calls = _parse_text_tool_calls(text) if "<tool_call>" in text else []
        if not calls:
            if text.strip():
                break
            messages.append({
                "role": "user",
                "content": "Stop calling tools. Reply with the final answer text only.",
            })
            continue
        # Blobs instead of an answer: run them and feed results back.
        messages.append({
            "role": "assistant",
            "content": _strip_text_tool_calls(text),
            "tool_calls": calls,
        })
        for tc in calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = _json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            emit({"event": "tool", "text": f"🔧 {name}({_brief_args(args)})"})
            out = await run_tool(name, args)
            if tool_output_hook is not None:
                try:
                    tool_output_hook(name, out)
                except Exception:
                    pass
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": out,
            })
        messages.append({
            "role": "user",
            "content": "Now reply with the final answer only — no tool calls.",
        })
    return _sanitize_result(result)


def _routing_once(emit: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
    """Drop routing/try/skip spam on repeat calls — keep fail/next/retry."""
    drop = {"routing", "try", "skip"}
    return lambda ev: None if ev.get("event") in drop else emit(ev)


_TEXT_CALL_RE = None


def _text_call_re():
    global _TEXT_CALL_RE
    if _TEXT_CALL_RE is None:
        import re

        _TEXT_CALL_RE = re.compile(
            r"<tool_call>\s*([A-Za-z_]\w*)\s*(.*?)</tool_call>", re.DOTALL
        )
    return _TEXT_CALL_RE


def _parse_text_tool_calls(content: str) -> list[dict[str, Any]]:
    """Convert ``<tool_call>name<arg_key>k</arg_key><arg_value>v</arg_value>…``
    blobs some providers emit inside ``content`` into real tool_calls."""
    import json as _json
    import re
    import uuid as _uuid

    if "<tool_call>" not in content:
        return []
    pair_re = re.compile(
        r"<arg_key>\s*(.*?)\s*</arg_key>\s*<arg_value>\s*(.*?)\s*</arg_value>",
        re.DOTALL,
    )
    calls: list[dict[str, Any]] = []
    for m in _text_call_re().finditer(content):
        name, args_blob = m.group(1), m.group(2)
        args = {k.strip(): v.strip() for k, v in pair_re.findall(args_blob)}
        calls.append({
            "id": f"textcall_{_uuid.uuid4().hex[:12]}",
            "type": "function",
            "function": {"name": name, "arguments": _json.dumps(args)},
        })
    return calls


def _strip_text_tool_calls(content: str) -> str:
    return _text_call_re().sub("", content).strip()


_ARG_TAG_RE = re.compile(r"<arg_(?:key|value)>[^<]*</arg_(?:key|value)>")
_ARG_OPEN_RE = re.compile(r"<arg_(?:key|value)>")
_TOOL_TAIL_RE = re.compile(r"<tool_call>.*", re.DOTALL)


def _sanitize_final_text(text: str) -> str:
    """Last line of defense: never let raw tool markup reach the user.

    Providers sometimes emit ``<tool_call>`` blobs as plain content (especially
    in the final answer after tools were removed). Strip paired and unclosed
    blobs plus stray arg tags, and collapse degenerate repeated-line loops.
    """
    if not isinstance(text, str) or not text:
        return text
    if "<tool_call>" not in text and "<arg_" not in text and "response_type" not in text:
        pass  # still collapse repeats below
    t = _text_call_re().sub("", text)
    t = _TOOL_TAIL_RE.sub("", t)          # unclosed <tool_call>… tail
    t = _ARG_TAG_RE.sub("", t)
    t = _ARG_OPEN_RE.sub("", t)
    # Collapse runs of 4+ identical lines (degenerate model loops).
    if "response_type" not in t:
        lines = t.split("\n")
        out: list[str] = []
        i = 0
        while i < len(lines):
            j = i
            while j + 1 < len(lines) and lines[j + 1] == lines[i] and lines[i].strip():
                j += 1
            run = j - i + 1
            out.append(lines[i])
            if run >= 4:
                out.append(f"_(repeated {run} times)_")
            i = j + 1
        t = "\n".join(out)
    return t.strip()


def _sanitize_result(result: dict[str, Any]) -> dict[str, Any]:
    try:
        msg = ((result.get("choices") or [{}])[0]).get("message", {})
        if isinstance(msg.get("content"), str):
            msg["content"] = _sanitize_final_text(msg["content"])
    except Exception:
        pass
    return result


_TOOL_POLICY = (
    "Tool use policy: answer directly, without calling any tool, whenever you "
    "can — greetings, small talk, opinions, or anything answerable from this "
    "conversation or your own knowledge. Call a tool only when the user needs "
    "information you don't have (current facts, sources), an exact calculation, "
    "or media generation they asked for. Never call tools speculatively."
)


def _with_tool_policy(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Append the tool-usage policy to the first system message (or insert one)."""
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            out = [dict(x) for x in messages]
            content = out[i].get("content")
            text = content if isinstance(content, str) else ""
            out[i] = {**out[i], "content": f"{text}\n\n{_TOOL_POLICY}" if text else _TOOL_POLICY}
            return out
    return [{"role": "system", "content": _TOOL_POLICY}, *messages]


def _brief_args(args: dict[str, Any]) -> str:
    """Compact 'query="x"'-style arg preview for activity events."""
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 40:
            s = s[:40] + "…"
        parts.append(f"{k}={s!r}")
    return ", ".join(parts)


async def run_tool(name: str, args: dict[str, Any]) -> str:
    t = TOOLS.get(name)
    if t is None:
        return f"error: unknown tool '{name}'"
    try:
        return await t.execute(**args)
    except TypeError as e:
        return f"tool arg error: {e}"
    except Exception as e:
        return f"tool error: {e}"


_WANTS_TOOLS_SYSTEM = (
    "Decide whether answering the user's latest message requires calling an "
    "external tool (web search, fetching a URL, exact arithmetic, image/audio "
    "generation). Answer with exactly one word: YES or NO. Greetings, small "
    "talk, opinions, explanations from general knowledge, and follow-ups "
    "answerable from the conversation are NO. Current facts, prices, news, "
    "'search', explicit calculations, and media requests are YES."
)


async def wants_tools(gateway: Any, session: Any, payload: dict[str, Any]) -> bool:
    """Model-based tool gate: one tiny classifier call decides whether the
    latest user message genuinely needs tools. Fail-open (True) on any error
    so a broken gate never disables tool use."""
    msgs = payload.get("messages") or []
    last = next((m for m in reversed(msgs) if m.get("role") == "user"), None)
    if not last:
        return True
    text = last.get("content")
    if isinstance(text, list):
        text = " ".join(str(p.get("text", "")) for p in text if isinstance(p, dict))
    text = (text or "").strip()[:2000]
    if not text:
        return True
    probe = {
        "model": payload.get("model") or "auto",
        "stream": False,
        "temperature": 0,
        "max_tokens": 4,
        "messages": [
            {"role": "system", "content": _WANTS_TOOLS_SYSTEM},
            {"role": "user", "content": text},
        ],
    }
    try:
        result, *_ = await gateway.chat(session, probe, attempt_timeout=25.0)
    except Exception:
        return True
    out = ""
    for c in result.get("choices") or []:
        out += (c.get("message") or {}).get("content") or ""
    out = out.strip().upper()
    return not (out.startswith("NO") or out in {"N", "0", "FALSE"})
