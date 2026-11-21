"""Tool service for v2 research sub-agents (spec §8).

Every tool is async, returns a uniform ``ToolResult`` envelope
``{ok, data, error, cache_hit, truncated}``, and is logged to
``tool_calls``. Errors travel inside the envelope — never raised into the
agent loop — with one of the standard error codes.

Tools: ``search``, ``fetch_page``, ``browse``, ``read_pdf``,
``academic_search``, ``run_python``.

Adapted for the SQLite + Astra stack: extracted page text and snapshots are
stored under ``<data>/research/`` and referenced from ``Source`` rows;
embeddings go to Astra via the evidence store.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import ToolCall
from app.services import evidence_store as es
from app.services.netutil import (
    TooManyRedirects,
    UnsafeRedirect,
    fetch_following_redirects,
    safe_url,
)
from app.services.safety import check_outbound_arg, strip_injection
from app.services.source_score import score_source

log = logging.getLogger("nova.tools")

ErrorCode = str  # timeout|blocked|not_found|paywalled|too_large|unsafe_url|
                 # sandbox_error|rate_limited|internal


# ---------------------------------------------------------------- envelope

@dataclass
class ToolError:
    code: ErrorCode
    message: str


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: Optional[ToolError] = None
    cache_hit: bool = False
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": (
                {"code": self.error.code, "message": self.error.message}
                if self.error else None
            ),
            "cache_hit": self.cache_hit,
            "truncated": self.truncated,
        }


def _err(code: ErrorCode, message: str, *, cache_hit: bool = False,
         truncated: bool = False) -> ToolResult:
    return ToolResult(ok=False, error=ToolError(code, message[:400]),
                      cache_hit=cache_hit, truncated=truncated)


def _ok(data: dict[str, Any], *, cache_hit: bool = False,
        truncated: bool = False) -> ToolResult:
    return ToolResult(ok=True, data=data, cache_hit=cache_hit,
                      truncated=truncated)


async def _log_call(
    session: Optional[AsyncSession],
    *,
    run_id: Optional[str],
    actor: str,
    tool: str,
    args: dict[str, Any],
    result: ToolResult,
    latency_ms: int,
) -> None:
    if session is None:
        return
    try:
        session.add(ToolCall(
            run_id=run_id, actor=actor, tool=tool, args=args,
            ok=result.ok, cache_hit=result.cache_hit,
            latency_ms=latency_ms,
            result_ref=str(result.data.get("source_id") or "") or None,
            error=result.error.message if result.error else None,
        ))
        await session.flush()
    except Exception as e:
        log.debug("tool_call log failed: %s", e)


# ---------------------------------------------------------------- storage

def _research_dir(sub: str) -> Path:
    db_url = get_settings().database_url
    root = Path(db_url.split("///")[-1]).parent
    p = root / get_settings().research_snapshot_dir / sub
    p.mkdir(parents=True, exist_ok=True)
    return p


def _store_bytes(data: bytes, suffix: str, sub: str = "snapshots") -> str:
    h = hashlib.sha256(data).hexdigest()
    path = _research_dir(sub) / f"{h}{suffix}"
    if not path.exists():
        path.write_bytes(data)
    return str(path)


# ---------------------------------------------------------------- HTML→text

_STRIP_TAGS = ("script", "style", "noscript", "nav", "footer", "header",
               "aside", "form", "iframe", "svg", "button", "select")

_PAYWALL_RE = re.compile(r"subscribe|paywall|sign in to (continue|read)|"
                         r"create a free account", re.I)


def extract_page(html: str, url: str) -> dict[str, Any]:
    """Readability-lite extraction: strip chrome, prefer <article>/<main>,
    pull title/author/date metadata. Returns text plus discovered fields."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.string or "").strip() if soup.title else ""
    author = ""
    published: Optional[datetime] = None
    meta_author = soup.find("meta", attrs={"name": re.compile("author", re.I)})
    if meta_author and meta_author.get("content"):
        author = str(meta_author["content"]).strip()[:200]
    for prop in ("article:published_time", "datePublished", "date", "dc.date"):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find(
            "meta", attrs={"name": prop})
        if tag and tag.get("content"):
            try:
                published = datetime.fromisoformat(
                    str(tag["content"]).replace("Z", "+00:00"))
                break
            except ValueError:
                continue
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()
    root = (soup.find("article") or soup.find("main")
            or soup.find(role="main") or soup.body or soup)
    for el in root.select("[class*=cookie], [id*=cookie], [class*=banner],"
                          " [class*=newsletter], [class*=subscribe]"):
        el.decompose()
    text = re.sub(r"\n{3,}", "\n\n", root.get_text("\n", strip=True))
    return {
        "title": title[:500],
        "author": author or None,
        "published_at": published,
        "text": text,
        "has_references": bool(soup.find_all(
            "a", href=re.compile(r"^https?://"))),
        "paywalled": bool(_PAYWALL_RE.search(text[:3000])),
    }


