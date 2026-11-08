"""Generic adapter for OpenAI-compatible providers (the common case)."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from app.providers.base import BaseAdapter, ProviderError


class OpenAICompatAdapter(BaseAdapter):
    def chat_stream(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        return self._stream(client, api_key, payload)

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
        ) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                self.raise_for_status(resp)
            async for line in resp.aiter_lines():
                if line:
                    yield (line + "\n\n").encode()
