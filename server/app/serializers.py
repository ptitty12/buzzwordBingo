"""Row -> API model conversion, shared by the routers."""

from __future__ import annotations

import json
import sqlite3

from .db import query_all, query_one
from .engine import _parse_aliases
from .models import CardCell, CardPublic, GamePublic, UserPublic, WordPublic


def user_public(row: sqlite3.Row) -> UserPublic:
    return UserPublic(
        id=row["id"],
        nickname=row["nickname"],
        avatar=row["avatar"],
        accent=row["accent"],
        is_admin=bool(row["is_admin"]),
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
    )


def word_public(row: sqlite3.Row) -> WordPublic:
    keys = row.keys()
    return WordPublic(
        id=row["id"],
        text=row["text"],
        category=row["category"],
        difficulty=row["difficulty"],
        aliases=_parse_aliases(row["aliases"]),
        strict_match=bool(row["strict_match"]),
        active=bool(row["active"]),
        created_at=row["created_at"],
        usage_count=row["usage_count"] if "usage_count" in keys else 0,
    )


def game_public(row: sqlite3.Row) -> GamePublic:
    keys = row.keys()
    return GamePublic(
        id=row["id"],
        name=row["name"],
        code=row["code"],
        status=row["status"],
        card_size=row["card_size"],
        free_space=bool(row["free_space"]),
        description=row["description"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        player_count=row["player_count"] if "player_count" in keys else 0,
        token_count=row["token_count"] if "token_count" in keys else 0,
        bingo_count=row["bingo_count"] if "bingo_count" in keys else 0,
    )


GAME_SELECT = """
SELECT g.*,
       (SELECT COUNT(*) FROM cards c WHERE c.game_id = g.id)              AS player_count,
       (SELECT COUNT(*) FROM transcript_tokens t WHERE t.game_id = g.id)  AS token_count,
       (SELECT COUNT(*) FROM bingos b WHERE b.game_id = g.id)             AS bingo_count
FROM games g
"""


def card_public(card_id: str) -> CardPublic | None:
    """Hydrate a full card — cells, words, marks and any lines already completed."""
    card = query_one(
        """
        SELECT cd.*, g.card_size, u.nickname, u.avatar, u.accent
        FROM cards cd
        JOIN games g ON g.id = cd.game_id
        JOIN users u ON u.id = cd.user_id
        WHERE cd.id = ?
        """,
        (card_id,),
    )
    if card is None:
        return None

    cell_rows = query_all(
        """
        SELECT c.*, w.text AS word_text, w.category AS word_category
        FROM card_cells c
        LEFT JOIN words w ON w.id = c.word_id
        WHERE c.card_id = ?
        ORDER BY c.position
        """,
        (card_id,),
    )

    cells = [
        CardCell(
            id=row["id"],
            position=row["position"],
            word_id=row["word_id"],
            text="FREE" if row["is_free"] else (row["word_text"] or "—"),
            category=row["word_category"] or "",
            is_free=bool(row["is_free"]),
            marked=bool(row["marked"]),
            marked_at=row["marked_at"],
        )
        for row in cell_rows
    ]

    bingo_rows = query_all(
        "SELECT pattern, rank FROM bingos WHERE card_id = ? ORDER BY rank", (card_id,)
    )

    return CardPublic(
        id=card["id"],
        game_id=card["game_id"],
        user_id=card["user_id"],
        nickname=card["nickname"],
        avatar=card["avatar"],
        accent=card["accent"],
        card_size=card["card_size"],
        locked=bool(card["locked"]),
        created_at=card["created_at"],
        cells=cells,
        marked_count=sum(1 for c in cells if c.marked),
        lines=[row["pattern"] for row in bingo_rows],
        best_rank=bingo_rows[0]["rank"] if bingo_rows else None,
    )


def dumps(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))
