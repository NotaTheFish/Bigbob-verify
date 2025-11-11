from __future__ import annotations

import hmac
import hashlib
import json
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient
from sqlmodel import select

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("HMAC_SECRET", "secret")
os.environ.setdefault("ADMIN_INITIAL_TOKEN", "init")

from bot.api.webhook import app  # noqa: E402
from bot.config import get_settings  # noqa: E402
from bot.db import configure_engine, init_db, session_scope  # noqa: E402
from bot.models import EventQueue  # noqa: E402


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    db_path = tmp_path / "test.db"
    os.environ["DB_URL"] = f"sqlite+aiosqlite:///{db_path}"
    import asyncio

    configure_engine(os.environ["DB_URL"])
    asyncio.run(init_db())


def test_verify_callback(monkeypatch):
    captured = {}

    async def fake_enqueue(event):
        captured["event"] = event

    monkeypatch.setattr("bot.api.webhook.enqueue_event", fake_enqueue)

    client = TestClient(app)
    payload = {"eventId": "evt-1", "playerId": 1, "code": "BB-123", "ts": "2024-01-01T00:00:00Z"}
    body = json.dumps(payload).encode()
    signature = hmac.new(get_settings().hmac_secret.encode(), body, hashlib.sha256).hexdigest()

    response = client.post("/api/verify-callback", json=payload, headers={"X-Signature": signature})
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert captured["event"]["type"] == "verification"

    import asyncio

    async def fetch_event():
        async with session_scope() as session:
            return await session.scalar(
                select(EventQueue).where(EventQueue.event_id == "evt-1")
            )

    event = asyncio.run(fetch_event())
    assert event is not None