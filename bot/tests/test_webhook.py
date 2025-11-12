from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlmodel")

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1].parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("HMAC_SECRET", "secret")
os.environ.setdefault("ADMIN_INITIAL_TOKEN", "init")

EVENT_STORE: dict[str, object] = {}


class FakeSession:
    def __init__(self) -> None:
        self._pending = []

    def add(self, entry) -> None:
        self._pending.append(entry)

    async def commit(self) -> None:
        for entry in self._pending:
            EVENT_STORE[getattr(entry, "event_id", id(entry))] = entry
        self._pending.clear()

    async def rollback(self) -> None:
        self._pending.clear()

    async def close(self) -> None:
        pass


@asynccontextmanager
async def fake_session_scope():
    session = FakeSession()
    try:
        yield session
    finally:
        await session.close()


async def fake_init_db() -> None:
    return None


def fake_configure_engine(db_url: str | None = None) -> None:
    return None


fake_db_module = types.ModuleType("bot.db")
fake_db_module.session_scope = fake_session_scope
fake_db_module.init_db = fake_init_db
fake_db_module.configure_engine = fake_configure_engine
sys.modules["bot.db"] = fake_db_module

from bot.api.webhook import app  # noqa: E402
from bot.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def clear_store():
    EVENT_STORE.clear()


class StubApplication:
    def __init__(self) -> None:
        self.bot = object()
        self.processed_updates = []
        self.initialized = False
        self.started = False
        self.stopped = False
        self.shut_down = False

    async def initialize(self) -> None:
        self.initialized = True

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def shutdown(self) -> None:
        self.shut_down = True

    async def process_update(self, update) -> None:
        self.processed_updates.append(update)


class StubEventQueue:
    def __init__(self, event_id: str, payload: str) -> None:
        self.event_id = event_id
        self.payload = payload


@pytest.fixture
def client_and_stub(monkeypatch):
    stub = StubApplication()

    async def fake_build_application():
        return stub

    monkeypatch.setattr("bot.api.webhook.build_application", fake_build_application)
    monkeypatch.setattr("bot.api.webhook.EventQueue", StubEventQueue)

    class DummyUpdate:
        @classmethod
        def de_json(cls, data, bot):
            return SimpleNamespace(data=data, bot=bot)

    monkeypatch.setattr("bot.api.webhook.Update", DummyUpdate)

    with TestClient(app) as client:
        yield client, stub


def test_verify_callback(monkeypatch, client_and_stub):
    client, _ = client_and_stub
    captured = {}

    async def fake_enqueue(event):
        captured["event"] = event

    monkeypatch.setattr("bot.api.webhook.enqueue_event", fake_enqueue)

    payload = {"eventId": "evt-1", "playerId": 1, "code": "BB-123", "ts": "2024-01-01T00:00:00Z"}
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(get_settings().hmac_secret.encode(), body, hashlib.sha256).hexdigest()

    response = client.post("/api/verify-callback", json=payload, headers={"X-Signature": signature})
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert captured["event"]["type"] == "verification"

    assert "evt-1" in EVENT_STORE
    assert isinstance(EVENT_STORE["evt-1"], StubEventQueue)


def test_telegram_webhook(client_and_stub):
    client, stub = client_and_stub
    update_payload = {"update_id": 100, "message": {"text": "hello"}}

    response = client.post("/webhook", json=update_payload)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert stub.processed_updates
    assert stub.processed_updates[-1].data == update_payload