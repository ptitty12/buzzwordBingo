"""Games, joining, cards, leaderboards and transcript history.

The lobby is public — you have to be able to see a game before you can join it — but
everything scoped to a game requires a token for *that* game (or an admin token).
"""

from __future__ import annotations

import random
import sqlite3
import string

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..db import execute, new_id, query_all, query_one, record_audit, utcnow
from ..engine import create_card, invalidate_index, leaderboard, pattern_label
from ..models import (
    CardCreate,
    CardPublic,
    GameCreate,
    GamePublic,
    GameStatusUpdate,
    JoinRequest,
    LeaderboardEntry,
    PlayerSession,
    TranscriptToken,
)
from ..realtime import hub
from ..security import (
    Identity,
    current_identity,
    issue_player_token,
    require_admin,
    require_identity,
    require_player,
)
from ..serializers import GAME_SELECT, card_public, game_public, player_public

router = APIRouter(prefix="/api/games", tags=["games"])

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1 — these get read aloud


def _generate_code() -> str:
    for _ in range(50):
        code = "".join(random.choice(CODE_ALPHABET) for _ in range(5))
        if not query_one("SELECT id FROM games WHERE code = ?", (code,)):
            return code
    return "".join(random.choice(string.ascii_uppercase) for _ in range(8))


def _load_game(game_id: str) -> sqlite3.Row:
    row = query_one(f"{GAME_SELECT} WHERE g.id = ? OR g.code = ?", (game_id, game_id.upper()))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found.")
    return row


