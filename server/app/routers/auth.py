"""Password-free identity.

Players either pick an existing nickname from a dropdown or create a new one. Sign-in
returns an HMAC-signed token so the server can trust the identity claim on later calls
without ever storing a secret for the account.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from ..config import get_settings
from ..db import execute, new_id, query_all, query_one, record_audit, utcnow
from ..models import Session, SignInRequest, SignUpRequest, UserPublic, UserSummary
from ..security import current_user, issue_token
from ..serializers import user_public

router = APIRouter(prefix="/api/auth", tags=["auth"])

DEFAULT_AVATARS = "◆◇▲△●○■□★☆✦✧"


@router.get("/users", response_model=list[UserSummary])
def list_users() -> list[UserSummary]:
    """Every account, for the sign-in dropdown. Ordered by most recently active."""
    rows = query_all(
        """
        SELECT u.id, u.nickname, u.avatar, u.accent, u.is_admin,
               (SELECT COUNT(*) FROM cards c WHERE c.user_id = u.id) AS games_played
        FROM users u
        ORDER BY COALESCE(u.last_seen_at, u.created_at) DESC
        """
    )
    return [
        UserSummary(
            id=row["id"],
            nickname=row["nickname"],
            avatar=row["avatar"],
            accent=row["accent"],
            is_admin=bool(row["is_admin"]),
            games_played=row["games_played"],
        )
        for row in rows
    ]


@router.post("/signup", response_model=Session, status_code=status.HTTP_201_CREATED)
def sign_up(payload: SignUpRequest) -> Session:
    """Create an account from nothing but a nickname."""
    settings = get_settings()
    nickname_key = payload.nickname.lower()

    if query_one("SELECT id FROM users WHERE nickname_key = ?", (nickname_key,)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{payload.nickname}' is taken. Pick another handle.",
        )

    user_id = new_id()
    now = utcnow()
    avatar = payload.avatar or DEFAULT_AVATARS[len(nickname_key) % len(DEFAULT_AVATARS)]
    is_admin = 1 if nickname_key in settings.bootstrap_admin_list else 0

    execute(
        """
        INSERT INTO users (id, nickname, nickname_key, avatar, accent, is_admin,
                           created_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, payload.nickname, nickname_key, avatar, payload.accent, is_admin, now, now),
    )
    record_audit(
        "user.created",
        actor_id=user_id,
        actor_name=payload.nickname,
        entity="user",
        entity_id=user_id,
        detail=f"admin={bool(is_admin)}",
    )

    user = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    assert user is not None
    return Session(token=issue_token(user_id), user=user_public(user))


@router.post("/signin", response_model=Session)
def sign_in(payload: SignInRequest) -> Session:
    """Sign in as an existing account. Admin accounts may additionally require a PIN."""
    user = query_one("SELECT * FROM users WHERE id = ?", (payload.user_id,))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such account.")

    settings = get_settings()
    if user["is_admin"] and settings.admin_pin and payload.admin_pin != settings.admin_pin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator PIN required.",
        )

    execute("UPDATE users SET last_seen_at = ? WHERE id = ?", (utcnow(), user["id"]))
    refreshed = query_one("SELECT * FROM users WHERE id = ?", (user["id"],))
    assert refreshed is not None
    return Session(token=issue_token(user["id"]), user=user_public(refreshed))


@router.get("/me", response_model=UserPublic)
def me(user: sqlite3.Row = Depends(current_user)) -> UserPublic:
    """Resolve the caller's own account — used to restore a session on page load."""
    execute("UPDATE users SET last_seen_at = ? WHERE id = ?", (utcnow(), user["id"]))
    return user_public(user)


@router.get("/config")
def auth_config() -> dict:
    """Public sign-in configuration, so the UI knows whether to prompt for a PIN."""
    settings = get_settings()
    return {
        "admin_pin_required": bool(settings.admin_pin),
        "environment": settings.environment,
        "app_name": settings.app_name,
    }
