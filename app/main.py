"""FreeLLM Gateway application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.admin_routes import router as admin_router
from app.api.agent_routes import admin_router as agent_admin_router
from app.api.agent_routes import chat_router as agent_chat_router
from app.api.openai_routes import router as openai_router
from app.config import BASE_DIR, get_settings
from app.db import close_db, get_session_factory, init_db
from app.gateway import Gateway
from app.logging_config import setup_logging
from app.providers.registry import seed_providers


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    if settings.admin_token == "admin-change-me":
        import logging

        logging.getLogger("freellm").warning(
            "ADMIN_TOKEN is the default 'admin-change-me' — set a real token in .env"
        )
    if not settings.gateway_api_key:
        import logging

        logging.getLogger("freellm").info(
            "GATEWAY_API_KEY not set — /v1 endpoints are unauthenticated"
        )
    await init_db()
    async with get_session_factory()() as session:
        await seed_providers(session)
    app.state.gateway = Gateway()
    yield
    await app.state.gateway.close()
    await close_db()


app = FastAPI(
    title="FreeLLM Gateway",
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

app.include_router(openai_router)
app.include_router(admin_router)
app.include_router(agent_admin_router)
app.include_router(agent_chat_router)

static_dir = BASE_DIR / "app" / "static"
static_dir.mkdir(parents=True, exist_ok=True)


class RevalidatingStaticFiles(StaticFiles):
    """Static files that browsers must revalidate on every load.

    ETag/Last-Modified still yield cheap 304s, but edits to JS/CSS are picked
    up immediately instead of being masked by heuristic browser caching.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", RevalidatingStaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(static_dir / "dashboard.html")


@app.get("/chat", include_in_schema=False)
async def chat():
    return FileResponse(static_dir / "chat.html")


@app.get("/keys", include_in_schema=False)
async def keys_page():
    return FileResponse(static_dir / "keys.html")


@app.get("/settings", include_in_schema=False)
async def settings_page():
    return FileResponse(static_dir / "settings.html")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
