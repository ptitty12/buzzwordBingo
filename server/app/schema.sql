-- Jargon Watch schema.
-- SQLite, WAL mode. All ids are uuid4 hex strings; all timestamps are ISO-8601 UTC.
--
-- Identity model: there are no persistent participant accounts. A participant is scoped to a
-- single meeting — they pick a nickname when they join and that identity lives and dies
-- with the meeting. Administrators are not accounts either; they authenticate with a PIN.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meetings (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    code        TEXT NOT NULL UNIQUE,     -- short human-shareable join code
    status      TEXT NOT NULL DEFAULT 'open',  -- open | live | paused | ended
    grid_size   INTEGER NOT NULL DEFAULT 5,
    free_space  INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    ended_at    TEXT,
    created_by  TEXT NOT NULL DEFAULT 'admin',
    description TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);

-- A participant exists only within one meeting. The same nickname may be used by different
-- people in different meetings; within a meeting it is unique.
CREATE TABLE IF NOT EXISTS participants (
    id           TEXT PRIMARY KEY,
    meeting_id      TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    nickname     TEXT NOT NULL,
    nickname_key TEXT NOT NULL,           -- lowercased, for case-insensitive uniqueness
    avatar       TEXT NOT NULL DEFAULT '',
    accent       TEXT NOT NULL DEFAULT 'green',
    created_at   TEXT NOT NULL,
    last_seen_at TEXT,
    UNIQUE (meeting_id, nickname_key)
);

CREATE INDEX IF NOT EXISTS idx_participants_meeting ON participants(meeting_id);

CREATE TABLE IF NOT EXISTS words (
    id           TEXT PRIMARY KEY,
    text         TEXT NOT NULL,
    text_key     TEXT NOT NULL UNIQUE,    -- normalized, for duplicate detection
    category     TEXT NOT NULL DEFAULT 'General',
    difficulty   INTEGER NOT NULL DEFAULT 2,   -- 1 common .. 3 rare
    aliases      TEXT NOT NULL DEFAULT '[]',   -- JSON array of extra surface forms
    strict_match INTEGER NOT NULL DEFAULT 0,   -- disable fuzzy stemming for this word
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    created_by   TEXT NOT NULL DEFAULT 'admin',  -- 'admin' | 'participant:<nickname>' | 'seed'
    source       TEXT NOT NULL DEFAULT 'admin'   -- admin | seed | suggestion
);

CREATE INDEX IF NOT EXISTS idx_words_active ON words(active);
CREATE INDEX IF NOT EXISTS idx_words_category ON words(category);

CREATE TABLE IF NOT EXISTS grids (
    id         TEXT PRIMARY KEY,
    meeting_id    TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    participant_id  TEXT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    locked     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (meeting_id, participant_id)
);

CREATE INDEX IF NOT EXISTS idx_grids_meeting ON grids(meeting_id);

CREATE TABLE IF NOT EXISTS grid_cells (
    id        TEXT PRIMARY KEY,
    grid_id   TEXT NOT NULL REFERENCES grids(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,          -- row-major index, 0-based
    word_id   TEXT REFERENCES words(id) ON DELETE CASCADE,
    is_free   INTEGER NOT NULL DEFAULT 0,
    marked    INTEGER NOT NULL DEFAULT 0,
    marked_at TEXT,
    token_id  TEXT,
    UNIQUE (grid_id, position)
);

CREATE INDEX IF NOT EXISTS idx_cells_grid ON grid_cells(grid_id);
CREATE INDEX IF NOT EXISTS idx_cells_word ON grid_cells(word_id);

CREATE TABLE IF NOT EXISTS transcript_tokens (
    id         TEXT PRIMARY KEY,
    meeting_id    TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    raw        TEXT NOT NULL,
    normalized TEXT NOT NULL,
    speaker    TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'api',   -- api | simulator | manual
    hit_count  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tokens_meeting_seq ON transcript_tokens(meeting_id, seq);

CREATE TABLE IF NOT EXISTS completions (
    id          TEXT PRIMARY KEY,
    meeting_id     TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    grid_id     TEXT NOT NULL REFERENCES grids(id) ON DELETE CASCADE,
    participant_id   TEXT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
    pattern     TEXT NOT NULL,            -- row-2 | col-0 | diag-main | corners | blackout
    cells       TEXT NOT NULL DEFAULT '[]',  -- JSON array of positions
    rank        INTEGER NOT NULL,         -- 1-based order of this line within the meeting
    achieved_at TEXT NOT NULL,
    UNIQUE (grid_id, pattern)
);

CREATE INDEX IF NOT EXISTS idx_completions_meeting ON completions(meeting_id, rank);

-- Participant-submitted words, judged by an LLM curator (or an admin when no model key
-- is configured). Kept even after a decision so the admin console can audit calls.
CREATE TABLE IF NOT EXISTS word_suggestions (
    id           TEXT PRIMARY KEY,
    text         TEXT NOT NULL,
    text_key     TEXT NOT NULL,
    meeting_id      TEXT REFERENCES meetings(id) ON DELETE SET NULL,
    participant_id    TEXT REFERENCES participants(id) ON DELETE SET NULL,
    participant_name  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    verdict      TEXT NOT NULL DEFAULT '',         -- the judge's reasoning
    canonical    TEXT NOT NULL DEFAULT '',         -- corrected spelling, if any
    category     TEXT NOT NULL DEFAULT '',
    difficulty   INTEGER NOT NULL DEFAULT 2,
    judged_by    TEXT NOT NULL DEFAULT '',         -- model id, or 'admin'
    word_id      TEXT REFERENCES words(id) ON DELETE SET NULL,
    created_at   TEXT NOT NULL,
    decided_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_suggestions_status ON word_suggestions(status, created_at DESC);

CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    prefix       TEXT NOT NULL,           -- first chars, safe to display
    key_hash     TEXT NOT NULL UNIQUE,    -- sha256 of the full key
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    call_count   INTEGER NOT NULL DEFAULT 0,
    created_by   TEXT NOT NULL DEFAULT 'admin'
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         TEXT PRIMARY KEY,
    actor_id   TEXT,
    actor_name TEXT NOT NULL DEFAULT 'system',
    action     TEXT NOT NULL,
    entity     TEXT NOT NULL DEFAULT '',
    entity_id  TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);
