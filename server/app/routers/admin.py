"""Admin console API: fleet stats, players, suggestions, API keys and the audit trail."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..config import get_settings
from ..db import execute, new_id, query_all, query_one, record_audit, utcnow
from ..engine import invalidate_all_indexes
from ..lexicon import exact_key
from ..models import (
    AdminStats,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyPublic,
    AuditEntry,
    CardPublic,
    PlayerPublic,
    SuggestionDecision,
    WordSuggestionPublic,
)
from ..realtime import hub
from ..security import Identity, generate_api_key, require_admin
from ..serializers import card_public, player_public, suggestion_public

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _count(sql: str, params: tuple = ()) -> int:
    row = query_one(sql, params)
    return int(row["n"]) if row else 0


@router.get("/stats", response_model=AdminStats)
def stats(_admin: Identity = Depends(require_admin)) -> AdminStats:
    """Headline numbers for the admin overview."""
    settings = get_settings()
    return AdminStats(
        players=_count("SELECT COUNT(*) AS n FROM players"),
        words=_count("SELECT COUNT(*) AS n FROM words"),
        active_words=_count("SELECT COUNT(*) AS n FROM words WHERE active = 1"),
        games=_count("SELECT COUNT(*) AS n FROM games"),
        live_games=_count("SELECT COUNT(*) AS n FROM games WHERE status = 'live'"),
        cards=_count("SELECT COUNT(*) AS n FROM cards"),
        tokens=_count("SELECT COUNT(*) AS n FROM transcript_tokens"),
        bingos=_count("SELECT COUNT(*) AS n FROM bingos"),
        connected_sockets=sum(hub.presence().values()),
        pending_suggestions=_count(
            "SELECT COUNT(*) AS n FROM word_suggestions WHERE status = 'pending'"
        ),
        moderation_enabled=settings.moderation_enabled,
        moderation_model=settings.moderation_model if settings.moderation_enabled else "",
        environment=settings.environment,
    )


# --------------------------------------------------------------------------- players


@router.get("/players", response_model=list[PlayerPublic])
def list_players(
    game_id: str | None = Query(default=None),
    _admin: Identity = Depends(require_admin),
) -> list[PlayerPublic]:
    """Everyone who has joined a game, optionally scoped to one game."""
    where = "WHERE game_id = ?" if game_id else ""
    params = (game_id,) if game_id else ()
    rows = query_all(f"SELECT * FROM players {where} ORDER BY created_at DESC", params)
    return [player_public(row) for row in rows]


@router.delete("/players/{player_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_player(player_id: str, _admin: Identity = Depends(require_admin)) -> None:
    """Remove a player and their card from a game."""
    target = query_one("SELECT * FROM players WHERE id = ?", (player_id,))
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found.")
    execute("DELETE FROM players WHERE id = ?", (player_id,))
    invalidate_all_indexes()
    record_audit(
        "player.removed",
        actor_name="admin",
        entity="player",
        entity_id=player_id,
        detail=target["nickname"],
    )


# --------------------------------------------------------------------------- suggestions


@router.get("/suggestions", response_model=list[WordSuggestionPublic])
def list_suggestions(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    _admin: Identity = Depends(require_admin),
) -> list[WordSuggestionPublic]:
    """Player word submissions and the judge's verdicts."""
    where = "WHERE status = ?" if status_filter else ""
    params: tuple = (status_filter, limit) if status_filter else (limit,)
    rows = query_all(
        f"SELECT * FROM word_suggestions {where} ORDER BY created_at DESC LIMIT ?", params
    )
    return [suggestion_public(row) for row in rows]


