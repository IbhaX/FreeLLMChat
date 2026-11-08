"""Application configuration via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration. All values may be overridden via env vars or .env."""

    model_config = SettingsConfigDict(env_file=str(BASE_DIR / ".env"), extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000

    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'gateway.db'}"

    # Optional bearer token protecting the gateway itself (/v1/*).
    gateway_api_key: Optional[str] = None
    # Required token for /admin/* endpoints.
    admin_token: str = "admin-change-me"

    # Fernet master key; auto-generated on first run if unset.
    encryption_key: Optional[str] = None
    encryption_key_file: Path = BASE_DIR / "data" / ".encryption_key"

    providers_yaml: Path = BASE_DIR / "providers.yaml"

    request_timeout_s: float = 120.0
    connect_timeout_s: float = 15.0
    stream_idle_timeout_s: float = 90.0

    model_cache_ttl_s: int = 3600
    circuit_breaker_failures: int = 5
    circuit_breaker_reset_s: float = 60.0

    max_retries_per_request: int = 6
    cooldown_base_s: float = 30.0
    cooldown_max_s: float = 3600.0

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
