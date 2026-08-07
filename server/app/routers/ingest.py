"""Transcript ingest — the integration surface for live captions.

This is the endpoint an external transcription pipeline hits. It is deliberately
forgiving about shape: post one word at a time, or a whole sentence, or a whole
paragraph. Everything is tokenised server-side and applied in order.

    curl -X POST http://localhost:8000/api/ingest \\
      -H 'X-API-Key: bb_...' -H 'Content-Type: application/json' \\
      -d '{"text": "synergy", "game_code": "K7Q2M"}'

Omitting both ``game_id`` and ``game_code`` fans the token out to every live game, which
is what you want when a single meeting drives every room in the building.
"""

from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from ..db import query_all, query_one
from ..engine import apply_transcript, game_lock, leaderboard
from ..models import (
    IngestBingo,
    IngestGameResult,
    IngestHit,
    IngestRequest,
    IngestResponse,
)
from ..realtime import hub
from ..security import require_api_key
from ..serializers import GAME_SELECT, game_public

router = APIRouter(prefix="/api/ingest", tags=["ingest"])
logger = logging.getLogger("bingo.ingest")


def _resolve_targets(payload: IngestRequest) -> list[sqlite3.Row]:
    """Pick the games a chunk applies to."""
    if payload.game_id:
        row = query_one(f"{GAME_SELECT} WHERE g.id = ?", (payload.game_id,))
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown game_id.")
        return [row]

    if payload.game_code:
        row = query_one(f"{GAME_SELECT} WHERE g.code = ?", (payload.game_code.upper(),))
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown game_code.")
        return [row]

    return list(query_all(f"{GAME_SELECT} WHERE g.status = 'live'"))


@router.post("", response_model=IngestResponse)
async def ingest(
    payload: IngestRequest, caller: str = Depends(require_api_key)
) -> IngestResponse:
    """Feed transcript text into one or every live game."""
    targets = _resolve_targets(payload)
    results: list[IngestGameResult] = []
    token_count = 0

    for game in targets:
        if game["status"] != "live":
            # Explicitly addressed games report why nothing happened; broadcast mode
            # silently skips games that are not running.
            if payload.game_id or payload.game_code:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Game '{game['name']}' is {game['status']}, not live.",
                )
            continue

        # Serialise per game so "first to bingo" reflects true transcript order.
        async with game_lock(game["id"]):
            outcome = apply_transcript(
                game, payload.text, speaker=payload.speaker, source=payload.source
            )

            hits = [
                IngestHit(
                    player_id=hit.player_id,
                    nickname=hit.nickname,
                    card_id=hit.card_id,
                    position=hit.position,
                    word=hit.word_text,
                    matched_phrase=hit.matched_phrase,
                )
                for hit in outcome.hits
            ]
            bingos = [
                IngestBingo(
                    player_id=award.player_id,
                    nickname=award.nickname,
                    pattern=award.pattern,
                    label=award.label,
                    rank=award.rank,
                    cells=award.cells,
                    achieved_at=award.achieved_at,
                )
                for award in outcome.bingos
            ]

            token_count += len(outcome.tokens)
            results.append(
                IngestGameResult(
                    game_id=game["id"],
                    game_name=game["name"],
                    tokens=outcome.tokens,
                    hits=hits,
                    bingos=bingos,
                )
            )

            # Tokens carry their own sequence and hit count so the client ticker can
            # highlight the exact word that scored, without re-deriving anything.
            await hub.broadcast(
                game["id"],
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
                await hub.broadcast(
                    game["id"], "marks", [hit.model_dump() for hit in hits]
                )
            if bingos:
                for award in bingos:
                    await hub.broadcast(game["id"], "bingo", award.model_dump())
                    logger.info(
                        "BINGO game=%s player=%s pattern=%s rank=%d",
                        game["name"], award.nickname, award.pattern, award.rank,
                    )
            if hits or bingos:
                await hub.broadcast(game["id"], "leaderboard", leaderboard(game["id"]))

    if results:
        logger.info(
            "ingest caller=%s games=%d tokens=%d hits=%d",
            caller, len(results), token_count, sum(len(r.hits) for r in results),
        )

    return IngestResponse(token_count=token_count, results=results)


@router.get("/targets")
def ingest_targets(_caller: str = Depends(require_api_key)) -> list[dict]:
    """Games currently accepting transcript — lets an integration discover game codes."""
    rows = query_all(f"{GAME_SELECT} WHERE g.status IN ('live', 'paused', 'lobby')")
    return [
        {
            **game_public(row).model_dump(include={"id", "name", "code", "status", "player_count"}),
        }
        for row in rows
    ]