@router.post("/suggestions/{suggestion_id}", response_model=WordSuggestionPublic)
def decide_suggestion(
    suggestion_id: str,
    payload: SuggestionDecision,
    _admin: Identity = Depends(require_admin),
) -> WordSuggestionPublic:
    """Approve or reject a suggestion by hand — the override for the judge's call.

    Approving adds the word to the live pool; rejecting an already-approved suggestion
    deactivates the word it created rather than deleting it, so cards holding it survive.
    """
    row = query_one("SELECT * FROM word_suggestions WHERE id = ?", (suggestion_id,))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found.")

    now = utcnow()
    word_id = row["word_id"]

    if payload.approve:
        if word_id is None:
            text = row["canonical"] or row["text"]
            key = exact_key(text)
            clash = query_one("SELECT id FROM words WHERE text_key = ?", (key,))
            if clash is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="That word is already in the pool.",
                )
            aliases = [row["text"]] if exact_key(row["text"]) != key else []
            word_id = new_id()
            execute(
                """
                INSERT INTO words (id, text, text_key, category, difficulty, aliases,
                                   strict_match, active, created_at, created_by, source)
                VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, ?, 'suggestion')
                """,
                (
                    word_id,
                    text,
                    key,
                    row["category"] or "General",
                    row["difficulty"],
                    json.dumps(aliases),
                    now,
                    f"player:{row['player_name']}",
                ),
            )
        else:
            execute("UPDATE words SET active = 1 WHERE id = ?", (word_id,))
        new_status = "approved"
    else:
        if word_id is not None:
            execute("UPDATE words SET active = 0 WHERE id = ?", (word_id,))
        new_status = "rejected"

    execute(
        """
        UPDATE word_suggestions
        SET status = ?, verdict = ?, judged_by = 'admin', word_id = ?, decided_at = ?
        WHERE id = ?
        """,
        (new_status, payload.reason or row["verdict"], word_id, now, suggestion_id),
    )
    invalidate_all_indexes()
    record_audit(
        f"suggestion.admin_{new_status}",
        actor_name="admin",
        entity="word_suggestion",
        entity_id=suggestion_id,
        detail=row["text"],
    )

    updated = query_one("SELECT * FROM word_suggestions WHERE id = ?", (suggestion_id,))
    assert updated is not None
    return suggestion_public(updated)


# --------------------------------------------------------------------------- cards


@router.get("/cards", response_model=list[CardPublic])
def all_cards(
    game_id: str | None = Query(default=None),
    _admin: Identity = Depends(require_admin),
) -> list[CardPublic]:
    """Every card across every game, or scoped to one game."""
    where = "WHERE cd.game_id = ?" if game_id else ""
    params = (game_id,) if game_id else ()
    rows = query_all(
        f"""
        SELECT cd.id FROM cards cd
        JOIN players p ON p.id = cd.player_id
        {where}
        ORDER BY cd.created_at DESC
        """,
        params,
    )
    return [card for card in (card_public(row["id"]) for row in rows) if card is not None]


# --------------------------------------------------------------------------- api keys


@router.get("/keys", response_model=list[ApiKeyPublic])
def list_keys(_admin: Identity = Depends(require_admin)) -> list[ApiKeyPublic]:
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
def create_key(payload: ApiKeyCreate, _admin: Identity = Depends(require_admin)) -> ApiKeyCreated:
    """Mint an ingest API key. The full value is returned once and only once."""
    full, prefix, key_hash = generate_api_key()
    key_id = new_id()
    now = utcnow()
    execute(
        """
        INSERT INTO api_keys (id, name, prefix, key_hash, active, created_at, created_by)
        VALUES (?, ?, ?, ?, 1, ?, 'admin')
        """,
        (key_id, payload.name, prefix, key_hash, now),
    )
    record_audit(
        "apikey.created",
        actor_name="admin",
        entity="api_key",
        entity_id=key_id,
        detail=payload.name,
    )
    return ApiKeyCreated(
        id=key_id, name=payload.name, prefix=prefix, active=True, created_at=now, key=full
    )


@router.delete("/keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_key(key_id: str, _admin: Identity = Depends(require_admin)) -> None:
    """Revoke a key. The row is kept so its usage history stays auditable."""
    row = query_one("SELECT * FROM api_keys WHERE id = ?", (key_id,))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found.")
    execute("UPDATE api_keys SET active = 0 WHERE id = ?", (key_id,))
    record_audit(
        "apikey.revoked",
        actor_name="admin",
        entity="api_key",
        entity_id=key_id,
        detail=row["name"],
    )


# --------------------------------------------------------------------------- audit


@router.get("/audit", response_model=list[AuditEntry])
def audit_trail(
    limit: int = Query(default=80, ge=1, le=500),
    _admin: Identity = Depends(require_admin),
) -> list[AuditEntry]:
    rows = query_all("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?", (limit,))
    return [AuditEntry.model_validate(dict(row)) for row in rows]


@router.get("/presence")
def presence(_admin: Identity = Depends(require_admin)) -> dict:
    """Live WebSocket viewer counts, keyed by game id."""
    return hub.presence()
