"""Admin console API: fleet stats, user management, API keys and the audit trail."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..config import get_settings
from ..db import execute, new_id, query_all, query_one, record_audit, utcnow
from ..models import (
    AdminStats,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyPublic,
    AuditEntry,
    CardPublic,
    UserAdminUpdate,
    UserPublic,
)
from ..realtime import hub
from ..security import generate_api_key, require_admin
from ..serializers import card_public, user_public

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _count(sql: str, params: tuple = ()) -> int:
    row = query_one(sql, params)
    return int(row["n"]) if row else 0


@router.get("/stats", response_model=AdminStats)
def stats(_admin: sqlite3.Row = Depends(require_admin)) -> AdminStats:
    """Headline numbers for the admin overview."""
    settings = get_settings()
    return AdminStats(
        users=_count("SELECT COUNT(*) AS n FROM users"),
        admins=_count("SELECT COUNT(*) AS n FROM users WHERE is_admin = 1"),
        words=_count("SELECT COUNT(*) AS n FROM words"),
        active_words=_count("SELECT COUNT(*) AS n FROM words WHERE active = 1"),
        games=_count("SELECT COUNT(*) AS n FROM games"),
        live_games=_count("SELECT COUNT(*) AS n FROM games WHERE status = 'live'"),
        cards=_count("SELECT COUNT(*) AS n FROM cards"),
        tokens=_count("SELECT COUNT(*) AS n FROM transcript_tokens"),
        bingos=_count("SELECT COUNT(*) AS n FROM bingos"),
        connected_sockets=sum(hub.presence().values()),
        admin_pin_set=bool(settings.admin_pin),
        environment=settings.environment,
    )


@router.get("/users", response_model=list[UserPublic])
def list_users(_admin: sqlite3.Row = Depends(require_admin)) -> list[UserPublic]:
    rows = query_all("SELECT * FROM users ORDER BY created_at DESC")
    return [user_public(row) for row in rows]


@router.patch("/users/{user_id}", response_model=UserPublic)
def update_user(
    user_id: str, payload: UserAdminUpdate, admin: sqlite3.Row = Depends(require_admin)
) -> UserPublic:
    """Promote/demote an admin or rename a player."""
    target = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    fields = payload.model_dump(exclude_unset=True)
    if payload.is_admin is False and target["is_admin"]:
        remaining = _count("SELECT COUNT(*) AS n FROM users WHERE is_admin = 1 AND id != ?", (user_id,))
        if remaining == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot demote the last remaining administrator.",
            )

    assignments, params = [], []
    if "is_admin" in fields:
        assignments.append("is_admin = ?")
        params.append(int(bool(payload.is_admin)))
    if "nickname" in fields and payload.nickname:
        key = payload.nickname.lower()
        clash = query_one(
            "SELECT id FROM users WHERE nickname_key = ? AND id != ?", (key, user_id)
        )
        if clash:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Nickname taken.")
        assignments += ["nickname = ?", "nickname_key = ?"]
        params += [payload.nickname, key]

    if assignments:
        params.append(user_id)
        execute(f"UPDATE users SET {', '.join(assignments)} WHERE id = ?", tuple(params))
        record_audit(
            "user.updated",
            actor_id=admin["id"],
            actor_name=admin["nickname"],
            entity="user",
            entity_id=user_id,
            detail=", ".join(fields.keys()),
        )

    updated = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    assert updated is not None
    return user_public(updated)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: str, admin: sqlite3.Row = Depends(require_admin)) -> None:
    target = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if target["id"] == admin["id"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="You cannot delete your own account."
        )
    execute("DELETE FROM users WHERE id = ?", (user_id,))
    record_audit(
        "user.deleted",
        actor_id=admin["id"],
        actor_name=admin["nickname"],
        entity="user",
        entity_id=user_id,
        detail=target["nickname"],
    )


@router.get("/cards", response_model=list[CardPublic])
def all_cards(
    game_id: str | None = Query(default=None),
    _admin: sqlite3.Row = Depends(require_admin),
) -> list[CardPublic]:
    """Every card across every game, or scoped to one game."""
    where = "WHERE cd.game_id = ?" if game_id else ""
    params = (game_id,) if game_id else ()
    rows = query_all(
        f"""
        SELECT cd.id FROM cards cd
        JOIN users u ON u.id = cd.user_id
        {where}
        ORDER BY cd.created_at DESC
        """,
        params,
    )
    return [card for card in (card_public(row["id"]) for row in rows) if card is not None]


# --------------------------------------------------------------------------- api keys


@router.get("/keys", response_model=list[ApiKeyPublic])
def list_keys(_admin: sqlite3.Row = Depends(require_admin)) -> list[ApiKeyPublic]:
    rows = query_all("SELECT * FROM api_keys ORDER BY created_at DESC")
    return [
        ApiKeyPublic(
            id=row["id"],
            name=row["name"],
            prefix=row["prefix"],
            active=bool(row["active"]),
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
            call_count=row["call_count"],
        )
        for row in rows
    ]


@router.post("/keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_key(
    payload: ApiKeyCreate, admin: sqlite3.Row = Depends(require_admin)
) -> ApiKeyCreated:
    """Mint an ingest API key. The full value is returned once and only once."""
    full, prefix, key_hash = generate_api_key()
    key_id = new_id()
    now = utcnow()
    execute(
        """
        INSERT INTO api_keys (id, name, prefix, key_hash, active, created_at, created_by)
        VALUES (?, ?, ?, ?, 1, ?, ?)
        """,
        (key_id, payload.name, prefix, key_hash, now, admin["id"]),
    )
    record_audit(
        "apikey.created",
        actor_id=admin["id"],
        actor_name=admin["nickname"],
        entity="api_key",
        entity_id=key_id,
        detail=payload.name,
    )
    return ApiKeyCreated(
        id=key_id, name=payload.name, prefix=prefix, active=True, created_at=now, key=full
    )


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_key(key_id: str, admin: sqlite3.Row = Depends(require_admin)) -> None:
    """Revoke a key. The row is kept so its usage history stays auditable."""
    row = query_one("SELECT * FROM api_keys WHERE id = ?", (key_id,))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found.")
    execute("UPDATE api_keys SET active = 0 WHERE id = ?", (key_id,))
    record_audit(
        "apikey.revoked",
        actor_id=admin["id"],
        actor_name=admin["nickname"],
        entity="api_key",
        entity_id=key_id,
        detail=row["name"],
    )


# --------------------------------------------------------------------------- audit


@router.get("/audit", response_model=list[AuditEntry])
def audit_trail(
    limit: int = Query(default=80, ge=1, le=500),
    _admin: sqlite3.Row = Depends(require_admin),
) -> list[AuditEntry]:
    rows = query_all("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?", (limit,))
    return [AuditEntry.model_validate(dict(row)) for row in rows]


@router.get("/presence")
def presence(_admin: sqlite3.Row = Depends(require_admin)) -> dict:
    """Live WebSocket viewer counts, keyed by game id."""
    return hub.presence()
