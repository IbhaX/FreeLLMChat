"""Shared network-safety helpers: SSRF blocking and redirect-safe fetches.

Single source of truth for "is this URL safe to fetch". Both the chat tool
loop (``app.services.tools``) and the v2 research tool service go through
this module.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx

_BLOCKED_HOST_RE = re.compile(
    r"^(localhost|127\.|0\.0\.0\.0|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|"
    r"169\.254\.|::1|metadata\.google|internal|.*\.local)$",
    re.IGNORECASE,
)

# Only these schemes may ever be fetched.
ALLOWED_SCHEMES = ("http", "https")


def ssrf_blocked(url: str) -> bool:
    """True if the URL targets a private/internal address or is malformed."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return True
    if not host or _BLOCKED_HOST_RE.match(host) or "%" in host:
        return True
    try:
        address = ipaddress.ip_address(host)
        return not address.is_global
    except ValueError:
        pass
    # Reject hostnames resolving to any non-public address before connecting.
    # Redirects are revalidated at every hop; DNS pinning is still best handled
    # by an outbound proxy/firewall in production to prevent rebinding races.
    try:
        for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
            if not ipaddress.ip_address(info[4][0].split("%", 1)[0]).is_global:
                return True
    except (OSError, ValueError):
        return True
    return False


def safe_url(url: str) -> bool:
    """True when the URL is http(s) and not SSRF-blocked."""
    if not url.startswith(ALLOWED_SCHEMES):
        return False
    return not ssrf_blocked(url)


async def fetch_following_redirects(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_hops: int = 3,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """GET ``url`` following redirects manually, re-checking SSRF at each hop.

    Raises ``UnsafeRedirect`` when a hop targets a blocked address and
    ``TooManyRedirects`` past ``max_hops``. Returns the final response.
    """
    hops = 0
    while True:
        response = await client.get(url, headers=headers or {})
        if response.status_code not in (301, 302, 303, 307, 308):
            return response
        location = response.headers.get("location", "")
        nxt = urljoin(str(response.url), location)
        if not location or not safe_url(nxt):
            raise UnsafeRedirect(nxt or "<empty location>")
        url = nxt
        hops += 1
        if hops > max_hops:
            raise TooManyRedirects(max_hops)


class UnsafeRedirect(Exception):
    pass


class TooManyRedirects(Exception):
    pass
