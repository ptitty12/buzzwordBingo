"""Meetings, joining, grids, standings and transcript history.

The open is public — you have to be able to see a meeting before you can join it — but
everything scoped to a meeting requires a token for *that* meeting (or an admin token).
"""

from __future__ import annotations

import random
import sqlite3
import string

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..db import execute, new_id, query_all, query_one, record_audit, utcnow
from ..engine import create_grid, invalidate_index, pattern_label, standings
from ..models import (
    GridCreate,
    GridPublic,
    JoinRequest,
    MeetingCreate,
    MeetingPublic,
    MeetingStatusUpdate,
    ParticipantSession,
    StandingsEntry,
    TranscriptToken,
)
from ..realtime import hub
from ..security import (
    Identity,
    current_identity,
    issue_participant_token,
    require_admin,
    require_identity,
    require_participant,
)
from ..serializers import MEETING_SELECT, grid_public, meeting_public, participant_public

router = APIRouter(prefix="/api/meetings", tags=["meetings"])

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1 — these get read aloud


def _generate_code() -> str:
    for _ in range(50):
        code = "".join(random.choice(CODE_ALPHABET) for _ in range(5))
        if not query_one("SELECT id FROM meetings WHERE code = ?", (code,)):
            return code
    return "".join(random.choice(string.ascii_uppercase) for _ in range(8))


