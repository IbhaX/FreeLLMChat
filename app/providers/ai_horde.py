"""AI Horde's hosted OpenAI-compatible text inference adapter."""

from __future__ import annotations

from app.providers.openai_compat import OpenAICompatAdapter


class AIHordeAdapter(OpenAICompatAdapter):
    """Use Horde's anonymous token for keyless seed rows.

    Users may optionally add a registered Horde API key in the normal key
    manager. Only the app's internal keyless placeholder is translated.
    """

    ANONYMOUS_KEY = "0000000000"
    KEYLESS_PLACEHOLDERS = {"ollama-local", ""}

    def build_headers(self, api_key: str) -> dict[str, str]:
        headers = dict(self.provider.extra_headers or {})
        key = (
            self.ANONYMOUS_KEY
            if api_key in self.KEYLESS_PLACEHOLDERS
            else api_key
        )
        headers[self.provider.auth_header] = f"Bearer {key}"
        return headers
