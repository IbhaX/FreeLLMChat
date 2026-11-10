"""/admin management API — provider + key CRUD, key testing, stats."""

from __future__ import annotations

import asyncio
import csv
import io
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from sqlalchemy import Integer, case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import require_admin
from app.crypto import decrypt_key
from app.db import get_session
from app.gateway import Gateway
from app.models import ApiKey, KeyUsage, Provider, RequestLog
from app.providers.registry import build_adapter, seed_providers
from app.schemas import (
    KeyBulkTestItem,
    KeyBulkTestResult,
    KeyCreate,
    KeyImportResult,
    KeyOut,
    KeyTestResult,
    KeyUpdate,
    ProviderCreate,
    ProviderOut,
    ProviderUpdate,
    StatsOut,
)
from app.services.keys import add_key

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def _gateway(request: Request) -> Gateway:
    return request.app.state.gateway


async def _provider(session: AsyncSession, name: str) -> Provider:
    p = (
        await session.execute(
            select(Provider).where(Provider.name == name).options(selectinload(Provider.keys))
        )
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, f"provider '{name}' not found")
    return p


def _key_out(k: ApiKey) -> KeyOut:
    now = datetime.now(timezone.utc)
    cd = k.cooldown_until
    cooling = cd is not None and (cd.tzinfo and cd or cd.replace(tzinfo=timezone.utc)) > now
    try:
        decrypt_key(k.encrypted_key)
        decryptable = True
    except ValueError:
        decryptable = False
    return KeyOut(
        id=k.id, provider=k.provider.name if k.provider else "",
        label=k.label, key_hint=k.key_hint, quota_group=k.quota_group,
        enabled=k.enabled, priority=k.priority, weight=k.weight,
        daily_request_count=k.daily_request_count,
        daily_token_count=k.daily_token_count,
        last_used_at=k.last_used_at, cooldown_until=k.cooldown_until,
        consecutive_failures=k.consecutive_failures, last_error=k.last_error,
        created_at=k.created_at, notes=k.notes,
        healthy=k.enabled and not cooling and decryptable,
        decryptable=decryptable,
    )


# ---------- providers ----------

@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(session: AsyncSession = Depends(get_session)) -> list[ProviderOut]:
    providers = (
        (await session.execute(select(Provider).options(selectinload(Provider.keys))))
        .scalars().all()
    )
    now = datetime.now(timezone.utc)
    out = []
    for p in providers:
        healthy = sum(
            1 for k in p.keys
            if k.enabled and (k.cooldown_until is None
                              or k.cooldown_until.replace(tzinfo=k.cooldown_until.tzinfo or timezone.utc) <= now)
        )
        out.append(ProviderOut(
            id=p.id, name=p.name, display_name=p.display_name, adapter=p.adapter,
            base_url=p.base_url, enabled=p.enabled,
            rotation_strategy=p.rotation_strategy, fallback_chain=p.fallback_chain,
            auto_route_tags=p.auto_route_tags, limits=p.limits,
            default_models=p.default_models, requires_card=p.requires_card,
            commercial_ok=p.commercial_ok, docs_url=p.docs_url, notes=p.notes,
            verified_on=p.verified_on, key_count=len(p.keys),
            healthy_key_count=healthy,
        ))
    return out


@router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider(
    body: ProviderCreate, session: AsyncSession = Depends(get_session)
) -> ProviderOut:
    exists = (
        await session.execute(select(Provider).where(Provider.name == body.name))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "provider already exists")
    p = Provider(**body.model_dump())
    session.add(p)
    await session.commit()
    return ProviderOut(
        id=p.id, name=p.name, display_name=p.display_name, adapter=p.adapter,
        base_url=p.base_url, enabled=p.enabled,
        rotation_strategy=p.rotation_strategy, fallback_chain=p.fallback_chain,
        auto_route_tags=p.auto_route_tags, limits=p.limits,
        default_models=p.default_models, requires_card=p.requires_card,
        commercial_ok=p.commercial_ok, docs_url=p.docs_url, notes=p.notes,
        verified_on=p.verified_on,
    )


