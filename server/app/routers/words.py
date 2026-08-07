"""The buzzword pool.

Reads are open to any signed-in player (they need the pool to build a card); every
mutation is admin-only and audited.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..db import execute, new_id, query_all, query_one, record_audit, utcnow
from ..engine import invalidate_all_indexes
from ..lexicon import exact_key, match_keys, normalize_text, phrases_match, tokenize
from ..models import WordBulkCreate, WordCreate, WordPublic, WordUpdate
from ..security import current_user, require_admin
from ..serializers import word_public

router = APIRouter(prefix="/api/words", tags=["words"])

WORD_SELECT = """
SELECT w.*,
       (SELECT COUNT(*) FROM card_cells c WHERE c.word_id = w.id) AS usage_count
FROM words w
"""


@router.get("", response_model=list[WordPublic])
def list_words(
    include_inactive: bool = Query(default=False),
    category: str | None = Query(default=None),
    search: str | None = Query(default=None),
    _user: sqlite3.Row = Depends(current_user),
) -> list[WordPublic]:
    """The word pool players draft from."""
    clauses, params = [], []
    if not include_inactive:
        clauses.append("w.active = 1")
    if category and category != "All":
        clauses.append("w.category = ?")
        params.append(category)
    if search:
        clauses.append("w.text_key LIKE ?")
        params.append(f"%{normalize_text(search)}%")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = query_all(f"{WORD_SELECT} {where} ORDER BY w.category, w.text", tuple(params))
    return [word_public(row) for row in rows]


@router.get("/categories", response_model=list[str])
def list_categories(_user: sqlite3.Row = Depends(current_user)) -> list[str]:
    rows = query_all("SELECT DISTINCT category FROM words WHERE active = 1 ORDER BY category")
    return [row["category"] for row in rows]


@router.post("", response_model=WordPublic, status_code=status.HTTP_201_CREATED)
def create_word(payload: WordCreate, admin: sqlite3.Row = Depends(require_admin)) -> WordPublic:
    """Add a buzzword to the pool."""
    text_key = exact_key(payload.text)
    if not text_key:
        raise HTTPException(status_code=422, detail="That word normalises to nothing.")
    if query_one("SELECT id FROM words WHERE text_key = ?", (text_key,)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{payload.text}' is already in the pool.",
        )

    word_id = new_id()
    execute(
        """
        INSERT INTO words (id, text, text_key, category, difficulty, aliases, strict_match,
                           active, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            word_id,
            payload.text,
            text_key,
            payload.category or "General",
            payload.difficulty,
            json.dumps(payload.aliases),
            int(payload.strict_match),
            int(payload.active),
            utcnow(),
            admin["id"],
        ),
    )
    record_audit(
        "word.created",
        actor_id=admin["id"],
        actor_name=admin["nickname"],
        entity="word",
        entity_id=word_id,
        detail=payload.text,
    )
    invalidate_all_indexes()

    row = query_one(f"{WORD_SELECT} WHERE w.id = ?", (word_id,))
    assert row is not None
    return word_public(row)


@router.post("/bulk", response_model=list[WordPublic], status_code=status.HTTP_201_CREATED)
def bulk_create(payload: WordBulkCreate, admin: sqlite3.Row = Depends(require_admin)):
    """Import many words at once.

    Accepts one entry per line (or comma-separated). A line may carry its own category
    using ``Category: word`` syntax; otherwise the request-level category applies.
    Duplicates are skipped rather than erroring, so re-running an import is safe.
    """
    entries: list[tuple[str, str]] = []
    for line in payload.payload.replace(",", "\n").splitlines():
        raw = line.strip()
        if not raw:
            continue
        category = payload.category
        text = raw
        if ":" in raw:
            head, _, tail = raw.partition(":")
            if tail.strip():
                category, text = head.strip() or payload.category, tail.strip()
        entries.append((" ".join(text.split()), category))

    created: list[WordPublic] = []
    now = utcnow()
    for text, category in entries:
        text_key = exact_key(text)
        if not text_key or query_one("SELECT id FROM words WHERE text_key = ?", (text_key,)):
            continue
        word_id = new_id()
        execute(
            """
            INSERT INTO words (id, text, text_key, category, difficulty, aliases, strict_match,
                               active, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, '[]', 0, 1, ?, ?)
            """,
            (word_id, text, text_key, category or "General", payload.difficulty, now, admin["id"]),
        )
        row = query_one(f"{WORD_SELECT} WHERE w.id = ?", (word_id,))
        if row is not None:
            created.append(word_public(row))

    record_audit(
        "word.bulk_import",
        actor_id=admin["id"],
        actor_name=admin["nickname"],
        entity="word",
        detail=f"{len(created)} added, {len(entries) - len(created)} skipped",
    )
    invalidate_all_indexes()
    return created


