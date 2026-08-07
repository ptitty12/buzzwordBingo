"""Seed the database with a starter buzzword pool, an ingest key and a demo game.

Seeding is idempotent: every insert is keyed on a natural unique column, so running it
against an existing database only tops up what is missing.
"""

from __future__ import annotations

import json
import logging

from .db import execute, new_id, query_one, record_audit, utcnow
from .engine import invalidate_all_indexes
from .lexicon import exact_key
from .security import generate_api_key

logger = logging.getLogger("bingo.seed")

#: (text, category, difficulty, aliases)
WORD_POOL: list[tuple[str, str, int, list[str]]] = [
    # --- Corporate Strategy -------------------------------------------------
    ("synergy", "Corporate Strategy", 1, ["synergistic"]),
    ("leverage", "Corporate Strategy", 1, []),
    ("alignment", "Corporate Strategy", 1, ["aligned", "align"]),
    ("stakeholder", "Corporate Strategy", 1, []),
    ("value add", "Corporate Strategy", 2, ["value-add", "add value"]),
    ("core competency", "Corporate Strategy", 2, ["core competencies"]),
    ("paradigm shift", "Corporate Strategy", 2, []),
    ("north star", "Corporate Strategy", 2, ["north star metric"]),
    ("strategic imperative", "Corporate Strategy", 3, []),
    ("operating model", "Corporate Strategy", 2, []),
    ("holistic", "Corporate Strategy", 2, []),
    ("best practice", "Corporate Strategy", 1, ["best practices"]),
    ("competitive advantage", "Corporate Strategy", 2, []),
    ("mission critical", "Corporate Strategy", 2, ["mission-critical"]),
    ("boil the ocean", "Corporate Strategy", 3, []),
    ("move the needle", "Corporate Strategy", 2, []),
    ("low hanging fruit", "Corporate Strategy", 1, ["low-hanging fruit"]),
    ("force multiplier", "Corporate Strategy", 3, []),
    ("center of excellence", "Corporate Strategy", 3, ["centre of excellence", "coe"]),
    ("digital transformation", "Corporate Strategy", 1, []),
    # --- Agile & Delivery ---------------------------------------------------
    ("sprint", "Agile & Delivery", 1, []),
    ("backlog", "Agile & Delivery", 1, []),
    ("blocker", "Agile & Delivery", 1, ["blocked", "blockers"]),
    ("velocity", "Agile & Delivery", 2, []),
    ("standup", "Agile & Delivery", 1, ["stand-up", "stand up"]),
    ("retro", "Agile & Delivery", 2, ["retrospective"]),
    ("story points", "Agile & Delivery", 2, ["story point"]),
    ("definition of done", "Agile & Delivery", 3, []),
    ("iterate", "Agile & Delivery", 1, ["iteration", "iterative"]),
    ("agile", "Agile & Delivery", 1, ["agility"]),
    ("scope creep", "Agile & Delivery", 2, []),
    ("technical debt", "Agile & Delivery", 1, ["tech debt"]),
    ("roadmap", "Agile & Delivery", 1, []),
    ("MVP", "Agile & Delivery", 1, ["minimum viable product"]),
    ("ship it", "Agile & Delivery", 2, []),
    ("deliverable", "Agile & Delivery", 1, ["deliverables"]),
    ("milestone", "Agile & Delivery", 1, []),
    ("capacity", "Agile & Delivery", 2, []),
    ("swim lane", "Agile & Delivery", 3, ["swimlane"]),
    ("cross functional", "Agile & Delivery", 2, ["cross-functional"]),
    # --- Tech & AI ----------------------------------------------------------
    ("AI", "Tech & AI", 1, ["artificial intelligence"]),
    ("machine learning", "Tech & AI", 1, ["ML"]),
    ("large language model", "Tech & AI", 2, ["LLM", "LLMs"]),
    ("cloud native", "Tech & AI", 2, ["cloud-native"]),
    ("scalable", "Tech & AI", 1, ["scalability", "scale"]),
    ("microservices", "Tech & AI", 2, ["microservice"]),
    ("API", "Tech & AI", 1, ["APIs"]),
    ("platform", "Tech & AI", 1, []),
    ("automation", "Tech & AI", 1, ["automate", "automated"]),
    ("observability", "Tech & AI", 3, []),
    ("single source of truth", "Tech & AI", 2, []),
    ("data driven", "Tech & AI", 1, ["data-driven"]),
    ("real time", "Tech & AI", 1, ["real-time", "realtime"]),
    ("edge case", "Tech & AI", 2, ["edge cases"]),
    ("tech stack", "Tech & AI", 2, ["technology stack"]),
    ("legacy system", "Tech & AI", 2, ["legacy systems"]),
    ("bleeding edge", "Tech & AI", 3, []),
    ("digital native", "Tech & AI", 3, []),
    ("copilot", "Tech & AI", 2, []),
    ("agentic", "Tech & AI", 2, ["agent", "agents"]),
    # --- Sales & Growth -----------------------------------------------------
    ("pipeline", "Sales & Growth", 1, []),
    ("touch base", "Sales & Growth", 1, ["touching base"]),
    ("customer journey", "Sales & Growth", 2, []),
    ("value proposition", "Sales & Growth", 1, ["value prop"]),
    ("growth hacking", "Sales & Growth", 3, ["growth hack"]),
    ("conversion", "Sales & Growth", 2, ["convert"]),
    ("churn", "Sales & Growth", 2, []),
    ("net new", "Sales & Growth", 2, []),
    ("upsell", "Sales & Growth", 2, ["up-sell"]),
    ("land and expand", "Sales & Growth", 3, []),
    ("market fit", "Sales & Growth", 2, ["product market fit", "product-market fit"]),
    ("thought leadership", "Sales & Growth", 2, ["thought leader"]),
    ("brand equity", "Sales & Growth", 3, []),
    ("customer centric", "Sales & Growth", 2, ["customer-centric"]),
    ("voice of the customer", "Sales & Growth", 3, ["voice of customer", "VOC"]),
    # --- Meeting Filler -----------------------------------------------------
    ("circle back", "Meeting Filler", 1, ["circling back"]),
    ("deep dive", "Meeting Filler", 1, ["deep-dive"]),
    ("take it offline", "Meeting Filler", 1, ["offline"]),
    ("bandwidth", "Meeting Filler", 1, []),
    ("ping me", "Meeting Filler", 1, ["ping"]),
    ("double click", "Meeting Filler", 2, ["double-click"]),
    ("parking lot", "Meeting Filler", 2, ["park that"]),
    ("action item", "Meeting Filler", 1, ["action items", "actionable"]),
    ("quick win", "Meeting Filler", 1, ["quick wins"]),
    ("at the end of the day", "Meeting Filler", 1, []),
    ("to be honest", "Meeting Filler", 1, ["to be fair", "honestly"]),
    ("let's unpack that", "Meeting Filler", 2, ["unpack"]),
    ("hard stop", "Meeting Filler", 1, []),
    ("sync up", "Meeting Filler", 1, ["sync", "syncing"]),
    ("loop in", "Meeting Filler", 1, ["looping in"]),
    ("wheelhouse", "Meeting Filler", 2, []),
    ("full send", "Meeting Filler", 3, []),
    ("open the kimono", "Meeting Filler", 3, []),
    ("drink from the firehose", "Meeting Filler", 3, []),
    ("herding cats", "Meeting Filler", 3, []),
    ("run it up the flagpole", "Meeting Filler", 3, []),
    ("peel the onion", "Meeting Filler", 3, []),
    ("eat our own dog food", "Meeting Filler", 3, ["dogfood", "dogfooding"]),
    ("throw it over the wall", "Meeting Filler", 3, []),
    ("baked in", "Meeting Filler", 2, ["baked-in"]),
    # --- Finance & Ops ------------------------------------------------------
    ("ROI", "Finance & Ops", 1, ["return on investment"]),
    ("run rate", "Finance & Ops", 2, []),
    ("burn rate", "Finance & Ops", 2, []),
    ("headcount", "Finance & Ops", 1, []),
    ("cost center", "Finance & Ops", 2, ["cost centre"]),
    ("top line", "Finance & Ops", 2, ["topline"]),
    ("bottom line", "Finance & Ops", 1, []),
    ("KPI", "Finance & Ops", 1, ["KPIs", "key performance indicator"]),
    ("OKR", "Finance & Ops", 1, ["OKRs", "objectives and key results"]),
    ("efficiency", "Finance & Ops", 1, ["efficient", "efficiencies"]),
    ("optimize", "Finance & Ops", 1, ["optimise", "optimization", "optimisation"]),
    ("rightsizing", "Finance & Ops", 3, ["rightsize", "right-size"]),
    ("fiscal year", "Finance & Ops", 2, ["FY"]),
    ("budget cycle", "Finance & Ops", 2, []),
    ("procurement", "Finance & Ops", 2, []),
    # --- Consulting-Speak ---------------------------------------------------
    ("disrupt", "Consulting-Speak", 1, ["disruption", "disruptive", "disruptor"]),
    ("innovate", "Consulting-Speak", 1, ["innovation", "innovative"]),
    ("ecosystem", "Consulting-Speak", 1, []),
    ("ideate", "Consulting-Speak", 2, ["ideation"]),
    ("pivot", "Consulting-Speak", 1, []),
    ("game changer", "Consulting-Speak", 1, ["game-changer", "game changing"]),
    ("best in class", "Consulting-Speak", 2, ["best-in-class"]),
    ("frictionless", "Consulting-Speak", 2, []),
    ("seamless", "Consulting-Speak", 1, ["seamlessly"]),
    ("robust", "Consulting-Speak", 1, []),
    ("granular", "Consulting-Speak", 2, ["granularity"]),
    ("operationalize", "Consulting-Speak", 3, ["operationalise", "operational"]),
    ("bespoke", "Consulting-Speak", 2, []),
    ("turnkey", "Consulting-Speak", 2, []),
    ("white glove", "Consulting-Speak", 3, ["white-glove"]),
    ("blue sky thinking", "Consulting-Speak", 3, ["blue-sky"]),
    ("net net", "Consulting-Speak", 3, ["net-net"]),
    ("table stakes", "Consulting-Speak", 2, []),
    ("secret sauce", "Consulting-Speak", 2, []),
    ("moving forward", "Consulting-Speak", 1, ["going forward"]),
]


