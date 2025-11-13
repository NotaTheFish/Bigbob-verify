from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from sqlalchemy import select

pytest.importorskip("aiosqlite")

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("HMAC_SECRET", "secret")
os.environ.setdefault("ADMIN_INITIAL_TOKEN", "init")

from bot.db import configure_engine, init_db, session_scope  # noqa: E402
from bot.main import start, start_verification  # noqa: E402
from bot.models import User  # noqa: E402


class StubMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[tuple[str, dict]] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append((text, kwargs))


class StubUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class StubUpdate:
    def __init__(self, user: StubUser, message: StubMessage) -> None:
        self.message = message
        self.effective_message = message
        self.effective_user = user
        self.callback_query = None


@pytest.mark.asyncio
async def test_verification_menu_blocks_after_late_ban(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path/'late-ban.db'}"
    os.environ["DB_URL"] = db_url
    configure_engine(db_url)
    await init_db()

    telegram_id = 4242
    async with session_scope() as session:
        session.add(User(telegram_id=telegram_id, username="late-ban"))
        await session.commit()

    context = SimpleNamespace(user_data={})
    user = StubUser(telegram_id)

    start_message = StubMessage(text="/start")
    start_update = StubUpdate(user, start_message)
    await start(start_update, context)

    async with session_scope() as session:
        db_user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        assert db_user is not None
        db_user.is_banned = True
        db_user.ban_reason = "spam"
        await session.commit()

    verification_message = StubMessage(text="Верификация")
    verification_update = StubUpdate(user, verification_message)
    await start_verification(verification_update, context)

    assert verification_message.replies, "Verification handler should respond to the user"
    ban_text, _ = verification_message.replies[-1]
    assert "Ваш доступ" in ban_text
    assert context.user_data.get("is_banned") is True