def _load_meeting(meeting_id: str) -> sqlite3.Row:
    row = query_one(
        f"{MEETING_SELECT} WHERE g.id = ? OR g.code = ?", (meeting_id, meeting_id.upper())
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found.")
    return row


def _require_membership(identity: Identity, meeting: sqlite3.Row) -> None:
    """A participant token is valid only inside the meeting it was issued for."""
    if not identity.owns_meeting(meeting["id"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your nickname belongs to a different meeting — join this one to play.",
        )


# --------------------------------------------------------------------------- open


@router.get("", response_model=list[MeetingPublic])
def list_meetings(
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[MeetingPublic]:
    """Public open. Live meetings sort to the top."""
    where = "WHERE g.status = ?" if status_filter else ""
    params = (status_filter,) if status_filter else ()
    rows = query_all(
        f"""
        {MEETING_SELECT} {where}
        ORDER BY CASE g.status WHEN 'live' THEN 0 WHEN 'paused' THEN 1
                               WHEN 'open' THEN 2 ELSE 3 END,
                 g.created_at DESC
        """,
        params,
    )
    return [meeting_public(row) for row in rows]


@router.get("/{meeting_id}", response_model=MeetingPublic)
def get_meeting(meeting_id: str) -> MeetingPublic:
    """Public meeting details, so a join screen can render before anyone has a token."""
    return meeting_public(_load_meeting(meeting_id))


@router.post(
    "/{meeting_id}/join", response_model=ParticipantSession, status_code=status.HTTP_201_CREATED
)
async def join_meeting(meeting_id: str, payload: JoinRequest) -> ParticipantSession:
    """Enter a meeting with a nickname. No account, no password — this *is* the sign-up.

    Nicknames are unique per meeting, so re-joining with the same nickname resumes that
    identity (and its grid) rather than colliding — which is what you want when someone
    refreshes the page or switches devices mid-meeting.
    """
    meeting = _load_meeting(meeting_id)
    if meeting["status"] == "ended":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This meeting has ended.")

    nickname_key = payload.nickname.lower()
    existing = query_one(
        "SELECT * FROM participants WHERE meeting_id = ? AND nickname_key = ?",
        (meeting["id"], nickname_key),
    )

    if existing is not None:
        execute("UPDATE participants SET last_seen_at = ? WHERE id = ?", (utcnow(), existing["id"]))
        refreshed = query_one("SELECT * FROM participants WHERE id = ?", (existing["id"],))
        assert refreshed is not None
        return ParticipantSession(
            token=issue_participant_token(refreshed["id"]),
            participant=participant_public(refreshed),
            meeting_id=meeting["id"],
        )

    participant_id = new_id()
    now = utcnow()
    avatar = payload.avatar or "◆"
    execute(
        """
        INSERT INTO participants (id, meeting_id, nickname, nickname_key, avatar, accent,
                             created_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            participant_id,
            meeting["id"],
            payload.nickname,
            nickname_key,
            avatar,
            payload.accent,
            now,
            now,
        ),
    )
    record_audit(
        "participant.joined",
        actor_id=participant_id,
        actor_name=payload.nickname,
        entity="meeting",
        entity_id=meeting["id"],
        detail=meeting["name"],
    )

    created = query_one("SELECT * FROM participants WHERE id = ?", (participant_id,))
    assert created is not None
    await hub.broadcast(
        meeting["id"], "roster", {"participant_id": participant_id, "nickname": payload.nickname}
    )
    return ParticipantSession(
        token=issue_participant_token(participant_id),
        participant=participant_public(created),
        meeting_id=meeting["id"],
    )


# --------------------------------------------------------------------------- admin control


@router.post("", response_model=MeetingPublic, status_code=status.HTTP_201_CREATED)
def create_meeting(
    payload: MeetingCreate, _admin: Identity = Depends(require_admin)
) -> MeetingPublic:
    """Open a new meeting. Administrators only."""
    meeting_id = new_id()
    code = _generate_code()
    execute(
        """
        INSERT INTO meetings (id, name, code, status, grid_size, free_space, created_at,
                           created_by, description)
        VALUES (?, ?, ?, 'open', ?, ?, ?, 'admin', ?)
        """,
        (
            meeting_id,
            payload.name,
            code,
            payload.grid_size,
            int(payload.free_space),
            utcnow(),
            payload.description,
        ),
    )
    record_audit(
        "meeting.created",
        actor_name="admin",
        entity="meeting",
        entity_id=meeting_id,
        detail=f"{payload.name} ({code})",
    )
    return meeting_public(_load_meeting(meeting_id))


@router.patch("/{meeting_id}/status", response_model=MeetingPublic)
async def update_status(
    meeting_id: str, payload: MeetingStatusUpdate, _admin: Identity = Depends(require_admin)
) -> MeetingPublic:
    """Drive a meeting through its lifecycle: open -> live -> paused -> ended.

    Going live locks every grid so nobody can rebuild after hearing the first buzzword.
    """
    meeting = _load_meeting(meeting_id)
    now = utcnow()

    if payload.status == "live":
        execute(
            "UPDATE meetings SET status = 'live', started_at = COALESCE(started_at, ?)"
            " WHERE id = ?",
            (now, meeting["id"]),
        )
        execute("UPDATE grids SET locked = 1 WHERE meeting_id = ?", (meeting["id"],))
    elif payload.status == "ended":
        execute(
            "UPDATE meetings SET status = 'ended', ended_at = ? WHERE id = ?", (now, meeting["id"])
        )
    elif payload.status == "open":
        execute(
            "UPDATE meetings SET status = 'open', started_at = NULL, ended_at = NULL WHERE id = ?",
            (meeting["id"],),
        )
        execute("UPDATE grids SET locked = 0 WHERE meeting_id = ?", (meeting["id"],))
    else:
        execute("UPDATE meetings SET status = ? WHERE id = ?", (payload.status, meeting["id"]))

    record_audit(
        "meeting.status",
        actor_name="admin",
        entity="meeting",
        entity_id=meeting["id"],
        detail=payload.status,
    )
    updated = _load_meeting(meeting["id"])
    await hub.broadcast(meeting["id"], "meeting", meeting_public(updated).model_dump())
    return meeting_public(updated)


@router.post("/{meeting_id}/reset", response_model=MeetingPublic)
async def reset_meeting(
    meeting_id: str, _admin: Identity = Depends(require_admin)
) -> MeetingPublic:
    """Clear the transcript, marks and completions while keeping participants and their grids."""
    meeting = _load_meeting(meeting_id)
    execute("DELETE FROM completions WHERE meeting_id = ?", (meeting["id"],))
    execute("DELETE FROM transcript_tokens WHERE meeting_id = ?", (meeting["id"],))
    execute(
        """
        UPDATE grid_cells
        SET marked = is_free, marked_at = NULL, token_id = NULL
        WHERE grid_id IN (SELECT id FROM grids WHERE meeting_id = ?)
        """,
        (meeting["id"],),
    )
    execute(
        "UPDATE meetings SET status = 'open', started_at = NULL, ended_at = NULL WHERE id = ?",
        (meeting["id"],),
    )
    execute("UPDATE grids SET locked = 0 WHERE meeting_id = ?", (meeting["id"],))
    invalidate_index(meeting["id"])

    record_audit(
        "meeting.reset",
        actor_name="admin",
        entity="meeting",
        entity_id=meeting["id"],
        detail=meeting["name"],
    )
    updated = _load_meeting(meeting["id"])
    await hub.broadcast(meeting["id"], "meeting", meeting_public(updated).model_dump())
    return meeting_public(updated)


@router.delete("/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meeting(meeting_id: str, _admin: Identity = Depends(require_admin)) -> None:
    meeting = _load_meeting(meeting_id)
    execute("DELETE FROM meetings WHERE id = ?", (meeting["id"],))
    invalidate_index(meeting["id"])
    record_audit(
        "meeting.deleted",
        actor_name="admin",
        entity="meeting",
        entity_id=meeting["id"],
        detail=meeting["name"],
    )


# --------------------------------------------------------------------------- grids


@router.get("/{meeting_id}/grid", response_model=GridPublic)
def get_my_grid(meeting_id: str, identity: Identity = Depends(require_participant)) -> GridPublic:
    """The caller's own grid for a meeting."""
    meeting = _load_meeting(meeting_id)
    _require_membership(identity, meeting)
    assert identity.participant is not None

    row = query_one(
        "SELECT id FROM grids WHERE meeting_id = ? AND participant_id = ?",
        (meeting["id"], identity.participant["id"]),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="You have not built a grid yet."
        )
    grid = grid_public(row["id"])
    assert grid is not None
    return grid


@router.post("/{meeting_id}/grid", response_model=GridPublic, status_code=status.HTTP_201_CREATED)
async def build_grid(
    meeting_id: str, payload: GridCreate, identity: Identity = Depends(require_participant)
) -> GridPublic:
    """Build or rebuild the caller's grid.

    Hand-picked words are placed first and any shortfall is topped up at random, so
    "draft every square" and "surprise me" are the same endpoint.
    """
    meeting = _load_meeting(meeting_id)
    _require_membership(identity, meeting)
    assert identity.participant is not None

    if meeting["status"] == "ended":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This meeting has ended.")

    try:
        grid_id = create_grid(meeting, identity.participant["id"], payload.word_ids)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    grid = grid_public(grid_id)
    assert grid is not None
    await hub.broadcast(
        meeting["id"],
        "roster",
        {
            "participant_id": identity.participant["id"],
            "nickname": identity.participant["nickname"],
            "grid_id": grid_id,
        },
    )
    await hub.broadcast(meeting["id"], "standings", standings(meeting["id"]))
    return grid


@router.get("/{meeting_id}/grids", response_model=list[GridPublic])
def list_grids(meeting_id: str, _admin: Identity = Depends(require_admin)) -> list[GridPublic]:
    """Every grid in the meeting — the admin "see everyone's grids" view."""
    meeting = _load_meeting(meeting_id)
    rows = query_all(
        """
        SELECT cd.id FROM grids cd JOIN participants p ON p.id = cd.participant_id
        WHERE cd.meeting_id = ? ORDER BY p.nickname COLLATE NOCASE
        """,
        (meeting["id"],),
    )
    return [grid for grid in (grid_public(row["id"]) for row in rows) if grid is not None]


@router.get("/{meeting_id}/grids/{grid_id}", response_model=GridPublic)
def get_grid(
    meeting_id: str, grid_id: str, identity: Identity = Depends(require_identity)
) -> GridPublic:
    """Inspect one grid. Participants may only read their own unless they are an admin."""
    meeting = _load_meeting(meeting_id)
    grid = grid_public(grid_id)
    if grid is None or grid.meeting_id != meeting["id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grid not found.")

    owner = identity.participant is not None and identity.participant["id"] == grid.participant_id
    if not owner and not identity.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="That is not your grid.")
    return grid


# --------------------------------------------------------------------------- standings


@router.get("/{meeting_id}/standings", response_model=list[StandingsEntry])
def get_standings(
    meeting_id: str, identity: Identity = Depends(current_identity)
) -> list[StandingsEntry]:
    """Standings. First to completion completions; ties break on lines, then squares marked.

    Open to spectators — the standings is the part you want on the big screen.
    """
    meeting = _load_meeting(meeting_id)
    del identity  # standings are not participant-specific
    return [StandingsEntry(**entry) for entry in standings(meeting["id"])]


@router.get("/{meeting_id}/transcript", response_model=list[TranscriptToken])
def get_transcript(
    meeting_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    identity: Identity = Depends(current_identity),
) -> list[TranscriptToken]:
    """Recent transcript tokens, oldest first — used to backfill the ticker on load."""
    meeting = _load_meeting(meeting_id)
    del identity
    rows = query_all(
        "SELECT * FROM transcript_tokens WHERE meeting_id = ? ORDER BY seq DESC LIMIT ?",
        (meeting["id"], limit),
    )
    return [TranscriptToken.model_validate(dict(row)) for row in reversed(rows)]


@router.get("/{meeting_id}/completions")
def get_completions(meeting_id: str) -> list[dict]:
    """Every completed line in the meeting, in the order they were achieved."""
    meeting = _load_meeting(meeting_id)
    rows = query_all(
        """
        SELECT b.*, p.nickname, p.avatar, p.accent
        FROM completions b JOIN participants p ON p.id = b.participant_id
        WHERE b.meeting_id = ? ORDER BY b.rank
        """,
        (meeting["id"],),
    )
    return [
        {
            "id": row["id"],
            "participant_id": row["participant_id"],
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
