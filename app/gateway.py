"""Request orchestration: model resolution, key rotation, failover,
mid-stream failure handling."""

from __future__ import annotations

import asyncio
import json
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
from app.providers.base import ProviderError
from app.providers.registry import build_adapter
from app.routing.circuit_breaker import CircuitBreaker
from app.routing.fallback import resolve_candidates
from app.routing.quota import QuotaTracker
from app.routing.rotator import Rotator


def _clean_exhaustion_msg(msg: str) -> str:
    """Turn a raw upstream error (often a full provider JSON body) into a
    short line safe to show in the chat bubble."""
    import re

    cleaned = re.sub(r"[\[{].*", "", msg or "", flags=re.DOTALL).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" :_")
    if not cleaned:
        cleaned = "provider error"
    return f"All providers are unavailable right now (last error: {cleaned[:140]})"


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

        # First candidate per provider, preserving provider order.
        by_provider: list[tuple[Provider, str]] = []
        seen: set[str] = set()
        for p, m in candidates:
            if p.name not in seen:
                seen.add(p.name)
                by_provider.append((p, m))
        emit({"event": "routing",
              "text": f"Routing '{requested}' across {len(by_provider)} provider(s)"})
        await self._sticky_first(session, requested, by_provider)

        last_err: ProviderError | None = None
        tried: set[int] = set()
        attempts = 0
        for sweep in range(2):
            if sweep:
                await self._second_sweep_reset(session, emit)
                tried.clear()
            inner = await self._chat_sweep(
                session, request_id, t0, payload, by_provider,
                emit if sweep == 0 else (lambda ev: None), tried, attempts,
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

    async def _sticky_first(
        self, session: AsyncSession, requested: str,
        by_provider: list[tuple[Provider, str]],
    ) -> None:
        """Move the last working (provider, model) to the front."""
        sticky = (
            await session.execute(
                select(StickyRoute).where(StickyRoute.requested == requested)
            )
        ).scalar_one_or_none()
        if not sticky:
            return
        for i, (p, m) in enumerate(by_provider):
            if p.name == sticky.provider_name and m == sticky.model:
                by_provider.insert(0, by_provider.pop(i))
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

    async def _emit_skip_reason(self, session, provider, attempts, prov_attempts, emit):
        """When a provider was skipped without a single attempt, say why so
        the activity list does not show a bare 'Trying' line."""
        if attempts != prov_attempts:
            return
        if attempts >= self.settings.max_retries_per_request:
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
        self, session, request_id, t0, payload, by_provider, emit, tried, attempts,
    ):
        """One pass over providers. Returns (result, provider, key, model)
        on success or None when the sweep is exhausted. Stashes
        _last_err/_last_attempts for the caller."""
        last_err: ProviderError | None = None
        for provider, model in by_provider:
            if not self.breaker.allow(provider.name):
                continue
            emit({"event": "try", "text": f"Trying {provider.name} · {model}"})
            body = dict(payload)
            body["model"] = model
            body["stream"] = False
            prov_attempts = attempts
            while attempts < self.settings.max_retries_per_request:
                key = await self.rotator.pick(session, provider, exclude_ids=tried)
                if key is None:
                    break
                tried.add(key.id)
                if key.id in self._undecryptable:
                    continue
                attempts += 1
                try:
                    raw = self._decrypt_key(key)
                    adapter = build_adapter(provider)
                    try:
                        result = await asyncio.wait_for(
                            adapter.chat_completion(self.client, raw, body),
                            timeout=self.settings.attempt_timeout_s,
                        )
                    except asyncio.TimeoutError:
                        raise ProviderError(
                            f"no response in {self.settings.attempt_timeout_s:.0f}s",
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
                    if e.status_code == 404 and "model" in e.message.lower():
                        # Dead model: the key is fine — don't cool it or
                        # trip the provider breaker.
                        await self._mark_model_dead(session, provider, model)
                        emit({"event": "fail",
                              "text": f"{provider.name} failed: {e.message[:80]}"})
                        emit({"event": "next",
                              "text": "Model unavailable — next provider"})
                        break
                    await self._record_failure(session, key, provider, e)
                    emit({"event": "fail",
                          "text": f"{provider.name} failed: {e.message[:80]}"})
                    if not e.retryable:
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
                await self._log(
                    session, request_id, payload["model"], model, provider,
                    key, "ok", 200, (time.monotonic() - t0) * 1000, tin, tout,
                    request_input=_payload_input(payload),
                    response_text=_result_text(result))
                self._last_err, self._last_attempts = last_err, attempts
                return result, provider, key, model
            await self._emit_skip_reason(session, provider, attempts, prov_attempts, emit)
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
        candidates = await self._attempts(session, requested)
        if not candidates:
            raise GatewayExhausted(
                f"No provider can serve model '{requested}'", 404
            )

        by_provider: list[tuple[Provider, str]] = []
        seen: set[str] = set()
        for p, m in candidates:
            if p.name not in seen:
                seen.add(p.name)
                by_provider.append((p, m))
        emit({"event": "routing", "text": f"Routing '{requested}' across {len(by_provider)} provider(s)"})
        await self._sticky_first(session, requested, by_provider)

        last_err: ProviderError | None = None
        tried: set[int] = set()
        attempts = 0
        for sweep in range(2):
            if sweep:
                await self._second_sweep_reset(session, emit)
                tried.clear()
            for provider, model in by_provider:
                if not self.breaker.allow(provider.name):
                    continue
                emit({"event": "try", "text": f"Trying {provider.name} · {model}"})
                body = dict(payload)
                body["model"] = model
                body["stream"] = True
                body.setdefault("stream_options", {"include_usage": True})
                prov_attempts = attempts
                while attempts < self.settings.max_retries_per_request:
                    key = await self.rotator.pick(session, provider, exclude_ids=tried)
                    if key is None:
                        break
                    tried.add(key.id)
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
                            timeout=self.settings.first_chunk_timeout_s,
                        )
                    except (asyncio.TimeoutError, TimeoutError):
                        last_err = ProviderError(
                            f"no response in {self.settings.first_chunk_timeout_s:.0f}s",
                            retryable=True,
                        )
                        await self._record_failure(session, key, provider, last_err)
                        emit({"event": "retry", "text": f"{provider.name} · {key.label} — no response, trying next"})
                        continue
                    except StopAsyncIteration:
                        last_err = ProviderError("empty stream", retryable=True)
                        await self._record_failure(session, key, provider, last_err)
                        emit({"event": "retry", "text": f"{provider.name} · {key.label} — empty stream, next key"})
                        continue
                    except ProviderError as e:
                        last_err = e
                        if e.status_code == 404 and "model" in e.message.lower():
                            # Dead model: don't cool the key.
                            await self._mark_model_dead(session, provider, model)
                            emit({"event": "retry",
                                  "text": f"{provider.name} · {key.label} — model unavailable, next provider"})
                            break
                        await self._record_failure(session, key, provider, e)
                        emit({"event": "retry", "text": f"{provider.name} · {key.label} failed ({e.status_code or 'error'})"})
                        if not e.retryable:
                            break
                        continue
                    except Exception as e:
                        last_err = ProviderError(str(e), retryable=True)
                        await self._record_failure(session, key, provider, last_err)
                        emit({"event": "retry", "text": f"{provider.name} · {key.label} — {e}, retrying"})
                        continue
                    await self._sticky_set(session, requested, provider, model)
                    return (
                        self._wrap_stream(
                            session, stream, first, request_id, requested, model,
                            provider, key, payload,
                        ),
                        provider,
                        key,
                        model,
                    )
                await self._emit_skip_reason(session, provider, attempts, prov_attempts, emit)
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
    ) -> AsyncIterator[bytes]:
        t0 = time.monotonic()
        tin = tout = 0
        resp_parts: list[str] = []
        idle_s = self.settings.stream_idle_timeout_s
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
        await self._log(session, request_id, requested, model, provider, key,
                        "ok", 200, (time.monotonic() - t0) * 1000, tin, tout,
                        request_input=_payload_input(payload),
                        response_text="".join(resp_parts)[:8000] or None)

    @staticmethod
    def _aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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