@router.patch("/providers/{name}", response_model=ProviderOut)
async def update_provider(
    name: str, body: ProviderUpdate, session: AsyncSession = Depends(get_session)
) -> ProviderOut:
    p = await _provider(session, name)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    await session.commit()
    return ProviderOut(
        id=p.id, name=p.name, display_name=p.display_name, adapter=p.adapter,
        base_url=p.base_url, enabled=p.enabled,
        rotation_strategy=p.rotation_strategy, fallback_chain=p.fallback_chain,
        auto_route_tags=p.auto_route_tags, limits=p.limits,
        default_models=p.default_models, requires_card=p.requires_card,
        commercial_ok=p.commercial_ok, docs_url=p.docs_url, notes=p.notes,
        verified_on=p.verified_on, key_count=len(p.keys),
    )


@router.delete("/providers/{name}", status_code=204)
async def delete_provider(name: str, session: AsyncSession = Depends(get_session)) -> None:
    p = await _provider(session, name)
    await session.delete(p)
    await session.commit()


@router.post("/providers/reseed")
async def reseed(session: AsyncSession = Depends(get_session)) -> dict:
    return {"upserted": await seed_providers(session)}


@router.post("/providers/{name}/refresh-models")
async def refresh_models(
    name: str, request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    p = await _provider(session, name)
    key = next((k for k in p.keys if k.enabled), None)
    if key is None:
        raise HTTPException(400, "no enabled key for provider")
    models = await _gateway(request).refresh_models(
        session, p, decrypt_key(key.encrypted_key)
    )
    return {"models": models, "count": len(models)}


# ---------- keys ----------

@router.get("/keys", response_model=list[KeyOut])
async def list_keys(
    provider: str | None = None, session: AsyncSession = Depends(get_session)
) -> list[KeyOut]:
    stmt = select(ApiKey).options(selectinload(ApiKey.provider))
    if provider:
        stmt = stmt.join(Provider).where(Provider.name == provider)
    keys = (await session.execute(stmt)).scalars().all()
    return [_key_out(k) for k in keys]


@router.post("/keys", response_model=KeyImportResult, status_code=201)
async def create_keys(
    body: KeyCreate, session: AsyncSession = Depends(get_session)
) -> KeyImportResult:
    p = await _provider(session, body.provider)
    created: list[int] = []
    skipped = 0
    for i, raw in enumerate(body.keys):
        raw = raw.strip()
        if not raw:
            skipped += 1
            continue
        label = f"{body.label_prefix}{i + 1}" if body.label_prefix else ""
        k = await add_key(
            session, p, raw, label=label, quota_group=body.quota_group,
            priority=body.priority, weight=body.weight, notes=body.notes,
        )
        if k is None:
            skipped += 1
        else:
            created.append(k.id)
    await session.commit()
    return KeyImportResult(created=len(created), skipped=skipped, key_ids=created)


@router.post("/keys/import-env", response_model=KeyImportResult)
async def import_keys_from_env(
    body: dict[str, str], session: AsyncSession = Depends(get_session)
) -> KeyImportResult:
    """Import keys from environment variables: {"groq": "GROQ_API_KEY", ...}."""
    created: list[int] = []
    skipped = 0
    from app.providers.registry import _env_value

    for pname, env_name in body.items():
        p = await _provider(session, pname)
        raw = _env_value(env_name)
        if not raw:
            skipped += 1
            continue
        for i, single in enumerate(raw.split(",")):
            k = await add_key(session, p, single.strip(), label=f"{env_name}-{i + 1}")
            if k is None:
                skipped += 1
            else:
                created.append(k.id)
    await session.commit()
    return KeyImportResult(created=len(created), skipped=skipped, key_ids=created)


@router.post("/keys/import-csv", response_model=KeyImportResult)
async def import_keys_csv(
    file: UploadFile, session: AsyncSession = Depends(get_session)
) -> KeyImportResult:
    """CSV columns: provider,key[,label,quota_group]."""
    content = (await file.read()).decode()
    reader = csv.DictReader(io.StringIO(content))
    created: list[int] = []
    skipped = 0
    for row in reader:
        p = (
            await session.execute(
                select(Provider).where(Provider.name == row["provider"].strip())
            )
        ).scalar_one_or_none()
        if p is None:
            skipped += 1
            continue
        k = await add_key(
            session, p, row["key"].strip(),
            label=row.get("label", ""), quota_group=row.get("quota_group") or None,
        )
        if k is None:
            skipped += 1
        else:
            created.append(k.id)
    await session.commit()
    return KeyImportResult(created=len(created), skipped=skipped, key_ids=created)


async def _key(session: AsyncSession, key_id: int) -> ApiKey:
    k = (
        await session.execute(
            select(ApiKey).where(ApiKey.id == key_id).options(selectinload(ApiKey.provider))
        )
    ).scalar_one_or_none()
    if k is None:
        raise HTTPException(404, "key not found")
    return k


@router.get("/keys/{key_id}", response_model=KeyOut)
async def get_key(key_id: int, session: AsyncSession = Depends(get_session)) -> KeyOut:
    return _key_out(await _key(session, key_id))


@router.patch("/keys/{key_id}", response_model=KeyOut)
async def update_key(
    key_id: int, body: KeyUpdate, session: AsyncSession = Depends(get_session)
) -> KeyOut:
    k = await _key(session, key_id)
    for field, v in body.model_dump(exclude_none=True).items():
        setattr(k, field, v)
    await session.commit()
    return _key_out(k)


@router.delete("/keys/{key_id}", status_code=204)
async def delete_key(key_id: int, session: AsyncSession = Depends(get_session)) -> None:
    k = await _key(session, key_id)
    await session.execute(delete(KeyUsage).where(KeyUsage.key_id == k.id))
    await session.delete(k)
    await session.commit()


@router.post("/keys/{key_id}/reset-cooldown", response_model=KeyOut)
async def reset_cooldown(
    key_id: int, session: AsyncSession = Depends(get_session)
) -> KeyOut:
    k = await _key(session, key_id)
    k.cooldown_until = None
    k.consecutive_failures = 0
    k.last_error = None
    await session.commit()
    return _key_out(k)


@router.post("/keys/{key_id}/test", response_model=KeyTestResult)
async def test_key(
    key_id: int, request: Request, session: AsyncSession = Depends(get_session)
) -> KeyTestResult:
    k = await _key(session, key_id)
    provider = k.provider
    model = (provider.default_models or [None])[0]
    if model is None:
        raise HTTPException(400, "provider has no default model to test")
    adapter = build_adapter(provider)
    gateway = _gateway(request)
    t0 = time.monotonic()
    try:
        await adapter.chat_completion(
            gateway.client,
            decrypt_key(k.encrypted_key),
            {"model": model, "messages": [{"role": "user", "content": "hi"}],
             "max_tokens": 1, "stream": False},
        )
    except Exception as e:
        status_code = getattr(e, "status_code", None)
        return KeyTestResult(
            ok=False, status_code=status_code,
            latency_ms=(time.monotonic() - t0) * 1000,
            error=str(e)[:300], model_used=model,
        )
    return KeyTestResult(
        ok=True, status_code=200,
        latency_ms=(time.monotonic() - t0) * 1000, model_used=model,
    )


async def _ping_key(gateway: Gateway, k: ApiKey, adapters: dict) -> dict:
    """Minimal chat ping for one key; never raises."""
    provider = k.provider
    base = {
        "key_id": k.id,
        "provider": provider.name if provider else "",
        "label": k.label or "",
        "key_hint": k.key_hint or "",
        "enabled": bool(k.enabled),
    }

    def fail(error: str, **kw) -> dict:
        return {**base, "ok": False, "error": error[:300],
                "status_code": kw.get("status_code"), "latency_ms": kw.get("latency_ms"),
                "model_used": kw.get("model_used")}

    if provider is None:
        return fail("provider missing")
    model = (provider.default_models or [None])[0]
    if model is None:
        return fail("provider has no default model to test")
    try:
        raw_key = decrypt_key(k.encrypted_key)
    except ValueError:
        return fail("cannot decrypt — ENCRYPTION_KEY changed; re-enter the key")
    adapter = adapters.get(provider.id)
    if adapter is None:
        adapter = build_adapter(provider)
        adapters[provider.id] = adapter
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(
            adapter.chat_completion(
                gateway.client, raw_key,
                {"model": model,
                 "messages": [{"role": "user", "content": "hi"}],
                 "max_tokens": 1, "stream": False},
            ),
            timeout=30,
        )
    except asyncio.TimeoutError:
        return fail("timed out after 30s", latency_ms=(time.monotonic() - t0) * 1000,
                    model_used=model)
    except Exception as e:
        return fail(str(e), status_code=getattr(e, "status_code", None),
                    latency_ms=(time.monotonic() - t0) * 1000, model_used=model)
    return {**base, "ok": True, "status_code": 200,
            "latency_ms": (time.monotonic() - t0) * 1000, "model_used": model}


@router.post("/keys/test-all", response_model=KeyBulkTestResult)
async def test_all_keys(
    request: Request, session: AsyncSession = Depends(get_session)
) -> KeyBulkTestResult:
    """Ping every stored key against its provider (4 at a time, 30s each)."""
    keys = (
        await session.execute(
            select(ApiKey)
            .options(selectinload(ApiKey.provider))
            .order_by(ApiKey.provider_id, ApiKey.id)
        )
    ).scalars().all()
    gateway = _gateway(request)
    sem = asyncio.Semaphore(4)
    adapters: dict[int, object] = {}

    async def guarded(k: ApiKey) -> dict:
        async with sem:
            return await _ping_key(gateway, k, adapters)

    results = await asyncio.gather(*(guarded(k) for k in keys))
    items = [KeyBulkTestItem(**r) for r in results]
    return KeyBulkTestResult(
        tested=len(items),
        ok=sum(1 for i in items if i.ok),
        failed=sum(1 for i in items if not i.ok),
        results=items,
    )


# ---------- conversations (server-side chat sync) ----------

@router.get("/conversations")
async def list_conversations(session: AsyncSession = Depends(get_session)) -> list[dict]:
    from app.models import Conversation

    rows = (
        await session.execute(
            select(Conversation).order_by(Conversation.updated_at.desc()).limit(200)
        )
    ).scalars().all()
    return [
        {"client_id": c.client_id, "name": c.name,
         "updated_at": c.updated_at.isoformat() if c.updated_at else None}
        for c in rows
    ]


@router.put("/conversations/{client_id}")
async def upsert_conversation(
    client_id: str, body: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    from datetime import datetime, timezone
    from app.models import Conversation

    conv = (
        await session.execute(
            select(Conversation).where(Conversation.client_id == client_id)
        )
    ).scalar_one_or_none()
    name = (body.get("name") or "")[:200]
    msgs = body.get("messages") or []
    if conv is None:
        conv = Conversation(client_id=client_id)
        session.add(conv)
    conv.name = name
    conv.messages = msgs
    conv.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"ok": True}


@router.get("/conversations/{client_id}")
async def get_conversation(
    client_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    from app.models import Conversation

    conv = (
        await session.execute(
            select(Conversation).where(Conversation.client_id == client_id)
        )
    ).scalar_one_or_none()
    if conv is None:
        raise HTTPException(404, "conversation not found")
    return {"client_id": conv.client_id, "name": conv.name,
            "messages": conv.messages or []}


@router.delete("/conversations/{client_id}", status_code=204)
async def delete_conversation(
    client_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    from app.models import Conversation

    conv = (
        await session.execute(
            select(Conversation).where(Conversation.client_id == client_id)
        )
    ).scalar_one_or_none()
    if conv is not None:
        await session.delete(conv)
        await session.commit()


# ---------- memories ----------

@router.get("/memories")
async def list_memories(session: AsyncSession = Depends(get_session)) -> list[dict]:
    from app.models import Memory

    rows = (
        await session.execute(select(Memory).order_by(Memory.id.desc()).limit(100))
    ).scalars().all()
    return [
        {"id": m.id, "content": m.content, "source": m.source,
         "created_at": m.created_at.isoformat()}
        for m in rows
    ]


@router.post("/memories", status_code=201)
async def create_memory(
    body: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    from app.models import Memory

    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "content required")
    m = Memory(content=content, source=body.get("source", "user"))
    session.add(m)
    await session.commit()
    return {"id": m.id, "content": m.content}


@router.delete("/memories/{mem_id}", status_code=204)
async def delete_memory(mem_id: int, session: AsyncSession = Depends(get_session)) -> None:
    from app.models import Memory

    m = (
        await session.execute(select(Memory).where(Memory.id == mem_id))
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, "memory not found")
    await session.delete(m)
    await session.commit()


# ---------- tools (agent plugins) ----------

@router.get("/tools")
async def list_available_tools() -> list[dict]:
    """Built-in tool plugins that agents can enable."""
    from app.services.tools import list_tools

    return list_tools()


# ---------- stats ----------

@router.get("/stats", response_model=StatsOut)
async def stats(session: AsyncSession = Depends(get_session)) -> StatsOut:
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    total = (await session.execute(select(func.count(RequestLog.id)))).scalar() or 0
    recent = (
        await session.execute(
            select(
                func.count(RequestLog.id),
                func.coalesce(func.sum(RequestLog.tokens_in), 0),
                func.coalesce(func.sum(RequestLog.tokens_out), 0),
                func.coalesce(
                    func.sum(case((RequestLog.status != "ok", 1), else_=0).cast(Integer)), 0
                ),
            ).where(RequestLog.ts >= day_ago)
        )
    ).one()
    n24, tin24, tout24, err24 = recent[0] or 0, recent[1] or 0, recent[2] or 0, recent[3] or 0

    providers = (await session.execute(select(func.count(Provider.id)))).scalar() or 0
    keys = (
        (await session.execute(select(ApiKey).options(selectinload(ApiKey.provider))))
        .scalars().all()
    )
    now = datetime.now(timezone.utc)
    cooling = sum(
        1 for k in keys
        if k.cooldown_until and k.cooldown_until.replace(tzinfo=k.cooldown_until.tzinfo or timezone.utc) > now
    )
    healthy = sum(1 for k in keys if k.enabled) - cooling

    per_provider = (
        await session.execute(
            select(
                RequestLog.provider_name,
                func.count(RequestLog.id),
                func.coalesce(func.sum(RequestLog.tokens_in + RequestLog.tokens_out), 0),
            )
            .where(RequestLog.ts >= day_ago)
            .group_by(RequestLog.provider_name)
        )
    ).all()

    # Estimated daily token budget per provider: display-only `est_tpd`
    # figures (from providers.yaml) multiplied by enabled key count, against
    # actual 24h usage from request logs.
    used_by_provider = {r[0]: r[2] for r in per_provider}
    prov_rows = (
        (await session.execute(
            select(Provider).options(selectinload(Provider.keys))
            .where(Provider.enabled == True)))  # noqa: E712
        .scalars().all()
    )
    token_budget, est_total = [], 0
    for p in prov_rows:
        est = (p.limits or {}).get("est_tpd")
        n_keys = sum(1 for k in p.keys if k.enabled)
        est_daily = est * n_keys if est else None
        est_total += est_daily or 0
        token_budget.append({
            "provider": p.name,
            "est_daily": est_daily,
            "keys": n_keys,
            "used_24h": used_by_provider.get(p.name, 0),
        })
    token_budget.sort(key=lambda r: -(r["est_daily"] or 0))

    return StatsOut(
        total_requests=total,
        requests_24h=n24,
        error_rate_24h=(err24 / n24) if n24 else 0.0,
        tokens_in_24h=tin24,
        tokens_out_24h=tout24,
        providers=providers,
        keys_total=len(keys),
        keys_healthy=max(healthy, 0),
        keys_in_cooldown=cooling,
        per_provider=[
            {"provider": r[0], "requests": r[1], "tokens": r[2]}
            for r in per_provider
        ],
        token_budget=token_budget,
        est_tokens_total=est_total or None,
    )


@router.get("/requests")
async def recent_requests(
    limit: int = 50, session: AsyncSession = Depends(get_session)
) -> list[dict]:
    rows = (
        await session.execute(
            select(RequestLog).order_by(RequestLog.id.desc()).limit(min(limit, 500))
        )
    ).scalars().all()
    return [
        {
            "request_id": r.request_id, "ts": r.ts.isoformat(),
            "model_requested": r.model_requested, "model_served": r.model_served,
            "provider": r.provider_name, "key_label": r.key_label,
            "status": r.status, "http_status": r.http_status,
            "latency_ms": r.latency_ms, "tokens_in": r.tokens_in,
            "tokens_out": r.tokens_out, "error": r.error,
            "request_input": r.request_input, "response_text": r.response_text,
        }
        for r in rows
    ]
