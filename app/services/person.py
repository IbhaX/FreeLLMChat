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
from collections.abc import Mapping, Sequence
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

# Person-question detection — lowercase-tolerant ("who is muhammad ali?").
# Words after the question stem are captured, then cut at the first
# function word so "who is X and what did Y" keeps only the name.
_WHO_IS_RE = re.compile(
    r"(?i)\bwho\s+(?:is|'s|was|are|were)\s+(?:the\s+|an?\s+)?"
    r"([a-z][a-z.'\-]*(?:\s+[a-z][a-z.'\-]*){0,4})")
_ABOUT_RE = re.compile(
    r"(?i)\b(?:tell\s+me\s+(?:more\s+|a\s+bit\s+)?about|"
    r"(?:full|complete|detailed)\s+(?:profile|biography)\s+of|"
    r"profile\s+of|biography\s+of)\s+(?:the\s+|an?\s+)?"
    r"([a-z][a-z.'\-]*(?:\s+[a-z][a-z.'\-]*){0,4})")
# Profile-search asks with an explicit name: "profiles of X",
# "find people named X", "all accounts for X". Requires >=2 name words
# so "profiles of the week" never resolves "week" as a person.
_FIND_RE = re.compile(
    r"(?i)\b(?:profiles?|people|persons?|accounts|users?)\s+"
    r"(?:of|for|named|called|matching)\s+(?:the\s+|an?\s+|that\s+)?"
    r"([a-z][a-z.'\-]*(?:\s+[a-z][a-z.'\-]*){0,4})")
# Profile-ish follow-ups that name nobody: the name comes from earlier
# conversation turns ("more context?", "give me all the profiles").
_PROFILE_ASK_RE = re.compile(
    r"(?i)\b(?:profiles?|dossiers?|carousel)\b|"
    r"\bmore\s+(?:context|details|info)\b|\bdifferent\s+spelling\b")
# Function words end the name span; leading pronouns mean "not a person".
_Q_STOP = {
    "and", "or", "but", "so", "the", "a", "an", "in", "on", "of", "for",
    "to", "is", "was", "are", "were", "what", "when", "where", "why",
    "how", "did", "does", "do", "he", "she", "it", "they", "them", "his",
    "her", "their", "that", "this", "you", "your", "yours", "i", "me",
    "my", "we", "our", "us", "by", "with", "from", "as", "at", "who",
    "whom", "whose", "than", "then", "also",
}
_PRONOUN_FIRST = {
    "you", "he", "she", "it", "they", "we", "i", "me", "him", "her",
    "them", "us", "himself", "herself", "yourself", "your", "their",
    "his", "its", "our", "my", "this", "that", "these", "those",
}


def person_question_name(text: str) -> str | None:
    """Extract a person name from a "who is X" / "tell me about X" question.

    Returns None for pronouns ("who are you"), non-question text, and
    captures that reduce to function words only.
    """
    t = (text or "").strip()
    if not t:
        return None
    min_words = 1
    m = _WHO_IS_RE.search(t) or _ABOUT_RE.search(t)
    if not m:
        m = _FIND_RE.search(t)
        min_words = 2
    if not m:
        return None
    words = m.group(1).lower().split()
    name_words: list[str] = []
    for w in words:
        w = w.strip("?!.,;:()'\"")
        if not w:
            continue
        if w in _Q_STOP:
            break
        name_words.append(w)
    if not name_words or name_words[0] in _PRONOUN_FIRST:
        return None
    if len(name_words) < min_words:
        return None
    name = " ".join(name_words)
    return name if 3 <= len(name) <= 80 else None


def profile_followup_name(
        question: str,
        messages: Sequence[Mapping[str, Any]] | None) -> str | None:
    """Name for profile-ish follow-ups that name nobody.

    "give me all the profiles you can find" or "can you provide more
    context?" carry no name — the subject lives in an earlier turn. When
    ``question`` matches the follow-up pattern, scan prior user messages
    (most recent first) for an extractable person name.
    """
    if not _PROFILE_ASK_RE.search(question or ""):
        return None
    for m in reversed(list(messages or ())[:-1]):
        if not isinstance(m, Mapping) or m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            name = person_question_name(c)
            if name:
                return name
    return None


