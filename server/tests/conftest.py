"""Test fixtures: an isolated database and callers for each role."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Configure the environment before anything imports app.config.
_TMPDIR = tempfile.mkdtemp(prefix="completion-tests-")
os.environ["DATABASE_URL"] = str(Path(_TMPDIR) / "test.db")
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["ENVIRONMENT"] = "test"
os.environ["ADMIN_PIN"] = "2165"
os.environ["INGEST_REQUIRE_KEY"] = "false"
os.environ["ANTHROPIC_API_KEY"] = ""  # moderation off by default; tests stub the judge

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.engine import invalidate_all_indexes  # noqa: E402
from app.main import create_app  # noqa: E402

ADMIN_PIN = "2165"


@pytest.fixture(autouse=True)
def fresh_database(tmp_path: Path) -> Iterator[None]:
    """Give every test its own database file."""
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = str(tmp_path / "completion.db")
    db.reset_connection()
    invalidate_all_indexes()
    db.get_connection()
    yield
    db.reset_connection()
    invalidate_all_indexes()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A TestClient whose lifespan seeds the word pool, demo meeting and ingest key."""
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def admin_headers(client: TestClient) -> dict[str, str]:
    """Admin token, obtained the only way there is — with the PIN."""
    response = client.post("/api/auth/admin", json={"pin": ADMIN_PIN})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def create_meeting(client: TestClient, admin_headers: dict, name: str = "Test Meeting") -> dict:
    response = client.post("/api/meetings", json={"name": name}, headers=admin_headers)
    assert response.status_code == 201, response.text
    return response.json()


def join(client: TestClient, meeting_id: str, nickname: str) -> dict:
    """Join a meeting with a nickname and return ``{'token', 'participant', 'headers'}``."""
    response = client.post(f"/api/meetings/{meeting_id}/join", json={"nickname": nickname})
    assert response.status_code == 201, response.text
    body = response.json()
    return {
        "token": body["token"],
        "participant": body["participant"],
        "headers": {"Authorization": f"Bearer {body['token']}"},
    }


def build_grid(client: TestClient, meeting_id: str, participant: dict, word_ids=None) -> dict:
    response = client.post(
        f"/api/meetings/{meeting_id}/grid",
        json={"word_ids": word_ids or []},
        headers=participant["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()
