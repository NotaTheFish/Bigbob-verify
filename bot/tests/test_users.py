from __future__ import annotations

import os

import pytest

pytest.importorskip("aiosqlite")

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("HMAC_SECRET", "secret")
os.environ.setdefault("ADMIN_INITIAL_TOKEN", "init")

from bot.db import configure_engine, init_db, session_scope  # noqa: E402
from bot.models import User  # noqa: E402


@pytest.mark.asyncio
async def test_user_creation_supports_large_telegram_ids(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path/'users.db'}"
    os.environ["DB_URL"] = db_url
    configure_engine(db_url)
    await init_db()

    large_telegram_id = 5_000_000_000  # larger than 32-bit signed integer

    async with session_scope() as session:
        user = User(telegram_id=large_telegram_id, username="big-number")
        session.add(user)
        await session.commit()
        user_id = user.id

    async with session_scope() as session:
        stored = await session.get(User, user_id)
        assert stored is not None
        assert stored.telegram_id == large_telegram_id