# ---------------------------------------------------------------- fetch

_UA = {"User-Agent": "Nova-Research/2.0 (+compatible; research-bot)"}


async def _fetch_response(url: str) -> tuple[Optional[httpx.Response], Optional[ToolError]]:
    """Safe GET: scheme + SSRF checks, manual redirect following."""
    if not safe_url(url):
        return None, ToolError("unsafe_url", f"blocked or non-http(s) URL: {url[:120]}")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as c:
            resp = await fetch_following_redirects(c, url, max_hops=4, headers=_UA)
        return resp, None
    except UnsafeRedirect as e:
        return None, ToolError("unsafe_url", f"redirect to blocked address: {e}")
    except TooManyRedirects:
        return None, ToolError("blocked", "too many redirects")
    except httpx.TimeoutException:
        return None, ToolError("timeout", "fetch timed out")
    except httpx.HTTPStatusError as e:
        return None, ToolError("blocked", f"HTTP {e.response.status_code}")
    except Exception as e:
        return None, ToolError("internal", f"fetch error: {str(e)[:200]}")


def _error_for_status(resp: httpx.Response) -> Optional[ToolError]:
    if resp.status_code == 404:
        return ToolError("not_found", "HTTP 404")
    if resp.status_code in (401, 403):
        return ToolError("paywalled", f"HTTP {resp.status_code}")
    if resp.status_code == 429:
        return ToolError("rate_limited", "HTTP 429")
    if resp.status_code >= 400:
        return ToolError("blocked", f"HTTP {resp.status_code}")
    return None


