"""The buzzword judge.

Participants can propose additions to the word pool. Rather than letting anyone drop "the"
onto a completion grid, each submission goes to Claude, which decides whether the term is
actually *buzzwordy* — corporate jargon, consultant-speak, meeting filler, tech
hype — and rejects ordinary vocabulary.

    "sales"                 -> rejected  (a plain business noun, not jargon)
    "the"                   -> rejected  (a stopword)
    "business fundamentals" -> approved  (management-speak)
    "crosspolination"       -> approved  (jargon; canonicalised to "cross-pollination")

The judge also returns a canonical spelling, a category and a rarity, so an approved
suggestion lands in the pool fully curated. The participant's original spelling is kept as
an alias so it still matches if a speaker says it that way.

When no API key is configured the module degrades to ``PENDING`` — suggestions queue
for a human admin instead of being silently auto-approved.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

from .config import get_settings
from .lexicon import tokenize

logger = logging.getLogger("jargon.moderation")

Decision = Literal["approved", "rejected", "pending"]

#: Categories the judge may file a word under — kept in sync with the seeded pool so
#: approved suggestions slot into the existing filters rather than inventing new ones.
CATEGORIES = [
    "Corporate Strategy",
    "Agile & Delivery",
    "Tech & AI",
    "Sales & Growth",
    "Meeting Filler",
    "Finance & Ops",
    "Consulting-Speak",
]

SYSTEM_PROMPT = """You are the curator of the word pool for Jargon Watch, a meeting \
played during corporate meetings. Participants submit terms; you decide which ones earn a \
square on a completion grid.

Approve a term when it is genuine workplace jargon — corporate strategy speak, \
consultant-speak, management cliché, meeting filler, agile/delivery ritual language, \
sales patter, or technology hype. Buzzwords are terms whose use signals a register \
rather than conveying much meaning, and a good completion square is one participants would \
groan at hearing.

Reject a term when it is ordinary vocabulary rather than jargon: bare common nouns \
("business", "sales", "meeting"), function words ("the", "and"), plain verbs, numbers, \
proper nouns with no jargon value, anything nonsensical, and anything slurring or \
harassing a real person or group. Reject a term already covered by an existing pool \
entry you are shown.

Judgement calls: a compound built from plain words can still be jargon ("business \
fundamentals", "value creation") — the phrase, not the vocabulary, is what matters. \
Misspelled jargon should be approved and corrected ("crosspolination" is \
cross-pollination). A single vivid jargon word is fine ("synergy", "ideate").

Set "canonical" to the term's standard spelling in lowercase, keeping acronyms \
uppercase. Set "reason" to one short sentence addressed to the participant explaining the \
call — it is shown to them verbatim."""

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "approved": {
            "type": "boolean",
            "description": "True when the term belongs on a completion grid.",
        },
        "canonical": {
            "type": "string",
            "description": "Standard spelling of the term, lowercase except acronyms.",
        },
        "category": {"type": "string", "enum": CATEGORIES},
        "difficulty": {
            "type": "integer",
            "enum": [1, 2, 3],
            "description": "1 = heard constantly, 2 = occasional, 3 = rare.",
        },
        "reason": {
            "type": "string",
            "description": "One short sentence for the participant explaining the decision.",
        },
    },
    "required": ["approved", "canonical", "category", "difficulty", "reason"],
    "additionalProperties": False,
}


@dataclass
class Verdict:
    """The outcome of judging one submission."""

    decision: Decision
    reason: str
    canonical: str = ""
    category: str = "General"
    difficulty: int = 2
    judged_by: str = ""

    @property
    def approved(self) -> bool:
        return self.decision == "approved"


def _pending(reason: str) -> Verdict:
    return Verdict(decision="pending", reason=reason, judged_by="")


