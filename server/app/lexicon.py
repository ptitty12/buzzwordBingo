"""Lexical normalisation and fuzzy buzzword matching.

A transcript arrives as a stream of raw, messy tokens ("Synergies,", "leveraging",
"LOW-HANGING"). A bingo card holds curated buzzwords ("synergy", "leverage",
"low hanging fruit"). This module decides whether a spoken token is "the same word"
as a card word.

Design
------
Every phrase resolves to a *set of match keys*. Two phrases match when their key sets
intersect. This is deliberately more forgiving than a single canonical stem:
derivational forms ("disruption" -> "disrupt") are modelled as extra keys without
corrupting the canonical stem of unrelated words::

    match_keys("synergy")    -> {"synerg", "synergy"}
    match_keys("synergies")  -> {"synerg", "synergy"}   # inflectional: -ies
    match_keys("leveraged")  -> {"leverag"}             # inflectional: -ed
    match_keys("disruption") -> {"disruption", "disrupt"}  # derivational: -ion

Matching is intentionally *lossy but symmetric*: both the card word and the transcript
token run through the identical pipeline, so a word always matches itself no matter how
aggressive the stemmer is.
"""

from __future__ import annotations

import re
import unicodedata

#: Longest multi-word phrase the n-gram scanner will consider (e.g. "move the needle").
MAX_PHRASE_LENGTH = 5

#: Carry no signal inside a phrase but are frequently dropped or slurred in speech.
PHRASE_STOPWORDS = frozenset({"the", "a", "an", "of", "to"})

#: Irregular forms suffix-stripping cannot reach. Kept deliberately small — anything
#: domain-specific belongs in a word's admin-managed alias list.
IRREGULARS: dict[str, str] = {
    "people": "person",
    "children": "child",
    "men": "man",
    "women": "woman",
    "data": "datum",
    "criteria": "criterion",
    "analyses": "analysis",
    "theses": "thesis",
    "matrices": "matrix",
    "indices": "index",
    "vertices": "vertex",
    "feet": "foot",
    "teeth": "tooth",
    "mice": "mouse",
    "went": "go",
    "gone": "go",
    "goes": "go",
    "going": "go",
    "said": "say",
    "says": "say",
    "saw": "see",
    "seen": "see",
    "been": "be",
    "was": "be",
    "were": "be",
    "are": "be",
    "is": "be",
    "am": "be",
    "had": "have",
    "has": "have",
    "did": "do",
    "does": "do",
    "done": "do",
    "made": "make",
    "makes": "make",
    "took": "take",
    "taken": "take",
    "got": "get",
    "gotten": "get",
    "built": "build",
    "bought": "buy",
    "brought": "bring",
    "thought": "think",
    "taught": "teach",
    "caught": "catch",
    "ran": "run",
    "began": "begin",
    "begun": "begin",
    "drove": "drive",
    "driven": "drive",
    "grew": "grow",
    "grown": "grow",
    "knew": "know",
    "known": "know",
    "spoke": "speak",
    "spoken": "speak",
    "wrote": "write",
    "written": "write",
}

#: Short function words and acronyms that must never be stemmed.
NEVER_STEM = frozenset(
    {
        "this", "that", "these", "those", "less", "miss", "boss", "loss", "news",
        "bus", "gas", "plus", "thus", "yes", "his", "its", "us", "as", "ops",
        "devops", "kpis", "okrs", "apis", "saas", "paas", "iaas", "aws", "ai",
        "ml", "llm", "roi", "sla", "eod", "eta", "poc", "mvp",
    }
)

VOWELS = frozenset("aeiou")

_COMBINING = re.compile(r"[̀-ͯ]")
_SMART_QUOTES = re.compile(r"[‘’ʼ]")
_DASHES = re.compile(r"[‐-―]")
_DISALLOWED = re.compile(r"[^a-z0-9'\-\s]")
_WHITESPACE = re.compile(r"\s+")
_SPLIT = re.compile(r"[\s\-]+")
_DIGITS = re.compile(r"^\d+$")


