"""Built-in agent tools — a small plugin registry of safe, local functions
exposed to models via OpenAI-style function calling.

Each tool: name, description (sent to model), JSON-schema parameters,
and an async execute(**args) -> str. Register new tools in TOOLS below.
"""

from __future__ import annotations

import ast
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
