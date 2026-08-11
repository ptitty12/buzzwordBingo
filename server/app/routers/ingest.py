"""Transcript ingest — the integration surface for live captions.

This is the endpoint an external transcription pipeline hits. It is deliberately
forgiving about shape: post one word at a time, or a whole sentence, or a whole
paragraph. Everything is tokenised server-side and applied in order.

    curl -X POST http://localhost:8000/api/ingest \\
      -H 'X-API-Key: jw_...' -H 'Content-Type: application/json' \\
      -d '{"text": "synergy", "meeting_code": "K7Q2M"}'

Omitting both ``meeting_id`` and ``meeting_code`` fans the token out to every live meeting, which
is what you want when a single meeting drives every room in the building.
"""

from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from ..db import query_all, query_one
from ..engine import apply_transcript, meeting_lock, standings
from ..models import (
    IngestCompletion,
    IngestHit,
    IngestMeetingResult,
    IngestRequest,
    IngestResponse,
)
from ..realtime import hub
from ..security import require_api_key
from ..serializers import MEETING_SELECT, meeting_public

router = APIRouter(prefix="/api/ingest", tags=["ingest"])
logger = logging.getLogger("jargon.ingest")


def _resolve_targets(payload: IngestRequest) -> list[sqlite3.Row]:
    """Pick the meetings a chunk applies to."""
    if payload.meeting_id:
        row = query_one(f"{MEETING_SELECT} WHERE g.id = ?", (payload.meeting_id,))
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown meeting_id.")
        return [row]

    if payload.meeting_code:
        row = query_one(f"{MEETING_SELECT} WHERE g.code = ?", (payload.meeting_code.upper(),))
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Unknown meeting_code."
            )
        return [row]

    return list(query_all(f"{MEETING_SELECT} WHERE g.status = 'live'"))


@router.post("", response_model=IngestResponse)
async def ingest(payload: IngestRequest, caller: str = Depends(require_api_key)) -> IngestResponse:
    """Feed transcript text into one or every live meeting."""
    targets = _resolve_targets(payload)
    results: list[IngestMeetingResult] = []
    token_count = 0

    for meeting in targets:
        if meeting["status"] != "live":
            # Explicitly addressed meetings report why nothing happened; broadcast mode
            # silently skips meetings that are not running.
            if payload.meeting_id or payload.meeting_code:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Meeting '{meeting['name']}' is {meeting['status']}, not live.",
                )
            continue

        # Serialise per meeting so "first to complete a line" reflects true transcript order.
        async with meeting_lock(meeting["id"]):
            outcome = apply_transcript(
                meeting, payload.text, speaker=payload.speaker, source=payload.source
            )

            hits = [
                IngestHit(
                    participant_id=hit.participant_id,
                    nickname=hit.nickname,
                    grid_id=hit.grid_id,
                    position=hit.position,
                    word=hit.word_text,
                    matched_phrase=hit.matched_phrase,
                )
                for hit in outcome.hits
            ]
            completions = [
                IngestCompletion(
                    participant_id=award.participant_id,
                    nickname=award.nickname,
                    pattern=award.pattern,
                    label=award.label,
                    rank=award.rank,
                    cells=award.cells,
                    achieved_at=award.achieved_at,
                )
                for award in outcome.completions
            ]

            token_count += len(outcome.tokens)
            results.append(
                IngestMeetingResult(
                    meeting_id=meeting["id"],
                    meeting_name=meeting["name"],
                    tokens=outcome.tokens,
                    hits=hits,
                    completions=completions,
                )
            )

            # Tokens carry their own sequence and hit count so the client ticker can
            # highlight the exact word that scored, without re-deriving anything.
            await hub.broadcast(
                meeting["id"],
                "token",
                {
                    "tokens": [
                        {"raw": raw, "seq": outcome.seq + offset, "hits": token_hits}
                        for offset, (raw, token_hits) in enumerate(
                            zip(outcome.tokens, outcome.token_hits, strict=True)
                        )
                    ],
                    "speaker": payload.speaker,
                    "source": payload.source,
                },
            )
            if hits:
                await hub.broadcast(meeting["id"], "marks", [hit.model_dump() for hit in hits])
            if completions:
                for award in completions:
                    await hub.broadcast(meeting["id"], "completion", award.model_dump())
                    logger.info(
                        "COMPLETION meeting=%s participant=%s pattern=%s rank=%d",
                        meeting["name"],
                        award.nickname,
                        award.pattern,
                        award.rank,
                    )
            if hits or completions:
                await hub.broadcast(meeting["id"], "standings", standings(meeting["id"]))

    if results:
        logger.info(
            "ingest caller=%s meetings=%d tokens=%d hits=%d",
            caller,
            len(results),
            token_count,
            sum(len(r.hits) for r in results),
        )

    return IngestResponse(token_count=token_count, results=results)


@router.get("/targets")
def ingest_targets(_caller: str = Depends(require_api_key)) -> list[dict]:
    """Meetings currently accepting transcript — lets an integration discover meeting codes."""
    rows = query_all(f"{MEETING_SELECT} WHERE g.status IN ('live', 'paused', 'open')")
    return [
        {
            **meeting_public(row).model_dump(
                include={"id", "name", "code", "status", "participant_count"}
            ),
        }
        for row in rows
    ]
