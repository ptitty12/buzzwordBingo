"""End-to-end API tests covering the full meeting lifecycle."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import _resolve_secret_key

from .conftest import ADMIN_PIN, build_grid, create_meeting, join


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

    def test_open_is_public(self, client: TestClient):
        """A participant must be able to see meetings before they have any credential."""
        response = client.get("/api/meetings")
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
        assert body["participant"] is None

    def test_forged_token_is_not_admin(self, client: TestClient):
        headers = {"Authorization": "Bearer YWRtaW4.not-a-real-signature"}
        assert client.get("/api/auth/me", headers=headers).json()["is_admin"] is False

    def test_failed_attempt_is_audited(self, client: TestClient, admin_headers: dict):
        client.post("/api/auth/admin", json={"pin": "9999"})
        entries = client.get("/api/admin/audit", headers=admin_headers).json()
        assert "admin.signin_failed" in [e["action"] for e in entries]


class TestJoining:
    def test_join_needs_only_a_nickname(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        response = client.post(f"/api/meetings/{meeting['id']}/join", json={"nickname": "Dwight"})
        assert response.status_code == 201
        body = response.json()
        assert body["participant"]["nickname"] == "Dwight"
        assert body["meeting_id"] == meeting["id"]
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

    def test_same_nickname_in_two_meetings_is_fine(self, client: TestClient, admin_headers: dict):
        first = create_meeting(client, admin_headers, "One")
        second = create_meeting(client, admin_headers, "Two")
        a = join(client, first["id"], "Jim")
        b = join(client, second["id"], "Jim")
        assert a["participant"]["id"] != b["participant"]["id"]

    def test_rejoining_resumes_the_same_identity(self, client: TestClient, admin_headers: dict):
        """A refresh mid-meeting must not orphan the participant's grid."""
        meeting = create_meeting(client, admin_headers)
        first = join(client, meeting["id"], "Pam")
        build_grid(client, meeting["id"], first)
        again = join(client, meeting["id"], "pam")  # case-insensitive
        assert again["participant"]["id"] == first["participant"]["id"]

        grid = client.get(f"/api/meetings/{meeting['id']}/grid", headers=again["headers"])
        assert grid.status_code == 200

    def test_nickname_needs_alphanumerics(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        response = client.post(f"/api/meetings/{meeting['id']}/join", json={"nickname": "!!!"})
        assert response.status_code == 422

    def test_cannot_join_an_ended_meeting(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        client.patch(
            f"/api/meetings/{meeting['id']}/status", json={"status": "ended"}, headers=admin_headers
        )
        response = client.post(f"/api/meetings/{meeting['id']}/join", json={"nickname": "TooLate"})
        assert response.status_code == 409


class TestAuthorization:
    def test_word_pool_requires_joining(self, client: TestClient):
        assert client.get("/api/words").status_code == 401

    def test_participants_cannot_create_meetings(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], "Oscar")
        response = client.post(
            "/api/meetings", json={"name": "Nope"}, headers=participant["headers"]
        )
        assert response.status_code == 403

    def test_anonymous_cannot_create_meetings(self, client: TestClient):
        assert client.post("/api/meetings", json={"name": "Nope"}).status_code == 403

    def test_participants_cannot_add_words_directly(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], "Kevin")
        response = client.post(
            "/api/words", json={"text": "cheese"}, headers=participant["headers"]
        )
        assert response.status_code == 403

    def test_participants_cannot_read_admin_stats(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], "Angela")
        assert client.get("/api/admin/stats", headers=participant["headers"]).status_code == 403

    def test_participants_cannot_see_every_grid(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], "Toby")
        build_grid(client, meeting["id"], participant)
        response = client.get(
            f"/api/meetings/{meeting['id']}/grids", headers=participant["headers"]
        )
        assert response.status_code == 403

    def test_participants_cannot_read_another_participants_grid(
        self, client: TestClient, admin_headers: dict
    ):
        meeting = create_meeting(client, admin_headers)
        alice = join(client, meeting["id"], "Alice")
        bob = join(client, meeting["id"], "Bob")
        alice_grid = build_grid(client, meeting["id"], alice)
        build_grid(client, meeting["id"], bob)
        response = client.get(
            f"/api/meetings/{meeting['id']}/grids/{alice_grid['id']}", headers=bob["headers"]
        )
        assert response.status_code == 403

    def test_a_token_is_scoped_to_one_meeting(self, client: TestClient, admin_headers: dict):
        """The core guarantee of per-meeting identity."""
        first = create_meeting(client, admin_headers, "First")
        second = create_meeting(client, admin_headers, "Second")
        participant = join(client, first["id"], "Wanderer")

        response = client.post(
            f"/api/meetings/{second['id']}/grid",
            json={"word_ids": []},
            headers=participant["headers"],
        )
        assert response.status_code == 403

    def test_participants_cannot_use_the_matcher_playground(
        self, client: TestClient, admin_headers: dict
    ):
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], "Nosy")
        response = client.get(
            "/api/words/inspect", params={"phrase": "synergy"}, headers=participant["headers"]
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
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], "Ryan")
        grid = build_grid(client, meeting["id"], participant)
        word_id = next(c["word_id"] for c in grid["cells"] if c["word_id"])

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