def seed_words() -> int:
    """Insert any missing words from the starter pool. Returns the number added."""
    added = 0
    now = utcnow()
    for text, category, difficulty, aliases in WORD_POOL:
        key = exact_key(text)
        if not key or query_one("SELECT id FROM words WHERE text_key = ?", (key,)):
            continue
        execute(
            """
            INSERT INTO words (id, text, text_key, category, difficulty, aliases, strict_match,
                               active, created_at, created_by, source)
            VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, 'seed', 'seed')
            """,
            (new_id(), text, key, category, difficulty, json.dumps(aliases), now),
        )
        added += 1
    if added:
        invalidate_all_indexes()
    return added


def seed_api_key() -> str | None:
    """Create a first ingest key if none exists. Returns the plaintext key, once."""
    if query_one("SELECT id FROM api_keys WHERE active = 1"):
        return None

    full, prefix, key_hash = generate_api_key()
    execute(
        """
        INSERT INTO api_keys (id, name, prefix, key_hash, active, created_at, created_by)
        VALUES (?, ?, ?, ?, 1, ?, 'seed')
        """,
        (new_id(), "Default ingest key", prefix, key_hash, utcnow()),
    )
    return full


def seed_demo_game() -> str | None:
    """Create a demo game in the lobby if the instance has none."""
    if query_one("SELECT id FROM games LIMIT 1"):
        return None
    game_id = new_id()
    execute(
        """
        INSERT INTO games (id, name, code, status, card_size, free_space, created_at,
                           created_by, description)
        VALUES (?, ?, ?, 'lobby', 5, 1, ?, 'seed', ?)
        """,
        (
            game_id,
            "Q3 All-Hands",
            "DEMO1",
            utcnow(),
            "The quarterly alignment session that could have been an email.",
        ),
    )
    return game_id


def run_seed() -> dict:
    """Full idempotent seed. Safe to call on every boot.

    Note there is no admin account to create — administrators authenticate with a PIN,
    and players are created when they join a game.
    """
    words_added = seed_words()
    api_key = seed_api_key()
    game_id = seed_demo_game()

    if words_added or api_key or game_id:
        record_audit(
            "system.seeded",
            entity="system",
            detail=f"words+{words_added} game={bool(game_id)}",
        )

    return {
        "words_added": words_added,
        "api_key": api_key,
        "demo_game_id": game_id,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = run_seed()
    logger.info("Seed complete: +%d words", result["words_added"])
    if result["api_key"]:
        logger.info("Ingest API key (shown once): %s", result["api_key"])
