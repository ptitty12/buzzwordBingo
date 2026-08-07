"""Tests for the player word-suggestion flow and the LLM buzzword judge.

The judge itself is a network call, so these tests exercise the parts we own: the
prescreen, the parsing of the model's JSON, and the endpoint's behaviour for each
verdict. The model call is stubbed — we are testing our contract with it, not Claude.
"""

from fastapi.testclient import TestClient

from app import moderation
from app.moderation import CATEGORIES, Verdict, _verdict_from, prescreen

from .conftest import create_game, join


class TestPrescreen:
    """Cheap local rejections that should never reach the model."""

    def test_passes_a_plausible_phrase(self):
        assert prescreen("business fundamentals") is None
        assert prescreen("synergy") is None

    def test_rejects_empty_and_punctuation(self):
        assert prescreen("!!!").decision == "rejected"
        assert prescreen("   ").decision == "rejected"

    def test_rejects_a_whole_sentence(self):
        verdict = prescreen("we should really circle back on this one next week please")
        assert verdict.decision == "rejected"
        assert "sentence" in verdict.reason.lower()

    def test_rejects_bare_numbers(self):
        assert prescreen("2026").decision == "rejected"

    def test_lets_the_model_judge_real_words(self):
        """The prescreen must not pre-empt the interesting calls."""
        for word in ("sales", "the", "business"):
            assert prescreen(word) is None


class TestVerdictParsing:
    def test_reads_a_well_formed_approval(self):
        verdict = _verdict_from(
            {
                "approved": True,
                "canonical": "cross-pollination",
                "category": "Consulting-Speak",
                "difficulty": 3,
                "reason": "Classic consultant metaphor.",
            },
            "claude-opus-5",
            fallback="crosspolination",
        )
        assert verdict.approved
        assert verdict.canonical == "cross-pollination"
        assert verdict.category == "Consulting-Speak"
        assert verdict.difficulty == 3

    def test_reads_a_rejection(self):
        verdict = _verdict_from(
            {"approved": False, "reason": "That is an ordinary noun."},
            "claude-opus-5",
            fallback="sales",
        )
        assert verdict.decision == "rejected"
        assert verdict.reason == "That is an ordinary noun."

    def test_unknown_category_falls_back(self):
        verdict = _verdict_from(
            {"approved": True, "canonical": "x", "category": "Nonsense", "difficulty": 2},
            "m",
            fallback="x",
        )
        assert verdict.category == "General"

    def test_difficulty_is_clamped(self):
        def difficulty(value):
            verdict = _verdict_from({"approved": True, "difficulty": value}, "m", fallback="x")
            return verdict.difficulty

        assert difficulty(99) == 3
        assert difficulty(-5) == 1
        assert difficulty("?") == 2

    def test_missing_canonical_falls_back_to_submission(self):
        verdict = _verdict_from({"approved": True}, "m", fallback="synergy")
        assert verdict.canonical == "synergy"

    def test_missing_reason_gets_a_default(self):
        assert _verdict_from({"approved": False}, "m", fallback="x").reason
        assert _verdict_from({"approved": True}, "m", fallback="x").reason

    def test_every_declared_category_is_accepted(self):
        for category in CATEGORIES:
            verdict = _verdict_from(
                {"approved": True, "category": category}, "m", fallback="x"
            )
            assert verdict.category == category


