"""Runtime configuration, sourced from environment variables (12-factor style)."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings. Every field can be overridden by an env var of the same name."""

    model_config = SettingsConfigDict(
        env_file=(SERVER_ROOT / ".env", SERVER_ROOT.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Buzzword Bingo"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    #: SQLite database location. Use ":memory:" for ephemeral test runs.
    database_url: str = str(SERVER_ROOT / "data" / "bingo.db")

    #: Signing secret for session tokens. Generated per-process if unset, which means
    #: sessions do not survive a restart — set this explicitly in any real deployment.
    secret_key: str = ""

    #: Browser origins allowed to call the API.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    #: Optional PIN gating admin sign-in. Empty (the default) leaves the admin console
    #: open, which matches the "no passwords" product brief but is flagged in the UI.
    admin_pin: str = ""

    #: Nicknames automatically granted admin on first sign-up.
    bootstrap_admins: str = "admin"

    #: When true, /api/ingest requires a valid X-API-Key header.
    ingest_require_key: bool = True

    #: Directory holding the built frontend. Served at "/" when present.
    static_dir: str = str(SERVER_ROOT.parent / "web" / "dist")

    #: Max transcript tokens accepted in a single ingest request.
    max_ingest_tokens: int = 400

    #: Rolling n-gram window retained per game for multi-word phrase detection.
    phrase_window: int = 8

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def bootstrap_admin_list(self) -> list[str]:
        return [n.strip().lower() for n in self.bootstrap_admins.split(",") if n.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.secret_key:
        # Ephemeral fallback keeps local development frictionless; production
        # deployments must supply SECRET_KEY so tokens survive restarts.
        settings.secret_key = secrets.token_urlsafe(32)
    return settings
