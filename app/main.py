"""iChat Gateway application entrypoint."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from app.api.admin_routes import router as admin_router
from app.api.artifact_routes import router as artifact_router
from app.api.connector_routes import router as connector_router
from app.api.admin_routes import user_router as admin_user_router
from app.api.agent_routes import admin_router as agent_admin_router
from app.api.agent_routes import admin_read_router as agent_admin_read_router
from app.api.agent_routes import chat_router as agent_chat_router
from app.api.auth_routes import router as auth_router
from app.api.character_routes import router as character_router
from app.api.group_routes import public_router as share_public_router
from app.api.group_routes import router as group_router
from app.api.knowledge_routes import router as knowledge_router
from app.api.openai_routes import router as openai_router
from app.api.payment_routes import router as payment_router
from app.api.person_routes import router as person_router
from app.api.research_routes import router as research_router
from app.api.research_v2_routes import router as research_v2_router
from app.api.roleplay_routes import router as roleplay_router
from app.api.skill_routes import router as skill_router
from app.config import BASE_DIR, get_settings
from app.db import close_db, get_session_factory, init_db
from app.gateway import Gateway
from app.logging_config import setup_logging
from app.providers.registry import seed_providers
from app.web.pages import VersionedStaticFiles, render_page
from app.web.seo import SITEMAP_PAGES, public_origin, web_manifest


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    if settings.admin_token == "admin-change-me":
        import logging

        logging.getLogger("nova").warning(
            "ADMIN_TOKEN is the default 'admin-change-me' — set a real token in .env"
        )
    await init_db()
    async with get_session_factory()() as session:
        # Runs left 'running' by a previous process can never finish —
        # their tasks died with it. Mark them interrupted so the ledger
        # doesn't show phantom live runs.
        from sqlalchemy import update as _upd

        from app.models import AgentRun
        await session.execute(
            _upd(AgentRun)
            .where(AgentRun.status == "running")
            .values(status="interrupted")
        )
        await session.commit()
        await seed_providers(session)
        from app.services.characters import seed_if_empty
        from app.services.ollama import auto_scan_ollama

        await seed_if_empty(session)
        await auto_scan_ollama(session)
        from app.services.experts import seed_experts

        seeded = await seed_experts(session)
        if seeded:
            import logging as _log

            _log.getLogger("nova").info(
                "Seeded/refreshed %d built-in expert agents", seeded
            )
        from app.services.agency_agents import seed_agency_agents

        try:
            n_agency = await seed_agency_agents(session)
            if n_agency:
                _log.getLogger("nova").info(
                    "Seeded/refreshed %d agency-agents catalog entries",
                    n_agency,
                )
        except Exception:
            _log.getLogger("nova").exception("agency-agents seed failed")
        from app.services.skills import load_custom_skills, \
            load_packaged_skills

        try:
            n_packaged = load_packaged_skills()
            if n_packaged:
                _log.getLogger("nova").info(
                    "Loaded %d packaged skills (anthropics/skills)",
                    n_packaged,
                )
        except Exception:
            _log.getLogger("nova").exception("packaged skill load failed")
        await load_custom_skills(session)
    app.state.gateway = Gateway()
    # iChat v2: durable research runner + evidence vector index.
    from app.services import evidence_store as _ev
    from app.services import research_v2 as _rv2
    from app.services.jobqueue import JobRunner

    _ev.ensure_vector_collection()
    _rv2.configure(app.state.gateway)
    runner = JobRunner(get_session_factory())
    runner_task = None
    if settings.research_worker_enabled:
        runner_task = asyncio.ensure_future(runner.run_forever())
    yield
    runner.stop()
    if runner_task is not None:
        runner_task.cancel()
        try:
            await runner_task
        except (asyncio.CancelledError, Exception):
            pass
    await app.state.gateway.close()
    await close_db()


app = FastAPI(
    title="iChat Gateway",
    version="0.1.0",
    description="Unified OpenAI-compatible gateway over free-tier LLM providers.",
    lifespan=lifespan,
)

class PageGZipMiddleware:
    """Gzip HTML pages, static assets and SEO files only.

    API routes (including SSE chat streams) are passed through untouched so
    streaming is never buffered by compression.
    """

    _PREFIXES = ("/static/", "/sitemap.xml", "/robots.txt", "/site.webmanifest")

    def __init__(self, app, minimum_size: int = 1024) -> None:
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") in ("GET", "HEAD"):
            path = scope.get("path", "")
            is_page = "." not in path.rsplit("/", 1)[-1] and not path.startswith(
                ("/v1", "/admin", "/api", "/media", "/healthz", "/docs", "/openapi"))
            if path.startswith(self._PREFIXES) or is_page:
                await self.gzip(scope, receive, send)
                return
        await self.app(scope, receive, send)


app.add_middleware(PageGZipMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_html(request, call_next):
    """HTML pages must revalidate — heuristic caching serves stale UI
    after deploys. Static assets keep normal caching."""
    resp = await call_next(request)
    if "text/html" in resp.headers.get("content-type", ""):
        resp.headers["Cache-Control"] = "no-cache"
    return resp

app.include_router(openai_router)
app.include_router(payment_router)
app.include_router(admin_router)
app.include_router(admin_user_router)
app.include_router(auth_router)
app.include_router(character_router)
app.include_router(person_router)
app.include_router(agent_admin_router)
app.include_router(agent_admin_read_router)
app.include_router(agent_chat_router)
app.include_router(group_router)
app.include_router(research_router)
app.include_router(research_v2_router)
app.include_router(roleplay_router)
app.include_router(knowledge_router)
app.include_router(skill_router)
app.include_router(share_public_router)
app.include_router(artifact_router)
app.include_router(connector_router)


# Inbound rate limiting — per client key/IP, generous defaults.
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address,
                  default_limits=["300/minute"], enabled=True)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limited(request, exc):
    return JSONResponse(status_code=429, content={
        "error": {"message": "Rate limit exceeded — slow down and retry",
                  "type": "rate_limit_error"}})

static_dir = BASE_DIR / "app" / "static"
static_dir.mkdir(parents=True, exist_ok=True)


class RevalidatingStaticFiles(VersionedStaticFiles):
    """Static files that browsers must revalidate on every load.

    ETag/Last-Modified still yield cheap 304s, but edits to JS/CSS are picked
    up immediately instead of being masked by heuristic browser caching.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", RevalidatingStaticFiles(directory=static_dir), name="static")