class TestSuggestEndpoint:
    def _player(self, client: TestClient, admin_headers: dict, nickname: str = "Suggester"):
        game = create_game(client, admin_headers)
        return game, join(client, game["id"], nickname)

    def test_approved_word_enters_the_pool(
        self, client: TestClient, admin_headers: dict, monkeypatch
    ):
        game, player = self._player(client, admin_headers)
        monkeypatch.setattr(
            "app.routers.words.judge",
            lambda text, pool: Verdict(
                decision="approved",
                reason="Genuine management-speak.",
                canonical="business fundamentals",
                category="Corporate Strategy",
                difficulty=2,
                judged_by="claude-opus-5",
            ),
        )

        response = client.post(
            "/api/words/suggest",
            json={"text": "business fundamentals"},
            headers=player["headers"],
        )
        assert response.status_code == 201
        body = response.json()
        assert body["suggestion"]["status"] == "approved"
        assert body["word"]["text"] == "business fundamentals"
        assert body["word"]["source"] == "suggestion"
        assert "Suggester" in body["word"]["created_by"]

        pool = client.get("/api/words", headers=player["headers"]).json()
        assert any(w["text"] == "business fundamentals" for w in pool)

    def test_rejected_word_stays_out_of_the_pool(
        self, client: TestClient, admin_headers: dict, monkeypatch
    ):
        game, player = self._player(client, admin_headers)
        monkeypatch.setattr(
            "app.routers.words.judge",
            lambda text, pool: Verdict(
                decision="rejected",
                reason="'Sales' is an ordinary business noun, not a buzzword.",
                judged_by="claude-opus-5",
            ),
        )

        response = client.post(
            "/api/words/suggest", json={"text": "sales"}, headers=player["headers"]
        )
        assert response.status_code == 201
        body = response.json()
        assert body["suggestion"]["status"] == "rejected"
        assert body["word"] is None
        assert "ordinary" in body["suggestion"]["verdict"]

        pool = client.get("/api/words", headers=player["headers"]).json()
        assert not any(w["text"] == "sales" for w in pool)

    def test_misspelling_is_canonicalised_and_aliased(
        self, client: TestClient, admin_headers: dict, monkeypatch
    ):
        """The player's spelling must still mark the square if a speaker says it."""
        game, player = self._player(client, admin_headers)
        monkeypatch.setattr(
            "app.routers.words.judge",
            lambda text, pool: Verdict(
                decision="approved",
                reason="Jargon — corrected the spelling.",
                canonical="cross-pollination",
                category="Consulting-Speak",
                difficulty=3,
                judged_by="claude-opus-5",
            ),
        )

        body = client.post(
            "/api/words/suggest", json={"text": "crosspolination"}, headers=player["headers"]
        ).json()
        assert body["word"]["text"] == "cross-pollination"
        assert "crosspolination" in body["word"]["aliases"]

    def test_duplicate_is_rejected_before_the_model_runs(
        self, client: TestClient, admin_headers: dict, monkeypatch
    ):
        game, player = self._player(client, admin_headers)

        def explode(text, pool):
            raise AssertionError("the judge must not be called for a known duplicate")

        monkeypatch.setattr("app.routers.words.judge", explode)
        response = client.post(
            "/api/words/suggest", json={"text": "synergy"}, headers=player["headers"]
        )
        assert response.status_code == 409

    def test_pending_when_no_model_is_configured(self, client: TestClient, admin_headers: dict):
        """With no API key the suggestion queues instead of auto-approving."""
        game, player = self._player(client, admin_headers)
        response = client.post(
            "/api/words/suggest", json={"text": "quantum leadership"}, headers=player["headers"]
        )
        assert response.status_code == 201
        assert response.json()["suggestion"]["status"] == "pending"
        assert response.json()["word"] is None

    def test_quota_is_enforced(self, client: TestClient, admin_headers: dict, monkeypatch):
        game, player = self._player(client, admin_headers)
        monkeypatch.setattr(
            "app.routers.words.judge",
            lambda text, pool: Verdict(decision="rejected", reason="No.", judged_by="stub"),
        )

        from app.config import get_settings

        limit = get_settings().suggestions_per_player
        for index in range(limit):
            response = client.post(
                "/api/words/suggest",
                json={"text": f"placeholder phrase {index}"},
                headers=player["headers"],
            )
            assert response.status_code == 201

        blocked = client.post(
            "/api/words/suggest", json={"text": "one too many"}, headers=player["headers"]
        )
        assert blocked.status_code == 429

    def test_suggesting_requires_joining_a_game(self, client: TestClient):
        assert client.post("/api/words/suggest", json={"text": "synergy"}).status_code == 401

    def test_player_can_see_their_own_history(
        self, client: TestClient, admin_headers: dict, monkeypatch
    ):
        game, player = self._player(client, admin_headers)
        monkeypatch.setattr(
            "app.routers.words.judge",
            lambda text, pool: Verdict(decision="rejected", reason="Not jargon.", judged_by="stub"),
        )
        client.post("/api/words/suggest", json={"text": "spreadsheet"}, headers=player["headers"])

        body = client.get("/api/words/suggestions/mine", headers=player["headers"]).json()
        assert len(body["suggestions"]) == 1
        assert body["remaining"] == body["limit"] - 1


