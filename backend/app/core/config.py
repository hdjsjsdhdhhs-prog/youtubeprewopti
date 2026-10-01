"""Application configuration (env vars with the ``YTL_`` prefix).

The env file location is taken from ``YTL_ENV_FILE`` (default: ``.env`` in the CWD).
Nothing security-relevant is hardcoded; see ``.env.example``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YTL_", extra="ignore", env_file_encoding="utf-8")

    env: Literal["dev", "test", "prod"] = "dev"

    # Database
    database_url: str
    migration_database_url: str | None = None
    test_database_url: str | None = None
    db_pool_size: int = 5

    # Security
    master_key: SecretStr
    session_cookie_name: str = "ytl_session"
    csrf_cookie_name: str = "ytl_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    session_idle_minutes: int = 12 * 60
    session_absolute_hours: int = 14 * 24
    cookie_secure: bool = False
    login_rate_limit_attempts: int = 10
    login_rate_limit_window_seconds: int = 15 * 60

    # Storage
    storage_path: Path = PROJECT_ROOT / "storage"
    max_upload_bytes: int = 10 * 1024 * 1024

    # Modes
    demo_mode: bool = False

    # External providers (optional; absence => "not_configured")
    youtube_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    openai_vision_model: str | None = None
    openai_image_model: str | None = None

    # Thumbnails: "http" downloads from i.ytimg.com (no API quota), "mock" generates demo images.
    # Unset => "mock" in demo mode, "http" otherwise.
    thumbnail_fetcher: Literal["http", "mock"] | None = None

    # YouTube discovery (ADR-0008): "api" = YouTube Data API v3 (needs youtube_api_key), "mock" = offline.
    # Unset => "mock" in demo mode, "api" when a key is configured, otherwise not configured.
    youtube_provider: Literal["api", "mock"] | None = None
    youtube_daily_quota: int = 10_000  # units per Pacific-time day (Google default for a GCP project)
    # Channels fetched more recently than this are not re-fetched by discovery (saves quota).
    youtube_channel_refresh_hours: int = 24

    # Worker (Procrastinate, ADR-0003)
    worker_concurrency: int = 2
    worker_retry_max_attempts: int = 5  # total runs, including the first one
    worker_retry_base_seconds: int = 10  # backoff = base * 2**attempt, capped
    worker_retry_max_backoff_seconds: int = 3600
    worker_stalled_after_seconds: int = 60  # no worker heartbeat for this long => job is retried

    # HTTP client
    http_timeout_seconds: float = 20.0

    # Logging
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator(
        "youtube_api_key", "openai_api_key", "thumbnail_fetcher", "youtube_provider", mode="before"
    )
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        return None if v in ("", None) else v

    @property
    def migration_url(self) -> str:
        return self.migration_database_url or self.database_url

    @property
    def effective_thumbnail_fetcher(self) -> Literal["http", "mock"]:
        if self.thumbnail_fetcher is not None:
            return self.thumbnail_fetcher
        return "mock" if self.demo_mode else "http"

    @property
    def effective_youtube_provider(self) -> Literal["api", "mock"] | None:
        if self.youtube_provider is not None:
            return self.youtube_provider
        if self.demo_mode:
            return "mock"
        return "api" if self.youtube_api_key is not None else None


def _env_file() -> str | None:
    path = os.environ.get("YTL_ENV_FILE", ".env")
    return path if Path(path).is_file() else None


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=_env_file())


def libpq_dsn(sqlalchemy_url: str) -> str:
    """Convert ``postgresql+psycopg://`` to a plain libpq URL (for Procrastinate)."""
    return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)