_people_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def name_matches(query: str, title: str) -> bool:
    """True when every meaningful query token appears in ``title``.

    Opensearch is fuzzy — "albin anthony" returns "Alvin Anthons" —
    so callers use this to tell a real match from a fuzzy neighbour.
    """
    want = {w.strip(".-,'\"")
            for w in query.lower().split() if len(w.strip(".-,'\"")) > 2}
    tl = (title or "").lower()
    return bool(want) and all(w in tl for w in want)


async def lookup_people(name: str) -> list[dict[str, Any]]:
    """All public profile candidates for a name.

    Wikipedia covers notable people, GitHub user search covers regular
    people with public profiles, and a web search surfaces social
    profile listings (LinkedIn, Facebook, Instagram, X, Threads,
    TikTok, Reddit, …) — profile URLs and snippets only, profiles
    themselves are never scraped. Used for ambiguous or non-notable
    names where no single dossier exists — real data only.
    """
    name = (name or "").strip()
    if len(name) < 3 or len(name) > 80:
        return []
    key = name.lower()
    hit = _people_cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]

    wiki, gh, social = await asyncio.gather(
        asyncio.to_thread(_fetch_wiki_people, name),
        asyncio.to_thread(_fetch_github_people, name),
        asyncio.to_thread(_fetch_social_people, name),
    )
    seen: set[str] = set()
    people: list[dict[str, Any]] = []
    for p in wiki + gh + social:
        u = str(p.get("url") or "").rstrip("/").lower()
        if u and u in seen:
            continue
        seen.add(u)
        people.append(p)
        if len(people) >= 10:
            break
    if len(_people_cache) >= _CACHE_MAX:
        _people_cache.clear()
    _people_cache[key] = (time.time(), people)
    return people


def _fetch_wiki_people(name: str) -> list[dict[str, Any]]:
    """Top notable matches for a name from Wikipedia opensearch."""
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": name,
                "limit": 4,
                "namespace": 0,
                "format": "json",
            },
            headers=_UA,
            timeout=6.0,
        )
        r.raise_for_status()
        data = r.json()
        titles = data[1] if isinstance(data, list) and len(data) > 1 else []
        # Opensearch is fuzzy — "albin anthony" can return "Anthony Alabi".
        # Keep only titles containing every query token so candidates
        # really are the person asked about.
        out: list[dict[str, Any]] = []
        for title in titles[:4]:
            if not name_matches(name, title):
                continue
            try:
                s = requests.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
                    headers=_UA,
                    timeout=6.0,
                )
                if s.status_code != 200:
                    continue
                summ = s.json()
                if summ.get("type") == "disambiguation" \
                        or not summ.get("extract"):
                    continue
                out.append({
                    "name": _TAG_RE.sub("", summ.get("title") or title),
                    "description": summ.get("description") or "",
                    "extract": (summ.get("extract") or "")[:400],
                    "thumbnail":
                        (summ.get("thumbnail") or {}).get("source"),
                    "url": summ.get("content_urls", {}).get(
                        "desktop", {}).get("page"),
                    "source": "wikipedia",
                })
            except Exception:
                continue
        return out
    except Exception:
        return []


_GH = {
    "User-Agent": "nova-person-lookup",
    "Accept": "application/vnd.github+json",
}


