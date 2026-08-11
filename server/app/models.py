"""Pydantic request/response models.

These double as the OpenAPI contract published at ``/api/docs``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MeetingStatus = Literal["open", "live", "paused", "ended"]

ACCENTS = ("green", "teal", "cyan", "violet", "amber", "lime", "rose", "sky")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- auth


class ParticipantPublic(ORMModel):
    """A participant, scoped to the single meeting they joined."""

    id: str
    meeting_id: str
    nickname: str
    avatar: str = ""
    accent: str = "green"
    created_at: str
    last_seen_at: str | None = None


class JoinRequest(BaseModel):
    """Everything needed to enter a meeting — a nickname, and nothing else required."""

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


class AdminSignIn(BaseModel):
    pin: str = Field(min_length=1, max_length=32)


class ParticipantSession(BaseModel):
    """Issued on join: a token scoped to one meeting, plus the participant it identifies."""

    token: str
    participant: ParticipantPublic
    meeting_id: str


class AdminSession(BaseModel):
    token: str
    is_admin: bool = True


class Identity(BaseModel):
    """Who the caller is, as the frontend sees it."""

    is_admin: bool = False
    participant: ParticipantPublic | None = None


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
    created_by: str = "admin"
    source: str = "admin"
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


# --------------------------------------------------------------------------- meetings


class MeetingPublic(ORMModel):
    id: str
    name: str
    code: str
    status: MeetingStatus
    grid_size: int
    free_space: bool
    description: str = ""
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    participant_count: int = 0
    token_count: int = 0
    completion_count: int = 0


class MeetingCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    description: str = Field(default="", max_length=200)
    grid_size: int = Field(default=5, ge=3, le=7)
    free_space: bool = True

    @field_validator("grid_size")
    @classmethod
    def odd_size_only(cls, value: int) -> int:
        if value % 2 == 0:
            raise ValueError("Grid size must be odd so the free space lands in the centre.")
        return value


class MeetingStatusUpdate(BaseModel):
    status: MeetingStatus


# --------------------------------------------------------------------------- grids


class GridCell(BaseModel):
    id: str
    position: int
    word_id: str | None = None
    text: str
    category: str = ""
    is_free: bool = False
    marked: bool = False
    marked_at: str | None = None


class GridPublic(BaseModel):
    id: str
    meeting_id: str
    participant_id: str
    nickname: str
    avatar: str = ""
    accent: str = "green"
    grid_size: int
    locked: bool = False
    created_at: str
    cells: list[GridCell]
    marked_count: int = 0
    lines: list[str] = []
    best_rank: int | None = None


class GridCreate(BaseModel):
    """Word ids the participant hand-picked. Any shortfall is auto-filled from the pool."""

    word_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- transcript


class IngestRequest(BaseModel):
    """A slice of live transcript.

    ``text`` may be a single word or a longer chunk — it is tokenised server-side, so a
    caller streaming word-by-word and one posting whole sentences behave identically.
    """

    text: str = Field(min_length=1, max_length=8000)
    meeting_id: str | None = Field(
        default=None,
        description="Target meeting. Omit to broadcast to every live meeting.",
    )
    meeting_code: str | None = Field(
        default=None, description="Join code alternative to meeting_id."
    )
    speaker: str = Field(default="", max_length=48)
    source: str = Field(default="api", max_length=24)


class IngestHit(BaseModel):
    participant_id: str
    nickname: str
    grid_id: str
    position: int
    word: str
    matched_phrase: str


class IngestCompletion(BaseModel):
    participant_id: str
    nickname: str
    pattern: str
    label: str
    rank: int
    cells: list[int]
    achieved_at: str


class IngestMeetingResult(BaseModel):
    meeting_id: str
    meeting_name: str
    tokens: list[str]
    hits: list[IngestHit]
    completions: list[IngestCompletion]


class IngestResponse(BaseModel):
    accepted: bool = True
    token_count: int
    results: list[IngestMeetingResult]


class TranscriptToken(ORMModel):
    id: str
    seq: int
    raw: str
    speaker: str = ""
    source: str = "api"
    hit_count: int = 0
    created_at: str


# --------------------------------------------------------------------------- standings


class StandingsEntry(BaseModel):
    position: int
    participant_id: str
    grid_id: str
    nickname: str
    avatar: str = ""
    accent: str = "green"
    marked: int
    total: int
    lines: int
    first_completion_at: str | None = None
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
    participants: int
    words: int
    active_words: int
    meetings: int
    live_meetings: int
    grids: int
    tokens: int
    completions: int
    connected_sockets: int
    pending_suggestions: int
    moderation_enabled: bool
    moderation_model: str
    environment: str


# --------------------------------------------------------------------------- suggestions


class WordSuggestionRequest(BaseModel):
    """A participant proposing a new buzzword for the pool."""

    text: str = Field(min_length=2, max_length=48)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Say something.")
        return cleaned


class WordSuggestionPublic(ORMModel):
    id: str
    text: str
    canonical: str = ""
    status: str
    verdict: str = ""
    category: str = ""
    difficulty: int = 2
    judged_by: str = ""
    participant_name: str = ""
    word_id: str | None = None
    created_at: str
    decided_at: str | None = None


class SuggestionResponse(BaseModel):
    """What the participant sees immediately after submitting."""

    suggestion: WordSuggestionPublic
    word: WordPublic | None = None
    remaining: int = 0


class SuggestionDecision(BaseModel):
    """An admin overriding (or standing in for) the judge."""

    approve: bool
    reason: str = Field(default="", max_length=280)
