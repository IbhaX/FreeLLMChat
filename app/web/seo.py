"""Search-engine and social metadata for every server-rendered page.

`render_page` calls :func:`seo_head` and injects the result into `<head>`,
so each page gets a consistent title, description, robots directive,
canonical URL, Open Graph / Twitter card tags and, for the landing page,
JSON-LD structured data. Only pages marked ``indexable`` are offered to
search engines; everything behind sign-in is ``noindex``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape

from starlette.requests import Request

SITE_NAME = "IbhaX"
DEFAULT_DESCRIPTION = (
    "IbhaX is one AI workspace for every model: automatic failover across "
    "providers, rich structured answers, deep research, agents and roleplay."
)
OG_IMAGE = "/static/img/logo.png"          # 1200x800
OG_IMAGE_WIDTH, OG_IMAGE_HEIGHT = 1200, 800
THEME_COLOR = "#080b14"


@dataclass(frozen=True)
class PageSEO:
    title: str
    description: str = DEFAULT_DESCRIPTION
    indexable: bool = False


PAGES: dict[str, PageSEO] = {
    "landing.html": PageSEO(
        "IbhaX - Every AI model in one workspace",
        "Chat with every leading AI model in one workspace. IbhaX routes "
        "across providers with automatic failover, renders rich structured "
        "answers, and runs deep research, agents and roleplay.",
        indexable=True,
    ),
    "login.html": PageSEO(
        "Sign in - IbhaX",
        "Sign in or create your IbhaX account to chat with every AI model, "
        "run deep research and build agents in one workspace.",
        indexable=True,
    ),
    "chat.html": PageSEO("Chat - IbhaX"),
    "dashboard.html": PageSEO("Dashboard - IbhaX"),
    "keys.html": PageSEO("API Keys - IbhaX"),
    "settings.html": PageSEO("Settings - IbhaX"),
    "profile.html": PageSEO("Profile - IbhaX"),
    "research.html": PageSEO("Research - IbhaX"),
    "worlds.html": PageSEO("Worlds - IbhaX"),
    "roleplay.html": PageSEO("The Stage - IbhaX"),
    "agents.html": PageSEO("Agents - IbhaX"),
    "skills.html": PageSEO("Skills - IbhaX"),
    "library.html": PageSEO("Library - IbhaX"),
    "memory.html": PageSEO("Memory - IbhaX"),
    "artifacts.html": PageSEO("Artifactory - IbhaX"),
    "connectors.html": PageSEO("Connectors - IbhaX"),
    "pay.html": PageSEO("Billing - IbhaX"),
    "textbook.html": PageSEO("Textbook - IbhaX Research"),
    "share.html": PageSEO(
        "Shared conversation - IbhaX",
        "A conversation shared from IbhaX, the AI workspace for every model.",
    ),
}

# Public paths listed in sitemap.xml: (path, template, changefreq, priority).
SITEMAP_PAGES: tuple[tuple[str, str, str, str], ...] = (
    ("/", "landing.html", "weekly", "1.0"),
    ("/login", "login.html", "monthly", "0.5"),
)


def page_seo(name: str) -> PageSEO:
    return PAGES.get(name) or PageSEO(f"{SITE_NAME}")


def public_origin(request: Request | None) -> str:
    """Canonical origin: PUBLIC_BASE_URL, else the (proxied) request host."""
    from app.config import get_settings

    configured = get_settings().public_base_url
    if configured:
        return configured.rstrip("/")
    if request is None:
        return ""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get(
        "host", request.url.netloc)
    return f"{proto.split(',')[0].strip()}://{host.split(',')[0].strip()}"


def robots_header(name: str) -> str | None:
    """Value for the X-Robots-Tag header, or None for indexable pages."""
    return None if page_seo(name).indexable else "noindex, nofollow"


def _structured_data(origin: str, seo: PageSEO) -> str:
    graph = [
        {
            "@type": "Organization",
            "@id": f"{origin}/#org",
            "name": SITE_NAME,
            "url": f"{origin}/",
            "logo": f"{origin}/static/img/logo-mark.png",
        },
        {
            "@type": "WebSite",
            "@id": f"{origin}/#website",
            "name": SITE_NAME,
            "url": f"{origin}/",
            "publisher": {"@id": f"{origin}/#org"},
        },
        {
            "@type": "SoftwareApplication",
            "name": SITE_NAME,
            "url": f"{origin}/",
            "applicationCategory": "BusinessApplication",
            "operatingSystem": "Web",
            "description": seo.description,
            "publisher": {"@id": f"{origin}/#org"},
        },
    ]
    payload = json.dumps({"@context": "https://schema.org", "@graph": graph},
                         separators=(",", ":"))
    # Keep the JSON from closing the script element.
    payload = payload.replace("</", "<\\/")
    return f'<script type="application/ld+json">{payload}</script>'


def seo_head(name: str, request: Request | None = None) -> str:
    """Build the metadata block for page `name` (without <title>)."""
    seo = page_seo(name)
    origin = public_origin(request)
    path = request.url.path if request is not None else "/"
    url = f"{origin}{path}" if origin else path
    image = f"{origin}{OG_IMAGE}" if origin else OG_IMAGE
    e = lambda value: escape(value, quote=True)  # noqa: E731

    tags = [
        f'<meta name="description" content="{e(seo.description)}">',
        '<meta name="robots" content="'
        + ("index, follow, max-image-preview:large" if seo.indexable
           else "noindex, nofollow") + '">',
        f'<meta name="theme-color" content="{THEME_COLOR}">',
        f'<meta name="application-name" content="{SITE_NAME}">',
        f'<meta name="apple-mobile-web-app-title" content="{SITE_NAME}">',
        '<link rel="apple-touch-icon" href="/static/img/logo-mark.png">',
        '<link rel="manifest" href="/site.webmanifest">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="{SITE_NAME}">',
        f'<meta property="og:title" content="{e(seo.title)}">',
        f'<meta property="og:description" content="{e(seo.description)}">',
        f'<meta property="og:url" content="{e(url)}">',
        f'<meta property="og:image" content="{e(image)}">',
        f'<meta property="og:image:width" content="{OG_IMAGE_WIDTH}">',
        f'<meta property="og:image:height" content="{OG_IMAGE_HEIGHT}">',
        f'<meta property="og:image:alt" content="{SITE_NAME} logo">',
        '<meta property="og:locale" content="en_US">',
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{e(seo.title)}">',
        f'<meta name="twitter:description" content="{e(seo.description)}">',
        f'<meta name="twitter:image" content="{e(image)}">',
    ]
    if seo.indexable:
        tags.append(f'<link rel="canonical" href="{e(url)}">')
    if name == "landing.html" and origin:
        tags.append(_structured_data(origin, seo))
    return "".join(tags)


def web_manifest() -> dict:
    return {
        "name": SITE_NAME,
        "short_name": SITE_NAME,
        "description": DEFAULT_DESCRIPTION,
        "start_url": "/chat",
        "scope": "/",
        "display": "standalone",
        "background_color": THEME_COLOR,
        "theme_color": THEME_COLOR,
        "icons": [
            {"src": "/static/img/favicon.png", "sizes": "64x64", "type": "image/png"},
            {"src": "/static/img/logo-mark.png", "sizes": "256x256",
             "type": "image/png", "purpose": "any"},
        ],
    }