def _fetch_github_people(name: str) -> list[dict[str, Any]]:
    """Public GitHub users matching the name — avatars, bios, stats."""
    try:
        r = requests.get(
            "https://api.github.com/search/users",
            params={"q": f"{name} in:name,login", "per_page": 4},
            headers=_GH,
            timeout=6.0,
        )
        if r.status_code != 200:
            return []
        out: list[dict[str, Any]] = []
        for item in (r.json().get("items") or [])[:4]:
            login = item.get("login")
            if not login:
                continue
            try:
                p = requests.get(
                    f"https://api.github.com/users/{login}",
                    headers=_GH,
                    timeout=6.0,
                )
                if p.status_code != 200:
                    continue
                g = p.json()
            except Exception:
                continue
            facts: list[dict[str, str]] = []

            def _f(label: str, value: Any) -> None:
                if value not in (None, ""):
                    facts.append({"label": label, "value": str(value)})

            _f("Username", f"@{login}")
            _f("Location", g.get("location"))
            _f("Company", g.get("company"))
            _f("Followers", g.get("followers"))
            _f("Repositories", g.get("public_repos"))
            created = g.get("created_at")
            if created:
                _f("Joined", str(created)[:10])
            socials = [{"label": "GitHub", "url": g.get("html_url")}]
            blog = (g.get("blog") or "").strip()
            if blog:
                if not blog.startswith("http"):
                    blog = "https://" + blog
                socials.append({"label": "Website", "url": blog})
            out.append({
                "name": g.get("name") or login,
                "description": g.get("bio") or "GitHub user",
                "thumbnail": g.get("avatar_url"),
                "url": g.get("html_url"),
                "source": "github",
                "facts": facts,
                "socials": socials,
            })
        return out
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Web/social profile discovery — public profile *listings* found through web
# search (DuckDuckGo lite → Bing → Exa when keyed). Only result URLs, titles
# and snippets are used; the profile pages themselves are never scraped, so
# LinkedIn/Facebook robots rules are respected.
# ---------------------------------------------------------------------------

