import hashlib
import hmac
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1].parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("HMAC_SECRET", "secret")
os.environ.setdefault("ADMIN_INITIAL_TOKEN", "init")

from bot.api.webhook import app  # noqa: E402
from bot.config import get_settings  # noqa: E402
from bot.verification.service import (  # noqa: E402
    VerificationCheckResult,
    VerificationStatusResult,
)


class StubApplication:
    def __init__(self) -> None:
        self.bot = object()
        self.processed_updates: list[object] = []

    async def initialize(self) -> None:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def process_update(self, update) -> None:
        self.processed_updates.append(update)


@pytest.fixture
def client_and_stub(monkeypatch):
    stub = StubApplication()

    async def fake_build_application():
        return stub

    async def fake_init_db():
        return None

    monkeypatch.setattr("bot.api.webhook.build_application", fake_build_application)
    monkeypatch.setattr("bot.api.webhook.init_db", fake_init_db)

    class DummyUpdate:
        @classmethod
        def de_json(cls, data, bot):
            return SimpleNamespace(data=data, bot=bot)

    monkeypatch.setattr("bot.api.webhook.Update", DummyUpdate)

    with TestClient(app) as client:
        yield client, stub


def _signature_for(payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":")).encode()
    secret = get_settings().hmac_secret.encode()
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def test_verification_check_endpoint(monkeypatch, client_and_stub):
    client, _ = client_and_stub

    async def fake_process(username, code, player_id):
        assert username == "PlayerOne"
        assert code == "BB-999"
        assert player_id == 777
        return VerificationCheckResult(status="verified", username="PlayerOne", telegram_id=1)

    monkeypatch.setattr(
        "bot.api.verification.verification_service.process_backend_confirmation",
        fake_process,
    )

    payload = {"username": "PlayerOne", "playerId": 777, "code": "BB-999"}
    signature = _signature_for(payload)

    response = client.post(
        "/bot/verification/check",
        json=payload,
        headers={"X-Signature": signature},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "verified", "username": "PlayerOne"}


def test_verification_status_endpoint(monkeypatch, client_and_stub):
    client, _ = client_and_stub

    async def fake_status(username):
        assert username == "PlayerTwo"
        return VerificationStatusResult(status="pending", username="PlayerTwo")

    monkeypatch.setattr(
        "bot.api.verification.verification_service.fetch_status_for_username",
        fake_status,
    )

    payload = {"username": "PlayerTwo", "playerId": 888}
    signature = _signature_for(payload)

    response = client.post(
        "/bot/verification/status",
        json=payload,
        headers={"X-Signature": signature},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "pending", "username": "PlayerTwo"}


def test_verification_rejects_bad_signature(client_and_stub):
    client, _ = client_and_stub
    payload = {"username": "Bad", "playerId": 1, "code": "NOPE"}

    response = client.post(
        "/bot/verification/check",
        json=payload,
        headers={"X-Signature": "invalid"},
    )

    assert response.status_code == 401


def test_telegram_webhook(client_and_stub):
    client, stub = client_and_stub
    update_payload = {"update_id": 100, "message": {"text": "hello"}}

    response = client.post("/webhook", json=update_payload)

    assert response.status_code == 200
    assert response.json() == {"status": "успешно"}
    assert stub.processed_updates
    assert stub.processed_updates[-1].data == update_payload