"""Cloudflare Workers AI adapter — expands {account_id} in base_url."""

from __future__ import annotations

from app.providers.base import ProviderError
from app.providers.openai_compat import OpenAICompatAdapter


class CloudflareAdapter(OpenAICompatAdapter):
    def build_url(self, path: str) -> str:
        base = self.provider.base_url
        if "{account_id}" in base:
            from app.providers.registry import _env_value

            env_name = self.provider.account_id_env or "CLOUDFLARE_ACCOUNT_ID"
            account_id = _env_value(env_name)
            if not account_id:
                raise ProviderError(
                    f"{self.provider.name}: env var {env_name} not set",
                    retryable=False,
                )
            base = base.replace("{account_id}", account_id)
        return f"{base.rstrip('/')}/{path.lstrip('/')}"
