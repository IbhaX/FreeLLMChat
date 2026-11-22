"""GitHub profile lookup for the user profile page.

Fetches the public ``/users/{login}`` profile plus the account's repos and
surfaces the top starred ones. Results are cached briefly; lookups are
best-effort and never log usernames at error level beyond the login itself.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_CACHE_TTL = 600.0
_CACHE_MAX = 200

_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "nova-gateway-profile/1.0",
    "X-GitHub-Api-Version": "2022-11-28",
}
_TIMEOUT = 8.0


async def lookup_github(username: str) -> dict[str, Any] | None:
    """Return a GitHub profile payload for ``username`` or None.

    None covers both "user not found" and "GitHub unreachable" — the
    route renders the same empty state either way."""
    login = (username or "").strip().lstrip("@")
    if not login or len(login) > 39:
        return None
    key = login.lower()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    payload = await _fetch(login)
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = (time.time(), payload)
    return payload


async def _fetch(login: str) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(
                timeout=_TIMEOUT, headers=_HEADERS,
                follow_redirects=True) as client:
            r = await client.get(f"https://api.github.com/users/{login}")
            if r.status_code != 200:
                return None
            u = r.json()
            rr = await client.get(
                f"https://api.github.com/users/{login}/repos",
                params={"per_page": 100, "sort": "updated",
                        "type": "owner"})
            repos = rr.json() if rr.status_code == 200 else []
    except httpx.HTTPError:
        return None

    top = sorted(
        (repo for repo in repos if isinstance(repo, dict)
         and not repo.get("fork")),
        key=lambda repo: -int(repo.get("stargazers_count") or 0))[:6]
    return {
        "login": u.get("login") or login,
        "name": u.get("name") or "",
        "avatar_url": u.get("avatar_url") or "",
        "bio": u.get("bio") or "",
        "company": u.get("company") or "",
        "location": u.get("location") or "",
        "blog": u.get("blog") or "",
        "twitter_username": u.get("twitter_username") or "",
        "followers": u.get("followers") or 0,
        "following": u.get("following") or 0,
        "public_repos": u.get("public_repos") or 0,
        "public_gists": u.get("public_gists") or 0,
        "html_url": u.get("html_url") or f"https://github.com/{login}",
        "created_at": u.get("created_at") or "",
        "repos": [{
            "name": repo.get("name") or "",
            "description": repo.get("description") or "",
            "html_url": repo.get("html_url") or "",
            "stars": repo.get("stargazers_count") or 0,
            "forks": repo.get("forks_count") or 0,
            "watchers": repo.get("watchers_count") or 0,
            "open_issues": repo.get("open_issues_count") or 0,
            "language": repo.get("language") or "",
            "default_branch": repo.get("default_branch") or "",
            "license": (repo.get("license") or {}).get("name") or "",
            "topics": [t for t in (repo.get("topics") or [])][:8],
            "homepage": repo.get("homepage") or "",
            "created_at": repo.get("created_at") or "",
            "updated_at": repo.get("updated_at") or "",
            "pushed_at": repo.get("pushed_at") or "",
            "archived": bool(repo.get("archived")),
        } for repo in top],
    }
