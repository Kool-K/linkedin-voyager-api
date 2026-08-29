"""
app/config.py
─────────────
Centralised configuration via Pydantic BaseSettings.
All secrets are read from environment variables (or a .env file).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application-level settings.

    Required env vars:
        LINKEDIN_LI_AT      – LinkedIn session cookie (li_at)
        LINKEDIN_JSESSIONID – LinkedIn CSRF cookie  (JSESSIONID, e.g. "ajax:1234...")

    Optional env vars:
        APP_ENV             – "development" | "production"   (default: development)
        LOG_LEVEL           – Python log level string        (default: INFO)
        REQUEST_TIMEOUT     – HTTP request timeout seconds   (default: 20)
        MAX_CONNECTIONS     – httpx connection pool size      (default: 20)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LinkedIn credentials ────────────────────────────────────────────────
    linkedin_li_at: str = Field(
        ...,
        description="LinkedIn li_at session authentication cookie value.",
    )
    linkedin_jsessionid: str = Field(
        ...,
        description='LinkedIn JSESSIONID cookie, e.g. "ajax:1234567890".',
    )

    # ── Application settings ────────────────────────────────────────────────
    app_env: Literal["development", "production"] = Field(
        default="development",
        description="Runtime environment.",
    )
    log_level: str = Field(
        default="INFO",
        description="Python logging level (DEBUG, INFO, WARNING, ERROR).",
    )
    request_timeout: float = Field(
        default=20.0,
        ge=1.0,
        le=120.0,
        description="HTTP request timeout in seconds.",
    )
    max_connections: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Maximum simultaneous HTTP connections in pool.",
    )

    # ── Derived / computed properties ──────────────────────────────────────
    @property
    def csrf_token(self) -> str:
        """
        LinkedIn's CSRF token is the JSESSIONID value with surrounding
        double-quotes stripped, e.g. `"ajax:123"` → `ajax:123`.
        """
        return self.linkedin_jsessionid.strip('"')

    @field_validator("linkedin_jsessionid")
    @classmethod
    def validate_jsessionid(cls, v: str) -> str:
        """Ensure the JSESSIONID looks like a valid LinkedIn session token."""
        cleaned = v.strip('"')
        if not cleaned.startswith("ajax:"):
            raise ValueError(
                "LINKEDIN_JSESSIONID must start with 'ajax:' "
                f"(received: {v!r}). "
                "Copy the exact cookie value from your browser DevTools."
            )
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}, got {v!r}")
        return upper


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a cached singleton Settings instance.
    Use FastAPI's Depends(get_settings) to inject settings into route handlers.
    """
    return Settings()  # type: ignore[call-arg]