async def fetch_page(
    session: Optional[AsyncSession],
    url: str,
    *,
    mode: str = "auto",
    max_chars: int = 20_000,
    run_id: Optional[str] = None,
    actor: str = "",
    time_sensitive: bool = False,
) -> ToolResult:
    """Fetch a page, extract text, register a Source row, fill the cache."""
    t0 = time.monotonic()
    result: ToolResult
    if session is not None:
        cached = await es.cache_lookup(session, url)
        if cached is not None:
            text = es.load_source_text(cached)
            result = _ok({
                "source_id": cached.id, "url": url,
                "canonical_url": cached.canonical_url,
                "domain": cached.domain, "title": cached.title,
                "text": text[:max_chars],
                "score": cached.score, "source_class": cached.source_class,
            }, cache_hit=True, truncated=len(text) > max_chars)
            await _log_call(session, run_id=run_id, actor=actor,
                            tool="fetch_page", args={"url": url},
                            result=result,
                            latency_ms=int((time.monotonic() - t0) * 1000))
            return result

    resp, err = await _fetch_response(url)
    if err is not None:
        result = _err(err.code, err.message)
    elif resp is None:
        result = _err("internal", "no response")
    elif (status_err := _error_for_status(resp)) is not None:
        result = _err(status_err.code, status_err.message)
    else:
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        final_url = str(resp.url)
        max_bytes = get_settings().research_fetch_max_bytes
        truncated = len(resp.content) > max_bytes
        raw = resp.content[:max_bytes]
        snapshot_ref = _store_bytes(
            raw, ".bin" if ctype not in ("text/html", "application/xhtml+xml")
            else ".html")

        if "pdf" in ctype or final_url.lower().endswith(".pdf"):
            text = "(PDF — extract with read_pdf)"
            content_type, meta = "pdf", {"title": None, "author": None,
                                         "published_at": None,
                                         "has_references": False,
                                         "paywalled": False}
        elif "html" in ctype or "xml" in ctype:
            meta = extract_page(raw.decode(resp.encoding or "utf-8",
                                           errors="replace"), final_url)
            text = meta["text"]
            content_type = "html"
            if mode == "raw":
                text = raw.decode(resp.encoding or "utf-8",
                                  errors="replace")
        elif "json" in ctype:
            content_type, meta = "json", {"title": None, "author": None,
                                          "published_at": None,
                                          "has_references": False,
                                          "paywalled": False}
            try:
                text = json.dumps(json.loads(raw), ensure_ascii=False,
                                  indent=1)[:max_chars]
            except Exception:
                text = raw.decode("utf-8", errors="replace")
        else:
            content_type, meta = "other", {"title": None, "author": None,
                                           "published_at": None,
                                           "has_references": False,
                                           "paywalled": False}
            text = raw.decode(resp.encoding or "utf-8", errors="replace")

        if meta.get("paywalled") and len(text) < 800:
            result = _err("paywalled", "page appears paywalled")
        elif session is None:
            result = _ok({"url": url, "canonical_url": es.canonicalize_url(final_url),
                          "text": text[:max_chars], "title": meta.get("title")},
                         truncated=truncated or len(text) > max_chars)
        else:
            scoring = score_source(
                final_url, content_type=content_type,
                author=meta.get("author"),
                published_at=meta.get("published_at"),
                has_references=bool(meta.get("has_references")),
                time_sensitive=time_sensitive)
            src = await es.register_source(
                session, url=url, final_url=final_url,
                title=meta.get("title"), author=meta.get("author"),
                published_at=meta.get("published_at"),
                content_type=content_type, content=text,
                snapshot_ref=snapshot_ref,
                text_ref=es.store_text_file(text) if text else None,
                scoring=scoring)
            await es.cache_store(session, final_url, src,
                                 es.ttl_for_class(scoring.source_class),
                                 etag=resp.headers.get("etag"))
            result = _ok({
                "source_id": src.id, "url": url,
                "canonical_url": src.canonical_url, "domain": src.domain,
                "title": src.title, "text": text[:max_chars],
                "score": src.score, "source_class": src.source_class,
            }, truncated=truncated or len(text) > max_chars)
        if result.ok:
            filt = strip_injection(str(result.data.get("text", "")))
            if filt.flagged:
                result.data["injection_flags"] = len(filt.flagged)
                result.data["text"] = filt.text[:max_chars]

    await _log_call(session, run_id=run_id, actor=actor, tool="fetch_page",
                    args={"url": url}, result=result,
                    latency_ms=int((time.monotonic() - t0) * 1000))
    return result


# ---------------------------------------------------------------- search

_DDG_DEAD = False  # skip DDG for the process once it proves unreachable