@app.get("/media/{fname}", include_in_schema=False)
async def media_file(fname: str):
    """Generated media (TTS audio etc.) stored under data/media."""
    from app.config import get_settings

    if "/" in fname or "\\" in fname or fname.startswith("."):
        return JSONResponse({"error": "bad name"}, status_code=400)
    media_dir = (
        Path(get_settings().database_url.split("///")[-1]).parent / "media"
    )
    path = media_dir / fname
    if not path.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path)


@app.get("/", include_in_schema=False)
async def landing(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="landing.html", request=request)


@app.get("/dashboard", include_in_schema=False)
async def dashboard(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="dashboard.html", request=request)


@app.get("/chat", include_in_schema=False)
async def chat(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="chat.html", request=request)


@app.get("/keys", include_in_schema=False)
async def keys_page(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="keys.html", request=request)


@app.get("/settings", include_in_schema=False)
async def settings_page(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="settings.html", request=request)


@app.get("/profile", include_in_schema=False)
async def profile_page(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="profile.html", request=request)


@app.get("/research", include_in_schema=False)
async def research_page(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="research.html", request=request)


@app.get("/roleplay", include_in_schema=False)
async def roleplay_page(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="worlds.html", request=request)


@app.get("/roleplay/scene", include_in_schema=False)
async def roleplay_scene_page(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="roleplay.html", request=request)


@app.get("/agents", include_in_schema=False)
async def agents_page(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="agents.html", request=request)


@app.get("/skills", include_in_schema=False)
async def skills_page(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="skills.html", request=request)


@app.get("/library", include_in_schema=False)
async def library_page(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="library.html", request=request)


@app.get("/memory", include_in_schema=False)
async def memory_page(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="memory.html", request=request)


@app.get("/artifacts", include_in_schema=False)
async def artifacts_page(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="artifacts.html", request=request)


@app.get("/connectors", include_in_schema=False)
async def connectors_page(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="connectors.html", request=request)


@app.get("/pay", include_in_schema=False)
async def pay_page(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="pay.html", request=request)


@app.get("/textbook/{run_id}", include_in_schema=False)
async def textbook_page(run_id: int, request: Request) -> Response:
    return render_page(static_dir=static_dir, name="textbook.html", request=request)


@app.get("/share/{share_id}", include_in_schema=False)
async def share_page(share_id: str, request: Request) -> Response:
    return render_page(static_dir=static_dir, name="share.html", request=request)


@app.get("/login", include_in_schema=False)
async def login_page(request: Request) -> Response:
    return render_page(static_dir=static_dir, name="login.html", request=request)


# App pages and APIs crawlers should skip.
ROBOTS_DISALLOW: tuple[str, ...] = (
    "/v1/", "/admin", "/api/", "/media/", "/share/", "/textbook/",
    "/dashboard", "/chat", "/keys", "/settings", "/profile", "/research",
    "/roleplay", "/agents", "/skills", "/library", "/memory", "/artifacts",
    "/connectors", "/pay",
)


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap(request: Request) -> Response:
    from datetime import datetime, timezone
    from xml.sax.saxutils import escape

    origin = public_origin(request)
    entries = []
    for path, template, changefreq, priority in SITEMAP_PAGES:
        source = static_dir / template
        lastmod = ""
        if source.is_file():
            stamp = datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc)
            lastmod = f"<lastmod>{stamp.date().isoformat()}</lastmod>"
        entries.append(
            f"<url><loc>{escape(origin + path)}</loc>{lastmod}"
            f"<changefreq>{changefreq}</changefreq>"
            f"<priority>{priority}</priority></url>"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(entries) + "</urlset>\n"
    )
    return Response(body, media_type="application/xml",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/robots.txt", include_in_schema=False)
async def robots(request: Request) -> Response:
    lines = ["User-agent: *", "Allow: /$", "Allow: /login", "Allow: /static/"]
    lines += [f"Disallow: {path}" for path in ROBOTS_DISALLOW]
    lines += ["", f"Sitemap: {public_origin(request)}/sitemap.xml", ""]
    return Response("\n".join(lines), media_type="text/plain",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/site.webmanifest", include_in_schema=False)
async def site_manifest() -> Response:
    return JSONResponse(web_manifest(), media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/sw.js", include_in_schema=False)
async def service_worker() -> Response:
    return FileResponse(static_dir / "sw.js", media_type="text/javascript",
                        headers={"Service-Worker-Allowed": "/",
                                 "Cache-Control": "no-cache"})


@app.get("/apple-touch-icon.png", include_in_schema=False)
async def apple_touch_icon() -> Response:
    return FileResponse(static_dir / "img" / "ichat-apple-touch-v3.png",
                        media_type="image/png",
                        headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
