"""Replay a synthetic meeting into a live game, word by word.

Useful for demos and load-sanity checks without wiring up a real transcription vendor.

    python -m app.scripts.simulate --game DEMO1 --wpm 160
    python -m app.scripts.simulate --game DEMO1 --api-key bb_xxx --url http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request

SPEAKERS = ["Priya (VP Strategy)", "Marcus (Eng)", "Dana (PMM)", "Chen (Ops)"]

SCRIPT_LINES = [
    "Thanks everyone for joining, I know we all have a hard stop at the top of the hour.",
    "Let me quickly level set on where we are with the digital transformation roadmap.",
    "At the end of the day this is about unlocking synergies across the ecosystem.",
    "We leveraged the new platform to drive real time observability into the pipeline.",
    "There is a lot of low hanging fruit here, honestly some genuine quick wins.",
    "I want to double click on the customer journey before we move on.",
    "Can we take that offline? I do not want to boil the ocean in this forum.",
    "Our north star is a frictionless, best in class experience that actually moves the needle.",
    "The team has been iterating rapidly and velocity is trending in the right direction.",
    "We are seeing some technical debt, but nothing mission critical at this stage.",
    "Strategically, this is table stakes for anyone operating at scale.",
    "Let me circle back with the stakeholders and run it up the flagpole.",
    "From an ROI perspective the run rate is holding, headcount is flat.",
    "We need to operationalize this and bake it into the operating model.",
    "The agentic AI copilot work is genuinely disruptive, a real game changer.",
    "I will ping the group, loop in procurement, and we can sync up next sprint.",
    "Blockers? The legacy system integration is still a scope creep risk.",
    "Net net, we are aligned. Best practices say we ship it and iterate.",
    "Let me unpack that a little because there is real thought leadership here.",
    "Great, action items are captured, I will take the parking lot items away.",
]


def post(url: str, payload: dict, api_key: str | None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if api_key:
        request.add_header("X-API-Key", api_key)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot reach {url}: {exc.reason}. Is the server running?") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream a fake meeting into Buzzword Bingo.")
    parser.add_argument("--url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--game", default=None, help="Game code or id (default: all live games)")
    parser.add_argument("--api-key", default=None, help="Ingest API key")
    parser.add_argument("--wpm", type=int, default=150, help="Speaking pace in words per minute")
    parser.add_argument("--loops", type=int, default=1, help="How many times to replay the script")
    parser.add_argument("--shuffle", action="store_true", help="Randomise line order")
    args = parser.parse_args()

    endpoint = f"{args.url.rstrip('/')}/api/ingest"
    delay = 60.0 / max(args.wpm, 1)
    total_words = 0
    total_hits = 0
    total_bingos = 0

    print(f"→ streaming to {endpoint} at ~{args.wpm} wpm")
    if args.game:
        print(f"  target game: {args.game}")
    else:
        print("  target: every live game")

    for loop in range(args.loops):
        lines = list(SCRIPT_LINES)
        if args.shuffle:
            random.shuffle(lines)

        for line in lines:
            speaker = random.choice(SPEAKERS)
            for word in line.split():
                payload: dict = {"text": word, "speaker": speaker, "source": "simulator"}
                if args.game:
                    if len(args.game) <= 6 and args.game.isalnum():
                        payload["game_code"] = args.game.upper()
                    else:
                        payload["game_id"] = args.game

                result = post(endpoint, payload, args.api_key)
                total_words += result.get("token_count", 0)

                for game_result in result.get("results", []):
                    for hit in game_result.get("hits", []):
                        total_hits += 1
                        print(f"  ✓ {hit['nickname']:<14} {hit['word']}")
                    for bingo in game_result.get("bingos", []):
                        total_bingos += 1
                        print(f"  ★ BINGO #{bingo['rank']}  {bingo['nickname']} — {bingo['label']}")

                time.sleep(delay)

        if args.loops > 1:
            print(f"— loop {loop + 1}/{args.loops} complete")

    print(f"\ndone: {total_words} words, {total_hits} squares marked, {total_bingos} bingos")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
