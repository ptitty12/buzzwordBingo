"""Row -> API model conversion, shared by the routers."""

from __future__ import annotations

import json
import sqlite3

from .db import query_all, query_one
from .engine import _parse_aliases
from .models import (
    GridCell,
    GridPublic,
    MeetingPublic,
    ParticipantPublic,
    WordPublic,
    WordSuggestionPublic,
)


def participant_public(row: sqlite3.Row) -> ParticipantPublic:
    return ParticipantPublic(
        id=row["id"],
        meeting_id=row["meeting_id"],
        nickname=row["nickname"],
        avatar=row["avatar"],
        accent=row["accent"],
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
        created_by=row["created_by"] if "created_by" in keys else "admin",
        source=row["source"] if "source" in keys else "admin",
        usage_count=row["usage_count"] if "usage_count" in keys else 0,
    )


def suggestion_public(row: sqlite3.Row) -> WordSuggestionPublic:
    return WordSuggestionPublic(
        id=row["id"],
        text=row["text"],
        canonical=row["canonical"],
        status=row["status"],
        verdict=row["verdict"],
        category=row["category"],
        difficulty=row["difficulty"],
        judged_by=row["judged_by"],
        participant_name=row["participant_name"],
        word_id=row["word_id"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
    )


def meeting_public(row: sqlite3.Row) -> MeetingPublic:
    keys = row.keys()
    return MeetingPublic(
        id=row["id"],
        name=row["name"],
        code=row["code"],
        status=row["status"],
        grid_size=row["grid_size"],
        free_space=bool(row["free_space"]),
        description=row["description"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        participant_count=row["participant_count"] if "participant_count" in keys else 0,
        token_count=row["token_count"] if "token_count" in keys else 0,
        completion_count=row["completion_count"] if "completion_count" in keys else 0,
    )


MEETING_SELECT = """
SELECT g.*,
       (SELECT COUNT(*) FROM grids c WHERE c.meeting_id = g.id)              AS participant_count,
       (SELECT COUNT(*) FROM transcript_tokens t WHERE t.meeting_id = g.id)  AS token_count,
       (SELECT COUNT(*) FROM completions b WHERE b.meeting_id = g.id)        AS completion_count
FROM meetings g
"""


def grid_public(grid_id: str) -> GridPublic | None:
    """Hydrate a full grid — cells, words, marks and any lines already completed."""
    grid = query_one(
        """
        SELECT cd.*, g.grid_size, p.nickname, p.avatar, p.accent
        FROM grids cd
        JOIN meetings g   ON g.id = cd.meeting_id
        JOIN participants p ON p.id = cd.participant_id
        WHERE cd.id = ?
        """,
        (grid_id,),
    )
    if grid is None:
        return None

    cell_rows = query_all(
        """
        SELECT c.*, w.text AS word_text, w.category AS word_category
        FROM grid_cells c
        LEFT JOIN words w ON w.id = c.word_id
        WHERE c.grid_id = ?
        ORDER BY c.position
        """,
        (grid_id,),
    )

    cells = [
        GridCell(
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

    completion_rows = query_all(
        "SELECT pattern, rank FROM completions WHERE grid_id = ? ORDER BY rank", (grid_id,)
    )

    return GridPublic(
        id=grid["id"],
        meeting_id=grid["meeting_id"],
        participant_id=grid["participant_id"],
        nickname=grid["nickname"],
        avatar=grid["avatar"],
        accent=grid["accent"],
        grid_size=grid["grid_size"],
        locked=bool(grid["locked"]),
        created_at=grid["created_at"],
        cells=cells,
        marked_count=sum(1 for c in cells if c.marked),
        lines=[row["pattern"] for row in completion_rows],
        best_rank=completion_rows[0]["rank"] if completion_rows else None,
    )


def dumps(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))
