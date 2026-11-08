"""Provider registry: loads providers.yaml, seeds the DB, builds adapters."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Provider
from app.providers.base import BaseAdapter
from app.providers.cloudflare import CloudflareAdapter
from app.providers.gemini import GeminiAdapter
from app.providers.openai_compat import OpenAICompatAdapter

ADAPTERS: dict[str, type[BaseAdapter]] = {
    "openai_compat": OpenAICompatAdapter,
    "gemini": GeminiAdapter,
    "cloudflare": CloudflareAdapter,
}


@dataclass(frozen=True)
class AdapterContext:
    provider: Provider
    adapter: BaseAdapter


def build_adapter(provider: Provider) -> BaseAdapter:
    cls = ADAPTERS.get(provider.adapter, OpenAICompatAdapter)
    return cls(provider)


def load_provider_seed() -> dict[str, Any]:
    path = get_settings().providers_yaml
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {"providers": [], "fallback": {}}


def _env_value(name: str) -> str:
    """Look up `name` in os.environ, falling back to the .env file."""
    val = os.environ.get(name, "").strip()
    if val:
        return val
    env_file = get_settings().model_config.get("env_file")
    if env_file:
        from pathlib import Path

        path = Path(env_file)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def load_fallback_chains() -> dict[str, list[str]]:
    return dict(load_provider_seed().get("fallback", {}))


async def seed_providers(session: AsyncSession) -> int:
    """Upsert providers.yaml entries into the DB. Returns upserted count."""
    seed = load_provider_seed()
    count = 0
    for entry in seed.get("providers", []):
        name = entry["name"]
        existing = (
            await session.execute(select(Provider).where(Provider.name == name))
        ).scalar_one_or_none()
        fields = {
            "display_name": entry.get("display_name", name),
            "adapter": entry.get("adapter", "openai_compat"),
            "base_url": entry["base_url"],
            "auth_style": entry.get("auth_style", "bearer"),
            "auth_header": entry.get("auth_header", "Authorization"),
            "extra_headers": entry.get("extra_headers", {}),
            "default_env_key": entry.get("default_env_key"),
            "default_models": entry.get("default_models", []),
            "limits": entry.get("limits", {}),
            "daily_reset_tz": entry.get("daily_reset_tz"),
            "requires_card": entry.get("requires_card", False),
            "commercial_ok": entry.get("commercial_ok", True),
            "rotation_strategy": entry.get("rotation_strategy", "least_used"),
            "fallback_chain": entry.get("fallback_chain", []),
            "auto_route_tags": entry.get("auto_route_tags", []),
            "docs_url": entry.get("docs_url"),
            "notes": entry.get("notes"),
            "verified_on": entry.get("verified_on"),
            "account_id_env": entry.get("account_id_env"),
        }
        if existing is None:
            session.add(Provider(name=name, **fields))
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
        count += 1
    await session.commit()

    # Import env-var keys for providers that have none yet.
    from app.services.keys import add_key

    from app.models import ApiKey

    for provider in (await session.execute(select(Provider))).scalars().all():
        if not provider.default_env_key:
            continue
        has_keys = (
            await session.execute(
                select(ApiKey.id).where(ApiKey.provider_id == provider.id).limit(1)
            )
        ).first()
        if has_keys:
            continue
        raw = _env_value(provider.default_env_key)
        for i, single in enumerate(k.strip() for k in raw.split(",") if k.strip()):
            await add_key(session, provider, single, label=f"env-{i + 1}")
    await session.commit()
    return count
