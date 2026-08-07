"""The bingo engine: card geometry, transcript matching, and win detection.

Ingest is the hot path and the one place where ordering genuinely matters — "first to
bingo" is only meaningful if tokens are applied in the order they were spoken. Each game
therefore serialises ingest behind its own asyncio lock, and every token is written with
a monotonic sequence number.

Matching works off an in-memory index (``GameIndex``) rebuilt whenever a game's cards
change. The index maps every match key produced by :mod:`app.lexicon` to the card cells
carrying that word, so a spoken token is resolved with a dict lookup rather than a scan
over every card.
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


def free_position(card_size: int) -> int:
    """Index of the free space — the exact centre, only defined for odd-sized cards."""
    return (card_size * card_size) // 2


def winning_lines(card_size: int) -> dict[str, list[int]]:
    """Every winning pattern for a card, keyed by a stable pattern id."""
    lines: dict[str, list[int]] = {}
    for row in range(card_size):
        lines[f"row-{row}"] = [row * card_size + col for col in range(card_size)]
    for col in range(card_size):
        lines[f"col-{col}"] = [row * card_size + col for row in range(card_size)]
    lines["diag-main"] = [i * card_size + i for i in range(card_size)]
    lines["diag-anti"] = [i * card_size + (card_size - 1 - i) for i in range(card_size)]
    lines["corners"] = [
        0,
        card_size - 1,
        card_size * (card_size - 1),
        card_size * card_size - 1,
    ]
    lines["blackout"] = list(range(card_size * card_size))
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
    card_id: str
    player_id: str
    nickname: str
    position: int
    word_id: str
    word_text: str


@dataclass
class GameIndex:
    """In-memory match index for one game."""

    game_id: str
    keys: dict[str, list[CellRef]] = field(default_factory=dict)
    strict_keys: dict[str, list[CellRef]] = field(default_factory=dict)
    max_phrase: int = 1
    window: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_PHRASE_LENGTH))

    def lookup(self, key: str, strict: bool) -> list[CellRef]:
        return (self.strict_keys if strict else self.keys).get(key, [])


_indexes: dict[str, GameIndex] = {}
_locks: dict[str, asyncio.Lock] = {}


def game_lock(game_id: str) -> asyncio.Lock:
    """Per-game ingest lock, so token application stays strictly ordered."""
    if game_id not in _locks:
        _locks[game_id] = asyncio.Lock()
    return _locks[game_id]


def invalidate_index(game_id: str) -> None:
    """Drop the cached index after cards or words change."""
    _indexes.pop(game_id, None)


def invalidate_all_indexes() -> None:
    _indexes.clear()


def build_index(game_id: str) -> GameIndex:
    """Rebuild a game's match index from the database."""
    rows = query_all(
        """
        SELECT c.id  AS cell_id,
               c.card_id,
               c.position,
               c.word_id,
               w.text         AS word_text,
               w.aliases      AS aliases,
               w.strict_match AS strict_match,
               cd.player_id   AS player_id,
               p.nickname     AS nickname
        FROM card_cells c
        JOIN cards cd   ON cd.id = c.card_id
        JOIN players p  ON p.id = cd.player_id
        JOIN words w    ON w.id = c.word_id
        WHERE cd.game_id = ? AND c.is_free = 0
        """,
        (game_id,),
    )

    index = GameIndex(game_id=game_id)
    index.window = deque(maxlen=max(get_settings().phrase_window, MAX_PHRASE_LENGTH))

    for row in rows:
        ref = CellRef(
            cell_id=row["cell_id"],
            card_id=row["card_id"],
            player_id=row["player_id"],
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


def get_index(game_id: str) -> GameIndex:
    if game_id not in _indexes:
        _indexes[game_id] = build_index(game_id)
    return _indexes[game_id]


def _parse_aliases(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(item) for item in parsed if str(item).strip()] if isinstance(parsed, list) else []


# --------------------------------------------------------------------------- card build


def generate_card_words(
    game_id: str,
    *,
    card_size: int,
    free_space: bool,
    chosen_word_ids: list[str] | None = None,
    seed: int | None = None,
) -> list[str | None]:
    """Lay out a card's words.

    ``chosen_word_ids`` are placed in the order given; any shortfall is topped up with a
    random sample of the remaining active pool, so "surprise me" and "I picked twelve of
    them, fill the rest" are the same code path.
    """
    total = card_size * card_size
    free_index = free_position(card_size) if free_space else None
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
                f"Word pool too small: need {needed} words for a {card_size}x{card_size} "
                f"card but only {len(chosen) + len(pool)} are available."
            )
        chosen.extend(pool[:shortfall])

    rng.shuffle(chosen)

    layout: list[str | None] = []
    cursor = 0
    for position in range(total):
        if free_index is not None and position == free_index:
            layout.append(None)
        else:
            layout.append(chosen[cursor])
            cursor += 1
    return layout


