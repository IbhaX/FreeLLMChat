"""Nova Gateway application entrypoint."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from app.api.admin_routes import router as admin_router
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
        from app.services.skills import load_custom_skills

        await load_custom_skills(session)
    app.state.gateway = Gateway()
    # Nova v2: durable research runner + evidence vector index.
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
    title="Nova Gateway",
    version="0.1.0",
    description="Unified OpenAI-compatible gateway over free-tier LLM providers.",
    lifespan=lifespan,
)

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
async def landing() -> Response:
    return render_page(static_dir=static_dir, name="landing.html")


@app.get("/dashboard", include_in_schema=False)
async def dashboard() -> Response:
    return render_page(static_dir=static_dir, name="dashboard.html")


@app.get("/chat", include_in_schema=False)
async def chat() -> Response:
    return render_page(static_dir=static_dir, name="chat.html")


@app.get("/keys", include_in_schema=False)
async def keys_page() -> Response:
    return render_page(static_dir=static_dir, name="keys.html")


@app.get("/settings", include_in_schema=False)
async def settings_page() -> Response:
    return render_page(static_dir=static_dir, name="settings.html")


@app.get("/profile", include_in_schema=False)
async def profile_page() -> Response:
    return render_page(static_dir=static_dir, name="profile.html")


@app.get("/research", include_in_schema=False)
async def research_page() -> Response:
    return render_page(static_dir=static_dir, name="research.html")


@app.get("/roleplay", include_in_schema=False)
async def roleplay_page() -> Response:
    return render_page(static_dir=static_dir, name="roleplay.html")


@app.get("/agents", include_in_schema=False)
async def agents_page() -> Response:
    return render_page(static_dir=static_dir, name="agents.html")


@app.get("/skills", include_in_schema=False)
async def skills_page() -> Response:
    return render_page(static_dir=static_dir, name="skills.html")


@app.get("/library", include_in_schema=False)
async def library_page() -> Response:
    return render_page(static_dir=static_dir, name="library.html")


@app.get("/memory", include_in_schema=False)
async def memory_page() -> Response:
    return render_page(static_dir=static_dir, name="memory.html")


@app.get("/textbook/{run_id}", include_in_schema=False)
async def textbook_page(run_id: int) -> Response:
    return render_page(static_dir=static_dir, name="textbook.html")


@app.get("/share/{share_id}", include_in_schema=False)
async def share_page(share_id: str) -> Response:
    return render_page(static_dir=static_dir, name="share.html")


@app.get("/login", include_in_schema=False)
async def login_page() -> Response:
    return render_page(static_dir=static_dir, name="login.html")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
