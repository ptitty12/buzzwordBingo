"""Authentication and authorisation.

The product brief calls for password-free accounts: a player picks a nickname from a
dropdown or creates one. That means a session token is an *identity claim*, not a proof
of secrecy — it is HMAC-signed so the server can trust that it issued the token, which
stops clients from forging admin identity by editing localStorage.

Two other credential types exist:
  * ``X-API-Key`` — required by the transcript ingest endpoint, stored as a SHA-256 hash.
  * ``admin_pin`` — optional PIN gating admin sign-in (empty by default, see config).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3

from fastapi import Depends, Header, HTTPException, status

from .config import get_settings
from .db import execute, query_one, utcnow

API_KEY_PREFIX = "bb"


def _sign(payload: str) -> str:
    secret = get_settings().secret_key.encode("utf-8")
    digest = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def issue_token(user_id: str) -> str:
    """Create a signed session token for a user id."""
    payload = base64.urlsafe_b64encode(user_id.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str) -> str | None:
    """Return the user id encoded in a token, or None when the signature is invalid."""
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


def current_user_optional(
    authorization: str | None = Header(default=None),
) -> sqlite3.Row | None:
    """Resolve the caller from an ``Authorization: Bearer <token>`` header, if present."""
    token = _extract_bearer(authorization)
    if not token:
        return None
    user_id = verify_token(token)
    if not user_id:
        return None
    return query_one("SELECT * FROM users WHERE id = ?", (user_id,))


def current_user(
    user: sqlite3.Row | None = Depends(current_user_optional),
) -> sqlite3.Row:
    """Require an authenticated player."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_admin(user: sqlite3.Row = Depends(current_user)) -> sqlite3.Row:
    """Require an authenticated player carrying the admin flag."""
    if not user["is_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )
    return user


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
