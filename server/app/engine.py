"""The completion engine: grid geometry, transcript matching, and win detection.

Ingest is the hot path and the one place where ordering genuinely matters — "first to
completion" is only meaningful if tokens are applied in the order they were spoken. Each meeting
therefore serialises ingest behind its own asyncio lock, and every token is written with
a monotonic sequence number.

Matching works off an in-memory index (``MeetingIndex``) rebuilt whenever a meeting's grids
change. The index maps every match key produced by :mod:`app.lexicon` to the grid cells
carrying that word, so a spoken token is resolved with a dict lookup rather than a scan
over every grid.
"""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3
from collections import deque
from dataclasses import dataclass, field

from .config import get_settings
from .db import execute, new_id, query_all, query_one, transaction, utcnow
from .lexicon import MAX_PHRASE_LENGTH, exact_key, match_keys, tokenize

# --------------------------------------------------------------------------- geometry


def free_position(grid_size: int) -> int:
    """Index of the free space — the exact centre, only defined for odd-sized grids."""
    return (grid_size * grid_size) // 2


def completed_lines(grid_size: int) -> dict[str, list[int]]:
    """Every completed pattern for a grid, keyed by a stable pattern id."""
    lines: dict[str, list[int]] = {}
    for row in range(grid_size):
        lines[f"row-{row}"] = [row * grid_size + col for col in range(grid_size)]
    for col in range(grid_size):
        lines[f"col-{col}"] = [row * grid_size + col for row in range(grid_size)]
    lines["diag-main"] = [i * grid_size + i for i in range(grid_size)]
    lines["diag-anti"] = [i * grid_size + (grid_size - 1 - i) for i in range(grid_size)]
    lines["corners"] = [
        0,
        grid_size - 1,
        grid_size * (grid_size - 1),
        grid_size * grid_size - 1,
    ]
    lines["blackout"] = list(range(grid_size * grid_size))
    return lines


def pattern_label(pattern: str) -> str:
    """Human-readable name for a pattern id."""
    if pattern.startswith("row-"):
        return f"Row {int(pattern.split('-')[1]) + 1}"
    if pattern.startswith("col-"):
        return f"Column {int(pattern.split('-')[1]) + 1}"
    return {
        "diag-main": "Diagonal ↘",
        "diag-anti": "Diagonal ↗",
        "corners": "Four Corners",
        "blackout": "BLACKOUT",
    }.get(pattern, pattern)


# --------------------------------------------------------------------------- index


@dataclass(frozen=True)
class CellRef:
    """A single markable square, denormalised for fast lookup."""

    cell_id: str
    grid_id: str
    participant_id: str
    nickname: str
    position: int
    word_id: str
    word_text: str


@dataclass
class MeetingIndex:
    """In-memory match index for one meeting."""

    meeting_id: str
    keys: dict[str, list[CellRef]] = field(default_factory=dict)
    strict_keys: dict[str, list[CellRef]] = field(default_factory=dict)
    max_phrase: int = 1
    window: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_PHRASE_LENGTH))

    def lookup(self, key: str, strict: bool) -> list[CellRef]:
        return (self.strict_keys if strict else self.keys).get(key, [])


_indexes: dict[str, MeetingIndex] = {}
_locks: dict[str, asyncio.Lock] = {}


def meeting_lock(meeting_id: str) -> asyncio.Lock:
    """Per-meeting ingest lock, so token application stays strictly ordered."""
    if meeting_id not in _locks:
        _locks[meeting_id] = asyncio.Lock()
    return _locks[meeting_id]


def invalidate_index(meeting_id: str) -> None:
    """Drop the cached index after grids or words change."""
    _indexes.pop(meeting_id, None)


def invalidate_all_indexes() -> None:
    _indexes.clear()