class TestAdminReview:
    def test_admin_can_approve_a_pending_suggestion(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Hopeful")
        client.post(
            "/api/words/suggest", json={"text": "quantum leadership"}, headers=player["headers"]
        )

        pending = client.get(
            "/api/admin/suggestions", params={"status": "pending"}, headers=admin_headers
        ).json()
        assert len(pending) == 1

        decided = client.post(
            f"/api/admin/suggestions/{pending[0]['id']}",
            json={"approve": True, "reason": "Good enough."},
            headers=admin_headers,
        )
        assert decided.status_code == 200
        assert decided.json()["status"] == "approved"

        pool = client.get("/api/words", headers=player["headers"]).json()
        assert any(w["text"] == "quantum leadership" for w in pool)

    def test_admin_can_overturn_an_approval(
        self, client: TestClient, admin_headers: dict, monkeypatch
    ):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Overruled")
        monkeypatch.setattr(
            "app.routers.words.judge",
            lambda text, pool: Verdict(
                decision="approved",
                reason="Looks fine to me.",
                canonical="regrettable phrase",
                category="Meeting Filler",
                difficulty=2,
                judged_by="stub",
            ),
        )
        client.post(
            "/api/words/suggest", json={"text": "regrettable phrase"}, headers=player["headers"]
        )

        suggestion = client.get("/api/admin/suggestions", headers=admin_headers).json()[0]
        client.post(
            f"/api/admin/suggestions/{suggestion['id']}",
            json={"approve": False, "reason": "On reflection, no."},
            headers=admin_headers,
        )

        active = client.get("/api/words", headers=player["headers"]).json()
        assert not any(w["text"] == "regrettable phrase" for w in active)

    def test_suggestions_are_admin_only(self, client: TestClient, admin_headers: dict):
        game = create_game(client, admin_headers)
        player = join(client, game["id"], "Curious")
        assert client.get("/api/admin/suggestions", headers=player["headers"]).status_code == 403


class TestJudgeFallbacks:
    """Every failure path must degrade to 'pending', never to silent approval."""

    def test_missing_api_key_yields_pending(self, monkeypatch):
        verdict = moderation.judge("quantum leadership", [])
        assert verdict.decision == "pending"

    def test_api_error_yields_pending(self, monkeypatch):
        import anthropic

        class BoomClient:
            def __init__(self, **kwargs):
                self.messages = self

            def create(self, **kwargs):
                raise anthropic.APIConnectionError(request=None)

        monkeypatch.setattr(anthropic, "Anthropic", BoomClient)
        monkeypatch.setattr(
            moderation, "get_settings", lambda: _settings_with_key("test-key")
        )
        assert moderation.judge("quantum leadership", []).decision == "pending"

    def test_malformed_json_yields_pending(self, monkeypatch):
        import anthropic

        class Block:
            type = "text"
            text = "not json at all"

        class Response:
            stop_reason = "end_turn"
            content = [Block()]

        class StubClient:
            def __init__(self, **kwargs):
                self.messages = self

            def create(self, **kwargs):
                return Response()

        monkeypatch.setattr(anthropic, "Anthropic", StubClient)
        monkeypatch.setattr(moderation, "get_settings", lambda: _settings_with_key("test-key"))
        assert moderation.judge("quantum leadership", []).decision == "pending"

    def test_refusal_yields_rejection(self, monkeypatch):
        import anthropic

        class Response:
            stop_reason = "refusal"
            content = []

        class StubClient:
            def __init__(self, **kwargs):
                self.messages = self

            def create(self, **kwargs):
                return Response()

        monkeypatch.setattr(anthropic, "Anthropic", StubClient)
        monkeypatch.setattr(moderation, "get_settings", lambda: _settings_with_key("test-key"))
        verdict = moderation.judge("something objectionable", [])
        assert verdict.decision == "rejected"


def _settings_with_key(key: str):
    """A settings object with moderation switched on, for stubbed judge tests."""
    from app.config import Settings

    settings = Settings()
    settings.anthropic_api_key = key
    return settings