async def _bing_search(query: str, num: int) -> list[dict[str, str]]:
    """Bing HTML fallback — keyless, reachable where duckduckgo is blocked."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
        r = await c.get("https://www.bing.com/search",
                        params={"q": query, "count": num},
                        headers={"User-Agent": "Mozilla/5.0"})
    if r.status_code != 200:
        raise RuntimeError(f"bing HTTP {r.status_code}")
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "lxml")
    out: list[dict[str, str]] = []
    for li in soup.select("li.b_algo"):
        if len(out) >= num:
            break
        a = li.select_one("h2 a")
        if not a:
            continue
        cap = li.select_one(".b_caption p")
        out.append({
            "title": a.get_text(" ", strip=True),
            "url": _bing_real_url(a.get("href", "") or ""),
            "snippet": cap.get_text(" ", strip=True) if cap else "",
        })
    return out


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


async def _ddg_search(query: str, num: int) -> list[dict[str, str]]:
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.get("https://lite.duckduckgo.com/lite/",
                        params={"q": query}, headers={"User-Agent": "Mozilla/5.0"})
    if r.status_code != 200:
        raise RuntimeError(
            f"duckduckgo HTTP {r.status_code} (rate-limited?)")
    if "anomaly" in r.text[:2000].lower():
        raise RuntimeError("duckduckgo rate-limited (anomaly check page)")
    links = re.findall(
        r"<a[^>]+href=\"([^\"]+)\"[^>]*class='result-link'[^>]*>(.*?)</a>",
        r.text)
    snippets = re.findall(r"<td class='result-snippet'>(.*?)</td>", r.text, re.S)

    def real(href: str) -> str:
        m = re.search(r"[?&]uddg=([^&]+)", href)
        return unquote(m.group(1)) if m else href

    def clean(s: str) -> str:
        return unescape(re.sub(r"<[^>]+>", "", s)).strip()

    out = []
    for i, (href, title) in enumerate(links[:num]):
        out.append({
            "title": clean(title), "url": real(href),
            "snippet": clean(snippets[i]) if i < len(snippets) else "",
        })
    return out


async def _brave_search(query: str, num: int, key: str) -> list[dict[str, str]]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": num},
            headers={"X-Subscription-Token": key,
                     "Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
    return [{"title": w.get("title", ""), "url": w.get("url", ""),
             "snippet": w.get("description", "")}
            for w in (data.get("web", {}).get("results") or [])[:num]]


async def search(
    session: Optional[AsyncSession],
    query: str,
    *,
    num: int = 5,
    recency: Optional[str] = None,
    run_id: Optional[str] = None,
    actor: str = "",
) -> ToolResult:
    """Search the web. Brave when a key is configured, else DuckDuckGo lite."""
    t0 = time.monotonic()
    num = max(1, min(int(num or 5), 10))
    if check_outbound_arg(query):
        result = _err("unsafe_url", "query rejected by exfiltration guard")
    else:
        global _DDG_DEAD
        try:
            key = get_settings().brave_search_api_key
            provider = "ddg"
            if key:
                provider = "brave"
                items = await _brave_search(query, num, key)
            else:
                try:
                    if _DDG_DEAD:
                        raise RuntimeError("ddg skipped (marked unreachable)")
                    items = await _ddg_search(query, num)
                except Exception:
                    _DDG_DEAD = True
                    provider = "bing"
                    items = await _bing_search(query, num)
            for it in items:
                it["domain"] = urlparse(it["url"]).hostname or ""
            result = _ok({"query": query, "provider": provider,
                          "results": items})
        except httpx.TimeoutException:
            result = _err("timeout", "search timed out (15s)")
        except Exception as e:
            result = _err("internal",
                          f"search error: {type(e).__name__}: {str(e)[:180]}")
    await _log_call(session, run_id=run_id, actor=actor, tool="search",
                    args={"query": query, "num": num}, result=result,
                    latency_ms=int((time.monotonic() - t0) * 1000))
    return result


# ---------------------------------------------------------------- read_pdf

def _parse_page_range(spec: Optional[str], total: int) -> range:
    """'2-5'/'3'/'all' → 0-based range, clamped to the document."""
    if not spec or spec == "all":
        return range(0, total)
    m = re.match(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$", str(spec))
    if not m:
        return range(0, total)
    start = max(1, int(m.group(1)))
    end = int(m.group(2)) if m.group(2) else start
    return range(start - 1, min(end, total))


async def read_pdf(
    session: Optional[AsyncSession],
    *,
    url: Optional[str] = None,
    path: Optional[str] = None,
    pages: Optional[str] = None,
    run_id: Optional[str] = None,
    actor: str = "",
) -> ToolResult:
    """Extract text (and page-wise text list) from a PDF at a URL or a local
    path under the data directory."""
    t0 = time.monotonic()
    result: ToolResult
    try:
        from pypdf import PdfReader
    except ImportError:
        result = _err("internal", "pypdf not installed")
        await _log_call(session, run_id=run_id, actor=actor, tool="read_pdf",
                        args={"url": url, "path": path}, result=result,
                        latency_ms=int((time.monotonic() - t0) * 1000))
        return result

    pdf_bytes: Optional[bytes] = None
    final_url = url
    if url:
        resp, err = await _fetch_response(url)
        if err is not None:
            pdf_bytes, result = None, _err(err.code, err.message)
        elif resp is None:
            pdf_bytes, result = None, _err("internal", "no response")
        elif (status_err := _error_for_status(resp)) is not None:
            pdf_bytes, result = None, _err(status_err.code, status_err.message)
        elif len(resp.content) > get_settings().research_fetch_max_bytes:
            pdf_bytes, result = None, _err("too_large", "pdf exceeds size cap")
        else:
            pdf_bytes = resp.content
            final_url = str(resp.url)
    elif path:
        data_root = Path(
            get_settings().database_url.split("///")[-1]).parent.resolve()
        p = Path(path).resolve()
        if not str(p).startswith(str(data_root)) or not p.is_file():
            pdf_bytes, result = None, _err(
                "unsafe_url", "path must live under the data directory")
        else:
            pdf_bytes = p.read_bytes()
            final_url = f"file://{p.name}"
    else:
        pdf_bytes, result = None, _err("invalid_request", "url or path required")  # type: ignore[arg-type]

    if pdf_bytes is not None:
        try:
            import io

            reader = PdfReader(io.BytesIO(pdf_bytes))
            rng = _parse_page_range(pages, len(reader.pages))
            page_texts = [(reader.pages[i].extract_text() or "").strip()
                          for i in rng]
            text = "\n\n".join(t for t in page_texts if t)
            if not text:
                result = _err("internal", "no text layer (scanned pdf?)")
            else:
                data: dict[str, Any] = {
                    "pages_total": len(reader.pages),
                    "pages_read": [i + 1 for i in rng],
                    "text": text,
                }
                if url and session is not None:
                    scoring = score_source(final_url or url, content_type="pdf")
                    src = await es.register_source(
                        session, url=url, final_url=final_url,
                        title=(reader.metadata.title if reader.metadata else None),
                        content_type="pdf", content=text,
                        snapshot_ref=_store_bytes(pdf_bytes, ".pdf"),
                        text_ref=es.store_text_file(text), scoring=scoring)
                    data["source_id"] = src.id
                result = _ok(data, truncated=len(page_texts) < len(reader.pages)
                             and pages is None)
        except Exception as e:
            result = _err("internal", f"pdf parse error: {str(e)[:200]}")

    await _log_call(session, run_id=run_id, actor=actor, tool="read_pdf",
                    args={"url": url, "path": path, "pages": pages},
                    result=result,
                    latency_ms=int((time.monotonic() - t0) * 1000))
    return result


# ---------------------------------------------------------------- academic

async def academic_search(
    session: Optional[AsyncSession],
    query: str,
    *,
    year_from: Optional[int] = None,
    num: int = 5,
    run_id: Optional[str] = None,
    actor: str = "",
) -> ToolResult:
    """Search OpenAlex (free, no key) for academic works."""
    t0 = time.monotonic()
    try:
        params: dict[str, Any] = {"search": query, "per-page": min(num, 10)}
        if year_from:
            params["filter"] = f"from_publication_date:{year_from}-01-01"
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get("https://api.openalex.org/works", params=params)
            r.raise_for_status()
            data = r.json()
        items = [{
            "title": w.get("title") or "",
            "authors": [a.get("author", {}).get("display_name", "")
                        for a in w.get("authorships", [])[:6]],
            "year": (w.get("publication_date") or "")[:4],
            "venue": (w.get("primary_location") or {}).get("source", {}) \
                        .get("display_name", "") if w.get("primary_location") else "",
            "doi": w.get("doi") or "",
            "url": ((w.get("primary_location") or {}).get("landing_page_url")
                    or w.get("id") or ""),
            "pdf_url": (w.get("open_access") or {}).get("oa_url") or "",
            "citation_count": w.get("cited_by_count", 0),
            "abstract": _invert_abstract(w.get("abstract_inverted_index")),
        } for w in data.get("results", [])]
        result = _ok({"provider": "openalex", "query": query, "results": items})
    except httpx.TimeoutException:
        result = _err("timeout", "academic search timed out")
    except Exception as e:
        result = _err("internal", f"academic search error: {str(e)[:200]}")
    await _log_call(session, run_id=run_id, actor=actor,
                    tool="academic_search",
                    args={"query": query, "year_from": year_from},
                    result=result,
                    latency_ms=int((time.monotonic() - t0) * 1000))
    return result


def _invert_abstract(index: Optional[dict[str, list[int]]]) -> str:
    if not index:
        return ""
    words: dict[int, str] = {}
    for word, positions in index.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words[i] for i in sorted(words))[:1200]


# ---------------------------------------------------------------- run_python

_ALLOWED_IMPORTS = {
    "math", "json", "re", "statistics", "itertools", "functools",
    "collections", "datetime", "string", "random", "textwrap", "csv",
    "io", "base64", "hashlib", "decimal", "fractions",
    "numpy", "numpy.*", "pandas", "pandas.*",
    "matplotlib", "matplotlib.*",
}

_BANNED_NAMES = {
    "eval", "exec", "compile", "__import__", "input", "breakpoint",
    "globals", "locals", "vars", "dir", "getattr", "setattr", "delattr",
    "open", "exit", "quit", "help", "memoryview", "bytearray", "super",
    "type", "isinstance.__call__",
}


def _module_allowed(name: str) -> bool:
    return name in _ALLOWED_IMPORTS or f"{name}.*" in _ALLOWED_IMPORTS or \
        any(name.startswith(m[:-2] + ".") for m in _ALLOWED_IMPORTS
            if m.endswith(".*"))


def _validate_code(tree: ast.AST) -> Optional[str]:
    """AST gate for the sandbox-lite runner. Returns a rejection reason."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([node.module] if isinstance(node, ast.ImportFrom)
                     and node.module else [a.name for a in node.names])
            for n in names:
                if n and not _module_allowed(n):
                    return f"import of '{n}' is not allowed"
        elif isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            return f"use of '{node.id}' is not allowed"
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return f"dunder access '{node.attr}' is not allowed"
    return None


