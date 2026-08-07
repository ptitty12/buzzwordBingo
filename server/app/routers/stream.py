"""WebSocket endpoint backing the live game view."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ..db import query_one
from ..engine import leaderboard
from ..realtime import hub
from ..security import ADMIN_SUBJECT, PLAYER_PREFIX, verify_token
from ..serializers import GAME_SELECT, game_public

router = APIRouter(tags=["realtime"])
logger = logging.getLogger("bingo.stream")


@router.websocket("/ws/games/{game_id}")
async def game_stream(
    websocket: WebSocket, game_id: str, token: str = Query(default="")
) -> None:
    """Subscribe to a game's event stream.

    The token is optional — spectators can watch without an account — and is passed as a
    query parameter because browsers cannot set headers on a WebSocket handshake.
    """
    game = query_one(f"{GAME_SELECT} WHERE g.id = ? OR g.code = ?", (game_id, game_id.upper()))
    if game is None:
        await websocket.close(code=4404, reason="Game not found")
        return

    nickname = "spectator"
    subject = verify_token(token) if token else None
    if subject == ADMIN_SUBJECT:
        nickname = "admin"
    elif subject and subject.startswith(PLAYER_PREFIX):
        player = query_one(
            "SELECT nickname FROM players WHERE id = ?", (subject[len(PLAYER_PREFIX):],)
        )
        if player:
            nickname = player["nickname"]

    await hub.connect(game["id"], websocket, nickname)
    try:
        # Prime the client so it renders correct state before the first live event.
        await websocket.send_json(
            {
                "event": "hello",
                "payload": {
                    "game": game_public(game).model_dump(),
                    "leaderboard": leaderboard(game["id"]),
                    "viewers": hub.viewers(game["id"]),
                },
            }
        )
        while True:
            # Clients only send keepalives; ignore the content, the read keeps the
            # connection honest and surfaces disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - never let one socket take down the endpoint
        logger.exception("websocket error game=%s", game["id"])
    finally:
        await hub.disconnect(game["id"], websocket)