def build_index(meeting_id: str) -> MeetingIndex:
    """Rebuild a meeting's match index from the database."""
    rows = query_all(
        """
        SELECT c.id  AS cell_id,
               c.grid_id,
               c.position,
               c.word_id,
               w.text         AS word_text,
               w.aliases      AS aliases,
               w.strict_match AS strict_match,
               cd.participant_id   AS participant_id,
               p.nickname     AS nickname
        FROM grid_cells c
        JOIN grids cd   ON cd.id = c.grid_id
        JOIN participants p  ON p.id = cd.participant_id
        JOIN words w    ON w.id = c.word_id
        WHERE cd.meeting_id = ? AND c.is_free = 0
        """,
        (meeting_id,),
    )

    index = MeetingIndex(meeting_id=meeting_id)
    index.window = deque(maxlen=max(get_settings().phrase_window, MAX_PHRASE_LENGTH))

    for row in rows:
        ref = CellRef(
            cell_id=row["cell_id"],
            grid_id=row["grid_id"],
            participant_id=row["participant_id"],
            nickname=row["nickname"],
            position=row["position"],
            word_id=row["word_id"],
            word_text=row["word_text"],
        )

        surfaces = [row["word_text"], *_parse_aliases(row["aliases"])]
        strict = bool(row["strict_match"])
        target = index.strict_keys if strict else index.keys

        for surface in surfaces:
            tokens = tokenize(surface)
            if not tokens:
                continue
            index.max_phrase = max(index.max_phrase, len(tokens))
            surface_keys = {exact_key(surface)} if strict else match_keys(surface)
            for key in surface_keys:
                target.setdefault(key, []).append(ref)

    index.max_phrase = min(index.max_phrase, MAX_PHRASE_LENGTH)
    return index


def get_index(meeting_id: str) -> MeetingIndex:
    if meeting_id not in _indexes:
        _indexes[meeting_id] = build_index(meeting_id)
    return _indexes[meeting_id]


def _parse_aliases(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(item) for item in parsed if str(item).strip()] if isinstance(parsed, list) else []


# --------------------------------------------------------------------------- grid build


def generate_grid_words(
    meeting_id: str,
    *,
    grid_size: int,
    free_space: bool,
    chosen_word_ids: list[str] | None = None,
    seed: int | None = None,
) -> list[str | None]:
    """Lay out a grid's words.

    ``chosen_word_ids`` are placed in the order given; any shortfall is topped up with a
    random sample of the remaining active pool, so "surprise me" and "I picked twelve of
    them, fill the rest" are the same code path.
    """
    total = grid_size * grid_size
    free_index = free_position(grid_size) if free_space else None
    needed = total - (1 if free_index is not None else 0)

    chosen = list(dict.fromkeys(chosen_word_ids or []))[:needed]

    pool = [
        row["id"]
        for row in query_all("SELECT id FROM words WHERE active = 1 ORDER BY id")
        if row["id"] not in set(chosen)
    ]
    rng = random.Random(seed)
    rng.shuffle(pool)

    shortfall = needed - len(chosen)
    if shortfall > 0:
        if len(pool) < shortfall:
            raise ValueError(
                f"Word pool too small: need {needed} words for a {grid_size}x{grid_size} "
                f"grid but only {len(chosen) + len(pool)} are available."
            )
        chosen.extend(pool[:shortfall])

    # Deliberately *not* shuffled: the participant arranges their own squares in the drafting
    # preview, and a shuffle here would silently rearrange the grid they just laid out.
    # Auto-filled squares are already random because `pool` was shuffled above, so a
    # grid built with no picks at all is still a random one.

    layout: list[str | None] = []
    cursor = 0
    for position in range(total):
        if free_index is not None and position == free_index:
            layout.append(None)
        else:
            layout.append(chosen[cursor])
            cursor += 1
    return layout


