"""PydanticAI agent layer — the application's single orchestration framework.

All agent definitions, model adaptation, tool bridging and orchestration
live here. ``app.gateway`` remains the provider/proxy layer underneath;
nothing else should orchestrate LLM calls directly.
"""
