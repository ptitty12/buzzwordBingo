"""WebSocket fan-out.

Clients subscribe to a single game channel and receive a typed event stream:

    ``token``       a transcript token was ingested (drives the live ticker)
    ``marks``       one or more squares were marked
    ``bingo``       a player completed a line
    ``game``        game status changed (lobby -> live -> ended)
    ``roster``      a player joined or rebuilt their card
    ``leaderboard`` recomputed standings

Delivery is best-effort: a socket that errors is dropped rather than blocking ingest.
The REST API remains the source of truth, so a client that misses an event recovers on
its next poll or reconnect.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("bingo.realtime")


class ConnectionHub:
    """Tracks live sockets per game and broadcasts JSON events to them."""

    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = defaultdict(set)
        self._identities: dict[WebSocket, str] = {}
        self._lock = asyncio.Lock()

    async def connect(self, game_id: str, socket: WebSocket, nickname: str = "spectator") -> None:
        await socket.accept()
        async with self._lock:
            self._rooms[game_id].add(socket)
            self._identities[socket] = nickname
        logger.info("ws connect game=%s who=%s viewers=%d", game_id, nickname, self.viewers(game_id))

    async def disconnect(self, game_id: str, socket: WebSocket) -> None:
        async with self._lock:
            self._rooms.get(game_id, set()).discard(socket)
            nickname = self._identities.pop(socket, "spectator")
            if not self._rooms.get(game_id):
                self._rooms.pop(game_id, None)
        logger.info("ws disconnect game=%s who=%s", game_id, nickname)

    def viewers(self, game_id: str) -> int:
        return len(self._rooms.get(game_id, ()))

    def presence(self) -> dict[str, int]:
        return {game_id: len(sockets) for game_id, sockets in self._rooms.items()}

    async def broadcast(self, game_id: str, event: str, payload: Any) -> None:
        """Send one event to every socket watching a game."""
        async with self._lock:
            sockets = list(self._rooms.get(game_id, ()))
        if not sockets:
            return

        message = {"event": event, "payload": payload}
        dead: list[WebSocket] = []
        for socket in sockets:
            try:
                await socket.send_json(message)
            except Exception:  # noqa: BLE001 - a broken socket must never break ingest
                dead.append(socket)

        if dead:
            async with self._lock:
                for socket in dead:
                    self._rooms.get(game_id, set()).discard(socket)
                    self._identities.pop(socket, None)


hub = ConnectionHub()
