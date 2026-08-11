"""WebSocket endpoint backing the live meeting view."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ..db import query_one
from ..engine import standings
from ..realtime import hub
from ..security import ADMIN_SUBJECT, PARTICIPANT_PREFIX, verify_token
from ..serializers import MEETING_SELECT, meeting_public

router = APIRouter(tags=["realtime"])
logger = logging.getLogger("jargon.stream")


@router.websocket("/ws/meetings/{meeting_id}")
async def meeting_stream(
    websocket: WebSocket, meeting_id: str, token: str = Query(default="")
) -> None:
    """Subscribe to a meeting's event stream.

    The token is optional — spectators can watch without an account — and is passed as a
    query parameter because browsers cannot set headers on a WebSocket handshake.
    """
    meeting = query_one(
        f"{MEETING_SELECT} WHERE g.id = ? OR g.code = ?", (meeting_id, meeting_id.upper())
    )
    if meeting is None:
        await websocket.close(code=4404, reason="Meeting not found")
        return

    nickname = "spectator"
    subject = verify_token(token) if token else None
    if subject == ADMIN_SUBJECT:
        nickname = "admin"
    elif subject and subject.startswith(PARTICIPANT_PREFIX):
        participant = query_one(
            "SELECT nickname FROM participants WHERE id = ?", (subject[len(PARTICIPANT_PREFIX) :],)
        )
        if participant:
            nickname = participant["nickname"]

    await hub.connect(meeting["id"], websocket, nickname)
    try:
        # Prime the client so it renders correct state before the first live event.
        await websocket.send_json(
            {
                "event": "hello",
                "payload": {
                    "meeting": meeting_public(meeting).model_dump(),
                    "standings": standings(meeting["id"]),
                    "viewers": hub.viewers(meeting["id"]),
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
        logger.exception("websocket error meeting=%s", meeting["id"])
    finally:
        await hub.disconnect(meeting["id"], websocket)
