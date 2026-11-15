"""Ollama adapter — local/self-hosted models served by an Ollama daemon.

Ollama requires no API key; the seeded placeholder key exists only so the
normal rotation/health machinery applies unchanged. Chat goes through
Ollama's OpenAI-compatible ``/v1`` API; model discovery uses the native
``/api/tags`` endpoint, which lists exactly the models pulled on the host.
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator

import httpx

from app.providers.openai_compat import OpenAICompatAdapter

DEFAULT_OLLAMA_HOST = "http://localhost:11434"


def normalize_host(host: str) -> str:
    """Accept ``host``, ``host:port``, or full URLs; return a base URL."""
    host = host.strip().rstrip("/")
    if host and "://" not in host:
        host = f"http://{host}"
    return host


def candidate_hosts() -> list[str]:
    """Ordered Ollama base URLs to probe, deduplicated.

    ``OLLAMA_HOST`` (the official env var) is tried first, then the
    conventional localhost addresses.
    """
    hosts = [
        os.environ.get("OLLAMA_HOST", ""),
        DEFAULT_OLLAMA_HOST,
        "http://127.0.0.1:11434",
    ]
    return list(dict.fromkeys(normalize_host(h) for h in hosts if h.strip()))


class OllamaAdapter(OpenAICompatAdapter):
    """OpenAI-compatible chat on ``{host}/v1`` + native model discovery."""

    def native_base_url(self) -> str:
        """Ollama root URL (the part before ``/v1``)."""
        base = self.provider.base_url.rstrip("/")
        return base[:-3] if base.endswith("/v1") else base

    def build_headers(self, api_key: str) -> dict[str, str]:
        # Ollama ignores auth; the placeholder key is never sent upstream.
        return dict(self.provider.extra_headers or {})

    def _request_timeout(self) -> httpx.Timeout | None:
        """Per-provider override via limits.request_timeout_s (local models are
        slow on big prompts). None → use the client's default timeout."""
        secs = (self.provider.limits or {}).get("request_timeout_s")
        return httpx.Timeout(float(secs), connect=15.0) if secs else None

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
            timeout=self._request_timeout() or httpx.USE_CLIENT_DEFAULT,
        )
        self.raise_for_status(resp)
        return resp.json()

    async def _stream(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        async with client.stream(
            "POST",
            self.build_url("/chat/completions"),
            headers=self.build_headers(api_key),
            json=payload,
            timeout=self._request_timeout() or httpx.USE_CLIENT_DEFAULT,
        ) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                self.raise_for_status(resp)
            async for line in resp.aiter_lines():
                if line:
                    yield (line + "\n\n").encode()

    async def list_models(
        self, client: httpx.AsyncClient, api_key: str
    ) -> list[str]:
        """Return pulled model names from the native ``/api/tags`` endpoint."""
        try:
            resp = await client.get(f"{self.native_base_url()}/api/tags")
        except httpx.HTTPError:
            return []
        if resp.status_code != 200:
            return []
        data: dict[str, Any] = resp.json()
        return sorted(
            m["name"]
            for m in data.get("models", [])
            if isinstance(m, dict) and m.get("name")
        )
