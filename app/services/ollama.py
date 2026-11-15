"""Ollama auto-discovery: probe candidate hosts, sync the ollama provider.

The scan runs at startup and via ``POST /admin/ollama/scan``. A reachable
host updates the provider's base_url, enables it, refreshes its model
cache, and ensures a placeholder key exists for rotation. An unreachable
scan disables the provider so `auto` and model routing skip a dead server.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ModelCache, Provider
from app.providers.ollama import candidate_hosts, normalize_host
from app.services.keys import add_key

log = logging.getLogger("freellm.ollama")

OLLAMA_PROVIDER_NAME = "ollama"
# Deterministic sentinel — never sent upstream, only satisfies key rotation.
PLACEHOLDER_KEY = "ollama-local"
PROBE_TIMEOUT_S = 1.5


@dataclass(frozen=True)
class OllamaModel:
    name: str
    size_bytes: int = 0
    modified_at: str = ""
    parameter_size: str = ""
    quantization: str = ""


@dataclass(frozen=True)
class OllamaHostScan:
    host: str
    reachable: bool
    version: str = ""
    models: tuple[OllamaModel, ...] = field(default_factory=tuple)


def _parse_tags(data: dict[str, Any]) -> tuple[OllamaModel, ...]:
    out: list[OllamaModel] = []
    for m in data.get("models", []):
        if not isinstance(m, dict) or not m.get("name"):
            continue
        details = m.get("details") or {}
        out.append(
            OllamaModel(
                name=m["name"],
                size_bytes=int(m.get("size") or 0),
                modified_at=str(m.get("modified_at") or ""),
                parameter_size=str(details.get("parameter_size") or ""),
                quantization=str(details.get("quantization_level") or ""),
            )
        )
    return tuple(sorted(out, key=lambda m: m.name))


async def probe_host(
    host: str, client: httpx.AsyncClient | None = None
) -> OllamaHostScan:
    """Probe one Ollama host via its native API; never raises."""
    base = normalize_host(host)
    if client is not None:
        return await _probe(base, client)
    timeout = httpx.Timeout(PROBE_TIMEOUT_S, connect=PROBE_TIMEOUT_S)
    async with httpx.AsyncClient(timeout=timeout) as owned:
        return await _probe(base, owned)


async def _probe(base: str, client: httpx.AsyncClient) -> OllamaHostScan:
    try:
        tags = await client.get(f"{base}/api/tags")
        if tags.status_code != 200:
            return OllamaHostScan(host=base, reachable=False)
        version = ""
        try:
            v = await client.get(f"{base}/api/version")
            if v.status_code == 200:
                version = str(v.json().get("version") or "")
        except httpx.HTTPError:
            pass
        return OllamaHostScan(
            host=base,
            reachable=True,
            version=version,
            models=_parse_tags(tags.json()),
        )
    except httpx.HTTPError:
        return OllamaHostScan(host=base, reachable=False)


async def scan_ollama_hosts() -> list[OllamaHostScan]:
    """Probe all candidate hosts concurrently, reachable hosts first."""
    scans = await asyncio.gather(*(probe_host(h) for h in candidate_hosts()))
    return sorted(scans, key=lambda s: not s.reachable)


async def _get_provider(session: AsyncSession) -> Provider | None:
    return (
        await session.execute(
            select(Provider).where(Provider.name == OLLAMA_PROVIDER_NAME)
        )
    ).scalar_one_or_none()


async def sync_ollama_provider(
    session: AsyncSession, scan: OllamaHostScan
) -> Provider:
    """Point the ollama provider at a reachable host and refresh its models."""
    model_names = [m.name for m in scan.models]
    provider = await _get_provider(session)
    if provider is None:
        provider = Provider(
            name=OLLAMA_PROVIDER_NAME,
            adapter="ollama",
            base_url=f"{scan.host}/v1",
            auth_style="none",
            enabled=True,
            default_models=model_names,
        )
        session.add(provider)
        await session.flush()

    provider.adapter = "ollama"
    provider.base_url = f"{scan.host}/v1"
    provider.auth_style = "none"
    provider.auth_header = "Authorization"
    provider.enabled = True
    provider.default_models = model_names
    limits = dict(provider.limits or {})
    limits.setdefault("concurrency", 2)
    limits.setdefault("request_timeout_s", 180)
    provider.limits = limits
    await add_key(
        session, provider, PLACEHOLDER_KEY, label="local (no key needed)"
    )

    cache = (
        await session.execute(
            select(ModelCache).where(ModelCache.provider_id == provider.id)
        )
    ).scalar_one_or_none()
    if cache is None:
        cache = ModelCache(provider_id=provider.id)
        session.add(cache)
    cache.models = model_names
    cache.fetched_at = datetime.now(timezone.utc)

    await session.commit()
    return provider


async def auto_scan_ollama(session: AsyncSession) -> OllamaHostScan | None:
    """Scan for Ollama and reconcile the provider row. Returns the live scan."""
    scans = await scan_ollama_hosts()
    reachable = next((s for s in scans if s.reachable), None)
    if reachable is not None:
        await sync_ollama_provider(session, reachable)
        log.info(
            "Ollama found at %s (v%s) with %d models",
            reachable.host, reachable.version or "?", len(reachable.models),
        )
        return reachable

    provider = await _get_provider(session)
    if provider is not None and provider.enabled:
        provider.enabled = False
        await session.commit()
    log.info("No Ollama server reachable; ollama provider disabled")
    return None
