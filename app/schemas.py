"""Pydantic schemas for the admin API and OpenAI-compatible surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------- Admin: providers ----------

class ProviderCreate(BaseModel):
    name: str
    display_name: str = ""
    adapter: str = "openai_compat"
    base_url: str
    auth_style: str = "bearer"
    auth_header: str = "Authorization"
    extra_headers: dict[str, str] = Field(default_factory=dict)
    default_env_key: str | None = None
    default_models: list[str] = Field(default_factory=list)
    limits: dict[str, int] = Field(default_factory=dict)
    pricing: dict[str, Any] = Field(default_factory=dict)
    daily_reset_tz: str | None = None
    requires_card: bool = False
    commercial_ok: bool = True
    enabled: bool = True
    rotation_strategy: str = "least_used"
    fallback_chain: list[str] = Field(default_factory=list)
    auto_route_tags: list[str] = Field(default_factory=list)
    docs_url: str | None = None
    notes: str | None = None
    verified_on: str | None = None
    account_id_env: str | None = None


class ProviderUpdate(BaseModel):
    display_name: str | None = None
    base_url: str | None = None
    enabled: bool | None = None
    rotation_strategy: str | None = None
    fallback_chain: list[str] | None = None
    auto_route_tags: list[str] | None = None
    limits: dict[str, int] | None = None
    pricing: dict[str, Any] | None = None
    default_models: list[str] | None = None
    extra_headers: dict[str, str] | None = None
    notes: str | None = None
    account_id_env: str | None = None


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
    pricing: dict[str, Any] = {}
    default_models: list[str]
    requires_card: bool
    commercial_ok: bool
    docs_url: str | None
    notes: str | None
    verified_on: str | None
    key_count: int = 0
    healthy_key_count: int = 0

    model_config = {"from_attributes": True}


# ---------- Admin: keys ----------

class KeyCreate(BaseModel):
    provider: str
    keys: list[str]
    label_prefix: str = ""
    quota_group: str | None = None
    priority: int = 100
    weight: int = 1
    notes: str | None = None


class KeyUpdate(BaseModel):
    label: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    weight: int | None = None
    quota_group: str | None = None
    notes: str | None = None


class KeyOut(BaseModel):
    id: int
    provider: str
    label: str
    key_hint: str
    quota_group: str | None
    enabled: bool
    priority: int
    weight: int
    daily_request_count: int
    daily_token_count: int
    last_used_at: datetime | None
    cooldown_until: datetime | None
    consecutive_failures: int
    last_error: str | None
    created_at: datetime
    notes: str | None
    healthy: bool = True
    decryptable: bool = True


class KeyImportResult(BaseModel):
    created: int
    skipped: int
    key_ids: list[int]


class KeyTestResult(BaseModel):
    ok: bool
    status_code: int | None = None
    latency_ms: float | None = None
    error: str | None = None
    model_used: str | None = None

class KeyBulkTestItem(BaseModel):
    key_id: int
    provider: str
    label: str = ""
    key_hint: str = ""
    enabled: bool = True
    ok: bool
    status_code: int | None = None
    latency_ms: float | None = None
    error: str | None = None
    model_used: str | None = None


class KeyBulkTestResult(BaseModel):
    tested: int
    ok: int
    failed: int
    results: list[KeyBulkTestItem]

class RawKeyTestIn(BaseModel):
    provider: str
    keys: list[str]


class RawKeyTestItem(BaseModel):
    key_hint: str = ""
    ok: bool
    status_code: int | None = None
    latency_ms: float | None = None
    error: str | None = None
    model_used: str | None = None


class RawKeyTestResult(BaseModel):
    tested: int
    ok: int
    failed: int
    results: list[RawKeyTestItem]


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
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: Any | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any | None = None
    response_format: dict[str, Any] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    user: str | None = None
    stream_options: dict[str, Any] | None = None

    model_config = {"extra": "allow"}


# ---------- Agents ----------

class AgentCreate(BaseModel):
    name: str
    description: str = ""
    system_prompt: str = ""
    model: str = "auto"
    temperature: float | None = None
    max_tokens: int | None = None
    enable_tools: bool = False
    output_format: str = "text"
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    description: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    enable_tools: bool | None = None
    output_format: str | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None


class AgentOut(BaseModel):
    id: int
    name: str
    description: str
    system_prompt: str
    model: str
    temperature: float | None
    max_tokens: int | None
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
    conversation_id: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    stream: bool = False


# ---------- Roleplay characters ----------

class CharacterIn(BaseModel):
    id: int | None = None
    name: str
    emoji: str = ""
    tagline: str = ""
    description: str = ""
    personality: str = ""
    style: str = ""
    greeting: str = ""
    background: str = ""
    knowledge_domain: str = ""
    motivations: str = ""
    boundaries: str = ""
    example_dialogue: str = ""
    speaking_style: str = ""
    category: str = "custom"
    tags: list[str] = Field(default_factory=list)
    visibility: str = "public"
    avatar_url: str = ""


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
    background: str = ""
    knowledge_domain: str = ""
    motivations: str = ""
    boundaries: str = ""
    example_dialogue: str = ""
    speaking_style: str = ""
    category: str = "custom"
    tags: list[str] = Field(default_factory=list)
    visibility: str = "public"
    avatar_url: str = ""
    creator_id: int | None = None
    created_at: datetime
    updated_at: datetime

    # Rows created before the extended columns existed hold NULL — coerce
    # to field defaults instead of failing validation.
    @field_validator(
        "background", "knowledge_domain", "motivations", "boundaries",
        "example_dialogue", "speaking_style", "avatar_url", mode="before")
    @classmethod
    def _none_str(cls, v: object) -> object:
        return "" if v is None else v

    @field_validator("category", mode="before")
    @classmethod
    def _none_category(cls, v: object) -> object:
        return "custom" if v is None else v

    @field_validator("visibility", mode="before")
    @classmethod
    def _none_visibility(cls, v: object) -> object:
        return "public" if v is None else v

    @field_validator("tags", mode="before")
    @classmethod
    def _none_tags(cls, v: object) -> object:
        return [] if v is None else v


class CharacterSyncIn(BaseModel):
    characters: list[CharacterIn] = Field(default_factory=list)