def create_grid(
    meeting: sqlite3.Row, participant_id: str, word_ids: list[str] | None = None
) -> str:
    """Create (or replace) a participant's grid for a meeting. Returns the grid id."""
    grid_size = meeting["grid_size"]
    free_space = bool(meeting["free_space"])
    layout = generate_grid_words(
        meeting["id"],
        grid_size=grid_size,
        free_space=free_space,
        chosen_word_ids=word_ids,
    )

    now = utcnow()
    grid_id = new_id()
    free_index = free_position(grid_size) if free_space else None

    with transaction() as conn:
        existing = conn.execute(
            "SELECT id, locked FROM grids WHERE meeting_id = ? AND participant_id = ?",
            (meeting["id"], participant_id),
        ).fetchone()
        if existing is not None:
            # Locking in is final. Redrafting after the fact would let a participant watch the
            # transcript, learn which words are landing, and rebuild around them — so the
            # first grid you commit to is the grid you play.
            raise PermissionError(
                "You have already locked in your grid for this meeting."
                if not existing["locked"]
                else "This grid is locked because the meeting is already live."
            )

        conn.execute(
            "INSERT INTO grids (id, meeting_id, participant_id, created_at, locked)"
            " VALUES (?, ?, ?, ?, 0)",
            (grid_id, meeting["id"], participant_id, now),
        )
        conn.executemany(
            """
            INSERT INTO grid_cells (id, grid_id, position, word_id, is_free, marked, marked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    new_id(),
                    grid_id,
                    position,
                    word_id,
                    1 if position == free_index else 0,
                    1 if position == free_index else 0,
                    now if position == free_index else None,
                )
                for position, word_id in enumerate(layout)
            ],
        )

    invalidate_index(meeting["id"])
    return grid_id


# --------------------------------------------------------------------------- ingest


@dataclass
class TokenHit:
    """One cell marked by one token."""

    cell_id: str
    grid_id: str
    participant_id: str
    nickname: str
    position: int
    word_id: str
    word_text: str
    matched_phrase: str


@dataclass
class CompletionAward:
    """A newly completed completed line."""

    id: str
    meeting_id: str
    grid_id: str
    participant_id: str
    nickname: str
    pattern: str
    label: str
    cells: list[int]
    rank: int
    achieved_at: str


@dataclass
class IngestResult:
    token_ids: list[str]
    tokens: list[str]
    #: Hits attributed to each token, positionally aligned with ``tokens``. A phrase
    #: scores on the token that completes it, so the ticker highlights the right word
    #: even when a whole sentence arrives in one call.
    token_hits: list[int]
    hits: list[TokenHit]
    completions: list[CompletionAward]
    seq: int


def _next_seq(meeting_id: str) -> int:
    row = query_one(
        "SELECT COALESCE(MAX(seq), 0) AS s FROM transcript_tokens WHERE meeting_id = ?",
        (meeting_id,),
    )
    return int(row["s"]) + 1 if row else 1


def apply_transcript(
    meeting: sqlite3.Row,
    text: str,
    *,
    speaker: str = "",
    source: str = "api",
) -> IngestResult:
    """Apply a chunk of transcript to every grid in a meeting.

    Callers must hold :func:`meeting_lock` for the meeting. The chunk is tokenised, and for each
    token every n-gram *ending* at that token (up to the longest phrase on any grid) is
    tested against the index. This is what lets "low hanging fruit" match across three
    separate ingest calls.
    """
    meeting_id = meeting["id"]
    index = get_index(meeting_id)
    tokens = tokenize(text)[: get_settings().max_ingest_tokens]

    result = IngestResult(
        token_ids=[], tokens=tokens, token_hits=[], hits=[], completions=[], seq=0
    )
    if not tokens:
        return result

    seq = _next_seq(meeting_id)
    result.seq = seq  # sequence of the first token in this chunk
    now = utcnow()
    already_marked = _marked_cell_ids(meeting_id)
    touched_grids: set[str] = set()

    for raw_token in tokens:
        token_id = new_id()
        index.window.append(raw_token)

        matches: list[tuple[CellRef, str]] = []
        max_n = min(index.max_phrase, len(index.window))
        for n in range(1, max_n + 1):
            gram_tokens = list(index.window)[-n:]
            phrase = " ".join(gram_tokens)
            for key in match_keys(phrase):
                for ref in index.lookup(key, strict=False):
                    matches.append((ref, phrase))
            for ref in index.lookup(exact_key(phrase), strict=True):
                matches.append((ref, phrase))

        hits: list[TokenHit] = []
        for ref, phrase in matches:
            if ref.cell_id in already_marked:
                continue
            already_marked.add(ref.cell_id)
            touched_grids.add(ref.grid_id)
            hits.append(
                TokenHit(
                    cell_id=ref.cell_id,
                    grid_id=ref.grid_id,
                    participant_id=ref.participant_id,
                    nickname=ref.nickname,
                    position=ref.position,
                    word_id=ref.word_id,
                    word_text=ref.word_text,
                    matched_phrase=phrase,
                )
            )

        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO transcript_tokens
                    (id, meeting_id, seq, raw, normalized, speaker, source, hit_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (token_id, meeting_id, seq, raw_token, raw_token, speaker, source, len(hits), now),
            )
            if hits:
                conn.executemany(
                    "UPDATE grid_cells SET marked = 1, marked_at = ?, token_id = ? WHERE id = ?",
                    [(now, token_id, hit.cell_id) for hit in hits],
                )

        result.token_ids.append(token_id)
        result.token_hits.append(len(hits))
        result.hits.extend(hits)
        seq += 1

    for grid_id in touched_grids:
        result.completions.extend(_award_completions(meeting, grid_id))

    return result


def _marked_cell_ids(meeting_id: str) -> set[str]:
    rows = query_all(
        """
        SELECT c.id FROM grid_cells c
        JOIN grids cd ON cd.id = c.grid_id
        WHERE cd.meeting_id = ? AND c.marked = 1
        """,
        (meeting_id,),
    )
    return {row["id"] for row in rows}


def _award_completions(meeting: sqlite3.Row, grid_id: str) -> list[CompletionAward]:
    """Record any completed lines newly completed on a grid."""
    grid_size = meeting["grid_size"]
    rows = query_all(
        "SELECT position, marked FROM grid_cells WHERE grid_id = ?",
        (grid_id,),
    )
    marked = {row["position"] for row in rows if row["marked"]}

    existing = {
        row["pattern"]
        for row in query_all("SELECT pattern FROM completions WHERE grid_id = ?", (grid_id,))
    }

    grid = query_one(
        """
        SELECT cd.id, cd.participant_id, p.nickname
        FROM grids cd JOIN participants p ON p.id = cd.participant_id
        WHERE cd.id = ?
        """,
        (grid_id,),
    )
    if grid is None:
        return []

    awards: list[CompletionAward] = []
    now = utcnow()

    for pattern, positions in completed_lines(grid_size).items():
        if pattern in existing or not set(positions).issubset(marked):
            continue
        rank_row = query_one(
            "SELECT COALESCE(MAX(rank), 0) AS r FROM completions WHERE meeting_id = ?",
            (meeting["id"],),
        )
        rank = int(rank_row["r"]) + 1 if rank_row else 1
        completion_id = new_id()
        execute(
            """
            INSERT INTO completions (id, meeting_id, grid_id, participant_id, pattern, cells, rank,
                                achieved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                completion_id,
                meeting["id"],
                grid_id,
                grid["participant_id"],
                pattern,
                json.dumps(positions),
                rank,
                now,
            ),
        )
        existing.add(pattern)
        awards.append(
            CompletionAward(
                id=completion_id,
                meeting_id=meeting["id"],
                grid_id=grid_id,
                participant_id=grid["participant_id"],
                nickname=grid["nickname"],
                pattern=pattern,
                label=pattern_label(pattern),
                cells=positions,
                rank=rank,
                achieved_at=now,
            )
        )

    return awards