async def run_python(
    session: Optional[AsyncSession],
    code: str,
    *,
    files: Optional[list[str]] = None,
    run_id: Optional[str] = None,
    actor: str = "",
) -> ToolResult:
    """Sandbox-lite Python executor: AST allowlist, isolated scratch dir,
    no env vars, wall-clock timeout. NOT a security boundary — real
    isolation needs the container sandbox (spec §15.3)."""
    t0 = time.monotonic()
    result: ToolResult
    try:
        tree = ast.parse(code or "")
        rejection = _validate_code(tree)
    except SyntaxError as e:
        tree, rejection = None, f"syntax error: {e}"
    if rejection:
        result = _err("sandbox_error", rejection)
    else:
        scratch = _research_dir("scratch") / uuid.uuid4().hex[:12]
        scratch.mkdir(parents=True, exist_ok=True)
        timeout = get_settings().research_sandbox_timeout_s
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-c", code,
                cwd=str(scratch),
                env={"PATH": "", "PYTHONDONTWRITEBYTECODE": "1",
                     "MPLBACKEND": "Agg"},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, errb = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                result = _err("timeout", f"exceeded {timeout}s")
            else:
                created = []
                total = 0
                for f in sorted(scratch.iterdir()):
                    if f.is_file():
                        size = f.stat().st_size
                        total += size
                        if total <= 256 * 1024 * 1024:
                            created.append({"name": f.name, "bytes": size})
                result = _ok({
                    "stdout": out.decode("utf-8", errors="replace")[:16000],
                    "stderr": errb.decode("utf-8", errors="replace")[:8000],
                    "exit_code": proc.returncode,
                    "files": created,
                    "workdir": str(scratch),
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                })
        except Exception as e:
            result = _err("sandbox_error", str(e)[:300])
    await _log_call(session, run_id=run_id, actor=actor, tool="run_python",
                    args={"code_len": len(code or "")}, result=result,
                    latency_ms=int((time.monotonic() - t0) * 1000))
    return result


