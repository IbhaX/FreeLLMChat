"""Gemini adapter.

AI Studio exposes an OpenAI-compatible surface, so this reuses the generic
adapter; kept as a named adapter so Gemini-specific quirks have a home.
"""

from __future__ import annotations

from app.providers.openai_compat import OpenAICompatAdapter


class GeminiAdapter(OpenAICompatAdapter):
    pass