# --------------------------------------------------------------------------- standings


def standings(meeting_id: str) -> list[dict]:
    """Rank participants: first to complete a line, then by lines, then by squares marked.

    Participants who have not yet hit completion are still listed so everyone can see how close
    the field is — they sort below anyone who has.
    """
    rows = query_all(
        """
        SELECT cd.id            AS grid_id,
               cd.participant_id     AS participant_id,
               p.nickname       AS nickname,
               p.avatar         AS avatar,
               p.accent         AS accent,
               COUNT(cc.id) FILTER (WHERE cc.marked = 1)  AS marked,
               COUNT(cc.id)                               AS total,
               (SELECT COUNT(*) FROM completions b WHERE b.grid_id = cd.id)      AS lines,
               (SELECT MIN(b.achieved_at) FROM completions b WHERE b.grid_id = cd.id)
                                                          AS first_completion_at,
               (SELECT MIN(b.rank) FROM completions b WHERE b.grid_id = cd.id)   AS best_rank
        FROM grids cd
        JOIN participants p ON p.id = cd.participant_id
        LEFT JOIN grid_cells cc ON cc.grid_id = cd.id
        WHERE cd.meeting_id = ?
        GROUP BY cd.id
        """,
        (meeting_id,),
    )

    entries = [
        {
            "grid_id": row["grid_id"],
            "participant_id": row["participant_id"],
            "nickname": row["nickname"],
            "avatar": row["avatar"],
            "accent": row["accent"],
            "marked": row["marked"] or 0,
            "total": row["total"] or 0,
            "lines": row["lines"] or 0,
            "first_completion_at": row["first_completion_at"],
            "best_rank": row["best_rank"],
        }
        for row in rows
    ]

    entries.sort(
        key=lambda e: (
            0 if e["first_completion_at"] else 1,
            e["first_completion_at"] or "",
            -e["lines"],
            -e["marked"],
            e["nickname"].lower(),
        )
    )
    for position, entry in enumerate(entries, start=1):
        entry["position"] = position
    return entries