# ---------------------------------------------------------------- browse

_browser_lock = asyncio.Lock()
_playwright_cm = None
_browser = None


async def _playwright_browser():
    """Lazily start one shared Chromium instance; None when unavailable."""
    global _playwright_cm, _browser
    if _browser is not None:
        return _browser
    async with _browser_lock:
        if _browser is not None:
            return _browser
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return None
        try:
            _playwright_cm = async_playwright()
            pw = await _playwright_cm.start()
            _browser = await pw.chromium.launch(headless=True)
            return _browser
        except Exception as e:
            log.debug("playwright launch failed: %s", e)
            return None


async def close_browser() -> None:
    global _playwright_cm, _browser
    try:
        if _browser is not None:
            await _browser.close()
        if _playwright_cm is not None:
            await _playwright_cm.stop()
    finally:
        _browser = None
        _playwright_cm = None


async def browse(
    session: Optional[AsyncSession],
    url: str,
    *,
    actions: Optional[list[dict[str, Any]]] = None,
    extract: str = "reader",
    max_chars: int = 20_000,
    run_id: Optional[str] = None,
    actor: str = "",
) -> ToolResult:
    """Rendered browsing via Playwright. Degrades to plain ``fetch_page``
    with a ``degraded`` flag when Playwright/Chromium is unavailable."""
    t0 = time.monotonic()
    if not safe_url(url):
        return _err("unsafe_url", f"blocked or non-http(s) URL: {url[:120]}")
    browser = await _playwright_browser()
    if browser is None:
        result = await fetch_page(session, url, mode="reader",
                                  max_chars=max_chars, run_id=run_id,
                                  actor=actor)
        result.data["degraded"] = "playwright unavailable — static fetch used"
        return result
    result: ToolResult
    try:
        page = await browser.new_page(user_agent=_UA["User-Agent"])
        try:
            resp = await page.goto(url, wait_until="domcontentloaded",
                                   timeout=30_000)
            if resp and resp.status >= 400:
                status_err = _error_for_status_code(resp.status)
                code, msg = status_err or ("blocked", f"HTTP {resp.status}")
                result = _err(code, msg)
            else:
                for act in (actions or [])[:10]:
                    kind = act.get("type")
                    if kind == "click" and act.get("selector"):
                        await page.click(act["selector"], timeout=5000)
                    elif kind == "scroll":
                        await page.mouse.wheel(0, int(act.get("pixels", 1200)))
                    elif kind == "type" and act.get("selector"):
                        await page.fill(act["selector"], str(act.get("text", "")))
                    elif kind == "wait_for" and act.get("selector"):
                        await page.wait_for_selector(act["selector"],
                                                     timeout=8000)
                    elif kind == "wait":
                        await page.wait_for_timeout(
                            min(int(act.get("ms", 1000)), 5000))
                html = await page.content()
                meta = extract_page(html, str(page.url))
                data = {"url": url, "final_url": str(page.url),
                        "title": meta["title"], "text": meta["text"][:max_chars]}
                if extract == "screenshot":
                    shot = _research_dir("shots") / f"{uuid.uuid4().hex}.png"
                    await page.screenshot(path=str(shot), full_page=False)
                    data["screenshot_ref"] = str(shot)
                if session is not None:
                    scoring = score_source(str(page.url), content_type="html",
                                           author=meta.get("author"),
                                           published_at=meta.get("published_at"),
                                           has_references=bool(
                                               meta.get("has_references")))
                    src = await es.register_source(
                        session, url=url, final_url=str(page.url),
                        title=meta.get("title"), author=meta.get("author"),
                        published_at=meta.get("published_at"),
                        content_type="html", content=meta["text"],
                        text_ref=es.store_text_file(meta["text"]),
                        scoring=scoring)
                    data["source_id"] = src.id
                    data["score"] = src.score
                result = _ok(data, truncated=len(meta["text"]) > max_chars)
        finally:
            await page.close()
    except Exception as e:
        result = _err("internal", f"browse error: {str(e)[:200]}")
    await _log_call(session, run_id=run_id, actor=actor, tool="browse",
                    args={"url": url}, result=result,
                    latency_ms=int((time.monotonic() - t0) * 1000))
    return result


