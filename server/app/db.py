"""SQLite access layer.

A single connection is shared process-wide with ``check_same_thread=False`` and guarded
by a re-entrant lock. FastAPI runs sync endpoints in a threadpool, so the lock is what
keeps concurrent requests from interleaving mid-transaction. WAL mode keeps readers from
blocking the transcript ingest path.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .config import get_settings

_connection: sqlite3.Connection | None = None
_lock = threading.RLock()

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def new_id() -> str:
    """Generate a compact, URL-safe primary key."""
    return uuid.uuid4().hex


def utcnow() -> str:
    """Current UTC time as an ISO-8601 string with a trailing Z."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def get_connection() -> sqlite3.Connection:
    """Return the shared connection, initialising the schema on first use."""
    global _connection
    with _lock:
        if _connection is None:
            settings = get_settings()
            url = settings.database_url
            if url != ":memory:":
                Path(url).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(url, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.commit()
            _connection = conn
        return _connection


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Run a block inside a single committed transaction, serialised against writers."""
    conn = get_connection()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def query_all(sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
    with _lock:
        return get_connection().execute(sql, params).fetchall()


def query_one(sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
    with _lock:
        return get_connection().execute(sql, params).fetchone()


def execute(sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
    with transaction() as conn:
        return conn.execute(sql, params)


def reset_connection() -> None:
    """Drop the cached connection. Used by the test suite between modules."""
    global _connection
    with _lock:
        if _connection is not None:
            _connection.close()
        _connection = None


def record_audit(
    action: str,
    *,
    actor_id: str | None = None,
    actor_name: str = "system",
    entity: str = "",
    entity_id: str = "",
    detail: str = "",
) -> None:
    """Append an entry to the admin audit trail."""
    execute(
        """
        INSERT INTO audit_log (id, actor_id, actor_name, action, entity, entity_id, detail,
                               created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (new_id(), actor_id, actor_name, action, entity, entity_id, detail, utcnow()),
    )
