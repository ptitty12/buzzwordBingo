"""Runtime configuration, sourced from environment variables (12-factor style)."""

from __future__ import annotations

import logging
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

    app_name: str = "Jargon Watch"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    #: SQLite database location. Use ":memory:" for ephemeral test runs.
    database_url: str = str(SERVER_ROOT / "data" / "jargon.db")

    #: Signing secret for session tokens. When unset, a key is generated once and
    #: persisted next to the database (see `_resolve_secret_key`) so restarts do not
    #: silently sign every participant out. Set it explicitly in any real deployment.
    secret_key: str = ""

    #: Browser origins allowed to call the API.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    #: PIN required to reach the admin console. Administrators are not accounts — this
    #: PIN *is* the admin credential, so an empty value disables admin access entirely
    #: rather than leaving the console open.
    admin_pin: str = "2165"

    #: When true, /api/ingest requires a valid X-API-Key header.
    ingest_require_key: bool = True

    #: Gate the OpenAPI docs behind HTTP Basic using the admin PIN, so participants poking
    #: around the site never land on the integration surface.
    protect_api_docs: bool = True

    #: Directory holding the built frontend. Served at "/" when present.
    static_dir: str = str(SERVER_ROOT.parent / "web" / "dist")

    #: Max transcript tokens accepted in a single ingest request.
    max_ingest_tokens: int = 400

    #: Rolling n-gram window retained per meeting for multi-word phrase detection.
    phrase_window: int = 8

    # ----------------------------------------------------------------- word moderation

    #: Anthropic API key for the buzzword judge. Without it, participant suggestions queue
    #: for manual admin review instead of being auto-decided.
    anthropic_api_key: str = ""

    #: Model used to judge whether a suggested word is "buzzwordy enough".
    moderation_model: str = "claude-opus-5"

    #: How many words a single participant may suggest per meeting.
    suggestions_per_participant: int = 10

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def moderation_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


def _resolve_secret_key(database_url: str) -> str:
    """Return a signing key that survives a restart, generating one on first boot.

    A per-process key looks harmless until you restart: every token ever issued stops
    verifying at once, and participants who are mid-draft get "Join a meeting to continue." with
    no idea why. In development that fires on something as innocuous as editing .env,
    because autoreload restarts the process. So the generated key is written next to the
    database and reused, which makes a restart invisible to everyone holding a session.

    An explicit SECRET_KEY always completions; this only covers the unset case.
    """
    if database_url == ":memory:":
        return secrets.token_urlsafe(32)  # nothing to persist alongside

    path = Path(database_url).expanduser().resolve().parent / ".secret_key"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass  # missing or unreadable — fall through and mint one

    key = secrets.token_urlsafe(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key, encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        # Read-only filesystem: degrade to an ephemeral key rather than refusing to
        # boot. Deployments in that shape should be setting SECRET_KEY anyway.
        logging.getLogger("jargon").warning(
            "Could not persist a generated SECRET_KEY at %s — sessions will not "
            "survive a restart. Set SECRET_KEY explicitly.",
            path,
        )
    return key


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.secret_key:
        settings.secret_key = _resolve_secret_key(settings.database_url)
    return settings