def _error_for_status_code(status: int) -> Optional[tuple[ErrorCode, str]]:
    if status == 404:
        return ("not_found", "HTTP 404")
    if status in (401, 403):
        return ("paywalled", f"HTTP {status}")
    if status == 429:
        return ("rate_limited", "HTTP 429")
    if status >= 400:
        return ("blocked", f"HTTP {status}")
    return None


# ---------------------------------------------------------------- dispatch

_DISPATCH = {
    "search": search,
    "fetch_page": fetch_page,
    "read_pdf": read_pdf,
    "academic_search": academic_search,
    "run_python": run_python,
    "browse": browse,
}


async def call_tool(
    session: Optional[AsyncSession],
    name: str,
    args: dict[str, Any],
    *,
    run_id: Optional[str] = None,
    actor: str = "",
) -> dict[str, Any]:
    """Sub-agent entry point: validate the tool name, run it, return the
    uniform envelope. Never raises."""
    fn = _DISPATCH.get(name)
    if fn is None:
        return _err("internal", f"unknown tool '{name}'").to_dict()
    try:
        # Exfiltration guard on every outbound string argument (§15.1).
        for v in args.values():
            if isinstance(v, str) and check_outbound_arg(v):
                return _err("unsafe_url",
                            "argument rejected by exfiltration guard").to_dict()
        res = await fn(session, run_id=run_id, actor=actor, **args)
        return res.to_dict()
    except TypeError as e:
        return _err("invalid_request", f"bad args for {name}: {e}").to_dict()  # type: ignore[arg-type]
    except Exception as e:
        log.warning("tool %s crashed: %s", name, e)
        return _err("internal", str(e)[:200]).to_dict()
