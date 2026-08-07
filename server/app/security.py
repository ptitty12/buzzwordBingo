"""Authentication and authorisation.

Two kinds of caller, deliberately asymmetric:

* **Administrators** authenticate with a PIN and have no account. The PIN is the only
  admin credential, so an empty ``ADMIN_PIN`` locks the console rather than opening it.
* **Players** have no account either. They join one game with a nickname and receive a
  token scoped to *that game* — it grants nothing anywhere else.

Session tokens are HMAC-signed so the server can trust the identity claim without
storing a secret per player. A token's subject is ``admin`` or ``p:<player_id>``.

Two other credential types exist:
  * ``X-API-Key`` — required by the transcript ingest endpoint, stored as a SHA-256 hash.
  * HTTP Basic with the admin PIN — gates the OpenAPI docs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

from .config import get_settings
from .db import execute, query_one, utcnow

API_KEY_PREFIX = "bb"
ADMIN_SUBJECT = "admin"
PLAYER_PREFIX = "p:"


@dataclass(frozen=True)
class Identity:
    """Who is calling. Exactly one of ``is_admin`` / ``player`` is meaningful."""

    is_admin: bool
    player: sqlite3.Row | None = None

    @property
    def game_id(self) -> str | None:
        return self.player["game_id"] if self.player is not None else None

    @property
    def display_name(self) -> str:
        if self.is_admin:
            return "admin"
        return self.player["nickname"] if self.player is not None else "anonymous"

    def owns_game(self, game_id: str) -> bool:
        """Admins reach every game; a player reaches only the one they joined."""
        return self.is_admin or self.game_id == game_id


def _sign(payload: str) -> str:
    secret = get_settings().secret_key.encode("utf-8")
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue_token(subject: str) -> str:
    """Create a signed session token for a subject (``admin`` or ``p:<player_id>``)."""
    payload = base64.urlsafe_b64encode(subject.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str) -> str | None:
    """Return the subject encoded in a token, or None when the signature is invalid."""
    if not token or "." not in token:
        return None
    payload, _, signature = token.partition(".")
    if not hmac.compare_digest(_sign(payload), signature):
        return None
    try:
        padding = "=" * (-len(payload) % 4)
        return base64.urlsafe_b64decode(payload + padding).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def issue_player_token(player_id: str) -> str:
    return issue_token(f"{PLAYER_PREFIX}{player_id}")


def issue_admin_token() -> str:
    return issue_token(ADMIN_SUBJECT)


def check_admin_pin(pin: str) -> bool:
    """Constant-time PIN comparison. An unset PIN denies every attempt."""
    configured = get_settings().admin_pin
    if not configured:
        return False
    return hmac.compare_digest(pin or "", configured)


def generate_api_key() -> tuple[str, str, str]:
    """Return ``(full_key, prefix, sha256_hash)``. The full key is shown exactly once."""
    raw = secrets.token_urlsafe(24)
    full = f"{API_KEY_PREFIX}_{raw}"
    return full, full[:11], hashlib.sha256(full.encode("utf-8")).hexdigest()


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- dependencies


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else authorization.strip()


def current_identity(authorization: str | None = Header(default=None)) -> Identity:
    """Resolve the caller. Returns an anonymous identity when no valid token is present."""
    subject = verify_token(_extract_bearer(authorization))
    if not subject:
        return Identity(is_admin=False, player=None)

    if subject == ADMIN_SUBJECT:
        return Identity(is_admin=True, player=None)

    if subject.startswith(PLAYER_PREFIX):
        player = query_one("SELECT * FROM players WHERE id = ?", (subject[len(PLAYER_PREFIX):],))
        if player is not None:
            return Identity(is_admin=False, player=player)

    return Identity(is_admin=False, player=None)


def require_identity(identity: Identity = Depends(current_identity)) -> Identity:
    """Require any authenticated caller — an admin or a player in some game."""
    if not identity.is_admin and identity.player is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Join a game to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return identity


def require_admin(identity: Identity = Depends(current_identity)) -> Identity:
    """Require an administrator token."""
    if not identity.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )
    return identity


def require_player(identity: Identity = Depends(require_identity)) -> Identity:
    """Require a player token specifically (admins have no card of their own)."""
    if identity.player is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action is for players — join the game with a nickname.",
        )
    return identity


def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    """Validate the ingest API key and record usage.

    Returns a human label for the caller so ingest events can be attributed.
    """
    settings = get_settings()
    if not settings.ingest_require_key:
        return x_api_key or "unauthenticated"

    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header.",
        )

    row = query_one(
        "SELECT * FROM api_keys WHERE key_hash = ? AND active = 1",
        (hash_api_key(x_api_key),),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API key.",
        )

    execute(
        "UPDATE api_keys SET last_used_at = ?, call_count = call_count + 1 WHERE id = ?",
        (utcnow(), row["id"]),
    )
    return row["name"]
