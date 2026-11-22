"""Request orchestration: model resolution, key rotation, failover,
mid-stream failure handling."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.crypto import decrypt_key
from app.models import ApiKey, ModelCache, Provider, RequestLog, StickyRoute, utcnow
from app.providers.base import ProviderError, is_billing_failure
from app.providers.registry import build_adapter
from app.routing.circuit_breaker import CircuitBreaker
from app.routing.fallback import resolve_candidates
from app.routing.rotator import Rotator


def _clean_exhaustion_msg(msg: str) -> str:
    """Turn a raw upstream error (often a full provider JSON body) into a
    short line safe to show in the chat bubble."""
    import re

    cleaned = re.sub(r"[\[{].*", "", msg or "", flags=re.DOTALL).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" :_")
    if not cleaned:
        cleaned = "provider error"
    hint = (" — the provider may be out of credit; check your balance"
            if is_billing_failure(msg) else "")
    return f"All providers are unavailable right now (last error: {cleaned[:140]}{hint})"


def _scrub_body(body: dict[str, Any]) -> dict[str, Any]:
    """Drop explicit ``null`` values and normalize ``content: null`` in
    messages. Strict OpenAI-compatible parsers (Ollama) reject the request
    with a 400 on ``{"temperature": null}`` — a null carries no information
    versus an omitted key, so removing it is safe for every provider."""
    out = {k: v for k, v in body.items() if v is not None}
    msgs = out.get("messages")
    if isinstance(msgs, list):
        out["messages"] = [
            ({**m, "content": ""} if isinstance(m, dict)
             and "content" in m and m["content"] is None else m)
            for m in msgs
        ]
    return out


def _payload_input(payload: dict[str, Any]) -> str | None:
    """Last user message text — the visible 'request input' for a log row."""
    for m in reversed(payload.get("messages") or []):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c[:8000] or None
        if isinstance(c, list):
            text = " ".join(
                p.get("text", "") for p in c
                if isinstance(p, dict) and p.get("type") == "text")
            return text[:8000] or None
    return None


def _result_text(result: dict[str, Any]) -> str | None:
    try:
        text = (result.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    except Exception:
        return None
    return text[:8000] or None


def _stream_delta_text(chunk: bytes) -> str:
    """Concatenated delta.content from an SSE chunk (best-effort)."""
    out = []
    for line in chunk.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("data:") or line == "data: [DONE]":
            continue
        try:
            data = json.loads(line[5:])
            for c in data.get("choices") or []:
                delta = (c.get("delta") or {}).get("content")
                if delta:
                    out.append(delta)
        except Exception:
            continue
    return "".join(out)


class GatewayExhausted(Exception):
    def __init__(self, message: str, last_status: int | None = None) -> None:
        super().__init__(message)
        self.last_status = last_status


class KeyUndecryptable(Exception):
    """A stored API key cannot be decrypted with the current ENCRYPTION_KEY.

    Local config problem, not a provider failure: the key is skipped
    without cooldown and remembered for the process lifetime so later
    requests fail fast instead of retrying it on every call.
    """


class Gateway:
    def __init__(self) -> None:
        s = get_settings()
        self.settings = s
        self.rotator = Rotator()
        self.quota = self.rotator.quota
        self.breaker = CircuitBreaker()
        self._undecryptable: set[int] = set()
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                s.request_timeout_s, connect=s.connect_timeout_s
            )
        )

    async def close(self) -> None:
        await self.client.aclose()

    def _decrypt_key(self, key: ApiKey) -> str:
        if key.id in self._undecryptable:
            raise KeyUndecryptable(
                f"{key.provider.name if key.provider else 'key'} · {key.label or key.id} "
                "can't be decrypted — ENCRYPTION_KEY changed; re-enter it in Admin → Keys"
            )
        try:
            return decrypt_key(key.encrypted_key)
        except ValueError:
            self._undecryptable.add(key.id)
            raise KeyUndecryptable(
                f"{key.provider.name if key.provider else 'key'} · {key.label or key.id} "
                "can't be decrypted — ENCRYPTION_KEY changed; re-enter it in Admin → Keys"
            ) from None

    async def _mark_model_dead(
        self, session: AsyncSession, provider: Provider, model: str
    ) -> None:
        """Remove a permanently-unavailable model from the provider's cache."""
        cache = (
            await session.execute(
                select(ModelCache).where(ModelCache.provider_id == provider.id)
            )
        ).scalar_one_or_none()
        if cache and cache.models and model in cache.models:
            cache.models = [m for m in cache.models if m != model]
            await session.commit()
        elif cache is None and provider.default_models and model in provider.default_models:
            # No cache row yet: persist the seeded list minus the dead model
            # so catalog reads stop retrying it.
            session.add(ModelCache(
                provider_id=provider.id,
                models=[m for m in provider.default_models if m != model],
                fetched_at=datetime.now(timezone.utc),
            ))
            await session.commit()

    # ---------- model catalog ----------

    async def provider_catalog(
        self, session: AsyncSession
    ) -> dict[str, tuple[Provider, list[str]]]:
        """provider name -> (Provider, model ids from cache or seed)."""
        providers = (
            (await session.execute(select(Provider).where(Provider.enabled.is_(True))))
            .scalars()
            .all()
        )
        out: dict[str, tuple[Provider, list[str]]] = {}
        now = datetime.now(timezone.utc)
        ttl = timedelta(seconds=self.settings.model_cache_ttl_s)
        for p in providers:
            cache = (
                await session.execute(
                    select(ModelCache).where(ModelCache.provider_id == p.id)
                )
            ).scalar_one_or_none()
            if cache and now - self._aware(cache.fetched_at) < ttl:
                out[p.name] = (p, list(cache.models or p.default_models or []))
                continue
            models = await self._discover_models(session, p)
            out[p.name] = (p, models or list(p.default_models or []))
        return out

    _NON_CHAT_SUBSTRINGS = (
        "embed", "whisper", "tts", "moderation", "image", "audio",
        "dall", "stable-diffusion", "rerank", "transcribe", "vision-",
    )

    async def _discover_models(
        self, session: AsyncSession, provider: Provider
    ) -> list[str] | None:
        """Fetch the provider's live model list via /models using any enabled key.

        Returns None when no key is configured (caller falls back to seed
        default_models) or discovery fails."""
        key = (
            await session.execute(
                select(ApiKey).where(
                    ApiKey.provider_id == provider.id, ApiKey.enabled.is_(True)
                )
            )
        ).scalars().first()
        if key is None:
            return None
        try:
            raw = decrypt_key(key.encrypted_key)
        except Exception:
            return None
        models = await self.refresh_models(session, provider, raw)
        if not models:
            return None
        return [
            m for m in models
            if not any(s in m.lower() for s in self._NON_CHAT_SUBSTRINGS)
        ]

    async def refresh_models(
        self, session: AsyncSession, provider: Provider, api_key: str
    ) -> list[str]:
        adapter = build_adapter(provider)
        try:
            models = await adapter.list_models(self.client, api_key)
        except Exception:
            return []
        if models:
            cache = (
                await session.execute(
                    select(ModelCache).where(ModelCache.provider_id == provider.id)
                )
            ).scalar_one_or_none()
            if cache is None:
                cache = ModelCache(provider_id=provider.id)
                session.add(cache)
            cache.models = models
            cache.fetched_at = datetime.now(timezone.utc)
            await session.commit()
        return models

    # ---------- failure accounting ----------

    async def _record_failure(
        self,
        session: AsyncSession,
        key: ApiKey | None,
        provider: Provider,
        err: ProviderError,
    ) -> None:
        self.breaker.failure(provider.name)
        if key is None:
            return
        key.consecutive_failures += 1
        key.last_error = err.message[:500]
        secs = self.quota.cooldown_seconds(
            key.consecutive_failures, err.retry_after_s, provider, err.status_code
        )
        key.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=secs)
        # quota_group: cool the whole pool.
        if key.quota_group:
            siblings = (
                await session.execute(
                    select(ApiKey).where(
                        ApiKey.provider_id == provider.id,
                        ApiKey.quota_group == key.quota_group,
                        ApiKey.id != key.id,
                    )
                )
            ).scalars().all()
            for sib in siblings:
                sib.cooldown_until = key.cooldown_until
                sib.last_error = f"quota_group:{key.quota_group} cooled"
        await session.commit()

    async def _record_success(
        self,
        session: AsyncSession,
        key: ApiKey,
        provider: Provider,
        tokens_in: int,
        tokens_out: int,
    ) -> None:
        self.breaker.success(provider.name)
        key.consecutive_failures = 0
        key.last_error = None
        key.cooldown_until = None
        key.last_used_at = datetime.now(timezone.utc)
        key.daily_request_count += 1
        key.daily_token_count += tokens_in + tokens_out
        await self.quota.record(session, key.id, tokens_in, tokens_out)
        await session.commit()

    async def _log(
        self,
        session: AsyncSession,
        request_id: str,
        model_requested: str,
        model_served: str | None,
        provider: Provider | None,
        key: ApiKey | None,
        status: str,
        http_status: int | None,
        latency_ms: float,
        tokens_in: int = 0,
        tokens_out: int = 0,
        error: str | None = None,
        request_input: str | None = None,
        response_text: str | None = None,
    ) -> None:
        session.add(
            RequestLog(
                request_id=request_id,
                model_requested=model_requested,
                model_served=model_served,
                provider_name=provider.name if provider else None,
                key_label=key.label if key else None,
                status=status,
                http_status=http_status,
                latency_ms=latency_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                error=error[:500] if error else None,
                request_input=request_input,
                response_text=response_text,
            )
        )
        await session.commit()

    # ---------- candidate iteration ----------

    async def _attempts(
        self, session: AsyncSession, requested: str
    ) -> list[tuple[Provider, str]]:
        catalog = await self.provider_catalog(session)
        return await resolve_candidates(session, requested, catalog)

    @staticmethod
    def _request_id() -> str:
        from app.logging_config import request_id_ctx

        rid = request_id_ctx.get()
        return rid if rid != "-" else uuid.uuid4().hex[:16]

    async def chat(
        self, session: AsyncSession, payload: dict[str, Any],
        on_event: Any | None = None,
        attempt_timeout: float | None = None,
    ) -> tuple[dict[str, Any], Provider, ApiKey, str]:
        """Non-streaming request with key rotation + provider failover.

        ``on_event`` (optional sync callable) receives activity dicts so
        callers can show routing/failover progress like the stream path.
        After a fully exhausted sweep it resets breakers and retries once
        — free tiers flake constantly, so the second pass usually lands.
        """
        emit = on_event or (lambda ev: None)
        request_id = self._request_id()
        requested = payload["model"]
        t0 = time.monotonic()
        candidates = await self._attempts(session, requested)
        if not candidates:
            await self._log(session, request_id, requested, None, None, None,
                            "no_route", None, 0, error="no provider serves model")
            raise GatewayExhausted(f"No provider can serve model '{requested}'", 404)

        plan = await self._plan_attempts(session, requested, candidates)
        if not plan:
            # Every key cooling: when the soonest recovery is short, waiting
            # beats erroring out or burning a weak backstop — emit a wait
            # event so the UI can show the hold, then re-plan once.
            wait_s = await self._soonest_cooldown(session, candidates)
            remaining = t0 + self.settings.response_deadline_s - time.monotonic()
            if (wait_s is not None
                    and wait_s <= min(self._COOLDOWN_WAIT_S, remaining - 5)):
                emit({"event": "wait",
                      "text": f"All keys cooling — retrying in ~{wait_s}s"})
                await asyncio.sleep(wait_s + 1)
                plan = await self._plan_attempts(session, requested, candidates)
            if not plan and wait_s is not None and remaining > 2:
                # Dead end: every candidate key is cooling for minutes. A
                # bounded retry beats an error — release cooldowns once and
                # re-plan; the response deadline still caps the cost.
                await self._release_cooldowns(session)
                emit({"event": "retry",
                      "text": "All keys on cooldown — releasing early, retrying"})
                plan = await self._plan_attempts(session, requested, candidates)
        if not plan:
            raise GatewayExhausted(await self._no_route_reason(session), 503)
        emit({"event": "routing",
              "text": f"Sequential fallback: {len(plan)} provider(s) with a live key"})
        await self._sticky_first(session, requested, plan)

        last_err: ProviderError | None = None
        tried: set[tuple[int, str]] = set()
        attempts = 0
        budget = self._attempt_budget(requested, plan)
        inner = await self._chat_sweep(
            session, request_id, t0, payload, plan,
            emit, tried, attempts, budget, attempt_timeout=attempt_timeout,
        )
        if inner is not None:
            await self._sticky_set(session, requested, inner[1], inner[3])
            return inner
        last_err = getattr(self, "_last_err", last_err)
        attempts = getattr(self, "_last_attempts", attempts)
        status = last_err.status_code if last_err else None
        await self._log(session, request_id, requested, None, None, None,
                        "exhausted", status, (time.monotonic() - t0) * 1000,
                        error=last_err.message if last_err else "exhausted",
                        request_input=_payload_input(payload))
        raise GatewayExhausted(
            _clean_exhaustion_msg(last_err.message) if last_err
            else "All providers exhausted", status or 503
        )

    _MODEL_REJECT = re.compile(
        r"request too large|context (?:length|window)|does not support|"
        r"no such model|unknown model|cannot be loaded|"
        r"model .{0,40}(not found|does not exist|unavailable|decommissioned|removed)",
        re.I,
    )

    _COOLDOWN_WAIT_S = 120

    async def _soonest_cooldown(
        self, session: AsyncSession,
        candidates: list[tuple[Provider, str]] | None = None,
    ) -> int | None:
        """Seconds until the soonest-expiring enabled key cooldown; None when
        nothing is cooling (or no keys exist). When ``candidates`` is given,
        only keys of providers that can serve this request count — waiting
        on an unrelated provider's cooldown is wasted time."""
        q = select(func.min(ApiKey.cooldown_until)).where(
            ApiKey.enabled.is_(True),
            ApiKey.cooldown_until.isnot(None))
        if candidates:
            ids = {p.id for p, _m in candidates}
            q = q.where(ApiKey.provider_id.in_(ids))
        soonest = (await session.execute(q)).scalar()
        if soonest is None:
            return None
        cd = self._aware(soonest)
        return max(0, int((cd - datetime.now(timezone.utc)).total_seconds()))

    async def _no_route_reason(self, session: AsyncSession) -> str:
        """Honest explanation when the attempt plan is empty: keys may all be
        on cooldown rather than missing."""
        n = (await session.execute(
            select(func.count(ApiKey.id)).where(ApiKey.enabled.is_(True))
        )).scalar() or 0
        if not n:
            return "No providers have an enabled, available API key"
        soonest = (await session.execute(
            select(func.min(ApiKey.cooldown_until)).where(
                ApiKey.enabled.is_(True),
                ApiKey.cooldown_until.isnot(None))
        )).scalar()
        if soonest is not None:
            cd = self._aware(soonest)
            wait = max(0, int((cd - datetime.now(timezone.utc)).total_seconds()))
            return (
                f"All {n} provider key(s) are on cooldown after upstream "
                f"errors — soonest retry in ~{wait // 60}m{wait % 60:02d}s")
        return "No providers are currently routable"

    @classmethod
    def _is_model_fault(cls, err: ProviderError) -> bool:
        """True when the upstream rejected the *model*, not the key.

        Model faults (dead model, unsupported tools, request too large for
        that model's context/TPM) must not cool the key — the key is fine
        and can serve the provider's next model.
        """
        msg = err.message or ""
        if err.status_code == 404 and "model" in msg.lower():
            return True
        # A 400 rejects the *request*, never the key — the same malformed
        # payload would fail on every key, so cooling the key is wrong.
        if err.status_code == 400:
            return True
        return bool(cls._MODEL_REJECT.search(msg))

    _AUTO_MODEL_ALTERNATES = 3

    async def _plan_attempts(
        self,
        session: AsyncSession,
        requested: str,
        candidates: list[tuple[Provider, str]],
    ) -> list[tuple[Provider, list[str]]]:
        """Build the ordered attempt plan: ``[(provider, [model, ...])]``.

        - Providers keep their configured chain order, but each is scored
          by key health so providers whose keys already failed sink.
        - Only providers with >= 1 currently usable key are included.
        - Each provider carries up to 3 model alternates (curated
          ``default_models`` first) so a model-level rejection retries the
          next model instead of abandoning the provider.
        - Auto routes follow their configured provider chain only. A locally
          hosted Ollama instance is never added implicitly.
        """
        groups: dict[str, tuple[Provider, list[str]]] = {}
        for p, m in candidates:
            entry = groups.setdefault(p.name, (p, []))
            if m not in entry[1]:
                entry[1].append(m)

        scored: list[tuple[int, int, Provider, list[str]]] = []
        for chain_idx, (p, models) in enumerate(groups.values()):
            healthy = await self.rotator.healthy_keys(session, p)
            if not healthy:
                continue
            min_fail = min(k.consecutive_failures or 0 for k in healthy)
            scored.append((min_fail, chain_idx, p, models))
        # Strict chain order — the configured fallback order is the policy.
        # Providers
        # with no live key are already filtered above; consecutive-failure
        # counts only decide which key within a provider is tried first.
        scored.sort(key=lambda t: t[1])

        plan: list[tuple[Provider, list[str]]] = []
        for _f, _i, p, models in scored:
            if requested.startswith("auto") and p.name == "ollama":
                continue
            plan.append((p, self._order_models(p, models)))
        return plan

    _SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b(?:illion)?\b", re.I)
    _REASONING_HINT = re.compile(
        r"(?:^|[-_:/])(r1|reasoning|think|qwq|o\d+)(?:[-_:/]|$)", re.I)

    @classmethod
    def _model_quality(cls, name: str) -> float:
        """Rough capability score for ordering *uncurated* model lists.
        Bigger parameter counts rank higher; reasoning-tuned variants
        (r1/o*/think/qwq) are penalized for general chat — they burn tokens
        on hidden chain-of-thought and routinely break structured output."""
        n = name.lower()
        m = cls._SIZE_RE.search(n)
        size = float(m.group(1)) if m else 0.0
        if cls._REASONING_HINT.search(n):
            size *= 0.2
        return size

    def _order_models(
        self, provider: Provider, models: list[str]
    ) -> list[str]:
        """Curated ``default_models`` first, then discovered models ranked
        by rough capability, capped at the alternate count."""
        defaults = provider.default_models or []
        dset = set(defaults)
        in_set = set(models)
        ordered = [m for m in defaults if m in in_set]
        rest = [m for m in models if m not in dset]
        rest.sort(key=lambda m: -self._model_quality(m))
        ordered += rest
        return ordered[: self._AUTO_MODEL_ALTERNATES]

    def _attempt_budget(
        self, requested: str, plan: list[tuple[Provider, list[str]]]
    ) -> int | None:
        """Upstream-call budget for one request.

        Explicit model picks keep the configured cap. Auto is bounded by its
        finite provider/model/key candidate set instead of an arbitrary
        attempt ceiling, so later configured free providers are not skipped.
        """
        if not requested.startswith("auto"):
            return self.settings.max_retries_per_request
        return None

    async def _sticky_first(
        self, session: AsyncSession, requested: str,
        plan: list[tuple[Provider, list[str]]],
    ) -> None:
        """Move the last working (provider, model) to the front."""
        sticky = (
            await session.execute(
                select(StickyRoute).where(StickyRoute.requested == requested)
            )
        ).scalar_one_or_none()
        if not sticky:
            return
        if requested.startswith("auto") and sticky.provider_name == "ollama":
            # Local backstop is last resort only — never let it stick.
            return
        for i, (p, models) in enumerate(plan):
            if p.name == sticky.provider_name and sticky.model in models:
                models.remove(sticky.model)
                models.insert(0, sticky.model)
                plan.insert(0, plan.pop(i))
                return

    async def _sticky_set(
        self, session: AsyncSession, requested: str,
        provider: Provider, model: str,
    ) -> None:
        row = (
            await session.execute(
                select(StickyRoute).where(StickyRoute.requested == requested)
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(StickyRoute(
                requested=requested, provider_name=provider.name, model=model))
        elif row.provider_name != provider.name or row.model != model:
            row.provider_name = provider.name
            row.model = model
            row.updated_at = utcnow()
        else:
            return
        await session.commit()

    async def _second_sweep_reset(self, session: AsyncSession, emit) -> None:
        """Second-chance sweep prep: brief pause, reopen all breakers and
        release key cooldowns so cooled keys are retried instead of the
        request erroring out while free capacity is actually available."""
        emit({"event": "retry",
              "text": "All providers busy — retrying in 4s"})
        await asyncio.sleep(4)
        await self._release_cooldowns(session)

    async def _release_cooldowns(self, session: AsyncSession) -> None:
        """Clear key cooldowns/failure counts and reopen breakers — last
        resort when every routable key is cooling."""
        self.breaker.reset_all()
        keys = (await session.execute(
            select(ApiKey).where(
                ApiKey.enabled.is_(True), ApiKey.cooldown_until.isnot(None)))
        ).scalars().all()
        for k in keys:
            k.cooldown_until = None
            k.consecutive_failures = 0
            k.last_error = None
        await session.commit()

    async def _emit_skip_reason(
        self, session, provider, attempts, prov_attempts, emit,
        budget: int | None = None,
    ) -> None:
        """When a provider was skipped without a single attempt, say why so
        the activity list does not show a bare 'Trying' line."""
        if attempts != prov_attempts:
            return
        if budget is not None and attempts >= budget:
            emit({"event": "skip",
                  "text": f"{provider.name} — retry budget reached, skipping"})
            return
        n = (await session.execute(
            select(func.count(ApiKey.id)).where(
                ApiKey.provider_id == provider.id,
                ApiKey.enabled.is_(True)))).scalar()
        emit({"event": "skip",
              "text": (f"{provider.name} — {n} key(s) on cooldown or over quota, skipping"
                       if n else f"{provider.name} — no key configured, skipping")})

    async def _chat_sweep(
        self, session, request_id, t0, payload,
        plan: list[tuple[Provider, list[str]]], emit,
        tried: set[tuple[int, str]], attempts: int, budget: int | None,
        attempt_timeout: float | None = None,
    ):
        """One sequential pass over the plan. Returns
        (result, provider, key, model) on success or None when the sweep
        is exhausted. Stashes _last_err/_last_attempts for the caller.

        Failure classes decide the next move:
        - model fault  → next model of the same provider (key not cooled)
        - key fault    → cool key, next key; none left → next provider
        - non-retryable → next provider
        """
        last_err: ProviderError | None = None
        attempt_timeout = attempt_timeout or self.settings.attempt_timeout_s
        deadline = t0 + self.settings.response_deadline_s
        for provider, models in plan:
            if budget is not None and attempts >= budget:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 1.0:
                emit({"event": "skip",
                      "text": "Response deadline reached — stopping attempts"})
                break
            if not self.breaker.allow(provider.name):
                continue
            prov_timeout = min(float(
                (provider.limits or {}).get("request_timeout_s") or attempt_timeout
            ), remaining)
            prov_attempts = attempts
            provider_done = False
            for model in models:
                if provider_done or (budget is not None and attempts >= budget):
                    break
                emit({"event": "try", "provider": provider.name, "model": model,
                      "text": f"Trying {provider.name} · {model}"})
                body = _scrub_body(dict(payload))
                body["model"] = model
                body["stream"] = False
                body.setdefault("max_tokens", 4096)
                while budget is None or attempts < budget:
                    key_remaining = deadline - time.monotonic()
                    if key_remaining <= 1.0:
                        break
                    # Keys already probed for THIS model are skipped; a key
                    # stays eligible for the provider's other models (a model
                    # fault does not implicate the key).
                    excl = {kid for kid, mdl in tried if mdl == model}
                    key = await self.rotator.pick(
                        session, provider, exclude_ids=excl)
                    if key is None:
                        break
                    tried.add((key.id, model))
                    if key.id in self._undecryptable:
                        continue
                    attempts += 1
                    try:
                        raw = self._decrypt_key(key)
                        adapter = build_adapter(provider)
                        try:
                            result = await asyncio.wait_for(
                                adapter.chat_completion(self.client, raw, body),
                                timeout=min(prov_timeout, key_remaining),
                            )
                        except asyncio.TimeoutError:
                            if key_remaining < prov_timeout:
                                # Cut short by the global response deadline,
                                # not provider slowness — don't cool the key.
                                emit({"event": "skip",
                                      "text": "Response deadline reached — "
                                              "stopping attempts"})
                                provider_done = True
                                break
                            raise ProviderError(
                                f"no response in {prov_timeout:.0f}s",
                                retryable=True,
                            )
                    except KeyUndecryptable as e:
                        # Local key problem: skip without cooldown, surface a
                        # clear message instead of a provider error.
                        last_err = ProviderError(str(e), retryable=False)
                        emit({"event": "fail", "text": str(e)[:120]})
                        continue
                    except ProviderError as e:
                        last_err = e
                        if self._is_model_fault(e):
                            if e.status_code == 404 and "model" in e.message.lower():
                                await self._mark_model_dead(
                                    session, provider, model)
                            emit({"event": "fail",
                                  "text": f"{provider.name} · {model} rejected: "
                                          f"{e.message[:70]} — next model"})
                            break
                        await self._record_failure(session, key, provider, e)
                        emit({"event": "fail",
                              "text": f"{provider.name} failed: {e.message[:80]}"})
                        if not e.retryable:
                            provider_done = True
                            break
                        emit({"event": "next", "text": "Retrying with next key"})
                        continue
                    except Exception as e:
                        last_err = ProviderError(str(e), retryable=True)
                        await self._record_failure(session, key, provider, last_err)
                        emit({"event": "fail",
                              "text": f"{provider.name} error: {str(e)[:80]}"})
                        continue
                    usage = result.get("usage") or {}
                    tin = usage.get("prompt_tokens", 0)
                    tout = usage.get("completion_tokens", 0)
                    await self._record_success(session, key, provider, tin, tout)
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    await self._log(
                        session, request_id, payload["model"], model, provider,
                        key, "ok", 200, elapsed_ms, tin, tout,
                        request_input=_payload_input(payload),
                        response_text=_result_text(result))
                    emit(_usage_event(provider, key, model, tin, tout, elapsed_ms))
                    self._last_err, self._last_attempts = last_err, attempts
                    return result, provider, key, model
            await self._emit_skip_reason(
                session, provider, attempts, prov_attempts, emit, budget)
        self._last_err, self._last_attempts = last_err, attempts
        return None

    # ---------- streaming ----------

    async def chat_stream(
        self,
        session: AsyncSession,
        payload: dict[str, Any],
        on_event: Any | None = None,
    ) -> tuple[AsyncIterator[bytes], Provider, ApiKey, str]:
        """Open a stream. Retries transparently until the first byte is
        yielded; callers receive an iterator whose mid-stream failures
        emit a terminal SSE error event. ``on_event`` (sync callable) is
        invoked with activity dicts as routing/failover progresses."""
        emit = on_event or (lambda ev: None)
        request_id = self._request_id()
        requested = payload["model"]
        t0 = time.monotonic()
        candidates = await self._attempts(session, requested)
        if not candidates:
            raise GatewayExhausted(
                f"No provider can serve model '{requested}'", 404
            )

        plan = await self._plan_attempts(session, requested, candidates)
        if not plan:
            wait_s = await self._soonest_cooldown(session, candidates)
            remaining = t0 + self.settings.response_deadline_s - time.monotonic()
            if (wait_s is not None
                    and wait_s <= min(self._COOLDOWN_WAIT_S, remaining - 5)):
                emit({"event": "wait",
                      "text": f"All keys cooling — retrying in ~{wait_s}s"})
                await asyncio.sleep(wait_s + 1)
                plan = await self._plan_attempts(session, requested, candidates)
            if not plan and wait_s is not None and remaining > 2:
                # Dead end: every candidate key is cooling for minutes. A
                # bounded retry beats an error — release cooldowns once and
                # re-plan; the response deadline still caps the cost.
                await self._release_cooldowns(session)
                emit({"event": "retry",
                      "text": "All keys on cooldown — releasing early, retrying"})
                plan = await self._plan_attempts(session, requested, candidates)
        if not plan:
            raise GatewayExhausted(await self._no_route_reason(session), 503)
        emit({"event": "routing",
              "text": f"Sequential fallback: {len(plan)} provider(s) with a live key"})
        await self._sticky_first(session, requested, plan)

        last_err: ProviderError | None = None
        tried: set[tuple[int, str]] = set()
        attempts = 0
        budget = self._attempt_budget(requested, plan)
        deadline = t0 + self.settings.response_deadline_s
        # One ordered pass over the plan. A provider is probed once per
        # key per model; the next provider is reached only after a real
        # upstream failure. Model faults move to the provider's next model
        # without cooling the key.
        for provider, models in plan:
            if budget is not None and attempts >= budget:
                break
            if deadline - time.monotonic() <= 1.0:
                emit({"event": "skip",
                      "text": "Response deadline reached — stopping attempts"})
                break
            if not self.breaker.allow(provider.name):
                continue
            prov_attempts = attempts
            provider_done = False
            for model in models:
                if provider_done or (budget is not None and attempts >= budget):
                    break
                emit({"event": "try", "provider": provider.name, "model": model,
                      "text": f"Trying {provider.name} · {model}"})
                body = _scrub_body(dict(payload))
                body["model"] = model
                body["stream"] = True
                body.setdefault("max_tokens", 4096)
                body.setdefault("stream_options", {"include_usage": True})
                first_to = float(
                    (provider.limits or {}).get("request_timeout_s")
                    or self.settings.first_chunk_timeout_s
                )
                while budget is None or attempts < budget:
                    first_remaining = deadline - time.monotonic()
                    if first_remaining <= 1.0:
                        break
                    excl = {kid for kid, mdl in tried if mdl == model}
                    key = await self.rotator.pick(
                        session, provider, exclude_ids=excl)
                    if key is None:
                        break
                    tried.add((key.id, model))
                    if key.id in self._undecryptable:
                        continue
                    attempts += 1
                    try:
                        raw = self._decrypt_key(key)
                    except KeyUndecryptable as e:
                        last_err = ProviderError(str(e), retryable=False)
                        emit({"event": "retry", "text": str(e)[:120]})
                        continue
                    adapter = build_adapter(provider)
                    stream = adapter.chat_stream(self.client, raw, body)
                    try:
                        first = await asyncio.wait_for(
                            anext(stream),  # wait for first SSE chunk
                            timeout=min(first_to, first_remaining),
                        )
                    except (asyncio.TimeoutError, TimeoutError):
                        if first_remaining < first_to:
                            # Deadline-clamped wait — the provider never got
                            # its full window; don't cool the key for it.
                            emit({"event": "skip",
                                  "text": "Response deadline reached — "
                                          "stopping attempts"})
                            provider_done = True
                            break
                        last_err = ProviderError(
                            f"no response in {first_to:.0f}s",
                            retryable=True,
                        )
                        await self._record_failure(session, key, provider, last_err)
                        emit({"event": "retry", "text": f"{provider.name} · {key.label} — "
                                "no response, trying next"})
                        continue
                    except StopAsyncIteration:
                        last_err = ProviderError("empty stream", retryable=True)
                        await self._record_failure(session, key, provider, last_err)
                        emit({"event": "retry", "text": f"{provider.name} · {key.label} — "
                                "empty stream, next key"})
                        continue
                    except ProviderError as e:
                        last_err = e
                        if self._is_model_fault(e):
                            if e.status_code == 404 and "model" in e.message.lower():
                                await self._mark_model_dead(
                                    session, provider, model)
                            emit({"event": "retry",
                                  "text": f"{provider.name} · {model} rejected "
                                          f"({e.status_code or 'err'}) — next model"})
                            break
                        await self._record_failure(session, key, provider, e)
                        emit({"event": "retry", "text": f"{provider.name} · {key.label} failed "
                                f"({e.status_code or 'error'})"})
                        if not e.retryable:
                            provider_done = True
                            break
                        continue
                    except Exception as e:
                        last_err = ProviderError(str(e), retryable=True)
                        await self._record_failure(session, key, provider, last_err)
                        emit({"event": "retry", "text": f"{provider.name} · {key.label} — {e}, "
                                "retrying"})
                        continue
                    await self._sticky_set(session, requested, provider, model)
                    return (
                        self._wrap_stream(
                            session, stream, first, request_id, requested, model,
                            provider, key, payload, emit,
                        ),
                        provider,
                        key,
                        model,
                    )
            await self._emit_skip_reason(
                session, provider, attempts, prov_attempts, emit, budget)
        raise GatewayExhausted(
            _clean_exhaustion_msg(last_err.message) if last_err
            else "All providers exhausted",
            last_err.status_code if last_err else 503,
        )

    async def _wrap_stream(
        self,
        session: AsyncSession,
        stream: AsyncIterator[bytes],
        first: bytes,
        request_id: str,
        requested: str,
        model: str,
        provider: Provider,
        key: ApiKey,
        payload: dict[str, Any],
        emit: Any | None = None,
    ) -> AsyncIterator[bytes]:
        emit = emit or (lambda ev: None)
        t0 = time.monotonic()
        tin = tout = 0
        resp_parts: list[str] = []
        idle_s = float(
            (provider.limits or {}).get("request_timeout_s")
            or self.settings.stream_idle_timeout_s
        )
        yield first
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(anext(stream), timeout=idle_s)
                except StopAsyncIteration:
                    break
                tin_, tout_ = _extract_usage(chunk)
                tin, tout = tin or tin_, tout or tout_
                if sum(len(p) for p in resp_parts) < 8000:
                    resp_parts.append(_stream_delta_text(chunk))
                yield chunk
        except Exception as e:
            err = {"error": {"message": f"upstream stream failed: {e}",
                             "type": "upstream_error", "code": 502}}
            yield f"data: {json.dumps(err)}\n\ndata: [DONE]\n\n".encode()
            await self._record_failure(
                session, key, provider,
                ProviderError(f"mid-stream failure: {e}", retryable=True),
            )
            return
        await self._record_success(session, key, provider, tin, tout)
        elapsed_ms = (time.monotonic() - t0) * 1000
        await self._log(session, request_id, requested, model, provider, key,
                        "ok", 200, elapsed_ms, tin, tout,
                        request_input=_payload_input(payload),
                        response_text="".join(resp_parts)[:8000] or None)
        emit(_usage_event(provider, key, model, tin, tout, elapsed_ms))

    @staticmethod
    def _aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _usage_event(provider: Provider | None, key: ApiKey | None, model: str,
                 tin: int, tout: int, duration_ms: float) -> dict[str, Any]:
    """Activity event carrying per-call model/token/cost accounting.

    Cost is computed from ``provider.pricing`` ({input_per_mtok,
    output_per_mtok, currency, model_overrides}); ``None`` when no pricing
    is configured — the UI hides the field rather than show a fake 0.
    """
    pricing = dict(provider.pricing or {}) if provider is not None else {}
    overrides = pricing.pop("model_overrides", None) or {}
    pricing.update(overrides.get(model) or {})
    pin, pout = pricing.get("input_per_mtok"), pricing.get("output_per_mtok")
    cost = (
        (tin or 0) * float(pin or 0) / 1e6
        + (tout or 0) * float(pout or 0) / 1e6
        if pin is not None or pout is not None else None
    )
    total = int(tin or 0) + int(tout or 0)
    return {
        "event": "usage",
        "text": f"{model} · {total:,} tokens",
        "model": model or "",
        "provider": provider.name if provider else "",
        "key_label": getattr(key, "label", "") or "",
        "tokens_in": int(tin or 0),
        "tokens_out": int(tout or 0),
        "tokens_total": total,
        "duration_ms": int(duration_ms),
        "cost_usd": cost,
        "currency": str(pricing.get("currency") or "USD"),
    }


def _extract_usage(chunk: bytes) -> tuple[int, int]:
    try:
        for line in chunk.decode(errors="ignore").splitlines():
            if line.startswith("data:") and "usage" in line:
                data = json.loads(line[5:].strip())
                u = data.get("usage") or {}
                return u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    except Exception:
        pass
    return 0, 0
