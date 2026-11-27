"""Application configuration via pydantic-settings."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration. All values may be overridden via env vars or .env."""

    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    # Canonical public origin (e.g. https://ichat.example.com) used in
    # sitemap.xml / robots.txt. Falls back to the request's base URL.
    # Env var: PUBLIC_BASE_URL.
    public_base_url: Optional[str] = None

    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'gateway.db'}"
    # Where media / research snapshots live. Independent of database_url so the
    # app can run on PostgreSQL. Env var: DATA_DIR.
    data_dir: str = str(BASE_DIR / "data")

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    # Required token for /admin/* endpoints.
    admin_token: str = "admin-change-me"
    # Comma-separated emails always granted the admin role — assigned on
    # registration and promoted on login. Env var: ADMIN_EMAILS.
    admin_emails: str = ""
    # When true, /v1 app endpoints require a user session token issued by
    # /v1/auth/login. Env var: NOVA_AUTH_REQUIRED (bool).
    auth_required: bool = Field(default=True,
                                validation_alias="NOVA_AUTH_REQUIRED")
    # When set, /v1/auth/register requires this referral code.
    # Env var: REFERRAL_CODE.
    referral_code: Optional[str] = None
    # Google Identity Services client id; enables "Sign in with Google".
    # Env var: GOOGLE_CLIENT_ID.
    google_client_id: Optional[str] = None
    # Env vars: GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET /
    # GITHUB_OAUTH_REDIRECT. When set, profile GitHub linking uses real
    # OAuth; otherwise it falls back to verified-username connect.
    github_client_id: Optional[str] = None
    github_client_secret: Optional[str] = None
    github_oauth_redirect: Optional[str] = None

    # Fernet master key; auto-generated on first run if unset.
    encryption_key: Optional[str] = None
    encryption_key_file: Path = BASE_DIR / "data" / ".encryption_key"

    providers_yaml: Path = BASE_DIR / "providers.yaml"

    request_timeout_s: float = 120.0
    connect_timeout_s: float = 15.0
    stream_idle_timeout_s: float = 90.0
    # Bail to the next key/provider if the first SSE chunk takes too long.
    first_chunk_timeout_s: float = 20.0
    # Bail to the next key/provider if a non-streaming attempt takes too long.
    attempt_timeout_s: float = 30.0
    # Hard wall-clock cap on time-to-first-response: when this budget is
    # spent, no new provider/key attempt is started and in-flight waits are
    # clamped, so a busy upstream cannot park a request indefinitely.
    response_deadline_s: float = 30.0

    model_cache_ttl_s: int = 3600
    circuit_breaker_failures: int = 5
    circuit_breaker_reset_s: float = 60.0

    max_retries_per_request: int = 6
    cooldown_base_s: float = 30.0
    cooldown_max_s: float = 3600.0
    preflight_quota_check: bool = False

    log_level: str = "INFO"

    # Astra DB vector memory (optional — memory is off when unset).
    astra_db_application_token: Optional[str] = os.getenv("ASTRA_DB_APPLICATION_TOKEN")
    astra_db_api_endpoint: Optional[str] = os.getenv("ASTRA_DB_API_ENDPOINT")
    astra_db_keyspace: Optional[str] = os.getenv("ASTRA_DB_KEYSPACE")
    memory_short_limit: int = 6
    memory_long_limit: int = 6

    # ---- Nova v2 research subsystem ----
    # Astra collection holding evidence embeddings (vectorize-enabled).
    astra_evidence_collection: str = "evidence"
    # Optional search provider keys; DuckDuckGo lite is the free fallback.
    brave_search_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    # Exa neural search key (env EXA_API_KEY or .env); DB setting still wins
    # as a fallback inside services.tools._exa_api_key.
    exa_api_key: Optional[SecretStr] = None
    # Fetched-page caps and on-disk snapshot location (under the data dir).
    research_fetch_max_bytes: int = 1_000_000
    research_snapshot_dir: str = "research"          # under <data_dir>/
    research_fetch_ttl_default_s: int = 86_400       # 24 h for unclassified pages
    research_worker_enabled: bool = True             # in-process job worker
    research_worker_poll_s: float = 1.0
    research_job_lease_s: int = 30                   # worker lease / heartbeat
    research_sandbox_timeout_s: int = 30
    research_sandbox_mem_mb: int = 512

    # ---- Razorpay payments ----
    # Public key id is safe to expose to the browser; the secret is
    # backend-only and used for HMAC-SHA256 signature verification.
    # Env vars: RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET.
    razorpay_key_id: Optional[str] = None
    razorpay_key_secret: Optional[SecretStr] = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
