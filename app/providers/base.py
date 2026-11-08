"""Provider adapter contract.

Adapters translate between the gateway's OpenAI-style requests and a
provider's HTTP API. Most providers are OpenAI-compatible and use the
generic adapter; only URL/auth quirks need a subclass.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Mapping

import httpx

from app.models import Provider


class ProviderError(Exception):
    """Error classified for routing decisions."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = True,
        retry_after_s: float | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after_s = retry_after_s
        self.headers = dict(headers or {})


class BaseAdapter:
    """Interface all provider adapters implement."""

    def __init__(self, provider: Provider) -> None:
        self.provider = provider

    def build_url(self, path: str) -> str:
        return f"{self.provider.base_url.rstrip('/')}/{path.lstrip('/')}"

    def build_headers(self, api_key: str) -> dict[str, str]:
        headers = dict(self.provider.extra_headers or {})
        if self.provider.auth_style == "bearer":
            headers[self.provider.auth_header] = f"Bearer {api_key}"
        elif self.provider.auth_style == "x-api-key":
            headers[self.provider.auth_header] = api_key
        elif self.provider.auth_style == "query":
            pass  # subclasses append to URL
        return headers

    def normalize_model(self, model: str) -> str:
        return model

    async def chat_completion(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        resp = await client.post(
            self.build_url("/chat/completions"),
            headers=self.build_headers(api_key),
            json=payload,
        )
        self.raise_for_status(resp)
        return resp.json()

    async def post_json(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Generic passthrough POST (embeddings, etc.)."""
        resp = await client.post(
            self.build_url(path), headers=self.build_headers(api_key), json=payload
        )
        self.raise_for_status(resp)
        return resp.json()

    def chat_stream(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        raise NotImplementedError

    async def list_models(
        self, client: httpx.AsyncClient, api_key: str
    ) -> list[str]:
        resp = await client.get(
            self.build_url("/models"), headers=self.build_headers(api_key)
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [m["id"] for m in data.get("data", []) if isinstance(m, dict) and "id" in m]

    def raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        retry_after = (
            _parse_retry_after(resp.headers.get("retry-after"))
            or _ratelimit_reset_s(resp.headers)
        )
        retryable = resp.status_code in (408, 409, 425, 429, 500, 502, 503, 504)
        body = resp.text[:500]
        # Model-gone/unknown-model 404s are permanent for this model.
        if resp.status_code == 404 and "model" in body.lower():
            retryable = False
        raise ProviderError(
            f"{self.provider.name} returned {resp.status_code}: {body}",
            status_code=resp.status_code,
            retryable=retryable or resp.status_code in (401, 403),
            retry_after_s=retry_after,
            headers=resp.headers,
        )


def _ratelimit_reset_s(headers) -> float | None:
    """Read x-ratelimit-reset-* style headers (Groq, OpenRouter, etc.)."""
    import re as _re

    for name, value in headers.items():
        if not _re.match(r"x-ratelimit-reset", name, _re.I):
            continue
        v = value.strip().lower().replace("ms", "").replace("s", "")
        try:
            f = float(v)
            # heuristic: values > 1e12 look like epoch-ms; >1e9 epoch-s
            if f > 1e12:
                import time
                return max(0.0, f / 1000 - time.time())
            if f > 1e9:
                import time
                return max(0.0, f - time.time())
            if "ms" in value.lower():
                return f / 1000
            return f
        except ValueError:
            continue
    return None


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
