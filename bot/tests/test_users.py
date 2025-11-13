from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

pytest.importorskip("aiosqlite")

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("HMAC_SECRET", "secret")
os.environ.setdefault("ADMIN_INITIAL_TOKEN", "init")

from bot.db import configure_engine, init_db, session_scope  # noqa: E402
from bot.models import User, Verification, VerificationStatus  # noqa: E402
from bot.worker import handle_verification  # noqa: E402


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


@pytest.mark.asyncio
async def test_verification_flow_supports_large_telegram_ids(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path/'verifications.db'}"
    os.environ["DB_URL"] = db_url
    configure_engine(db_url)
    await init_db()

    large_telegram_id = 6_000_000_000
    verification_code = "BIGVERIF"
    roblox_player_id = 987_654_321

    async with session_scope() as session:
        verification = Verification(
            telegram_id=large_telegram_id,
            roblox_nick="BigNick",
            code=verification_code,
            status=VerificationStatus.pending,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
            created_at=datetime.utcnow(),
        )
        session.add(verification)
        await session.commit()

    await handle_verification({"code": verification_code, "playerId": roblox_player_id})

    async with session_scope() as session:
        stored_verification = await session.scalar(
            select(Verification).where(Verification.code == verification_code)
        )
        stored_user = await session.scalar(select(User).where(User.telegram_id == large_telegram_id))

        assert stored_verification is not None
        assert stored_verification.status == VerificationStatus.used
        assert stored_user is not None
        assert stored_user.telegram_id == large_telegram_id
        assert stored_user.roblox_id == roblox_player_id


@pytest.mark.asyncio
async def test_verification_history_preserved_for_multiple_attempts(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path/'verification-history.db'}"
    os.environ["DB_URL"] = db_url
    configure_engine(db_url)
    await init_db()

    telegram_id = 7_777
    now = datetime.utcnow()

    async with session_scope() as session:
        first = Verification(
            telegram_id=telegram_id,
            roblox_nick="PlayerOne",
            code="HIST-1",
            status=VerificationStatus.pending,
            expires_at=now + timedelta(minutes=5),
            created_at=now,
        )
        session.add(first)
        await session.commit()
        first_id = first.id

    async with session_scope() as session:
        stored_first = await session.get(Verification, first_id)
        assert stored_first is not None
        stored_first.status = VerificationStatus.expired
        await session.commit()

    async with session_scope() as session:
        second = Verification(
            telegram_id=telegram_id,
            roblox_nick="PlayerOne",
            code="HIST-2",
            status=VerificationStatus.pending,
            expires_at=now + timedelta(minutes=10),
            created_at=now + timedelta(minutes=1),
        )
        session.add(second)
        await session.commit()
        second_id = second.id

    async with session_scope() as session:
        stored_second = await session.get(Verification, second_id)
        assert stored_second is not None
        stored_second.status = VerificationStatus.expired
        await session.commit()

    async with session_scope() as session:
        result = await session.execute(
            select(Verification).where(Verification.telegram_id == telegram_id)
        )
        history = result.scalars().all()

    assert len(history) == 2
    assert all(entry.status == VerificationStatus.expired for entry in history)