def create_card(game: sqlite3.Row, player_id: str, word_ids: list[str] | None = None) -> str:
    """Create (or replace) a player's card for a game. Returns the card id."""
    card_size = game["card_size"]
    free_space = bool(game["free_space"])
    layout = generate_card_words(
        game["id"],
        card_size=card_size,
        free_space=free_space,
        chosen_word_ids=word_ids,
    )

    now = utcnow()
    card_id = new_id()
    free_index = free_position(card_size) if free_space else None

    with transaction() as conn:
        existing = conn.execute(
            "SELECT id, locked FROM cards WHERE game_id = ? AND player_id = ?",
            (game["id"], player_id),
        ).fetchone()
        if existing is not None:
            if existing["locked"]:
                raise PermissionError("This card is locked because the game is already live.")
            conn.execute("DELETE FROM cards WHERE id = ?", (existing["id"],))

        conn.execute(
            "INSERT INTO cards (id, game_id, player_id, created_at, locked)"
            " VALUES (?, ?, ?, ?, 0)",
            (card_id, game["id"], player_id, now),
        )
        conn.executemany(
            """
            INSERT INTO card_cells (id, card_id, position, word_id, is_free, marked, marked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    new_id(),
                    card_id,
                    position,
                    word_id,
                    1 if position == free_index else 0,
                    1 if position == free_index else 0,
                    now if position == free_index else None,
                )
                for position, word_id in enumerate(layout)
            ],
        )

    invalidate_index(game["id"])
    return card_id


# --------------------------------------------------------------------------- ingest


@dataclass
class TokenHit:
    """One cell marked by one token."""

    cell_id: str
    card_id: str
    player_id: str
    nickname: str
    position: int
    word_id: str
    word_text: str
    matched_phrase: str


@dataclass
class BingoAward:
    """A newly completed winning line."""

    id: str
    game_id: str
    card_id: str
    player_id: str
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
    bingos: list[BingoAward]
    seq: int


def _next_seq(game_id: str) -> int:
    row = query_one("SELECT COALESCE(MAX(seq), 0) AS s FROM transcript_tokens WHERE game_id = ?",
                    (game_id,))
    return int(row["s"]) + 1 if row else 1


def apply_transcript(
    game: sqlite3.Row,
    text: str,
    *,
    speaker: str = "",
    source: str = "api",
) -> IngestResult:
    """Apply a chunk of transcript to every card in a game.

    Callers must hold :func:`game_lock` for the game. The chunk is tokenised, and for each
    token every n-gram *ending* at that token (up to the longest phrase on any card) is
    tested against the index. This is what lets "low hanging fruit" match across three
    separate ingest calls.
    """
    game_id = game["id"]
    index = get_index(game_id)
    tokens = tokenize(text)[: get_settings().max_ingest_tokens]

    result = IngestResult(
        token_ids=[], tokens=tokens, token_hits=[], hits=[], bingos=[], seq=0
    )
    if not tokens:
        return result

    seq = _next_seq(game_id)
    result.seq = seq  # sequence of the first token in this chunk
    now = utcnow()
    already_marked = _marked_cell_ids(game_id)
    touched_cards: set[str] = set()

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
            touched_cards.add(ref.card_id)
            hits.append(
                TokenHit(
                    cell_id=ref.cell_id,
                    card_id=ref.card_id,
                    player_id=ref.player_id,
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
                    (id, game_id, seq, raw, normalized, speaker, source, hit_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (token_id, game_id, seq, raw_token, raw_token, speaker, source, len(hits), now),
            )
            if hits:
                conn.executemany(
                    "UPDATE card_cells SET marked = 1, marked_at = ?, token_id = ? WHERE id = ?",
                    [(now, token_id, hit.cell_id) for hit in hits],
                )

        result.token_ids.append(token_id)
        result.token_hits.append(len(hits))
        result.hits.extend(hits)
        seq += 1

    for card_id in touched_cards:
        result.bingos.extend(_award_bingos(game, card_id))

    return result


def _marked_cell_ids(game_id: str) -> set[str]:
    rows = query_all(
        """
        SELECT c.id FROM card_cells c
        JOIN cards cd ON cd.id = c.card_id
        WHERE cd.game_id = ? AND c.marked = 1
        """,
        (game_id,),
    )
    return {row["id"] for row in rows}


def _award_bingos(game: sqlite3.Row, card_id: str) -> list[BingoAward]:
    """Record any winning lines newly completed on a card."""
    card_size = game["card_size"]
    rows = query_all(
        "SELECT position, marked FROM card_cells WHERE card_id = ?",
        (card_id,),
    )
    marked = {row["position"] for row in rows if row["marked"]}

    existing = {
        row["pattern"]
        for row in query_all("SELECT pattern FROM bingos WHERE card_id = ?", (card_id,))
    }

    card = query_one(
        """
        SELECT cd.id, cd.player_id, p.nickname
        FROM cards cd JOIN players p ON p.id = cd.player_id
        WHERE cd.id = ?
        """,
        (card_id,),
    )
    if card is None:
        return []

    awards: list[BingoAward] = []
    now = utcnow()

    for pattern, positions in winning_lines(card_size).items():
        if pattern in existing or not set(positions).issubset(marked):
            continue
        rank_row = query_one(
            "SELECT COALESCE(MAX(rank), 0) AS r FROM bingos WHERE game_id = ?", (game["id"],)
        )
        rank = int(rank_row["r"]) + 1 if rank_row else 1
        bingo_id = new_id()
        execute(
            """
            INSERT INTO bingos (id, game_id, card_id, player_id, pattern, cells, rank,
                                achieved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bingo_id,
                game["id"],
                card_id,
                card["player_id"],
                pattern,
                json.dumps(positions),
                rank,
                now,
            ),
        )
        existing.add(pattern)
        awards.append(
            BingoAward(
                id=bingo_id,
                game_id=game["id"],
                card_id=card_id,
                player_id=card["player_id"],
                nickname=card["nickname"],
                pattern=pattern,
                label=pattern_label(pattern),
                cells=positions,
                rank=rank,
                achieved_at=now,
            )
        )

    return awards