class TestGridBuilding:
    def test_auto_filled_grid_has_the_right_shape(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], "Creed")
        grid = build_grid(client, meeting["id"], participant)

        assert len(grid["cells"]) == 25
        free = [c for c in grid["cells"] if c["is_free"]]
        assert len(free) == 1 and free[0]["position"] == 12 and free[0]["marked"] is True
        assert grid["marked_count"] == 1

    def test_grid_words_are_unique(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], "Meredith")
        grid = build_grid(client, meeting["id"], participant)
        word_ids = [c["word_id"] for c in grid["cells"] if c["word_id"]]
        assert len(word_ids) == len(set(word_ids)) == 24

    def test_hand_picked_words_are_all_placed(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], "Phyllis")
        pool = client.get("/api/words", headers=participant["headers"]).json()
        chosen = [w["id"] for w in pool[:10]]

        grid = build_grid(client, meeting["id"], participant, chosen)
        placed = {c["word_id"] for c in grid["cells"] if c["word_id"]}
        assert set(chosen).issubset(placed)

    def test_locking_in_a_grid_is_final(self, client: TestClient, admin_headers: dict):
        """A second build is refused: redrafting after hearing the meeting is cheating."""
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], "Darryl")
        build_grid(client, meeting["id"], participant)

        response = client.post(
            f"/api/meetings/{meeting['id']}/grid",
            json={"word_ids": []},
            headers=participant["headers"],
        )
        assert response.status_code == 409
        assert "already locked in" in response.json()["detail"]

        grids = client.get(f"/api/meetings/{meeting['id']}/grids", headers=admin_headers).json()
        assert len(grids) == 1, "a participant must never end up with two grids in one meeting"

    def test_chosen_words_keep_the_order_they_were_arranged_in(
        self, client: TestClient, admin_headers: dict
    ):
        """The drafting preview lets participants arrange squares, so order must survive."""
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], "Meredith")
        pool = client.get("/api/words", headers=participant["headers"]).json()
        chosen = [word["id"] for word in pool[:12]]

        grid = build_grid(client, meeting["id"], participant, chosen)
        playable = [cell["word_id"] for cell in grid["cells"] if not cell["is_free"]]
        assert playable[: len(chosen)] == chosen

    def test_grids_lock_once_the_meeting_is_live(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], "Kelly")
        build_grid(client, meeting["id"], participant)
        client.patch(
            f"/api/meetings/{meeting['id']}/status", json={"status": "live"}, headers=admin_headers
        )
        response = client.post(
            f"/api/meetings/{meeting['id']}/grid",
            json={"word_ids": []},
            headers=participant["headers"],
        )
        assert response.status_code == 409


