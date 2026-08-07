"""End-to-end API tests covering the full game lifecycle."""

from fastapi.testclient import TestClient

from .conftest import make_player


def _create_game(client: TestClient, admin_headers: dict, name: str = "Test Game") -> dict:
    response = client.post("/api/games", json={"name": name}, headers=admin_headers)
    assert response.status_code == 201, response.text
    return response.json()


def _build_card(client: TestClient, game_id: str, player: dict, word_ids=None) -> dict:
    response = client.post(
        f"/api/games/{game_id}/card",
        json={"word_ids": word_ids or []},
        headers=player["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


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

    def test_demo_game_exists(self, client: TestClient, admin_headers: dict):
        games = client.get("/api/games", headers=admin_headers).json()
        assert any(g["code"] == "DEMO1" for g in games)


class TestAuth:
    def test_signup_returns_a_session(self, client: TestClient):
        player = make_player(client, "Dwight")
        assert player["user"]["nickname"] == "Dwight"
        assert player["user"]["is_admin"] is False
        assert player["token"]

    def test_duplicate_nickname_is_rejected(self, client: TestClient):
        make_player(client, "Michael")
        response = client.post("/api/auth/signup", json={"nickname": "michael"})
        assert response.status_code == 409

    def test_nickname_needs_alphanumerics(self, client: TestClient):
        assert client.post("/api/auth/signup", json={"nickname": "!!!"}).status_code == 422

    def test_dropdown_lists_accounts(self, client: TestClient):
        make_player(client, "Pam")
        nicknames = [u["nickname"] for u in client.get("/api/auth/users").json()]
        assert "Pam" in nicknames

    def test_signin_without_password(self, client: TestClient):
        player = make_player(client, "Jim")
        response = client.post("/api/auth/signin", json={"user_id": player["user"]["id"]})
        assert response.status_code == 200
        assert response.json()["user"]["nickname"] == "Jim"

    def test_me_round_trips(self, client: TestClient):
        player = make_player(client, "Stanley")
        assert client.get("/api/auth/me", headers=player["headers"]).json()["nickname"] == "Stanley"

    def test_forged_token_is_rejected(self, client: TestClient):
        headers = {"Authorization": "Bearer YWRtaW4.not-a-real-signature"}
        assert client.get("/api/auth/me", headers=headers).status_code == 401

    def test_bootstrap_admin_is_flagged(self, client: TestClient):
        admins = [u for u in client.get("/api/auth/users").json() if u["is_admin"]]
        assert len(admins) >= 1


class TestAuthorization:
    def test_word_pool_requires_sign_in(self, client: TestClient):
        assert client.get("/api/words").status_code == 401

    def test_players_cannot_add_words(self, client: TestClient):
        player = make_player(client, "Kevin")
        response = client.post("/api/words", json={"text": "cheese"}, headers=player["headers"])
        assert response.status_code == 403

    def test_players_cannot_create_games(self, client: TestClient):
        player = make_player(client, "Oscar")
        response = client.post("/api/games", json={"name": "Nope"}, headers=player["headers"])
        assert response.status_code == 403

    def test_players_cannot_read_admin_stats(self, client: TestClient):
        player = make_player(client, "Angela")
        assert client.get("/api/admin/stats", headers=player["headers"]).status_code == 403

    def test_players_cannot_see_every_card(self, client: TestClient, admin_headers: dict):
        game = _create_game(client, admin_headers)
        player = make_player(client, "Toby")
        _build_card(client, game["id"], player)
        response = client.get(f"/api/games/{game['id']}/cards", headers=player["headers"])
        assert response.status_code == 403

    def test_players_cannot_read_another_players_card(
        self, client: TestClient, admin_headers: dict
    ):
        game = _create_game(client, admin_headers)
        alice = make_player(client, "Alice")
        bob = make_player(client, "Bob")
        alice_card = _build_card(client, game["id"], alice)
        _build_card(client, game["id"], bob)
        response = client.get(
            f"/api/games/{game['id']}/cards/{alice_card['id']}", headers=bob["headers"]
        )
        assert response.status_code == 403


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
        created = response.json()
        assert len(created) == 2
        assert all(w["category"] == "Imported" for w in created)

    def test_bulk_import_honours_inline_category(self, client: TestClient, admin_headers: dict):
        response = client.post(
            "/api/words/bulk",
            json={"payload": "Buzzwords: quantum leap", "category": "General"},
            headers=admin_headers,
        )
        assert response.json()[0]["category"] == "Buzzwords"

    def test_update_word(self, client: TestClient, admin_headers: dict):
        word = client.post("/api/words", json={"text": "hypercare"}, headers=admin_headers).json()
        response = client.patch(
            f"/api/words/{word['id']}",
            json={"category": "Consulting-Speak", "difficulty": 3},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["category"] == "Consulting-Speak"

    def test_unused_word_is_deleted_outright(self, client: TestClient, admin_headers: dict):
        word = client.post(
            "/api/words", json={"text": "ideation station"}, headers=admin_headers
        ).json()
        assert client.delete(f"/api/words/{word['id']}", headers=admin_headers).status_code == 204
        remaining = client.get("/api/words", headers=admin_headers).json()
        assert not any(w["id"] == word["id"] for w in remaining)

    def test_word_in_play_is_deactivated_not_deleted(self, client: TestClient, admin_headers: dict):
        game = _create_game(client, admin_headers)
        player = make_player(client, "Ryan")
        card = _build_card(client, game["id"], player)
        word_id = next(c["word_id"] for c in card["cells"] if c["word_id"])

        assert client.delete(f"/api/words/{word_id}", headers=admin_headers).status_code == 204
        all_words = client.get(
            "/api/words", params={"include_inactive": True}, headers=admin_headers
        ).json()
        target = next(w for w in all_words if w["id"] == word_id)
        assert target["active"] is False

    def test_inspect_explains_a_match(self, client: TestClient, admin_headers: dict):
        response = client.get(
            "/api/words/inspect",
            params={"phrase": "leveraging", "against": "leverage"},
            headers=admin_headers,
        )
        assert response.json()["against"]["matches"] is True


class TestCardBuilding:
    def test_auto_filled_card_has_the_right_shape(self, client: TestClient, admin_headers: dict):
        game = _create_game(client, admin_headers)
        player = make_player(client, "Creed")
        card = _build_card(client, game["id"], player)

        assert len(card["cells"]) == 25
        free = [c for c in card["cells"] if c["is_free"]]
        assert len(free) == 1
        assert free[0]["position"] == 12
        assert free[0]["marked"] is True
        assert card["marked_count"] == 1

    def test_card_words_are_unique(self, client: TestClient, admin_headers: dict):
        game = _create_game(client, admin_headers)
        player = make_player(client, "Meredith")
        card = _build_card(client, game["id"], player)
        word_ids = [c["word_id"] for c in card["cells"] if c["word_id"]]
        assert len(word_ids) == len(set(word_ids)) == 24

    def test_hand_picked_words_are_all_placed(self, client: TestClient, admin_headers: dict):
        game = _create_game(client, admin_headers)
        player = make_player(client, "Phyllis")
        pool = client.get("/api/words", headers=player["headers"]).json()
        chosen = [w["id"] for w in pool[:10]]

        card = _build_card(client, game["id"], player, chosen)
        placed = {c["word_id"] for c in card["cells"] if c["word_id"]}
        assert set(chosen).issubset(placed), "every drafted word must appear on the card"

    def test_rebuilding_replaces_the_previous_card(self, client: TestClient, admin_headers: dict):
        game = _create_game(client, admin_headers)
        player = make_player(client, "Darryl")
        first = _build_card(client, game["id"], player)
        second = _build_card(client, game["id"], player)
        assert first["id"] != second["id"]

        cards = client.get(f"/api/games/{game['id']}/cards", headers=admin_headers).json()
        assert len(cards) == 1, "a player must never end up with two cards in one game"

    def test_cards_lock_once_the_game_is_live(self, client: TestClient, admin_headers: dict):
        game = _create_game(client, admin_headers)
        player = make_player(client, "Kelly")
        _build_card(client, game["id"], player)
        client.patch(
            f"/api/games/{game['id']}/status", json={"status": "live"}, headers=admin_headers
        )
        response = client.post(
            f"/api/games/{game['id']}/card", json={"word_ids": []}, headers=player["headers"]
        )
        assert response.status_code == 409

    def test_card_is_private_to_its_owner(self, client: TestClient, admin_headers: dict):
        game = _create_game(client, admin_headers)
        player = make_player(client, "Erin")
        _build_card(client, game["id"], player)
        response = client.get(f"/api/games/{game['id']}/card", headers=player["headers"])
        assert response.status_code == 200


class TestGameLifecycle:
    def test_join_code_is_readable(self, client: TestClient, admin_headers: dict):
        game = _create_game(client, admin_headers)
        assert len(game["code"]) == 5
        assert not set(game["code"]) & set("IO01"), "ambiguous characters must be excluded"

    def test_even_card_sizes_are_rejected(self, client: TestClient, admin_headers: dict):
        response = client.post(
            "/api/games", json={"name": "Even", "card_size": 4}, headers=admin_headers
        )
        assert response.status_code == 422

    def test_status_transitions(self, client: TestClient, admin_headers: dict):
        game = _create_game(client, admin_headers)
        for status in ("live", "paused", "ended"):
            response = client.patch(
                f"/api/games/{game['id']}/status", json={"status": status}, headers=admin_headers
            )
            assert response.status_code == 200
            assert response.json()["status"] == status

    def test_game_can_be_looked_up_by_code(self, client: TestClient, admin_headers: dict):
        game = _create_game(client, admin_headers)
        found = client.get(f"/api/games/{game['code']}", headers=admin_headers).json()
        assert found["id"] == game["id"]


class TestIngestAndScoring:
    def _live_game_with_player(self, client: TestClient, admin_headers: dict, nickname: str):
        game = _create_game(client, admin_headers)
        player = make_player(client, nickname)
        card = _build_card(client, game["id"], player)
        client.patch(
            f"/api/games/{game['id']}/status", json={"status": "live"}, headers=admin_headers
        )
        return game, player, card

    def test_spoken_word_marks_the_square(self, client: TestClient, admin_headers: dict):
        game, player, card = self._live_game_with_player(client, admin_headers, "Holly")
        target = next(c for c in card["cells"] if not c["is_free"])

        response = client.post("/api/ingest", json={"text": target["text"], "game_id": game["id"]})
        assert response.status_code == 200
        assert len(response.json()["results"][0]["hits"]) >= 1

        refreshed = client.get(f"/api/games/{game['id']}/card", headers=player["headers"]).json()
        marked = next(c for c in refreshed["cells"] if c["position"] == target["position"])
        assert marked["marked"] is True

    def test_inflected_speech_still_marks(self, client: TestClient, admin_headers: dict):
        """The headline requirement: -s / -ed / -ly forms count."""
        game = _create_game(client, admin_headers)
        player = make_player(client, "Nellie")
        pool = client.get("/api/words", headers=player["headers"]).json()
        synergy = next(w for w in pool if w["text"] == "synergy")
        _build_card(client, game["id"], player, [synergy["id"]])
        client.patch(
            f"/api/games/{game['id']}/status", json={"status": "live"}, headers=admin_headers
        )

        response = client.post(
            "/api/ingest", json={"text": "we need more synergies here", "game_id": game["id"]}
        )
        hits = response.json()["results"][0]["hits"]
        assert any(h["word"] == "synergy" for h in hits), "'synergies' must mark 'synergy'"

    def test_multi_word_phrase_across_separate_calls(self, client: TestClient, admin_headers: dict):
        game = _create_game(client, admin_headers)
        player = make_player(client, "Gabe")
        pool = client.get("/api/words", headers=player["headers"]).json()
        phrase = next(w for w in pool if w["text"] == "low hanging fruit")
        _build_card(client, game["id"], player, [phrase["id"]])
        client.patch(
            f"/api/games/{game['id']}/status", json={"status": "live"}, headers=admin_headers
        )

        # Stream it one word at a time, exactly as a live captioner would.
        for word in ("some", "low", "hanging"):
            assert client.post(
                "/api/ingest", json={"text": word, "game_id": game["id"]}
            ).json()["results"][0]["hits"] == []

        final = client.post("/api/ingest", json={"text": "fruit", "game_id": game["id"]})
        assert any(h["word"] == "low hanging fruit" for h in final.json()["results"][0]["hits"])

    def test_repeated_word_does_not_double_mark(self, client: TestClient, admin_headers: dict):
        game, player, card = self._live_game_with_player(client, admin_headers, "Andy")
        target = next(c for c in card["cells"] if not c["is_free"])

        client.post("/api/ingest", json={"text": target["text"], "game_id": game["id"]})
        second = client.post("/api/ingest", json={"text": target["text"], "game_id": game["id"]})
        assert second.json()["results"][0]["hits"] == []

    def test_ingest_rejects_a_game_that_is_not_live(self, client: TestClient, admin_headers: dict):
        game = _create_game(client, admin_headers)
        response = client.post("/api/ingest", json={"text": "synergy", "game_id": game["id"]})
        assert response.status_code == 409

    def test_broadcast_mode_targets_every_live_game(self, client: TestClient, admin_headers: dict):
        game_a, player_a, card_a = self._live_game_with_player(client, admin_headers, "Roy")
        game_b = _create_game(client, admin_headers, "Second Room")
        player_b = make_player(client, "Val")
        _build_card(client, game_b["id"], player_b)
        client.patch(
            f"/api/games/{game_b['id']}/status", json={"status": "live"}, headers=admin_headers
        )

        response = client.post("/api/ingest", json={"text": "synergy leverage pipeline"})
        assert len(response.json()["results"]) == 2

    def test_bingo_is_detected_and_ranked(self, client: TestClient, admin_headers: dict):
        game, player, card = self._live_game_with_player(client, admin_headers, "Jan")

        row = [c for c in card["cells"] if c["position"] < 5]
        awarded = None
        for cell in row:
            response = client.post(
                "/api/ingest", json={"text": cell["text"], "game_id": game["id"]}
            )
            bingos = response.json()["results"][0]["bingos"]
            if bingos:
                awarded = bingos[0]

        assert awarded is not None, "completing a full row must award a bingo"
        assert awarded["pattern"] == "row-0"
        assert awarded["rank"] == 1
        assert awarded["nickname"] == "Jan"

    def test_leaderboard_orders_by_first_bingo(self, client: TestClient, admin_headers: dict):
        game = _create_game(client, admin_headers)
        fast = make_player(client, "Speedy")
        slow = make_player(client, "Steady")

        pool = client.get("/api/words", headers=fast["headers"]).json()
        picks = [w["id"] for w in pool[:24]]
        fast_card = _build_card(client, game["id"], fast, picks)
        _build_card(client, game["id"], slow)
        client.patch(
            f"/api/games/{game['id']}/status", json={"status": "live"}, headers=admin_headers
        )

        for cell in [c for c in fast_card["cells"] if c["position"] < 5]:
            client.post("/api/ingest", json={"text": cell["text"], "game_id": game["id"]})

        board = client.get(
            f"/api/games/{game['id']}/leaderboard", headers=fast["headers"]
        ).json()
        assert board[0]["nickname"] == "Speedy"
        assert board[0]["position"] == 1
        assert board[0]["lines"] >= 1
        assert board[1]["nickname"] == "Steady"

    def test_transcript_history_is_recorded(self, client: TestClient, admin_headers: dict):
        game, player, _ = self._live_game_with_player(client, admin_headers, "Clark")
        client.post("/api/ingest", json={"text": "let us circle back", "game_id": game["id"]})
        transcript = client.get(
            f"/api/games/{game['id']}/transcript", headers=player["headers"]
        ).json()
        assert [t["raw"] for t in transcript] == ["let", "us", "circle", "back"]
        assert [t["seq"] for t in transcript] == [1, 2, 3, 4]

    def test_reset_clears_marks_and_wins(self, client: TestClient, admin_headers: dict):
        game, player, card = self._live_game_with_player(client, admin_headers, "Pete")
        for cell in [c for c in card["cells"] if c["position"] < 5]:
            client.post("/api/ingest", json={"text": cell["text"], "game_id": game["id"]})

        client.post(f"/api/games/{game['id']}/reset", headers=admin_headers)
        refreshed = client.get(f"/api/games/{game['id']}/card", headers=player["headers"]).json()
        assert refreshed["marked_count"] == 1, "only the free space survives a reset"
        assert refreshed["lines"] == []
        assert client.get(
            f"/api/games/{game['id']}/transcript", headers=player["headers"]
        ).json() == []


class TestAdminConsole:
    def test_stats(self, client: TestClient, admin_headers: dict):
        stats = client.get("/api/admin/stats", headers=admin_headers).json()
        assert stats["words"] > 100
        assert stats["admins"] >= 1
        assert stats["environment"] == "test"

    def test_admin_sees_every_card(self, client: TestClient, admin_headers: dict):
        game = _create_game(client, admin_headers)
        for name in ("P1", "P2", "P3"):
            _build_card(client, game["id"], make_player(client, name))
        cards = client.get(f"/api/games/{game['id']}/cards", headers=admin_headers).json()
        assert len(cards) == 3
        assert {c["nickname"] for c in cards} == {"P1", "P2", "P3"}

    def test_promote_and_demote(self, client: TestClient, admin_headers: dict):
        player = make_player(client, "Promotable")
        user_id = player["user"]["id"]
        promoted = client.patch(
            f"/api/admin/users/{user_id}", json={"is_admin": True}, headers=admin_headers
        )
        assert promoted.json()["is_admin"] is True

        demoted = client.patch(
            f"/api/admin/users/{user_id}", json={"is_admin": False}, headers=admin_headers
        )
        assert demoted.json()["is_admin"] is False

    def test_cannot_demote_the_last_admin(self, client: TestClient, admin_headers: dict):
        everyone = client.get("/api/admin/users", headers=admin_headers).json()
        admins = [u for u in everyone if u["is_admin"]]
        assert len(admins) == 1
        response = client.patch(
            f"/api/admin/users/{admins[0]['id']}", json={"is_admin": False}, headers=admin_headers
        )
        assert response.status_code == 409

    def test_api_key_lifecycle(self, client: TestClient, admin_headers: dict):
        created = client.post(
            "/api/admin/keys", json={"name": "Zoom bridge"}, headers=admin_headers
        ).json()
        assert created["key"].startswith("bb_")

        listed = client.get("/api/admin/keys", headers=admin_headers).json()
        assert all("key" not in k for k in listed), "full keys must never be listed"

        revoke = client.delete(f"/api/admin/keys/{created['id']}", headers=admin_headers)
        assert revoke.status_code == 204
        revoked = next(
            k for k in client.get("/api/admin/keys", headers=admin_headers).json()
            if k["id"] == created["id"]
        )
        assert revoked["active"] is False

    def test_audit_trail_records_mutations(self, client: TestClient, admin_headers: dict):
        client.post("/api/words", json={"text": "auditable moment"}, headers=admin_headers)
        entries = client.get("/api/admin/audit", headers=admin_headers).json()
        actions = [e["action"] for e in entries]
        assert "word.created" in actions
