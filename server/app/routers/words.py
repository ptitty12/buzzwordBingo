"""The buzzword pool.

Reads are open to any joined player (they need the pool to build a card). Direct
mutation is admin-only. Players influence the pool through :func:`suggest_word`, which
routes the submission to an LLM curator that decides whether it is buzzwordy enough.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..config import get_settings
from ..db import execute, new_id, query_all, query_one, record_audit, utcnow
from ..engine import invalidate_all_indexes
from ..lexicon import exact_key, match_keys, normalize_text, phrases_match, tokenize
from ..models import (
    SuggestionResponse,
    WordBulkCreate,
    WordCreate,
    WordPublic,
    WordSuggestionRequest,
    WordUpdate,
)
from ..moderation import judge
from ..security import Identity, require_admin, require_identity
from ..serializers import suggestion_public, word_public

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
    _identity: Identity = Depends(require_identity),
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
def list_categories(_identity: Identity = Depends(require_identity)) -> list[str]:
    rows = query_all("SELECT DISTINCT category FROM words WHERE active = 1 ORDER BY category")
    return [row["category"] for row in rows]


# --------------------------------------------------------------------------- suggestions


@router.post("/suggest", response_model=SuggestionResponse, status_code=status.HTTP_201_CREATED)
def suggest_word(
    payload: WordSuggestionRequest, identity: Identity = Depends(require_identity)
) -> SuggestionResponse:
    """Propose a word for the pool and get an immediate verdict.

    The submission goes to an LLM curator which decides whether it is genuine jargon
    ("business fundamentals" — yes) or ordinary vocabulary ("sales" — no). An approved
    word is added to the live pool straight away, filed under the category and rarity
    the judge assigned, with the player's original spelling kept as an alias.

    When no model is configured the suggestion is queued for an administrator rather
    than being auto-approved.
    """
    settings = get_settings()
    player = identity.player
    player_name = identity.display_name

    # Per-player quota, so one enthusiast cannot flood the pool (or the API bill).
    if player is not None:
        used = query_one(
            "SELECT COUNT(*) AS n FROM word_suggestions WHERE player_id = ?", (player["id"],)
        )
        used_count = int(used["n"]) if used else 0
        if used_count >= settings.suggestions_per_player:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"You have used all {settings.suggestions_per_player} of your "
                    "word suggestions for this game."
                ),
            )
    else:
        used_count = 0

    text_key = exact_key(payload.text)
    if not text_key:
        raise HTTPException(status_code=422, detail="That does not contain any usable characters.")

    duplicate = query_one("SELECT * FROM words WHERE text_key = ?", (text_key,))
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{duplicate['text']}' is already in the pool.",
        )

    pool = [row["text"] for row in query_all("SELECT text FROM words WHERE active = 1 LIMIT 200")]
    verdict = judge(payload.text, pool)

    now = utcnow()
    suggestion_id = new_id()
    word_id: str | None = None
    created_word = None

    if verdict.approved:
        word_id, created_word = _create_word_from_verdict(payload.text, verdict, player_name)
        # The judge may reject on a technicality we can only see after canonicalisation
        # (e.g. the corrected spelling already exists), in which case it is not approved.
        if word_id is None:
            verdict.decision = "rejected"
            verdict.reason = "That is already in the pool under a different spelling."

    execute(
        """
        INSERT INTO word_suggestions
            (id, text, text_key, game_id, player_id, player_name, status, verdict, canonical,
             category, difficulty, judged_by, word_id, created_at, decided_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            suggestion_id,
            payload.text,
            text_key,
            player["game_id"] if player is not None else None,
            player["id"] if player is not None else None,
            player_name,
            verdict.decision,
            verdict.reason,
            verdict.canonical,
            verdict.category,
            verdict.difficulty,
            verdict.judged_by,
            word_id,
            now,
            now if verdict.decision != "pending" else None,
        ),
    )

    record_audit(
        f"suggestion.{verdict.decision}",
        actor_id=player["id"] if player is not None else None,
        actor_name=player_name,
        entity="word_suggestion",
        entity_id=suggestion_id,
        detail=f"{payload.text} — {verdict.reason}",
    )

    row = query_one("SELECT * FROM word_suggestions WHERE id = ?", (suggestion_id,))
    assert row is not None
    remaining = max(0, settings.suggestions_per_player - (used_count + 1)) if player else 0
    return SuggestionResponse(
        suggestion=suggestion_public(row), word=created_word, remaining=remaining
    )


