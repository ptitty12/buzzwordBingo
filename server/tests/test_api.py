"""End-to-end API tests covering the full game lifecycle."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import _resolve_secret_key

from .conftest import ADMIN_PIN, build_card, create_game, join


class TestHealthAndSeed:
    def test_health(self, client: TestClient):
        body = client.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["words"] > 100, "the starter pool should be seeded"

    def test_seed_is_idempotent(self, client: TestClient):
        from app.seed import run_seed

        first = client.get("/api/health").json()["words"]
        run_seed()
        assert client.get("/api/health").json()["words"] == first

    def test_lobby_is_public(self, client: TestClient):
        """A player must be able to see games before they have any credential."""
        response = client.get("/api/games")
        assert response.status_code == 200
        assert any(g["code"] == "DEMO1" for g in response.json())


class TestAdminAuth:
    def test_correct_pin_returns_a_token(self, client: TestClient):
        response = client.post("/api/auth/admin", json={"pin": ADMIN_PIN})
        assert response.status_code == 200
        assert response.json()["token"]

    def test_wrong_pin_is_rejected(self, client: TestClient):
        assert client.post("/api/auth/admin", json={"pin": "0000"}).status_code == 401

    def test_admin_token_identifies_as_admin(self, client: TestClient, admin_headers: dict):
        body = client.get("/api/auth/me", headers=admin_headers).json()
        assert body["is_admin"] is True
        assert body["player"] is None

    def test_forged_token_is_not_admin(self, client: TestClient):
        headers = {"Authorization": "Bearer YWRtaW4.not-a-real-signature"}
        assert client.get("/api/auth/me", headers=headers).json()["is_admin"] is False

    def test_failed_attempt_is_audited(self, client: TestClient, admin_headers: dict):
        client.post("/api/auth/admin", json={"pin": "9999"})
        entries = client.get("/api/admin/audit", headers=admin_headers).json()
        assert "admin.signin_failed" in [e["action"] for e in entries]


class TestJoining:
    def test_join_needs_only_a_nickname(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        response = client.post(f"/api/games/{game['id']}/join", json={"nickname": "Dwight"})
        assert response.status_code == 201
        body = response.json()
        assert body["player"]["nickname"] == "Dwight"
        assert body["game_id"] == game["id"]
        assert body["token"]

    def test_no_signup_endpoint_exists(self, client: TestClient):
        """Accounts are gone — the old global sign-up surface must not linger.

        405 is as good as 404 here: both mean the route is not served. What must not
        happen is a 2xx, or the SPA shell being returned in place of an API response.
        """
        signup = client.post("/api/auth/signup", json={"nickname": "Ghost"})
        assert signup.status_code in (404, 405)

        listing = client.get("/api/auth/users")
        assert listing.status_code == 404
        assert "text/html" not in listing.headers.get("content-type", "")

    def test_same_nickname_in_two_games_is_fine(self, client: TestClient, admin_headers: dict):
        first = create_game(client, admin_headers, "One")
        second = create_game(client, admin_headers, "Two")
        a = join(client, first["id"], "Jim")
        b = join(client, second["id"], "Jim")
        assert a["player"]["id"] != b["player"]["id"]

    def test_rejoining_resumes_the_same_identity(self, client: TestClient, admin_headers: dict):
        """A refresh mid-meeting must not orphan the player's card."""
        game = create_game(client, admin_headers)
        first = join(client, game["id"], "Pam")
        build_card(client, game["id"], first)
        again = join(client, game["id"], "pam")  # case-insensitive
        assert again["player"]["id"] == first["player"]["id"]

        card = client.get(f"/api/games/{game['id']}/card", headers=again["headers"])
        assert card.status_code == 200

    def test_nickname_needs_alphanumerics(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        response = client.post(f"/api/games/{game['id']}/join", json={"nickname": "!!!"})
        assert response.status_code == 422

    def test_cannot_join_an_ended_game(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        client.patch(
            f"/api/games/{game['id']}/status", json={"status": "ended"}, headers=admin_headers
        )
        response = client.post(f"/api/games/{game['id']}/join", json={"nickname": "TooLate"})
        assert response.status_code == 409


class TestAuthorization:
    def test_word_pool_requires_joining(self, client: TestClient):
        assert client.get("/api/words").status_code == 401

    def test_players_cannot_create_games(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Oscar")
        response = client.post("/api/games", json={"name": "Nope"}, headers=player["headers"])
        assert response.status_code == 403

    def test_anonymous_cannot_create_games(self, client: TestClient):
        assert client.post("/api/games", json={"name": "Nope"}).status_code == 403

    def test_players_cannot_add_words_directly(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Kevin")
        response = client.post("/api/words", json={"text": "cheese"}, headers=player["headers"])
        assert response.status_code == 403

    def test_players_cannot_read_admin_stats(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Angela")
        assert client.get("/api/admin/stats", headers=player["headers"]).status_code == 403

    def test_players_cannot_see_every_card(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Toby")
        build_card(client, game["id"], player)
        response = client.get(f"/api/games/{game['id']}/cards", headers=player["headers"])
        assert response.status_code == 403

    def test_players_cannot_read_another_players_card(
        self, client: TestClient, admin_headers: dict
    ):
        game = create_game(client, admin_headers)
        alice = join(client, game["id"], "Alice")
        bob = join(client, game["id"], "Bob")
        alice_card = build_card(client, game["id"], alice)
        build_card(client, game["id"], bob)
        response = client.get(
            f"/api/games/{game['id']}/cards/{alice_card['id']}", headers=bob["headers"]
        )
        assert response.status_code == 403

    def test_a_token_is_scoped_to_one_game(self, client: TestClient, admin_headers: dict):
        """The core guarantee of per-game identity."""
        first = create_game(client, admin_headers, "First")
        second = create_game(client, admin_headers, "Second")
        player = join(client, first["id"], "Wanderer")

        response = client.post(
            f"/api/games/{second['id']}/card", json={"word_ids": []}, headers=player["headers"]
        )
        assert response.status_code == 403

    def test_players_cannot_use_the_matcher_playground(
        self, client: TestClient, admin_headers: dict
    ):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Nosy")
        response = client.get(
            "/api/words/inspect", params={"phrase": "synergy"}, headers=player["headers"]
        )
        assert response.status_code == 403


class TestApiDocsAreAdminOnly:
    def test_docs_require_the_pin(self, client: TestClient):
        assert client.get("/api/docs").status_code == 401
        assert client.get("/api/openapi.json").status_code == 401
        assert client.get("/api/redoc").status_code == 401

    def test_docs_open_with_the_pin(self, client: TestClient):
        assert client.get("/api/docs", auth=("admin", ADMIN_PIN)).status_code == 200
        assert client.get("/api/openapi.json", auth=("admin", ADMIN_PIN)).status_code == 200

    def test_wrong_pin_is_rejected(self, client: TestClient):
        assert client.get("/api/docs", auth=("admin", "0000")).status_code == 401


class TestWordAdmin:
    def test_admin_can_add_a_word(self, client: TestClient, admin_headers: dict):
        response = client.post(
            "/api/words",
            json={"text": "blamestorming", "category": "Meeting Filler", "difficulty": 3},
            headers=admin_headers,
        )
        assert response.status_code == 201
        assert response.json()["text"] == "blamestorming"

    def test_duplicate_words_are_rejected(self, client: TestClient, admin_headers: dict):
        client.post("/api/words", json={"text": "webscale"}, headers=admin_headers)
        response = client.post("/api/words", json={"text": "Webscale"}, headers=admin_headers)
        assert response.status_code == 409

    def test_bulk_import_skips_duplicates(self, client: TestClient, admin_headers: dict):
        payload = "alpha thing\nbeta thing\nsynergy"  # synergy is already seeded
        response = client.post(
            "/api/words/bulk",
            json={"payload": payload, "category": "Imported"},
            headers=admin_headers,
        )
        assert response.status_code == 201
        assert len(response.json()) == 2

    def test_word_in_play_is_deactivated_not_deleted(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Ryan")
        card = build_card(client, game["id"], player)
        word_id = next(c["word_id"] for c in card["cells"] if c["word_id"])

        assert client.delete(f"/api/words/{word_id}", headers=admin_headers).status_code == 204
        all_words = client.get(
            "/api/words", params={"include_inactive": True}, headers=admin_headers
        ).json()
        assert next(w for w in all_words if w["id"] == word_id)["active"] is False

    def test_inspect_explains_a_match(self, client: TestClient, admin_headers: dict):
        response = client.get(
            "/api/words/inspect",
            params={"phrase": "leveraging", "against": "leverage"},
            headers=admin_headers,
        )
        assert response.json()["against"]["matches"] is True


class TestCardBuilding:
    def test_auto_filled_card_has_the_right_shape(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Creed")
        card = build_card(client, game["id"], player)

        assert len(card["cells"]) == 25
        free = [c for c in card["cells"] if c["is_free"]]
        assert len(free) == 1 and free[0]["position"] == 12 and free[0]["marked"] is True
        assert card["marked_count"] == 1

    def test_card_words_are_unique(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Meredith")
        card = build_card(client, game["id"], player)
        word_ids = [c["word_id"] for c in card["cells"] if c["word_id"]]
        assert len(word_ids) == len(set(word_ids)) == 24

    def test_hand_picked_words_are_all_placed(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Phyllis")
        pool = client.get("/api/words", headers=player["headers"]).json()
        chosen = [w["id"] for w in pool[:10]]

        card = build_card(client, game["id"], player, chosen)
        placed = {c["word_id"] for c in card["cells"] if c["word_id"]}
        assert set(chosen).issubset(placed)

    def test_locking_in_a_card_is_final(self, client: TestClient, admin_headers: dict):
        """A second build is refused: redrafting after hearing the meeting is cheating."""
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Darryl")
        build_card(client, game["id"], player)

        response = client.post(
            f"/api/games/{game['id']}/card", json={"word_ids": []}, headers=player["headers"]
        )
        assert response.status_code == 409
        assert "already locked in" in response.json()["detail"]

        cards = client.get(f"/api/games/{game['id']}/cards", headers=admin_headers).json()
        assert len(cards) == 1, "a player must never end up with two cards in one game"

    def test_chosen_words_keep_the_order_they_were_arranged_in(
        self, client: TestClient, admin_headers: dict
    ):
        """The drafting preview lets players arrange squares, so order must survive."""
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Meredith")
        pool = client.get("/api/words", headers=player["headers"]).json()
        chosen = [word["id"] for word in pool[:12]]

        card = build_card(client, game["id"], player, chosen)
        playable = [cell["word_id"] for cell in card["cells"] if not cell["is_free"]]
        assert playable[: len(chosen)] == chosen

    def test_cards_lock_once_the_game_is_live(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Kelly")
        build_card(client, game["id"], player)
        client.patch(
            f"/api/games/{game['id']}/status", json={"status": "live"}, headers=admin_headers
        )
        response = client.post(
            f"/api/games/{game['id']}/card", json={"word_ids": []}, headers=player["headers"]
        )
        assert response.status_code == 409


class TestGameLifecycle:
    def test_join_code_is_readable(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        assert len(game["code"]) == 5
        assert not set(game["code"]) & set("IO01")

    def test_even_card_sizes_are_rejected(self, client: TestClient, admin_headers: dict):
        response = client.post(
            "/api/games", json={"name": "Even", "card_size": 4}, headers=admin_headers
        )
        assert response.status_code == 422

    def test_status_transitions(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        for status_value in ("live", "paused", "ended"):
            response = client.patch(
                f"/api/games/{game['id']}/status",
                json={"status": status_value},
                headers=admin_headers,
            )
            assert response.status_code == 200
            assert response.json()["status"] == status_value

    def test_game_can_be_looked_up_by_code(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        assert client.get(f"/api/games/{game['code']}").json()["id"] == game["id"]


class TestIngestAndScoring:
    def _live_game(self, client: TestClient, admin_headers: dict, nickname: str):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], nickname)
        card = build_card(client, game["id"], player)
        client.patch(
            f"/api/games/{game['id']}/status", json={"status": "live"}, headers=admin_headers
        )
        return game, player, card

    def test_spoken_word_marks_the_square(self, client: TestClient, admin_headers: dict):
        game, player, card = self._live_game(client, admin_headers, "Holly")
        target = next(c for c in card["cells"] if not c["is_free"])

        response = client.post("/api/ingest", json={"text": target["text"], "game_id": game["id"]})
        assert response.status_code == 200
        assert len(response.json()["results"][0]["hits"]) >= 1

        refreshed = client.get(f"/api/games/{game['id']}/card", headers=player["headers"]).json()
        marked = next(c for c in refreshed["cells"] if c["position"] == target["position"])
        assert marked["marked"] is True

    def test_inflected_speech_still_marks(self, client: TestClient, admin_headers: dict):
        """The headline requirement: -s / -ed / -ly forms count."""
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Nellie")
        pool = client.get("/api/words", headers=player["headers"]).json()
        synergy = next(w for w in pool if w["text"] == "synergy")
        build_card(client, game["id"], player, [synergy["id"]])
        client.patch(
            f"/api/games/{game['id']}/status", json={"status": "live"}, headers=admin_headers
        )

        response = client.post(
            "/api/ingest", json={"text": "we need more synergies here", "game_id": game["id"]}
        )
        hits = response.json()["results"][0]["hits"]
        assert any(h["word"] == "synergy" for h in hits)

    def test_multi_word_phrase_across_separate_calls(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Gabe")
        pool = client.get("/api/words", headers=player["headers"]).json()
        phrase = next(w for w in pool if w["text"] == "low hanging fruit")
        build_card(client, game["id"], player, [phrase["id"]])
        client.patch(
            f"/api/games/{game['id']}/status", json={"status": "live"}, headers=admin_headers
        )

        for word in ("some", "low", "hanging"):
            assert client.post(
                "/api/ingest", json={"text": word, "game_id": game["id"]}
            ).json()["results"][0]["hits"] == []

        final = client.post("/api/ingest", json={"text": "fruit", "game_id": game["id"]})
        assert any(h["word"] == "low hanging fruit" for h in final.json()["results"][0]["hits"])

    def test_repeated_word_does_not_double_mark(self, client: TestClient, admin_headers: dict):
        game, player, card = self._live_game(client, admin_headers, "Andy")
        target = next(c for c in card["cells"] if not c["is_free"])
        client.post("/api/ingest", json={"text": target["text"], "game_id": game["id"]})
        second = client.post("/api/ingest", json={"text": target["text"], "game_id": game["id"]})
        assert second.json()["results"][0]["hits"] == []

    def test_ingest_rejects_a_game_that_is_not_live(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        response = client.post("/api/ingest", json={"text": "synergy", "game_id": game["id"]})
        assert response.status_code == 409

    def test_bingo_is_detected_and_ranked(self, client: TestClient, admin_headers: dict):
        game, player, card = self._live_game(client, admin_headers, "Jan")

        awarded = None
        for cell in [c for c in card["cells"] if c["position"] < 5]:
            response = client.post(
                "/api/ingest", json={"text": cell["text"], "game_id": game["id"]}
            )
            bingos = response.json()["results"][0]["bingos"]
            if bingos:
                awarded = bingos[0]

        assert awarded is not None
        assert awarded["pattern"] == "row-0"
        assert awarded["rank"] == 1
        assert awarded["nickname"] == "Jan"

    def test_leaderboard_orders_by_first_bingo(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        fast = join(client, game["id"], "Speedy")
        slow = join(client, game["id"], "Steady")

        pool = client.get("/api/words", headers=fast["headers"]).json()
        fast_card = build_card(client, game["id"], fast, [w["id"] for w in pool[:24]])
        build_card(client, game["id"], slow)
        client.patch(
            f"/api/games/{game['id']}/status", json={"status": "live"}, headers=admin_headers
        )

        for cell in [c for c in fast_card["cells"] if c["position"] < 5]:
            client.post("/api/ingest", json={"text": cell["text"], "game_id": game["id"]})

        board = client.get(f"/api/games/{game['id']}/leaderboard").json()
        assert board[0]["nickname"] == "Speedy"
        assert board[0]["position"] == 1 and board[0]["lines"] >= 1
        assert board[1]["nickname"] == "Steady"

    def test_transcript_history_is_recorded(self, client: TestClient, admin_headers: dict):
        game, player, _ = self._live_game(client, admin_headers, "Clark")
        client.post("/api/ingest", json={"text": "let us circle back", "game_id": game["id"]})
        transcript = client.get(f"/api/games/{game['id']}/transcript").json()
        assert [t["raw"] for t in transcript] == ["let", "us", "circle", "back"]

    def test_reset_clears_marks_and_wins(self, client: TestClient, admin_headers: dict):
        game, player, card = self._live_game(client, admin_headers, "Pete")
        for cell in [c for c in card["cells"] if c["position"] < 5]:
            client.post("/api/ingest", json={"text": cell["text"], "game_id": game["id"]})

        client.post(f"/api/games/{game['id']}/reset", headers=admin_headers)
        refreshed = client.get(f"/api/games/{game['id']}/card", headers=player["headers"]).json()
        assert refreshed["marked_count"] == 1
        assert refreshed["lines"] == []


class TestAdminConsole:
    def test_stats(self, client: TestClient, admin_headers: dict):
        stats = client.get("/api/admin/stats", headers=admin_headers).json()
        assert stats["words"] > 100
        assert stats["environment"] == "test"
        assert stats["moderation_enabled"] is False

    def test_admin_sees_every_card(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        for name in ("P1", "P2", "P3"):
            build_card(client, game["id"], join(client, game["id"], name))
        cards = client.get(f"/api/games/{game['id']}/cards", headers=admin_headers).json()
        assert {c["nickname"] for c in cards} == {"P1", "P2", "P3"}

    def test_players_are_listed_per_game(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        join(client, game["id"], "Listed")
        players = client.get(
            "/api/admin/players", params={"game_id": game["id"]}, headers=admin_headers
        ).json()
        assert [p["nickname"] for p in players] == ["Listed"]

    def test_removing_a_player_drops_their_card(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Doomed")
        build_card(client, game["id"], player)

        response = client.delete(
            f"/api/admin/players/{player['player']['id']}", headers=admin_headers
        )
        assert response.status_code == 204
        assert client.get(f"/api/games/{game['id']}/cards", headers=admin_headers).json() == []

    def test_api_key_lifecycle(self, client: TestClient, admin_headers: dict):
        created = client.post(
            "/api/admin/keys", json={"name": "Zoom bridge"}, headers=admin_headers
        ).json()
        assert created["key"].startswith("bb_")

        listed = client.get("/api/admin/keys", headers=admin_headers).json()
        assert all("key" not in k for k in listed), "full keys must never be listed"

        revoke = client.delete(f"/api/admin/keys/{created['id']}", headers=admin_headers)
        assert revoke.status_code == 204

    def test_audit_trail_records_mutations(self, client: TestClient, admin_headers: dict):
        client.post("/api/words", json={"text": "auditable moment"}, headers=admin_headers)
        entries = client.get("/api/admin/audit", headers=admin_headers).json()
        assert "word.created" in [e["action"] for e in entries]


class TestSigningKeyPersistence:
    """A restart must not sign everybody out.

    The generated SECRET_KEY used to live only in memory, so every restart — including
    the autoreload that fires when you edit .env — invalidated every token in every
    browser. Players mid-draft got "Join a game to continue." with no explanation.
    """

    def test_generated_key_is_reused_across_processes(self, tmp_path: Path):
        database = str(tmp_path / "bingo.db")
        first = _resolve_secret_key(database)
        second = _resolve_secret_key(database)

        assert first == second, "a restart must not invalidate every issued token"
        assert (tmp_path / ".secret_key").read_text(encoding="utf-8") == first

    def test_separate_databases_get_separate_keys(self, tmp_path: Path):
        a = _resolve_secret_key(str(tmp_path / "a" / "bingo.db"))
        b = _resolve_secret_key(str(tmp_path / "b" / "bingo.db"))
        assert a != b

    def test_in_memory_database_stays_ephemeral(self, tmp_path: Path):
        """Tests and throwaway instances have nowhere to persist, and want no file."""
        assert _resolve_secret_key(":memory:") != _resolve_secret_key(":memory:")
        assert not (tmp_path / ".secret_key").exists()
