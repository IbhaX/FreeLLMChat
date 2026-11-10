"""Person lookup for chat cards — Wikipedia/Wikimedia summaries.

LinkedIn and social networks block automated fetching, so public figures
are resolved through Wikipedia (opensearch + page summary). Results are
cached in memory; lookups are best-effort and return ``None`` on any
failure so chat is never blocked by a missing bio.

Note: Wikimedia's robot policy blocks some HTTP clients by TLS
fingerprint (httpx gets 403), while ``requests`` is allowed for
low-volume lookups.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import requests

_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_CACHE_TTL = 24 * 3600
_CACHE_MAX = 500

_UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}
_TAG_RE = re.compile(r"<[^>]+>")


async def lookup_person(name: str) -> dict[str, Any] | None:
    """Return a person card payload for ``name`` or None if not found."""
    name = (name or "").strip()
    if len(name) < 3 or len(name) > 80:
        return None
    key = name.lower()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]

    person = await asyncio.to_thread(_fetch, name)
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = (time.time(), person)
    return person


def _fetch(name: str) -> dict[str, Any] | None:
    try:
        # Resolve the best-matching article title first.
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": name,
                "limit": 1,
                "namespace": 0,
                "format": "json",
            },
            headers=_UA,
            timeout=6.0,
        )
        r.raise_for_status()
        data = r.json()
        titles = data[1] if isinstance(data, list) and len(data) > 1 else []
        if not titles:
            return None
        title = titles[0]

        s = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
            headers=_UA,
            timeout=6.0,
        )
        if s.status_code != 200:
            return None
        summ = s.json()
        if summ.get("type") == "disambiguation" or not summ.get("extract"):
            return None
        thumb = (summ.get("thumbnail") or {}).get("source")
        content = summ.get("content_urls", {}).get("desktop", {}).get("page")
        return {
            "name": _TAG_RE.sub("", summ.get("title") or title),
            "description": summ.get("description") or "",
            "extract": (summ.get("extract") or "")[:400],
            "thumbnail": thumb,
            "url": content,
            "source": "wikipedia",
        }
    except Exception:
        return None
