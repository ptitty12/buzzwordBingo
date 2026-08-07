"""Test fixtures: an isolated database and an authenticated API client per test."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Configure the environment before anything imports app.config.
_TMPDIR = tempfile.mkdtemp(prefix="bingo-tests-")
os.environ["DATABASE_URL"] = str(Path(_TMPDIR) / "test.db")
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["ENVIRONMENT"] = "test"
os.environ["ADMIN_PIN"] = ""
os.environ["INGEST_REQUIRE_KEY"] = "false"
os.environ["BOOTSTRAP_ADMINS"] = "admin"

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.engine import invalidate_all_indexes  # noqa: E402
from app.main import create_app  # noqa: E402
from app.seed import run_seed  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_database(tmp_path: Path) -> Iterator[None]:
    """Give every test its own database file."""
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = str(tmp_path / "bingo.db")
    db.reset_connection()
    invalidate_all_indexes()
    db.get_connection()
    yield
    db.reset_connection()
    invalidate_all_indexes()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A TestClient whose lifespan seeds the pool, admin account and demo game."""
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def admin_token(client: TestClient) -> str:
    """Session token for the seeded bootstrap admin."""
    users = client.get("/api/auth/users").json()
    admin = next(u for u in users if u["is_admin"])
    response = client.post("/api/auth/signin", json={"user_id": admin["id"]})
    assert response.status_code == 200, response.text
    return response.json()["token"]


@pytest.fixture
def admin_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def seeded(client: TestClient) -> dict:
    """Re-run the seed and hand back its summary (words, admin id, demo game)."""
    return run_seed()


def make_player(client: TestClient, nickname: str) -> dict:
    """Create a player account and return ``{'token', 'user', 'headers'}``."""
    response = client.post("/api/auth/signup", json={"nickname": nickname})
    assert response.status_code == 201, response.text
    body = response.json()
    return {
        "token": body["token"],
        "user": body["user"],
        "headers": {"Authorization": f"Bearer {body['token']}"},
    }