def _create_word_from_verdict(
    submitted: str, verdict, author: str
) -> tuple[str | None, WordPublic | None]:
    """Insert an approved suggestion into the pool.

    Returns ``(None, None)`` when the canonical spelling collides with an existing word,
    which the caller turns into a rejection.
    """
    text = verdict.canonical or submitted
    key = exact_key(text)
    if not key or query_one("SELECT id FROM words WHERE text_key = ?", (key,)):
        return None, None

    # Keep the player's spelling as an alias when the judge corrected it, so the square
    # still marks if a speaker says it the way it was submitted.
    aliases = [submitted] if exact_key(submitted) != key else []

    word_id = new_id()
    execute(
        """
        INSERT INTO words (id, text, text_key, category, difficulty, aliases, strict_match,
                           active, created_at, created_by, source)
        VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, ?, 'suggestion')
        """,
        (
            word_id,
            text,
            key,
            verdict.category,
            verdict.difficulty,
            json.dumps(aliases),
            utcnow(),
            f"player:{author}",
        ),
    )
    invalidate_all_indexes()

    row = query_one(f"{WORD_SELECT} WHERE w.id = ?", (word_id,))
    return word_id, (word_public(row) if row is not None else None)


@router.get("/suggestions/mine")
def my_suggestions(identity: Identity = Depends(require_identity)) -> dict:
    """A player's own submission history and remaining quota."""
    settings = get_settings()
    if identity.player is None:
        return {"suggestions": [], "remaining": 0, "limit": settings.suggestions_per_player}

    rows = query_all(
        "SELECT * FROM word_suggestions WHERE player_id = ? ORDER BY created_at DESC",
        (identity.player["id"],),
    )
    return {
        "suggestions": [suggestion_public(row).model_dump() for row in rows],
        "remaining": max(0, settings.suggestions_per_player - len(rows)),
        "limit": settings.suggestions_per_player,
    }


# --------------------------------------------------------------------------- admin CRUD


@router.post("", response_model=WordPublic, status_code=status.HTTP_201_CREATED)
def create_word(payload: WordCreate, _admin: Identity = Depends(require_admin)) -> WordPublic:
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
                           active, created_at, created_by, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'admin', 'admin')
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
        ),
    )
    record_audit(
        "word.created",
        actor_name="admin",
        entity="word",
        entity_id=word_id,
        detail=payload.text,
    )
    invalidate_all_indexes()

    row = query_one(f"{WORD_SELECT} WHERE w.id = ?", (word_id,))
    assert row is not None
    return word_public(row)


@router.post("/bulk", response_model=list[WordPublic], status_code=status.HTTP_201_CREATED)
def bulk_create(payload: WordBulkCreate, _admin: Identity = Depends(require_admin)):
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
                               active, created_at, created_by, source)
            VALUES (?, ?, ?, ?, ?, '[]', 0, 1, ?, 'admin', 'admin')
            """,
            (word_id, text, text_key, category or "General", payload.difficulty, now),
        )
        row = query_one(f"{WORD_SELECT} WHERE w.id = ?", (word_id,))
        if row is not None:
            created.append(word_public(row))

    record_audit(
        "word.bulk_import",
        actor_name="admin",
        entity="word",
        detail=f"{len(created)} added, {len(entries) - len(created)} skipped",
    )
    invalidate_all_indexes()
    return created


@router.patch("/{word_id}", response_model=WordPublic)
def update_word(
    word_id: str, payload: WordUpdate, _admin: Identity = Depends(require_admin)
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
        actor_name="admin",
        entity="word",
        entity_id=word_id,
        detail=", ".join(fields.keys()),
    )
    invalidate_all_indexes()

    row = query_one(f"{WORD_SELECT} WHERE w.id = ?", (word_id,))
    assert row is not None
    return word_public(row)


@router.delete("/{word_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_word(word_id: str, _admin: Identity = Depends(require_admin)) -> None:
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
        action, actor_name="admin", entity="word", entity_id=word_id, detail=existing["text"]
    )
    invalidate_all_indexes()


@router.get("/inspect")
def inspect_match(
    phrase: str = Query(min_length=1),
    against: str | None = Query(default=None),
    _admin: Identity = Depends(require_admin),
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