# --------------------------------------------------------------------------- leaderboard


def leaderboard(game_id: str) -> list[dict]:
    """Rank players: first to bingo wins, then by lines, then by squares marked.

    Players who have not yet hit bingo are still listed so everyone can see how close
    the field is — they sort below anyone who has.
    """
    rows = query_all(
        """
        SELECT cd.id            AS card_id,
               cd.player_id     AS player_id,
               p.nickname       AS nickname,
               p.avatar         AS avatar,
               p.accent         AS accent,
               COUNT(cc.id) FILTER (WHERE cc.marked = 1)  AS marked,
               COUNT(cc.id)                               AS total,
               (SELECT COUNT(*) FROM bingos b WHERE b.card_id = cd.id)      AS lines,
               (SELECT MIN(b.achieved_at) FROM bingos b WHERE b.card_id = cd.id) AS first_bingo_at,
               (SELECT MIN(b.rank) FROM bingos b WHERE b.card_id = cd.id)   AS best_rank
        FROM cards cd
        JOIN players p ON p.id = cd.player_id
        LEFT JOIN card_cells cc ON cc.card_id = cd.id
        WHERE cd.game_id = ?
        GROUP BY cd.id
        """,
        (game_id,),
    )

    entries = [
        {
            "card_id": row["card_id"],
            "player_id": row["player_id"],
            "nickname": row["nickname"],
            "avatar": row["avatar"],
            "accent": row["accent"],
            "marked": row["marked"] or 0,
            "total": row["total"] or 0,
            "lines": row["lines"] or 0,
            "first_bingo_at": row["first_bingo_at"],
            "best_rank": row["best_rank"],
        }
        for row in rows
    ]

    entries.sort(
        key=lambda e: (
            0 if e["first_bingo_at"] else 1,
            e["first_bingo_at"] or "",
            -e["lines"],
            -e["marked"],
            e["nickname"].lower(),
        )
    )
    for position, entry in enumerate(entries, start=1):
        entry["position"] = position
    return entries
