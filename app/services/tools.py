"""Built-in agent tools — a small plugin registry of safe, local functions
exposed to models via OpenAI-style function calling.

Each tool: name, description (sent to model), JSON-schema parameters,
and an async execute(**args) -> str. Register new tools in TOOLS below.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import operator
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from typing import Any
from urllib.parse import unquote, urljoin

import httpx

from app.services.netutil import ssrf_blocked as _ssrf_blocked


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
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ):
        if abs(node.value) > 10**12:
            raise ValueError("numeric literal too large")
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
        if sum(1 for _ in ast.walk(tree)) > 64:
            return "calculation error: expression too complex"
        result = _eval(tree.body)
        if not isinstance(result, (int, float)) or not __import__("math").isfinite(result):
            return "calculation error: non-finite result"
        if abs(result) > 1e100:
            return "calculation error: result overflow"
        return f"{expression} = {result}"
    except Exception as e:
        return f"calculation error: {e}"


async def _get_datetime() -> str:
    now = datetime.now(UTC)
    return f"{now.strftime('%A, %B %d, %Y %H:%M:%S')} UTC"


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
        return f"fetch error: {type(e).__name__}: {e}"


_DDG_DEAD = False  # set once DDG times out/errors — skip it for the process


async def _ddg_search_text(query: str, max_results: int) -> str:
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.get(
            "https://lite.duckduckgo.com/lite/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
        )
    if r.status_code != 200:
        return f"search error: duckduckgo HTTP {r.status_code} (rate-limited?)"
    text = r.text
    if "anomaly" in text[:2000].lower():
        return "search error: duckduckgo rate-limited (anomaly check page)"
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


def _bing_real_url(href: str) -> str:
    """Bing wraps targets as bing.com/ck/a?…u=a1<base64url(url)> — decode."""
    import base64

    m = re.search(r"[?&]u=a1([^&]+)", href)
    if not m:
        return href
    try:
        return base64.urlsafe_b64decode(m.group(1) + "===").decode("utf-8", "replace")
    except Exception:
        return href


async def _bing_search_text(query: str, max_results: int) -> str:
    """Bing HTML scrape — reachable where duckduckgo is blocked, still keyless."""
    from bs4 import BeautifulSoup

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
        r = await c.get(
            "https://www.bing.com/search",
            params={"q": query, "count": max_results},
            headers={"User-Agent": "Mozilla/5.0"},
        )
    if r.status_code != 200:
        return f"search error: bing HTTP {r.status_code}"
    soup = BeautifulSoup(r.text, "lxml")
    parts: list[str] = []
    for li in soup.select("li.b_algo"):
        if len(parts) >= max_results:
            break
        a = li.select_one("h2 a")
        if not a:
            continue
        cap = li.select_one(".b_caption p")
        snip = cap.get_text(" ", strip=True) if cap else ""
        title = a.get_text(" ", strip=True)
        entry = f"{len(parts) + 1}. {title}\n   {_bing_real_url(a.get('href', ''))}"
        if snip:
            entry += f"\n   {snip}"
        parts.append(entry)
    return "\n".join(parts) or "No results found."


async def _web_search(query: str, max_results: int = 5) -> str:
    """Keyless web search: DuckDuckGo lite, falling back to Bing.

    Some networks block duckduckgo entirely — remember the failure for the
    process and go straight to Bing instead of paying an 8s timeout per call.
    """
    global _DDG_DEAD
    if not _DDG_DEAD:
        try:
            out = await _ddg_search_text(query, max_results)
            if not out.startswith("search error:"):
                return out
        except Exception:
            pass
        _DDG_DEAD = True
    try:
        return await _bing_search_text(query, max_results)
    except Exception as e:
        return f"search error: {type(e).__name__}: {e}"


# ---------- Exa neural search (keyed, low-cost tier) ----------

_EXA_KEY_CACHE: str | None = None


async def _exa_api_key() -> str:
    """Resolve the Exa key: EXA_API_KEY env first, then Settings
    (``.env`` is read by pydantic Settings, not os.environ), then the
    settings table key ``exa_api_key``. Only non-empty keys are cached —
    an unset key is re-resolved on the next call."""
    global _EXA_KEY_CACHE
    import os
    env = os.environ.get("EXA_API_KEY", "").strip()
    if env:
        return env
    try:
        from app.config import get_settings
        sec = get_settings().exa_api_key
        val = sec.get_secret_value().strip() if sec is not None else ""
        if val:
            return val
    except Exception:
        pass
    if _EXA_KEY_CACHE:
        return _EXA_KEY_CACHE
    try:
        from app.db import get_session_factory
        from sqlalchemy import select
        from app.models import Setting
        async with get_session_factory()() as s:
            row = (await s.execute(
                select(Setting).where(Setting.key == "exa_api_key"))
            ).scalar_one_or_none()
            val = (row.value or "").strip() if row else ""
            if val:
                _EXA_KEY_CACHE = val
            return val
    except Exception:
        return ""


async def _exa_search(query: str, num_results: int = 5,
                      contents: str = "none") -> str:
    """Exa neural/semantic web search — higher-quality results than
    keyword scraping. Default ``contents=none`` keeps it on Exa's
    cheapest metadata-only tier; pass ``highlights`` or ``text`` only
    when snippets/page text are actually needed."""
    key = await _exa_api_key()
    if not key:
        return ("exa_search unavailable: no EXA_API_KEY configured. "
                "Use web_search instead.")
    body: dict[str, Any] = {
        "query": query,
        "numResults": max(1, min(int(num_results or 5), 10)),
        "type": "auto",
    }
    if contents in ("highlights", "text"):
        body["contents"] = {contents: {"maxCharacters": 1000}}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://api.exa.ai/search", json=body,
                headers={"x-api-key": key, "Content-Type": "application/json"})
        if r.status_code != 200:
            return f"exa_search error: HTTP {r.status_code} {r.text[:200]}"
        data = r.json()
        parts: list[str] = []
        for i, res in enumerate(data.get("results") or [], 1):
            title = res.get("title") or res.get("url") or "untitled"
            entry = f"{i}. {title}\n   {res.get('url')}"
            if res.get("publishedDate"):
                entry += f"\n   {res['publishedDate'][:10]}"
            for h in (res.get("highlights") or [])[:2]:
                entry += f"\n   {h[:240]}"
            txt = (res.get("text") or "")[:400]
            if txt:
                entry += f"\n   {txt}"
            parts.append(entry)
        return "\n".join(parts) or "No results found."
    except Exception as e:
        return f"exa_search error: {type(e).__name__}: {e}"


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
        f'{{"type": "image_gallery", "data": {{"images": '
        f'[{{"url": "{url}", "caption": "{prompt[:80]}"}}]}}}}'
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
            f'Play it with an audio component: {{"type": "audio", "data": '
            f'{{"url": "{url}", "title": "{text[:60]}"}}}}'
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
            description=(
                "Evaluate a math expression. "
                "Use for any arithmetic or numeric calculation."
            ),
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
            description=(
                "Search the web and return ranked results with titles, URLs "
                "and snippets (DuckDuckGo, no key needed)."
            ),
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
            name="exa_search",
            icon="🔎",
            description=(
                "Exa neural/semantic web search — higher-quality results "
                "than keyword scraping. Requires EXA_API_KEY; cheapest "
                "metadata-only tier by default (no page contents). Use "
                "web_search when no key is configured."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "search query"},
                    "num_results": {"type": "integer", "default": 5},
                    "contents": {
                        "type": "string", "default": "none",
                        "enum": ["none", "highlights", "text"],
                        "description": "none=cheapest; highlights/text cost more"},
                },
                "required": ["query"],
            },
            execute=_exa_search,
        ),
        Tool(
            name="http_get",
            icon="📡",
            description=(
                "Fetch a URL over HTTP GET and return status + body "
                "(truncated). Use for public APIs and docs."
            ),
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


async def _execute_tool_call(tc: dict[str, Any]) -> tuple[str, str, dict[str, Any], str]:
    """Validate, execute and time one tool call without leaking arguments to logs."""
    import json as _json
    import logging
    import time

    logger = logging.getLogger("freellm.agent.tools")
    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
    name = fn.get("name", "") if isinstance(fn, dict) else ""
    try:
        args = _json.loads(fn.get("arguments") or "{}")
    except (TypeError, ValueError):
        args = None
    call_id = tc.get("id", "") if isinstance(tc, dict) else ""
    if name not in TOOLS or not isinstance(args, dict):
        return call_id, str(name), {}, "tool error: unknown tool or arguments must be an object"

    spec = TOOLS[name].parameters
    allowed = set(spec.get("properties", {}))
    required = set(spec.get("required", []))
    unexpected = set(args) - allowed
    missing = required - set(args)
    if unexpected or missing:
        details = []
        if missing:
            details.append("missing: " + ", ".join(sorted(missing)))
        if unexpected:
            details.append("unexpected: " + ", ".join(sorted(unexpected)))
        return call_id, name, args, "tool error: " + "; ".join(details)

    started = time.perf_counter()
    try:
        output = await asyncio.wait_for(run_tool(name, args), timeout=30)
        output = str(output)[:12000]
        logger.info("tool execution completed", extra={
            "tool_name": name,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "success": not output.lower().startswith(("error:", "tool error:", "fetch error:")),
        })
    except TimeoutError:
        output = "tool error: execution timed out after 30 seconds"
        logger.warning("tool execution timed out", extra={"tool_name": name})
    except Exception as exc:
        output = f"tool error: {type(exc).__name__}: {exc}"
        logger.exception("tool execution failed", extra={"tool_name": name})
    return call_id, name, args, output


async def _timed_tool_call(
    tc: dict[str, Any],
    *, emit: Callable[[dict[str, Any]], None] | None = None,
    allowed: set[str] | None = None,
    parent_task_id: str | None = None,
    actor: str = "",
) -> tuple[str, str, dict[str, Any], str, float]:
    """_execute_tool_call + wall-clock duration for the activity log.

    When ``emit`` is given the call is observed: ``tool_start`` fires before
    awaiting and ``tool`` completion fires the moment THIS call finishes
    (not after slower gather siblings), sharing one task_id.
    """
    import json as _json
    import time
    import uuid as _uuid

    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
    raw_name = str(fn.get("name", "") if isinstance(fn, dict) else "")
    try:
        raw_args = _json.loads(fn.get("arguments") or "{}")
    except Exception:
        raw_args = {}
    if not isinstance(raw_args, dict):
        raw_args = {}
    call_id_str = str(tc.get("id", "") if isinstance(tc, dict) else "")
    if allowed is not None and raw_name not in allowed:
        blocked = f"tool error: '{raw_name}' is not in the allowed tool set"
        if emit is not None:
            emit({"event": "tool", "tool": raw_name, "ok": False,
                  "status": "failed", "result": blocked[:400],
                  "args": _brief_args(raw_args), "duration_ms": 0.0,
                  "task_id": _uuid.uuid4().hex[:10],
                  "parent_task_id": parent_task_id,
                  "actor": actor or f"tool:{raw_name}",
                  "text": f"⛔ {raw_name} blocked (not advertised)"})
        return call_id_str, raw_name, raw_args, blocked, 0.0
    if emit is None:
        t0 = time.perf_counter()
        call_id, name, args, out = await _execute_tool_call(tc)
        return call_id, name, args, out, (time.perf_counter() - t0) * 1000
    task_id = _uuid.uuid4().hex[:10]
    emit({
        "event": "tool_start", "tool": raw_name,
        "args": _brief_args(raw_args), "task_id": task_id,
        "parent_task_id": parent_task_id,
        "actor": actor or f"tool:{raw_name}",
        "text": f"🔧 {raw_name}({_brief_args(raw_args)})",
    })
    t0 = time.perf_counter()
    try:
        call_id, name, args, out = await _execute_tool_call(tc)
    except asyncio.CancelledError:
        # Cancelled mid-await: close the lifecycle as cancelled, never success.
        emit({"event": "tool", "tool": raw_name, "ok": False,
              "status": "cancelled", "result": "",
              "args": _brief_args(raw_args),
              "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
              "task_id": task_id, "parent_task_id": parent_task_id,
              "actor": actor or f"tool:{raw_name}",
              "text": f"⏹ {raw_name} cancelled"})
        raise
    dur = (time.perf_counter() - t0) * 1000
    _emit_tool_result(emit, name, args, out, dur, task_id=task_id,
                      parent_task_id=parent_task_id, actor=actor)
    return call_id, name, args, out, dur


_SEARCH_RESULT_RE = re.compile(r"^\s*(\d{1,2})[.)]\s+(.*)$")
_URL_LINE_RE = re.compile(r"^\s{2,}(https?://\S+)\s*$")


def search_sources(output: str) -> list[dict[str, str]]:
    """Parse the numbered ``N. title / url / snippet`` text format the
    search tools emit into structured source dicts.

    Bounded to 10 entries, http(s) URLs only, deduped by URL, stable id
    from a SHA-256 prefix of the URL. Snippets are copied verbatim —
    never invented.
    """
    import hashlib

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    lines = (output or "").splitlines()
    i = 0
    while i < len(lines) and len(out) < 10:
        m = _SEARCH_RESULT_RE.match(lines[i])
        if not m:
            i += 1
            continue
        title = m.group(2).strip()
        url = ""
        snip_lines: list[str] = []
        j = i + 1
        # URL line first (indented), then optional snippet lines.
        while j < len(lines):
            um = _URL_LINE_RE.match(lines[j])
            if um and not url:
                url = um.group(1).rstrip(".,;:)")
                j += 1
                continue
            if url and lines[j].startswith(("   ", "\t")) and lines[j].strip() \
                    and not _SEARCH_RESULT_RE.match(lines[j]):
                snip_lines.append(lines[j].strip())
                j += 1
                continue
            break
        i = j
        if not url.lower().startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        src: dict[str, str] = {
            "id": "src:" + hashlib.sha256(url.encode()).hexdigest()[:12],
            "title": title[:200], "url": url,
        }
        if snip_lines:
            src["snippet"] = " ".join(snip_lines)[:400]
        out.append(src)
    return out


def _tool_outcome(name: str, out: str) -> tuple[bool, bool]:
    """(ok, empty) classification for a finished tool call.

    Exa 'unavailable'/errors, HTTP >=400 and 'No results found' are
    unavailable/failed/empty — never reported as successful facts.
    """
    # Classify by the leading status/prefix only — a fetched article that
    # happens to contain the word "unavailable" is still a success.
    low = (out or "").lstrip().lower()
    failed = low.startswith((
        "error:", "tool error:", "fetch error:", "search error:",
        "exa_search unavailable", "exa_search error", "no api key")) \
        or bool(re.match(r"^http\s+[45]\d\d\b", low))
    empty = low.startswith("no results found")
    return (not failed), empty


def _emit_source_discoveries(
    emit: Callable[[dict[str, Any]], None], name: str, out: str,
    *, task_id: str | None = None, actor: str = "",
) -> None:
    """Emit one source_discovered (channel=finding) per real result."""
    if name not in ("web_search", "exa_search", "search", "academic_search"):
        return
    ok, empty = _tool_outcome(name, out)
    if not ok or empty:
        return
    for src in search_sources(out):
        emit({"event": "source_discovered", "source": src,
              **{k: v for k, v in {
                  "task_id": task_id, "actor": actor or f"tool:{name}"}.items()
                 if v}})


def _emit_tool_result(
    emit: Callable[[dict[str, Any]], None],
    name: str, args: dict[str, Any], out: str, dur_ms: float,
    *, task_id: str | None = None, parent_task_id: str | None = None,
    actor: str = "",
) -> None:
    """One structured tool-result event — name, inputs, outcome, duration."""
    ok, empty = _tool_outcome(name, out)
    emit({
        "event": "tool", "tool": name, "args": _brief_args(args),
        "result": out[:400], "ok": ok, "empty": empty,
        "duration_ms": round(dur_ms, 1),
        "text": f"🔧 {name}({_brief_args(args)})",
        **{k: v for k, v in {
            "task_id": task_id, "parent_task_id": parent_task_id,
            "actor": actor}.items() if v},
    })
    _emit_source_discoveries(emit, name, out, task_id=task_id, actor=actor)


async def execute_observed_tool(
    name: str, args: dict[str, Any], *,
    emit: Emit,
    parent_task_id: Optional[str] = None,
    actor: str = "",
) -> str:
    """Validate, execute and observe one named tool call.

    Emits ``tool_start`` BEFORE awaiting the tool and a ``tool`` completion
    with the SAME ``task_id``/``parent_task_id``/``actor`` plus duration as
    soon as this call finishes. Returns the tool output string.
    """
    import json as _json
    import time
    import uuid as _uuid

    task_id = _uuid.uuid4().hex[:10]
    tname = str(name or "")
    emit({
        "event": "tool_start", "tool": tname, "args": _brief_args(args or {}),
        "task_id": task_id, "parent_task_id": parent_task_id,
        "actor": actor or f"tool:{tname}",
        "text": f"🔧 {tname}({_brief_args(args or {})})",
    })
    t0 = time.perf_counter()
    tc = {"id": "obs", "type": "function",
          "function": {"name": tname, "arguments": _json.dumps(args or {})}}
    try:
        _cid, _n, targs, out = await _execute_tool_call(tc)
    except asyncio.CancelledError:
        emit({"event": "tool", "tool": tname, "ok": False,
              "status": "cancelled", "result": "",
              "args": _brief_args(args or {}),
              "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
              "task_id": task_id, "parent_task_id": parent_task_id,
              "actor": actor, "text": f"⏹ {tname} cancelled"})
        raise
    dur = (time.perf_counter() - t0) * 1000
    _emit_tool_result(emit, _n or tname, targs or (args or {}), out, dur,
                      task_id=task_id, parent_task_id=parent_task_id,
                      actor=actor)
    return out


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
    closing_message: str | None = None,
    parent_task_id: str | None = None,
    actor: str = "",
) -> dict[str, Any]:
    """Server-side tool-call loop over the built-in tools.

    Sends ``payload`` (non-streaming) through the gateway with the requested
    built-in tool specs; executes any tool_calls locally and re-calls until the
    model answers directly (or rounds are exhausted). ``emit`` gets activity
    dicts for each executed call. ``call_timeout`` bounds each provider call.
    Returns the final completion dict. ``tool_policy`` prepends a usage policy
    to the system prompt so the model answers directly when it can instead of
    calling tools speculatively (used for plain chat; researchers/agents keep
    their own prompts). ``closing_message`` replaces the default "answer with
    components" instruction sent when rounds run out — callers with their own
    output contract (research sub-agents return JSON) must pass one.
    """

    emit = emit or (lambda ev: None)
    if names is True or names in (None, [], "*"):
        names = ["*"]
    specs = specs_for(list(names))
    if not specs:
        result, _p, _k, _m = await gateway.chat(session, payload, on_event=emit)
        return result
    allowed_names: set[str] = {s["function"]["name"] for s in specs}

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
        except TimeoutError:
            raise TimeoutError(f"agent timed out after {call_timeout}s") from None
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
        # Tool calls in the same model turn are independent; execute concurrently
        # while preserving their original order when appending results to history.
        # Only names in the advertised spec set may execute (allowlist boundary).
        executed = await asyncio.gather(*(
            _timed_tool_call(tc, emit=emit, allowed=allowed_names,
                             parent_task_id=parent_task_id, actor=actor)
            for tc in calls))
        for call_id, name, args, out, dur_ms in executed:
            if tool_output_hook is not None:
                with contextlib.suppress(Exception):
                    tool_output_hook(name, out)
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
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
        "content": closing_message or (
            "Tool use is finished. Answer now with components using "
            "only the gathered data."),
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
        executed = await asyncio.gather(*(
            _timed_tool_call(tc, emit=emit, allowed=allowed_names,
                             parent_task_id=parent_task_id, actor=actor)
            for tc in calls))
        for call_id, name, args, out, dur_ms in executed:
            if tool_output_hook is not None:
                with contextlib.suppress(Exception):
                    tool_output_hook(name, out)
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": out,
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
    # Repair the envelope fence tag when a model/stream splits it —
    # ```nova\n-response, ```nova response, ```nova_response all normalize
    # to ```nova-response so the envelope parser (and UI) can read it.
    t = re.sub(r"```\s*nova[\s\-_]*response\b", "```nova-response", t)
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