@router.patch("/{word_id}", response_model=WordPublic)
def update_word(
    word_id: str, payload: WordUpdate, admin: sqlite3.Row = Depends(require_admin)
) -> WordPublic:
    existing = query_one("SELECT * FROM words WHERE id = ?", (word_id,))
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Word not found.")

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        row = query_one(f"{WORD_SELECT} WHERE w.id = ?", (word_id,))
        assert row is not None
        return word_public(row)

    assignments, params = [], []
    for key, value in fields.items():
        if key == "text":
            text_key = exact_key(value)
            clash = query_one(
                "SELECT id FROM words WHERE text_key = ? AND id != ?", (text_key, word_id)
            )
            if clash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="Another word already uses that."
                )
            assignments += ["text = ?", "text_key = ?"]
            params += [value, text_key]
        elif key == "aliases":
            assignments.append("aliases = ?")
            params.append(json.dumps(value))
        elif key in {"strict_match", "active"}:
            assignments.append(f"{key} = ?")
            params.append(int(bool(value)))
        else:
            assignments.append(f"{key} = ?")
            params.append(value)

    params.append(word_id)
    execute(f"UPDATE words SET {', '.join(assignments)} WHERE id = ?", tuple(params))
    record_audit(
        "word.updated",
        actor_id=admin["id"],
        actor_name=admin["nickname"],
        entity="word",
        entity_id=word_id,
        detail=", ".join(fields.keys()),
    )
    invalidate_all_indexes()

    row = query_one(f"{WORD_SELECT} WHERE w.id = ?", (word_id,))
    assert row is not None
    return word_public(row)


@router.delete("/{word_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_word(word_id: str, admin: sqlite3.Row = Depends(require_admin)) -> None:
    """Remove a word. Words already dealt onto a card are deactivated instead of deleted,
    so live games keep working."""
    existing = query_one("SELECT * FROM words WHERE id = ?", (word_id,))
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Word not found.")

    in_play = query_one("SELECT COUNT(*) AS n FROM card_cells WHERE word_id = ?", (word_id,))
    if in_play and in_play["n"]:
        execute("UPDATE words SET active = 0 WHERE id = ?", (word_id,))
        action = "word.deactivated"
    else:
        execute("DELETE FROM words WHERE id = ?", (word_id,))
        action = "word.deleted"

    record_audit(
        action,
        actor_id=admin["id"],
        actor_name=admin["nickname"],
        entity="word",
        entity_id=word_id,
        detail=existing["text"],
    )
    invalidate_all_indexes()


@router.get("/inspect")
def inspect_match(
    phrase: str = Query(min_length=1),
    against: str | None = Query(default=None),
    _admin: sqlite3.Row = Depends(require_admin),
) -> dict:
    """Explain how the matching engine sees a phrase.

    Invaluable when a player insists the speaker "definitely said it" — the admin console
    surfaces this as a live matcher playground.
    """
    result = {
        "phrase": phrase,
        "tokens": tokenize(phrase),
        "match_keys": sorted(match_keys(phrase)),
        "exact_key": exact_key(phrase),
    }
    if against:
        result["against"] = {
            "phrase": against,
            "match_keys": sorted(match_keys(against)),
            "matches": phrases_match(phrase, against),
        }
    return result