def _require_membership(identity: Identity, game: sqlite3.Row) -> None:
    """A player token is valid only inside the game it was issued for."""
    if not identity.owns_game(game["id"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your nickname belongs to a different game — join this one to play.",
        )


# --------------------------------------------------------------------------- lobby


@router.get("", response_model=list[GamePublic])
def list_games(
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[GamePublic]:
    """Public lobby. Live games sort to the top."""
    where = "WHERE g.status = ?" if status_filter else ""
    params = (status_filter,) if status_filter else ()
    rows = query_all(
        f"""
        {GAME_SELECT} {where}
        ORDER BY CASE g.status WHEN 'live' THEN 0 WHEN 'paused' THEN 1
                               WHEN 'lobby' THEN 2 ELSE 3 END,
                 g.created_at DESC
        """,
        params,
    )
    return [game_public(row) for row in rows]


@router.get("/{game_id}", response_model=GamePublic)
def get_game(game_id: str) -> GamePublic:
    """Public game details, so a join screen can render before anyone has a token."""
    return game_public(_load_game(game_id))


@router.post("/{game_id}/join", response_model=PlayerSession, status_code=status.HTTP_201_CREATED)
async def join_game(game_id: str, payload: JoinRequest) -> PlayerSession:
    """Enter a game with a nickname. No account, no password — this *is* the sign-up.

    Nicknames are unique per game, so re-joining with the same nickname resumes that
    identity (and its card) rather than colliding — which is what you want when someone
    refreshes the page or switches devices mid-meeting.
    """
    game = _load_game(game_id)
    if game["status"] == "ended":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This game has ended.")

    nickname_key = payload.nickname.lower()
    existing = query_one(
        "SELECT * FROM players WHERE game_id = ? AND nickname_key = ?",
        (game["id"], nickname_key),
    )

    if existing is not None:
        execute("UPDATE players SET last_seen_at = ? WHERE id = ?", (utcnow(), existing["id"]))
        refreshed = query_one("SELECT * FROM players WHERE id = ?", (existing["id"],))
        assert refreshed is not None
        return PlayerSession(
            token=issue_player_token(refreshed["id"]),
            player=player_public(refreshed),
            game_id=game["id"],
        )

    player_id = new_id()
    now = utcnow()
    avatar = payload.avatar or "◆"
    execute(
        """
        INSERT INTO players (id, game_id, nickname, nickname_key, avatar, accent,
                             created_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (player_id, game["id"], payload.nickname, nickname_key, avatar, payload.accent, now, now),
    )
    record_audit(
        "player.joined",
        actor_id=player_id,
        actor_name=payload.nickname,
        entity="game",
        entity_id=game["id"],
        detail=game["name"],
    )

    created = query_one("SELECT * FROM players WHERE id = ?", (player_id,))
    assert created is not None
    await hub.broadcast(
        game["id"], "roster", {"player_id": player_id, "nickname": payload.nickname}
    )
    return PlayerSession(
        token=issue_player_token(player_id),
        player=player_public(created),
        game_id=game["id"],
    )


# --------------------------------------------------------------------------- admin control


@router.post("", response_model=GamePublic, status_code=status.HTTP_201_CREATED)
def create_game(payload: GameCreate, _admin: Identity = Depends(require_admin)) -> GamePublic:
    """Open a new game. Administrators only."""
    game_id = new_id()
    code = _generate_code()
    execute(
        """
        INSERT INTO games (id, name, code, status, card_size, free_space, created_at,
                           created_by, description)
        VALUES (?, ?, ?, 'lobby', ?, ?, ?, 'admin', ?)
        """,
        (
            game_id,
            payload.name,
            code,
            payload.card_size,
            int(payload.free_space),
            utcnow(),
            payload.description,
        ),
    )
    record_audit(
        "game.created",
        actor_name="admin",
        entity="game",
        entity_id=game_id,
        detail=f"{payload.name} ({code})",
    )
    return game_public(_load_game(game_id))


@router.patch("/{game_id}/status", response_model=GamePublic)
async def update_status(
    game_id: str, payload: GameStatusUpdate, _admin: Identity = Depends(require_admin)
) -> GamePublic:
    """Drive a game through its lifecycle: lobby -> live -> paused -> ended.

    Going live locks every card so nobody can rebuild after hearing the first buzzword.
    """
    game = _load_game(game_id)
    now = utcnow()

    if payload.status == "live":
        execute(
            "UPDATE games SET status = 'live', started_at = COALESCE(started_at, ?) WHERE id = ?",
            (now, game["id"]),
        )
        execute("UPDATE cards SET locked = 1 WHERE game_id = ?", (game["id"],))
    elif payload.status == "ended":
        execute("UPDATE games SET status = 'ended', ended_at = ? WHERE id = ?", (now, game["id"]))
    elif payload.status == "lobby":
        execute(
            "UPDATE games SET status = 'lobby', started_at = NULL, ended_at = NULL WHERE id = ?",
            (game["id"],),
        )
        execute("UPDATE cards SET locked = 0 WHERE game_id = ?", (game["id"],))
    else:
        execute("UPDATE games SET status = ? WHERE id = ?", (payload.status, game["id"]))

    record_audit(
        "game.status",
        actor_name="admin",
        entity="game",
        entity_id=game["id"],
        detail=payload.status,
    )
    updated = _load_game(game["id"])
    await hub.broadcast(game["id"], "game", game_public(updated).model_dump())
    return game_public(updated)


@router.post("/{game_id}/reset", response_model=GamePublic)
async def reset_game(game_id: str, _admin: Identity = Depends(require_admin)) -> GamePublic:
    """Clear the transcript, marks and wins while keeping players and their cards."""
    game = _load_game(game_id)
    execute("DELETE FROM bingos WHERE game_id = ?", (game["id"],))
    execute("DELETE FROM transcript_tokens WHERE game_id = ?", (game["id"],))
    execute(
        """
        UPDATE card_cells
        SET marked = is_free, marked_at = NULL, token_id = NULL
        WHERE card_id IN (SELECT id FROM cards WHERE game_id = ?)
        """,
        (game["id"],),
    )
    execute(
        "UPDATE games SET status = 'lobby', started_at = NULL, ended_at = NULL WHERE id = ?",
        (game["id"],),
    )
    execute("UPDATE cards SET locked = 0 WHERE game_id = ?", (game["id"],))
    invalidate_index(game["id"])

    record_audit(
        "game.reset", actor_name="admin", entity="game", entity_id=game["id"], detail=game["name"]
    )
    updated = _load_game(game["id"])
    await hub.broadcast(game["id"], "game", game_public(updated).model_dump())
    return game_public(updated)


@router.delete("/{game_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_game(game_id: str, _admin: Identity = Depends(require_admin)) -> None:
    game = _load_game(game_id)
    execute("DELETE FROM games WHERE id = ?", (game["id"],))
    invalidate_index(game["id"])
    record_audit(
        "game.deleted",
        actor_name="admin",
        entity="game",
        entity_id=game["id"],
        detail=game["name"],
    )


# --------------------------------------------------------------------------- cards


@router.get("/{game_id}/card", response_model=CardPublic)
def get_my_card(game_id: str, identity: Identity = Depends(require_player)) -> CardPublic:
    """The caller's own card for a game."""
    game = _load_game(game_id)
    _require_membership(identity, game)
    assert identity.player is not None

    row = query_one(
        "SELECT id FROM cards WHERE game_id = ? AND player_id = ?",
        (game["id"], identity.player["id"]),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="You have not built a card yet."
        )
    card = card_public(row["id"])
    assert card is not None
    return card


@router.post("/{game_id}/card", response_model=CardPublic, status_code=status.HTTP_201_CREATED)
async def build_card(
    game_id: str, payload: CardCreate, identity: Identity = Depends(require_player)
) -> CardPublic:
    """Build or rebuild the caller's card.

    Hand-picked words are placed first and any shortfall is topped up at random, so
    "draft every square" and "surprise me" are the same endpoint.
    """
    game = _load_game(game_id)
    _require_membership(identity, game)
    assert identity.player is not None

    if game["status"] == "ended":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This game has ended.")

    try:
        card_id = create_card(game, identity.player["id"], payload.word_ids)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    card = card_public(card_id)
    assert card is not None
    await hub.broadcast(
        game["id"],
        "roster",
        {
            "player_id": identity.player["id"],
            "nickname": identity.player["nickname"],
            "card_id": card_id,
        },
    )
    await hub.broadcast(game["id"], "leaderboard", leaderboard(game["id"]))
    return card


@router.get("/{game_id}/cards", response_model=list[CardPublic])
def list_cards(game_id: str, _admin: Identity = Depends(require_admin)) -> list[CardPublic]:
    """Every card in the game — the admin "see everyone's cards" view."""
    game = _load_game(game_id)
    rows = query_all(
        """
        SELECT cd.id FROM cards cd JOIN players p ON p.id = cd.player_id
        WHERE cd.game_id = ? ORDER BY p.nickname COLLATE NOCASE
        """,
        (game["id"],),
    )
    return [card for card in (card_public(row["id"]) for row in rows) if card is not None]


@router.get("/{game_id}/cards/{card_id}", response_model=CardPublic)
def get_card(
    game_id: str, card_id: str, identity: Identity = Depends(require_identity)
) -> CardPublic:
    """Inspect one card. Players may only read their own unless they are an admin."""
    game = _load_game(game_id)
    card = card_public(card_id)
    if card is None or card.game_id != game["id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Card not found.")

    owner = identity.player is not None and identity.player["id"] == card.player_id
    if not owner and not identity.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="That is not your card.")
    return card


# --------------------------------------------------------------------------- standings


@router.get("/{game_id}/leaderboard", response_model=list[LeaderboardEntry])
def get_leaderboard(
    game_id: str, identity: Identity = Depends(current_identity)
) -> list[LeaderboardEntry]:
    """Standings. First to bingo wins; ties break on lines, then squares marked.

    Open to spectators — the leaderboard is the part you want on the big screen.
    """
    game = _load_game(game_id)
    del identity  # standings are not player-specific
    return [LeaderboardEntry(**entry) for entry in leaderboard(game["id"])]


@router.get("/{game_id}/transcript", response_model=list[TranscriptToken])
def get_transcript(
    game_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    identity: Identity = Depends(current_identity),
) -> list[TranscriptToken]:
    """Recent transcript tokens, oldest first — used to backfill the ticker on load."""
    game = _load_game(game_id)
    del identity
    rows = query_all(
        "SELECT * FROM transcript_tokens WHERE game_id = ? ORDER BY seq DESC LIMIT ?",
        (game["id"], limit),
    )
    return [TranscriptToken.model_validate(dict(row)) for row in reversed(rows)]


@router.get("/{game_id}/bingos")
def get_bingos(game_id: str) -> list[dict]:
    """Every winning line in the game, in the order they were achieved."""
    game = _load_game(game_id)
    rows = query_all(
        """
        SELECT b.*, p.nickname, p.avatar, p.accent
        FROM bingos b JOIN players p ON p.id = b.player_id
        WHERE b.game_id = ? ORDER BY b.rank
        """,
        (game["id"],),
    )
    return [
        {
            "id": row["id"],
            "player_id": row["player_id"],
            "nickname": row["nickname"],
            "avatar": row["avatar"],
            "accent": row["accent"],
            "pattern": row["pattern"],
            "label": pattern_label(row["pattern"]),
            "rank": row["rank"],
            "achieved_at": row["achieved_at"],
        }
        for row in rows
    ]