_PROFILE_HOSTS: tuple[tuple[str, str, str], ...] = (
    ("github.com", "GitHub",
     r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))(?:[/?#]|$)"),
    ("linkedin.com", "LinkedIn",
     r"linkedin\.com/(?:in|pub)/([A-Za-z0-9\-_%]+)"),
    ("facebook.com", "Facebook",
     r"facebook\.com/(?:profile\.php\?id=(\d+)|people/[^/?#]+/[^/?#]+|([A-Za-z0-9.]{3,}))"),
    ("instagram.com", "Instagram",
     r"instagram\.com/([A-Za-z0-9_.]{2,30})"),
    ("threads.net", "Threads",
     r"threads\.net/@([A-Za-z0-9_.]+)"),
    ("tiktok.com", "TikTok",
     r"tiktok\.com/@([A-Za-z0-9_.]+)"),
    ("x.com", "X", r"x\.com/([A-Za-z0-9_]{2,20})"),
    ("twitter.com", "X", r"twitter\.com/([A-Za-z0-9_]{2,20})"),
    ("bsky.app", "Bluesky", r"bsky\.app/profile/([A-Za-z0-9._:\-]+)"),
    ("youtube.com", "YouTube",
     r"youtube\.com/(?:@|channel/|c/)([A-Za-z0-9_.\-]+)"),
    ("reddit.com", "Reddit", r"reddit\.com/(?:u|user)/([A-Za-z0-9_\-]+)"),
    ("medium.com", "Medium", r"medium\.com/@([A-Za-z0-9_.]+)"),
    ("dev.to", "DEV Community", r"dev\.to/([A-Za-z0-9_]+)"),
    ("pinterest.com", "Pinterest", r"pinterest\.com/([A-Za-z0-9_]+)"),
    ("keybase.io", "Keybase", r"keybase\.io/([A-Za-z0-9_]+)"),
)

# URL path segments that are never profiles on these hosts.
_HANDLE_BAD = {
    "search", "login", "signup", "join", "settings", "explore", "orgs",
    "organizations", "marketplace", "features", "pricing", "about",
    "topics", "trending", "collections", "new", "import", "contact",
    "security", "site", "blog", "business", "password_reset", "watching",
    "notifications", "stars", "issues", "pulls", "apps", "feed", "home",
    "share", "sharer", "intent", "hashtag", "groups", "events", "pages",
    "help", "legal", "policies", "gaming", "watch", "reel", "reels",
    "stories", "directory", "public", "people", "p", "tv", "accounts",
    "developer", "developers", "jobs", "company", "school", "learning",
    "premium", "mynetwork", "messaging", "sales", "talent", "ads",
}


def _strip_html(s: str) -> str:
    from html import unescape as _un
    return _un(_TAG_RE.sub("", s or "")).strip()


def _ddg_results(query: str, num: int) -> list[dict[str, str]]:
    """DuckDuckGo lite result links — keyless."""
    from urllib.parse import unquote
    try:
        r = requests.get("https://lite.duckduckgo.com/lite/",
                         params={"q": query}, headers=_UA, timeout=8.0)
        if r.status_code != 200 or "anomaly" in r.text[:2000].lower():
            return []
        links = re.findall(
            r"<a[^>]+href=\"([^\"]+)\"[^>]*class='result-link'[^>]*>"
            r"(.*?)</a>", r.text)
        snippets = re.findall(r"<td class='result-snippet'>(.*?)</td>",
                              r.text, re.S)
        out: list[dict[str, str]] = []
        for i, (href, title) in enumerate(links[:num]):
            m = re.search(r"[?&]uddg=([^&]+)", href)
            out.append({
                "title": _strip_html(title),
                "url": unquote(m.group(1)) if m else href,
                "snippet": _strip_html(snippets[i])
                if i < len(snippets) else "",
            })
        return out
    except Exception:
        return []


def _bing_results(query: str, num: int) -> list[dict[str, str]]:
    """Bing HTML results — keyless fallback when DDG is unreachable."""
    import base64
    from html import unescape as _un
    try:
        r = requests.get("https://www.bing.com/search",
                         params={"q": query, "count": num},
                         headers=_UA, timeout=10.0)
        if r.status_code != 200:
            return []
        blocks = re.findall(r"<li class=\"b_algo\".*?</li>", r.text, re.S)
        out: list[dict[str, str]] = []
        for b in blocks[:num]:
            am = re.search(r"<h2[^>]*>\s*<a[^>]+href=\"([^\"]+)\"[^>]*>"
                           r"(.*?)</a>", b, re.S)
            if not am:
                continue
            href, title = _un(am.group(1)), _strip_html(am.group(2))
            wu = re.search(r"[?&]u=a1([^&]+)", href)
            if wu:
                try:
                    href = base64.urlsafe_b64decode(
                        wu.group(1) + "===").decode("utf-8", "replace")
                except Exception:
                    pass
            pm = re.search(r"<p[^>]*>(.*?)</p>", b, re.S)
            out.append({"title": title, "url": href,
                        "snippet": _strip_html(pm.group(1)) if pm else ""})
        return out
    except Exception:
        return []


def _exa_key() -> str:
    """EXA_API_KEY from env or pydantic Settings (sync variant of
    tools._exa_api_key — the settings-table read needs a session and is
    skipped here)."""
    import os
    env = os.environ.get("EXA_API_KEY", "").strip()
    if env:
        return env
    try:
        from app.config import get_settings
        sec = get_settings().exa_api_key
        return sec.get_secret_value().strip() if sec is not None else ""
    except Exception:
        return ""


def _exa_results(query: str, num: int) -> list[dict[str, str]]:
    """Exa neural search — higher recall on names than keyword scrapers.
    No-op without a configured key."""
    key = _exa_key()
    if not key:
        return []
    try:
        r = requests.post(
            "https://api.exa.ai/search",
            json={"query": query, "numResults": max(1, min(num, 10)),
                  "type": "auto",
                  "contents": {"highlights": {"maxCharacters": 300}}},
            headers={"x-api-key": key,
                     "Content-Type": "application/json"},
            timeout=15.0,
        )
        if r.status_code != 200:
            return []
        return [{
            "title": res.get("title") or res.get("url") or "",
            "url": res.get("url") or "",
            "snippet": " ".join(res.get("highlights") or [])[:400],
        } for res in (r.json().get("results") or [])]
    except Exception:
        return []


def _clean_result_name(title: str, fallback: str) -> str:
    """'albinanthony (Albin Anthony) · GitHub' → 'Albin Anthony'."""
    t = (title or "").strip()
    for sep in (" | ", " — ", " – ", " • ", " · ", " :: "):
        if sep in t:
            t = t.split(sep, 1)[0].strip()
    paren = re.search(r"\(([^)]{3,60})\)", t)
    t = re.sub(r"\([^)]*\)", "", t).strip()
    t = re.sub(r"\s*-\s*(Facebook|LinkedIn|Instagram|Twitter|X|"
               r"GitHub|YouTube|TikTok|Reddit|Medium|Pinterest|Threads)"
               r"\s*$", "", t, flags=re.I).strip(" -–—")
    if paren and len(paren.group(1).split()) >= 2:
        cand = paren.group(1).strip()
        if len(t.split()) < 2 or len(cand) > len(t):
            t = cand
    t = re.sub(r"\s{2,}", " ", t).strip(" ,.-")
    if not t or len(t) > 60 or len(t.split()) > 6:
        t = fallback
    return t


def _handle_from(url: str, pattern: str) -> str:
    m = re.search(pattern, url)
    if not m:
        return ""
    for g in m.groups():
        if g:
            h = g.strip("@").strip("/")
            if h and h.lower() not in _HANDLE_BAD:
                return h
            return ""
    return ""


def _profile_from_result(name: str, res: dict[str, str]
                         ) -> dict[str, Any] | None:
    url = (res.get("url") or "").strip()
    if not url.startswith("http"):
        return None
    low = url.lower()
    for frag, label, pat in _PROFILE_HOSTS:
        if frag not in low:
            continue
        handle = _handle_from(url, pat)
        if not handle:
            return None
        facts: list[dict[str, str]] = [
            {"label": "Platform", "value": label},
            {"label": "Handle", "value": f"@{handle}"},
        ]
        return {
            "name": _clean_result_name(res.get("title") or "", name),
            "description": (res.get("snippet") or "")[:300]
            or f"{label} profile",
            "thumbnail": "",
            "url": url,
            "source": label.lower(),
            "facts": facts,
            "socials": [{"label": label, "url": url}],
        }
    return None


def _fetch_social_people(name: str) -> list[dict[str, Any]]:
    """Profile listings found via web search for ``name``."""
    results: list[dict[str, str]] = []
    for q in (f'"{name}"',
              f'"{name}" linkedin facebook instagram twitter github'):
        rs = _ddg_results(q, 8)
        if not rs:
            rs = _bing_results(q, 8)
        results += rs
    results += _exa_results(
        f'{name} (profile OR linkedin OR github OR twitter OR instagram)',
        8)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for res in results:
        u = (res.get("url") or "").rstrip("/").lower()
        if not u or u in seen:
            continue
        seen.add(u)
        card = _profile_from_result(name, res)
        if card:
            out.append(card)
        if len(out) >= 6:
            break
    return out


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
        wd = _wikidata(title)
        return {
            "name": _TAG_RE.sub("", summ.get("title") or title),
            "description": summ.get("description") or "",
            "extract": (summ.get("extract") or "")[:1000],
            "thumbnail": thumb,
            "url": content,
            "source": "wikipedia",
            "qid": wd.get("qid") or "",
            "facts": wd.get("facts") or [],
            "socials": wd.get("socials") or [],
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Wikidata enrichment — biographical facts + social handles (never invented).
# ---------------------------------------------------------------------------

_WD_API = "https://www.wikidata.org/w/api.php"
_FT_IN = 0.3048
_KG_LB = 2.20462


def _snak_value(v: Any) -> str:
    """String claims may be plain strings or monolingual {text, language}."""
    if isinstance(v, dict):
        v = v.get("text") or ""
    return v.strip() if isinstance(v, str) else ""


def _snak_str(claims: dict, prop: str) -> str:
    for cl in claims.get(prop, []):
        v = _snak_value(
            (cl.get("mainsnak", {}).get("datavalue") or {}).get("value"))
        if v:
            return v
    return ""


def _snak_strs(claims: dict, prop: str, limit: int) -> list[str]:
    out: list[str] = []
    for cl in claims.get(prop, []):
        v = _snak_value(
            (cl.get("mainsnak", {}).get("datavalue") or {}).get("value"))
        if v:
            out.append(v)
        if len(out) >= limit:
            break
    return out


def _snak_qids(claims: dict, prop: str, limit: int) -> list[str]:
    ids: list[str] = []
    for cl in claims.get(prop, []):
        v = (cl.get("mainsnak", {}).get("datavalue") or {}).get("value")
        qid = v.get("id") if isinstance(v, dict) else ""
        if qid:
            ids.append(qid)
        if len(ids) >= limit:
            break
    return ids


def _snak_qty(claims: dict, prop: str) -> tuple[float, str] | None:
    """(amount, unit-qid) for the first quantity claim, if any."""
    for cl in claims.get(prop, []):
        v = (cl.get("mainsnak", {}).get("datavalue") or {}).get("value")
        amt = v.get("amount") if isinstance(v, dict) else None
        if amt:
            try:
                unit = str(v.get("unit") or "").rsplit("/", 1)[-1]
                return float(str(amt).lstrip("+")), unit
            except ValueError:
                continue
    return None


# Unit conversions — Wikidata may state height in centimetres etc.
_M_FACTOR = {"Q11573": 1.0, "Q174728": 0.01, "Q200323": 0.1,
             "Q174789": 0.001, "Q218593": 0.0254, "Q3710": 0.3048}
_KG_FACTOR = {"Q11570": 1.0, "Q100995": 0.453592, "Q483261": 0.0283495,
              "Q23813": 6.35029}


def _snak_time(claims: dict, prop: str) -> str:
    for cl in claims.get(prop, []):
        v = (cl.get("mainsnak", {}).get("datavalue") or {}).get("value")
        t = v.get("time") if isinstance(v, dict) else ""
        if isinstance(t, str) and t:
            return t
    return ""


def _fmt_date(wd_time: str) -> str:
    """'+1942-01-17T00:00:00Z' → 'Jan 17, 1942'."""
    try:
        import datetime as _dt

        y, m, d = wd_time.lstrip("+").split("T", 1)[0].split("-")[:3]
        dt = _dt.date(int(y), int(m), int(d))
        return f"{dt.strftime('%b')} {dt.day}, {dt.year}"
    except Exception:
        return ""


def _age(born_wd: str) -> int | None:
    try:
        import datetime as _dt

        y, m, d = born_wd.lstrip("+").split("T", 1)[0].split("-")[:3]
        b = _dt.date(int(y), int(m), int(d))
        today = _dt.date.today()
        return today.year - b.year - ((today.month, today.day) < (b.month, b.day))
    except Exception:
        return None


def _wd_get(params: dict) -> requests.Response | None:
    """Wikidata GET with one retry — transient failures are common on
    filtered networks and must not blank the whole card."""
    for _ in range(2):
        try:
            r = requests.get(_WD_API, params=params, headers=_UA,
                             timeout=6.0)
            if r.status_code == 200:
                return r
        except Exception:
            continue
    return None


def _labels(qids: list[str]) -> dict[str, str]:
    if not qids:
        return {}
    try:
        r = _wd_get({"action": "wbgetentities", "ids": "|".join(qids),
                     "props": "labels", "languages": "en",
                     "format": "json"})
        if r is None:
            return {}
        out: dict[str, str] = {}
        for qid, ent in r.json().get("entities", {}).items():
            lab = (ent.get("labels", {}).get("en") or {}).get("value", "")
            if lab:
                out[qid] = lab
        return out
    except Exception:
        return {}


# Label-bearing claim properties, grouped for the dossier sections.
_WD_IDENTITY_QIDS = [("P19", 1), ("P20", 1), ("P27", 2)]
_WD_PHYSICAL_QIDS = [("P1340", 1), ("P1884", 1)]
_WD_RECORD_QIDS = [
    ("P106", 3),   # occupation
    ("P39", 3),    # position held
    ("P69", 2),    # educated at
    ("P108", 2),   # employer
    ("P551", 2),   # residence
    ("P26", 2),    # spouse
    ("P1412", 3),  # languages
    ("P166", 3),   # awards received
    ("P800", 3),   # notable work
]


def _wikidata(title: str) -> dict[str, Any]:
    """Resolve the enwiki article to its Wikidata entity and extract a
    dossier — grouped biographical facts + social links. Best-effort:
    any failure returns an empty dict so the base card is unaffected."""
    try:
        r = _wd_get({"action": "wbgetentities", "sites": "enwiki",
                     "titles": title, "props": "claims", "format": "json",
                     "normalize": "1"})
        if r is None:
            return {}
        ents = r.json().get("entities", {})
        ent = next(iter(ents.values()), {})
        claims = ent.get("claims", {}) if isinstance(ent, dict) else {}
        if not claims:
            return {}
        qid = str(ent.get("id") or "")

        qids: list[str] = []
        for prop, limit in (_WD_IDENTITY_QIDS + _WD_PHYSICAL_QIDS
                            + _WD_RECORD_QIDS):
            qids += _snak_qids(claims, prop, limit)
        labs = _labels(sorted(set(qids)))

        def _lab(prop: str, limit: int) -> str:
            return ", ".join(
                labs[q] for q in _snak_qids(claims, prop, limit)
                if labs.get(q))

        facts: list[dict[str, str]] = []

        def _fact(group: str, label: str, value: str) -> None:
            if value:
                facts.append({"group": group, "label": label,
                              "value": value})

        born = _snak_time(claims, "P569")
        died = _snak_time(claims, "P570")
        if born:
            bd = _fmt_date(born)
            if bd and not died:
                a = _age(born)
                if a is not None and 0 < a < 130:
                    bd += f" (age {a})"
            _fact("identity", "Born", bd)
        if died:
            _fact("identity", "Died", _fmt_date(died))
        _fact("identity", "Birth name", _snak_str(claims, "P1477"))
        _fact("identity", "Aliases",
              ", ".join(_snak_strs(claims, "P1449", 3)))
        _fact("identity", "Birthplace", _lab("P19", 1))
        _fact("identity", "Place of death", _lab("P20", 1))
        _fact("identity", "Citizenship", _lab("P27", 2))

        hq = _snak_qty(claims, "P2048")
        if hq:
            h = hq[0] * _M_FACTOR.get(hq[1], 1.0)
            if 0.3 < h < 3.0:
                inches = h / _FT_IN * 12
                _fact("physical", "Height",
                      f"{h:.2f} m ({int(inches // 12)} ft {round(inches % 12)} in)")
        wq = _snak_qty(claims, "P2067")
        if wq:
            w = wq[0] * _KG_FACTOR.get(wq[1], 1.0)
            if 1 < w < 1000:
                _fact("physical", "Weight",
                      f"{round(w)} kg ({round(w * _KG_LB)} lb)")
        _fact("physical", "Eye color", _lab("P1340", 1))
        _fact("physical", "Hair color", _lab("P1884", 1))

        _fact("record", "Occupation", _lab("P106", 3))
        _fact("record", "Positions", _lab("P39", 3))
        _fact("record", "Education", _lab("P69", 2))
        _fact("record", "Employer", _lab("P108", 2))
        _fact("record", "Residence", _lab("P551", 2))
        _fact("record", "Spouse", _lab("P26", 2))
        children = len(_snak_qids(claims, "P40", 20))
        if children:
            _fact("record", "Children", str(children))
        _fact("record", "Languages", _lab("P1412", 3))
        _fact("record", "Awards", _lab("P166", 3))
        _fact("record", "Notable works", _lab("P800", 3))

        socials: list[dict[str, str]] = []

        def _social(label: str, url: str) -> None:
            if url:
                socials.append({"label": label, "url": url})

        tw = _snak_str(claims, "P2002")
        _social("X", f"https://x.com/{tw}" if tw else "")
        ig = _snak_str(claims, "P2003")
        _social("Instagram", f"https://instagram.com/{ig}" if ig else "")
        fb = _snak_str(claims, "P2013")
        _social("Facebook", f"https://facebook.com/{fb}" if fb else "")
        yt = _snak_str(claims, "P2397")
        _social("YouTube", f"https://youtube.com/channel/{yt}" if yt else "")
        web = _snak_str(claims, "P856")
        _social("Website", web)

        return {"qid": qid, "facts": facts, "socials": socials}
    except Exception:
        return {}
