"""Pydantic schemas for the admin API and OpenAI-compatible surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------- Admin: providers ----------

class ProviderCreate(BaseModel):
    name: str
    display_name: str = ""
    adapter: str = "openai_compat"
    base_url: str
    auth_style: str = "bearer"
    auth_header: str = "Authorization"
    extra_headers: dict[str, str] = Field(default_factory=dict)
    default_env_key: Optional[str] = None
    default_models: list[str] = Field(default_factory=list)
    limits: dict[str, int] = Field(default_factory=dict)
    daily_reset_tz: Optional[str] = None
    requires_card: bool = False
    commercial_ok: bool = True
    enabled: bool = True
    rotation_strategy: str = "least_used"
    fallback_chain: list[str] = Field(default_factory=list)
    auto_route_tags: list[str] = Field(default_factory=list)
    docs_url: Optional[str] = None
    notes: Optional[str] = None
    verified_on: Optional[str] = None
    account_id_env: Optional[str] = None


class ProviderUpdate(BaseModel):
    display_name: Optional[str] = None
    base_url: Optional[str] = None
    enabled: Optional[bool] = None
    rotation_strategy: Optional[str] = None
    fallback_chain: Optional[list[str]] = None
    auto_route_tags: Optional[list[str]] = None
    limits: Optional[dict[str, int]] = None
    default_models: Optional[list[str]] = None
    extra_headers: Optional[dict[str, str]] = None
    notes: Optional[str] = None
    account_id_env: Optional[str] = None


class ProviderOut(BaseModel):
    id: int
    name: str
    display_name: str
    adapter: str
    base_url: str
    enabled: bool
    rotation_strategy: str
    fallback_chain: list[str]
    auto_route_tags: list[str]
    limits: dict[str, Any]
    default_models: list[str]
    requires_card: bool
    commercial_ok: bool
    docs_url: Optional[str]
    notes: Optional[str]
    verified_on: Optional[str]
    key_count: int = 0
    healthy_key_count: int = 0

    model_config = {"from_attributes": True}


# ---------- Admin: keys ----------

class KeyCreate(BaseModel):
    provider: str
    keys: list[str]
    label_prefix: str = ""
    quota_group: Optional[str] = None
    priority: int = 100
    weight: int = 1
    notes: Optional[str] = None


class KeyUpdate(BaseModel):
    label: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    weight: Optional[int] = None
    quota_group: Optional[str] = None
    notes: Optional[str] = None


class KeyOut(BaseModel):
    id: int
    provider: str
    label: str
    key_hint: str
    quota_group: Optional[str]
    enabled: bool
    priority: int
    weight: int
    daily_request_count: int
    daily_token_count: int
    last_used_at: Optional[datetime]
    cooldown_until: Optional[datetime]
    consecutive_failures: int
    last_error: Optional[str]
    created_at: datetime
    notes: Optional[str]
    healthy: bool = True


class KeyImportResult(BaseModel):
    created: int
    skipped: int
    key_ids: list[int]


class KeyTestResult(BaseModel):
    ok: bool
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    model_used: Optional[str] = None


# ---------- Admin: stats ----------

class StatsOut(BaseModel):
    total_requests: int
    requests_24h: int
    error_rate_24h: float
    tokens_in_24h: int
    tokens_out_24h: int
    providers: int
    keys_total: int
    keys_healthy: int
    keys_in_cooldown: int
    per_provider: list[dict[str, Any]]
    token_budget: list[dict[str, Any]] = []
    est_tokens_total: int | None = None


# ---------- OpenAI-compatible ----------

class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    stream: bool = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    stop: Optional[Any] = None
    tools: Optional[list[dict[str, Any]]] = None
    tool_choice: Optional[Any] = None
    response_format: Optional[dict[str, Any]] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    seed: Optional[int] = None
    user: Optional[str] = None
    stream_options: Optional[dict[str, Any]] = None

    model_config = {"extra": "allow"}


# ---------- Agents ----------

class AgentCreate(BaseModel):
    name: str
    description: str = ""
    system_prompt: str = ""
    model: str = "auto"
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    enable_tools: bool = False
    output_format: str = "text"
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    enable_tools: Optional[bool] = None
    output_format: Optional[str] = None
    enabled: Optional[bool] = None
    config: Optional[dict[str, Any]] = None


class AgentOut(BaseModel):
    id: int
    name: str
    description: str
    system_prompt: str
    model: str
    temperature: Optional[float]
    max_tokens: Optional[int]
    enable_tools: bool
    output_format: str
    enabled: bool
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentChatRequest(BaseModel):
    message: Any  # str or OpenAI-style content parts list (text + image_url)
    history: list[dict[str, Any]] = Field(default_factory=list)
    stream: bool = False


# ---------- Roleplay characters ----------

class CharacterIn(BaseModel):
    id: Optional[int] = None
    name: str
    emoji: str = ""
    tagline: str = ""
    description: str = ""
    personality: str = ""
    style: str = ""
    greeting: str = ""


class CharacterOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    emoji: str
    tagline: str
    description: str
    personality: str
    style: str
    greeting: str
    created_at: datetime
    updated_at: datetime


class CharacterSyncIn(BaseModel):
    characters: list[CharacterIn] = Field(default_factory=list)