class TestMeetingLifecycle:
    def test_join_code_is_readable(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        assert len(meeting["code"]) == 5
        assert not set(meeting["code"]) & set("IO01")

    def test_even_grid_sizes_are_rejected(self, client: TestClient, admin_headers: dict):
        response = client.post(
            "/api/meetings", json={"name": "Even", "grid_size": 4}, headers=admin_headers
        )
        assert response.status_code == 422

    def test_status_transitions(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        for status_value in ("live", "paused", "ended"):
            response = client.patch(
                f"/api/meetings/{meeting['id']}/status",
                json={"status": status_value},
                headers=admin_headers,
            )
            assert response.status_code == 200
            assert response.json()["status"] == status_value

    def test_meeting_can_be_looked_up_by_code(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        assert client.get(f"/api/meetings/{meeting['code']}").json()["id"] == meeting["id"]


class TestIngestAndScoring:
    def _live_meeting(self, client: TestClient, admin_headers: dict, nickname: str):
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], nickname)
        grid = build_grid(client, meeting["id"], participant)
        client.patch(
            f"/api/meetings/{meeting['id']}/status", json={"status": "live"}, headers=admin_headers
        )
        return meeting, participant, grid

    def test_spoken_word_marks_the_square(self, client: TestClient, admin_headers: dict):
        meeting, participant, grid = self._live_meeting(client, admin_headers, "Holly")
        target = next(c for c in grid["cells"] if not c["is_free"])

        response = client.post(
            "/api/ingest", json={"text": target["text"], "meeting_id": meeting["id"]}
        )
        assert response.status_code == 200
        assert len(response.json()["results"][0]["hits"]) >= 1

        refreshed = client.get(
            f"/api/meetings/{meeting['id']}/grid", headers=participant["headers"]
        ).json()
        marked = next(c for c in refreshed["cells"] if c["position"] == target["position"])
        assert marked["marked"] is True

    def test_inflected_speech_still_marks(self, client: TestClient, admin_headers: dict):
        """The headline requirement: -s / -ed / -ly forms count."""
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], "Nellie")
        pool = client.get("/api/words", headers=participant["headers"]).json()
        synergy = next(w for w in pool if w["text"] == "synergy")
        build_grid(client, meeting["id"], participant, [synergy["id"]])
        client.patch(
            f"/api/meetings/{meeting['id']}/status", json={"status": "live"}, headers=admin_headers
        )

        response = client.post(
            "/api/ingest", json={"text": "we need more synergies here", "meeting_id": meeting["id"]}
        )
        hits = response.json()["results"][0]["hits"]
        assert any(h["word"] == "synergy" for h in hits)

    def test_multi_word_phrase_across_separate_calls(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], "Gabe")
        pool = client.get("/api/words", headers=participant["headers"]).json()
        phrase = next(w for w in pool if w["text"] == "low hanging fruit")
        build_grid(client, meeting["id"], participant, [phrase["id"]])
        client.patch(
            f"/api/meetings/{meeting['id']}/status", json={"status": "live"}, headers=admin_headers
        )

        for word in ("some", "low", "hanging"):
            assert (
                client.post("/api/ingest", json={"text": word, "meeting_id": meeting["id"]}).json()[
                    "results"
                ][0]["hits"]
                == []
            )

        final = client.post("/api/ingest", json={"text": "fruit", "meeting_id": meeting["id"]})
        assert any(h["word"] == "low hanging fruit" for h in final.json()["results"][0]["hits"])

    def test_repeated_word_does_not_double_mark(self, client: TestClient, admin_headers: dict):
        meeting, participant, grid = self._live_meeting(client, admin_headers, "Andy")
        target = next(c for c in grid["cells"] if not c["is_free"])
        client.post("/api/ingest", json={"text": target["text"], "meeting_id": meeting["id"]})
        second = client.post(
            "/api/ingest", json={"text": target["text"], "meeting_id": meeting["id"]}
        )
        assert second.json()["results"][0]["hits"] == []

    def test_ingest_rejects_a_meeting_that_is_not_live(
        self, client: TestClient, admin_headers: dict
    ):
        meeting = create_meeting(client, admin_headers)
        response = client.post("/api/ingest", json={"text": "synergy", "meeting_id": meeting["id"]})
        assert response.status_code == 409

    def test_completion_is_detected_and_ranked(self, client: TestClient, admin_headers: dict):
        meeting, participant, grid = self._live_meeting(client, admin_headers, "Jan")

        awarded = None
        for cell in [c for c in grid["cells"] if c["position"] < 5]:
            response = client.post(
                "/api/ingest", json={"text": cell["text"], "meeting_id": meeting["id"]}
            )
            completions = response.json()["results"][0]["completions"]
            if completions:
                awarded = completions[0]

        assert awarded is not None
        assert awarded["pattern"] == "row-0"
        assert awarded["rank"] == 1
        assert awarded["nickname"] == "Jan"

    def test_standings_orders_by_first_completion(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        fast = join(client, meeting["id"], "Speedy")
        slow = join(client, meeting["id"], "Steady")

        pool = client.get("/api/words", headers=fast["headers"]).json()
        fast_grid = build_grid(client, meeting["id"], fast, [w["id"] for w in pool[:24]])
        build_grid(client, meeting["id"], slow)
        client.patch(
            f"/api/meetings/{meeting['id']}/status", json={"status": "live"}, headers=admin_headers
        )

        for cell in [c for c in fast_grid["cells"] if c["position"] < 5]:
            client.post("/api/ingest", json={"text": cell["text"], "meeting_id": meeting["id"]})

        board = client.get(f"/api/meetings/{meeting['id']}/standings").json()
        assert board[0]["nickname"] == "Speedy"
        assert board[0]["position"] == 1 and board[0]["lines"] >= 1
        assert board[1]["nickname"] == "Steady"

    def test_transcript_history_is_recorded(self, client: TestClient, admin_headers: dict):
        meeting, participant, _ = self._live_meeting(client, admin_headers, "Clark")
        client.post("/api/ingest", json={"text": "let us circle back", "meeting_id": meeting["id"]})
        transcript = client.get(f"/api/meetings/{meeting['id']}/transcript").json()
        assert [t["raw"] for t in transcript] == ["let", "us", "circle", "back"]

    def test_reset_clears_marks_and_wins(self, client: TestClient, admin_headers: dict):
        meeting, participant, grid = self._live_meeting(client, admin_headers, "Pete")
        for cell in [c for c in grid["cells"] if c["position"] < 5]:
            client.post("/api/ingest", json={"text": cell["text"], "meeting_id": meeting["id"]})

        client.post(f"/api/meetings/{meeting['id']}/reset", headers=admin_headers)
        refreshed = client.get(
            f"/api/meetings/{meeting['id']}/grid", headers=participant["headers"]
        ).json()
        assert refreshed["marked_count"] == 1
        assert refreshed["lines"] == []


class TestAdminConsole:
    def test_stats(self, client: TestClient, admin_headers: dict):
        stats = client.get("/api/admin/stats", headers=admin_headers).json()
        assert stats["words"] > 100
        assert stats["environment"] == "test"
        assert stats["moderation_enabled"] is False

    def test_admin_sees_every_grid(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        for name in ("P1", "P2", "P3"):
            build_grid(client, meeting["id"], join(client, meeting["id"], name))
        grids = client.get(f"/api/meetings/{meeting['id']}/grids", headers=admin_headers).json()
        assert {c["nickname"] for c in grids} == {"P1", "P2", "P3"}

    def test_participants_are_listed_per_meeting(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        join(client, meeting["id"], "Listed")
        participants = client.get(
            "/api/admin/participants", params={"meeting_id": meeting["id"]}, headers=admin_headers
        ).json()
        assert [p["nickname"] for p in participants] == ["Listed"]

    def test_removing_a_participant_drops_their_grid(self, client: TestClient, admin_headers: dict):
        meeting = create_meeting(client, admin_headers)
        participant = join(client, meeting["id"], "Doomed")
        build_grid(client, meeting["id"], participant)

        response = client.delete(
            f"/api/admin/participants/{participant['participant']['id']}", headers=admin_headers
        )
        assert response.status_code == 204
        assert (
            client.get(f"/api/meetings/{meeting['id']}/grids", headers=admin_headers).json() == []
        )

    def test_api_key_lifecycle(self, client: TestClient, admin_headers: dict):
        created = client.post(
            "/api/admin/keys", json={"name": "Zoom bridge"}, headers=admin_headers
        ).json()
        assert created["key"].startswith("jw_")

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
    browser. Participants mid-draft got "Join a meeting to continue." with no explanation.
    """

    def test_generated_key_is_reused_across_processes(self, tmp_path: Path):
        database = str(tmp_path / "completion.db")
        first = _resolve_secret_key(database)
        second = _resolve_secret_key(database)

        assert first == second, "a restart must not invalidate every issued token"
        assert (tmp_path / ".secret_key").read_text(encoding="utf-8") == first

    def test_separate_databases_get_separate_keys(self, tmp_path: Path):
        a = _resolve_secret_key(str(tmp_path / "a" / "completion.db"))
        b = _resolve_secret_key(str(tmp_path / "b" / "completion.db"))
        assert a != b

    def test_in_memory_database_stays_ephemeral(self, tmp_path: Path):
        """Tests and throwaway instances have nowhere to persist, and want no file."""
        assert _resolve_secret_key(":memory:") != _resolve_secret_key(":memory:")
        assert not (tmp_path / ".secret_key").exists()