def normalize_text(value: str) -> str:
    """Lowercase, strip diacritics, and remove everything but letters, digits and separators."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = _COMBINING.sub("", text)
    text = text.lower()
    text = _SMART_QUOTES.sub("'", text)
    text = _DASHES.sub("-", text)
    text = text.replace("&", " and ")
    text = _DISALLOWED.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def tokenize(value: str) -> list[str]:
    """Split arbitrary text into word tokens. Hyphens split ("low-hanging" -> 2 tokens)."""
    normalized = normalize_text(value)
    if not normalized:
        return []
    tokens = []
    for raw in _SPLIT.split(normalized):
        token = raw.strip("'")
        if token and any(c.isalnum() for c in token):
            tokens.append(token)
    return tokens


def _undouble(word: str) -> str:
    """Collapse a doubled final consonant: "runn" -> "run", "stopp" -> "stop"."""
    if len(word) < 4:
        return word
    last, prev = word[-1], word[-2]
    if last == prev and last not in VOWELS and last not in ("l", "s"):
        return word[:-1]
    return word


def _drop_silent_e(word: str) -> str:
    """Drop a silent trailing "e" so "leverage" and "leveraging" converge on "leverag"."""
    if len(word) > 4 and word.endswith("e") and not word.endswith("ee"):
        return word[:-1]
    return word


def stem(token: str) -> str:
    """Reduce a single token to its canonical stem.

    Applies at most one inflectional rule (adverbial ``-ly`` first, then exactly one of
    ``-ing`` / ``-ed`` / plural / comparative), followed by shared normalisation. Keeping
    the inflectional step exclusive avoids the runaway over-stemming you get from
    chaining every rule together.
    """
    word = normalize_text(token).replace(" ", "")
    word = word.removesuffix("'s").replace("'", "")

    if not word:
        return ""
    if word in IRREGULARS:
        return IRREGULARS[word]
    if word in NEVER_STEM:
        return word
    if len(word) <= 3 or _DIGITS.match(word):
        return word

    # Adverbial -ly comes first because it stacks on top of other suffixes
    # ("strategically" = strategic + al + ly).
    if word.endswith("ly") and len(word) > 4:
        word = word[:-2]
        if word.endswith("ical"):
            word = word[:-2]  # strategical -> strategic
        elif word.endswith("i"):
            word = f"{word[:-1]}y"  # happi -> happy

    # Exactly one inflectional rule.
    if word.endswith("ing") and len(word) > 5:
        word = _undouble(word[:-3])
    elif word.endswith("ied") and len(word) > 4:
        word = f"{word[:-3]}y"
    elif word.endswith("ed") and len(word) > 4 and not word.endswith("eed"):
        word = _undouble(word[:-2])
    elif word.endswith("ies") and len(word) > 4:
        word = f"{word[:-3]}y"
    elif word.endswith("sses") or len(word) > 4 and re.search(r"(ch|sh|ss|x|z|o)es$", word):
        word = word[:-2]
    elif word.endswith("s") and len(word) > 3 and not re.search(r"(ss|us|is)$", word):
        word = word[:-1]
    elif word.endswith("est") and len(word) > 5:
        word = _undouble(word[:-3])
    elif word.endswith("er") and len(word) > 5:
        word = _undouble(word[:-2])

    return _undouble(_drop_silent_e(word))


#: Derivational rewrites. Unlike :func:`stem` these produce *additional* candidate keys
#: rather than replacing the canonical stem, so "disruption" can reach "disrupt" without
#: "decision" hijacking "decide".
_DERIVATIONS: list[tuple[re.Pattern[str], object]] = [
    (re.compile(r"(t|s)ion$"), lambda w: w[:-3]),          # disruption -> disrupt
    (re.compile(r"ization$"), lambda w: f"{w[:-7]}ize"),
    (re.compile(r"isation$"), lambda w: f"{w[:-7]}ise"),
    (re.compile(r"ative$"), lambda w: f"{w[:-5]}ate"),     # iterative -> iterate
    (re.compile(r"ive$"), lambda w: w[:-3]),               # disruptive -> disrupt
    (re.compile(r"ness$"), lambda w: w[:-4]),              # awareness -> aware
    (re.compile(r"ment$"), lambda w: w[:-4]),              # alignment -> align
    (re.compile(r"ability$"), lambda w: f"{w[:-7]}able"),  # scalability -> scalable
    (re.compile(r"ibility$"), lambda w: f"{w[:-7]}ible"),
    (re.compile(r"ility$"), lambda w: f"{w[:-5]}le"),      # agility -> agile
    (re.compile(r"ity$"), lambda w: w[:-3]),               # velocity -> veloc
    (re.compile(r"ance$"), lambda w: w[:-4]),              # performance -> perform
    (re.compile(r"ence$"), lambda w: w[:-4]),
    (re.compile(r"al$"), lambda w: w[:-2]),                # operational -> operation
    (re.compile(r"ic$"), lambda w: w[:-2]),                # strategic -> strateg
    (re.compile(r"able$"), lambda w: w[:-4]),              # actionable -> action
    (re.compile(r"ify$"), lambda w: w[:-3]),               # gamify -> gam
    (re.compile(r"ize$"), lambda w: w[:-3]),               # operationalize -> operational
    (re.compile(r"ise$"), lambda w: w[:-3]),
    (re.compile(r"er$"), lambda w: w[:-2]),                # disrupter -> disrupt
    (re.compile(r"or$"), lambda w: w[:-2]),                # innovator -> innovat
    (re.compile(r"y$"), lambda w: w[:-1]),                 # synergy -> synerg
]

MIN_KEY_LENGTH = 3
DERIVATION_DEPTH = 2


def _token_keys(token: str) -> set[str]:
    """Expand a single token into every key it could legitimately match on."""
    canonical = stem(token)
    keys: set[str] = set()
    if not canonical:
        return keys
    keys.add(canonical)

    frontier = [canonical]
    for _ in range(DERIVATION_DEPTH):
        nxt: list[str] = []
        for word in frontier:
            # `stem` has already dropped any silent trailing "e", which would hide
            # suffixes like -ive/-ance/-ize from the rules below. Probe the restored
            # spelling as well so "disruptiv" can still reach "disrupt".
            candidates = (word, f"{word}e") if not word.endswith("e") else (word,)
            for candidate in candidates:
                for pattern, apply in _DERIVATIONS:
                    if not pattern.search(candidate):
                        continue
                    derived = _undouble(_drop_silent_e(apply(candidate)))  # type: ignore[operator]
                    if len(derived) < MIN_KEY_LENGTH or derived in keys:
                        continue
                    keys.add(derived)
                    nxt.append(derived)
        if not nxt:
            break
        frontier = nxt

    return keys


def match_keys(phrase: str) -> set[str]:
    """Build the full match-key set for a phrase of one or more words.

    Single words expand through the derivation ladder. Multi-word phrases use only
    canonical stems — the cross-product of derivations would explode and the extra recall
    is not worth the false positives — but they do emit a stopword-stripped variant so
    "voice of the customer" matches "voice of customer".
    """
    tokens = tokenize(phrase)
    if not tokens:
        return set()
    if len(tokens) == 1:
        return _token_keys(tokens[0])

    keys: set[str] = set()
    stems = [s for s in (stem(t) for t in tokens) if s]
    if stems:
        keys.add(" ".join(stems))

    without_stopwords = [
        s for s in (stem(t) for t in tokens if t not in PHRASE_STOPWORDS) if s
    ]
    if without_stopwords:
        keys.add(" ".join(without_stopwords))

    return keys


def phrase_length(phrase: str) -> int:
    """How many transcript tokens a phrase consumes."""
    return len(tokenize(phrase))


def phrases_match(left: str, right: str) -> bool:
    """True when two phrases should be considered the same buzzword."""
    return bool(match_keys(left) & match_keys(right))


def exact_key(phrase: str) -> str:
    """Exact (non-fuzzy) key for words an admin has flagged ``strict_match``."""
    return " ".join(tokenize(phrase))
