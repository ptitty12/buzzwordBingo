"""Pydantic request/response models.

These double as the OpenAPI contract published at ``/api/docs``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

GameStatus = Literal["lobby", "live", "paused", "ended"]

ACCENTS = ("green", "teal", "cyan", "violet", "amber", "lime", "rose", "sky")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- auth


class UserPublic(ORMModel):
    id: str
    nickname: str
    avatar: str = ""
    accent: str = "green"
    is_admin: bool = False
    created_at: str
    last_seen_at: str | None = None


class UserSummary(ORMModel):
    """Shape used by the sign-in dropdown — deliberately free of anything sensitive."""

    id: str
    nickname: str
    avatar: str = ""
    accent: str = "green"
    is_admin: bool = False
    games_played: int = 0


class SignUpRequest(BaseModel):
    nickname: str = Field(min_length=2, max_length=24)
    avatar: str = Field(default="", max_length=8)
    accent: str = "green"

    @field_validator("nickname")
    @classmethod
    def clean_nickname(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not any(ch.isalnum() for ch in cleaned):
            raise ValueError("Nickname needs at least one letter or number.")
        return cleaned

    @field_validator("accent")
    @classmethod
    def known_accent(cls, value: str) -> str:
        return value if value in ACCENTS else "green"


class SignInRequest(BaseModel):
    user_id: str
    admin_pin: str = ""


class Session(BaseModel):
    token: str
    user: UserPublic


# --------------------------------------------------------------------------- words


class WordPublic(ORMModel):
    id: str
    text: str
    category: str
    difficulty: int
    aliases: list[str] = []
    strict_match: bool = False
    active: bool = True
    created_at: str
    usage_count: int = 0


class WordCreate(BaseModel):
    text: str = Field(min_length=1, max_length=48)
    category: str = Field(default="General", max_length=32)
    difficulty: int = Field(default=2, ge=1, le=3)
    aliases: list[str] = Field(default_factory=list)
    strict_match: bool = False
    active: bool = True

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Word cannot be blank.")
        return cleaned

    @field_validator("aliases")
    @classmethod
    def clean_aliases(cls, value: list[str]) -> list[str]:
        return [" ".join(a.split()) for a in value if a and a.strip()][:12]


class WordUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=48)
    category: str | None = Field(default=None, max_length=32)
    difficulty: int | None = Field(default=None, ge=1, le=3)
    aliases: list[str] | None = None
    strict_match: bool | None = None
    active: bool | None = None


class WordBulkCreate(BaseModel):
    """Paste-a-list bulk import. One word per line, optional ``Category: word`` prefix."""

    payload: str = Field(min_length=1)
    category: str = Field(default="General", max_length=32)
    difficulty: int = Field(default=2, ge=1, le=3)


# --------------------------------------------------------------------------- games


class GamePublic(ORMModel):
    id: str
    name: str
    code: str
    status: GameStatus
    card_size: int
    free_space: bool
    description: str = ""
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    player_count: int = 0
    token_count: int = 0
    bingo_count: int = 0


class GameCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    description: str = Field(default="", max_length=200)
    card_size: int = Field(default=5, ge=3, le=7)
    free_space: bool = True

    @field_validator("card_size")
    @classmethod
    def odd_size_only(cls, value: int) -> int:
        if value % 2 == 0:
            raise ValueError("Card size must be odd so the free space lands in the centre.")
        return value


class GameStatusUpdate(BaseModel):
    status: GameStatus


# --------------------------------------------------------------------------- cards


class CardCell(BaseModel):
    id: str
    position: int
    word_id: str | None = None
    text: str
    category: str = ""
    is_free: bool = False
    marked: bool = False
    marked_at: str | None = None


class CardPublic(BaseModel):
    id: str
    game_id: str
    user_id: str
    nickname: str
    avatar: str = ""
    accent: str = "green"
    card_size: int
    locked: bool = False
    created_at: str
    cells: list[CardCell]
    marked_count: int = 0
    lines: list[str] = []
    best_rank: int | None = None


class CardCreate(BaseModel):
    """Word ids the player hand-picked. Any shortfall is auto-filled from the pool."""

    word_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- transcript


class IngestRequest(BaseModel):
    """A slice of live transcript.

    ``text`` may be a single word or a longer chunk — it is tokenised server-side, so a
    caller streaming word-by-word and one posting whole sentences behave identically.
    """

    text: str = Field(min_length=1, max_length=8000)
    game_id: str | None = Field(
        default=None,
        description="Target game. Omit to broadcast to every live game.",
    )
    game_code: str | None = Field(default=None, description="Join code alternative to game_id.")
    speaker: str = Field(default="", max_length=48)
    source: str = Field(default="api", max_length=24)


class IngestHit(BaseModel):
    user_id: str
    nickname: str
    card_id: str
    position: int
    word: str
    matched_phrase: str


class IngestBingo(BaseModel):
    user_id: str
    nickname: str
    pattern: str
    label: str
    rank: int
    cells: list[int]
    achieved_at: str


class IngestGameResult(BaseModel):
    game_id: str
    game_name: str
    tokens: list[str]
    hits: list[IngestHit]
    bingos: list[IngestBingo]


class IngestResponse(BaseModel):
    accepted: bool = True
    token_count: int
    results: list[IngestGameResult]


class TranscriptToken(ORMModel):
    id: str
    seq: int
    raw: str
    speaker: str = ""
    source: str = "api"
    hit_count: int = 0
    created_at: str


# --------------------------------------------------------------------------- leaderboard


class LeaderboardEntry(BaseModel):
    position: int
    user_id: str
    card_id: str
    nickname: str
    avatar: str = ""
    accent: str = "green"
    marked: int
    total: int
    lines: int
    first_bingo_at: str | None = None
    best_rank: int | None = None


# --------------------------------------------------------------------------- admin


class ApiKeyPublic(ORMModel):
    id: str
    name: str
    prefix: str
    active: bool
    created_at: str
    last_used_at: str | None = None
    call_count: int = 0


class ApiKeyCreated(ApiKeyPublic):
    key: str = Field(description="Full key — shown once and never stored in plaintext.")


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=48)


class AuditEntry(ORMModel):
    id: str
    actor_name: str
    action: str
    entity: str = ""
    entity_id: str = ""
    detail: str = ""
    created_at: str


class AdminStats(BaseModel):
    users: int
    admins: int
    words: int
    active_words: int
    games: int
    live_games: int
    cards: int
    tokens: int
    bingos: int
    connected_sockets: int
    admin_pin_set: bool
    environment: str


class UserAdminUpdate(BaseModel):
    is_admin: bool | None = None
    nickname: str | None = Field(default=None, min_length=2, max_length=24)