def prescreen(text: str) -> Verdict | None:
    """Cheap local checks so obviously-bad input never costs an API call.

    Only rejects things no reasonable judge would accept — length, emptiness, and
    digits. Deciding whether a real phrase is *buzzwordy* is the model's job.
    """
    tokens = tokenize(text)
    if not tokens:
        return Verdict(
            decision="rejected",
            reason="That does not contain any letters or numbers.",
            judged_by="prescreen",
        )
    if len(tokens) > 6:
        return Verdict(
            decision="rejected",
            reason="That is a sentence, not a buzzword — try a shorter phrase.",
            judged_by="prescreen",
        )
    if all(token.isdigit() for token in tokens):
        return Verdict(
            decision="rejected",
            reason="Numbers alone do not make a buzzword.",
            judged_by="prescreen",
        )
    return None


def _build_prompt(text: str, existing: list[str]) -> str:
    sample = ", ".join(existing[:60])
    return (
        f"Submitted term: {text!r}\n\n"
        f"A sample of terms already in the pool: {sample}\n\n"
        "Decide whether this term earns a square on a Jargon Watch grid."
    )


def judge(text: str, existing_words: list[str] | None = None) -> Verdict:
    """Ask Claude whether a submitted term belongs in the pool.

    Never raises: any failure (missing key, network, refusal, malformed output) becomes
    a ``pending`` verdict so the suggestion falls back to admin review.
    """
    early = prescreen(text)
    if early is not None:
        return early

    settings = get_settings()
    if not settings.moderation_enabled:
        return _pending("Queued for an administrator to review.")

    try:
        import anthropic
    except ImportError:  # pragma: no cover - dependency is declared, guard anyway
        logger.warning("anthropic SDK unavailable; queuing suggestion for admin review")
        return _pending("Queued for an administrator to review.")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    try:
        response = client.messages.create(
            model=settings.moderation_model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            # A short classification does not need deep reasoning; low effort keeps the
            # round-trip fast enough to answer the participant inline.
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": RESULT_SCHEMA},
            },
            messages=[{"role": "user", "content": _build_prompt(text, existing_words or [])}],
        )
    except anthropic.APIStatusError as exc:
        logger.warning("buzzword judge HTTP %s: %s", exc.status_code, exc.message)
        return _pending("The word judge is unavailable — an administrator will review this.")
    except anthropic.APIConnectionError:
        logger.warning("buzzword judge unreachable")
        return _pending("The word judge is unreachable — an administrator will review this.")

    # Safety classifiers can decline a request; `content` is then empty or partial, so
    # check the stop reason before indexing into it.
    if response.stop_reason == "refusal":
        return Verdict(
            decision="rejected",
            reason="That submission was declined by the content filter.",
            judged_by=settings.moderation_model,
        )

    payload = "".join(block.text for block in response.content if block.type == "text")
    if not payload.strip():
        logger.warning("buzzword judge returned no text (stop_reason=%s)", response.stop_reason)
        return _pending("The word judge returned nothing — an administrator will review this.")

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("buzzword judge returned non-JSON: %.200s", payload)
        return _pending("The word judge returned an unreadable answer — queued for review.")

    return _verdict_from(parsed, settings.moderation_model, fallback=text)


def _verdict_from(parsed: dict, model: str, *, fallback: str) -> Verdict:
    """Convert the model's JSON into a Verdict, tolerating missing or odd fields."""
    approved = bool(parsed.get("approved"))
    canonical = str(parsed.get("canonical") or fallback).strip() or fallback
    category = str(parsed.get("category") or "General").strip()
    if category not in CATEGORIES:
        category = "General"

    try:
        difficulty = int(parsed.get("difficulty", 2))
    except (TypeError, ValueError):
        difficulty = 2
    difficulty = min(3, max(1, difficulty))

    reason = str(parsed.get("reason") or "").strip()
    if not reason:
        reason = "Approved." if approved else "That is not buzzwordy enough."

    return Verdict(
        decision="approved" if approved else "rejected",
        reason=reason,
        canonical=canonical,
        category=category,
        difficulty=difficulty,
        judged_by=model,
    )
