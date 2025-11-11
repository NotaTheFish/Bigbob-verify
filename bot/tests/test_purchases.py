from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

pytest.importorskip("sqlmodel")

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("HMAC_SECRET", "secret")
os.environ.setdefault("ADMIN_INITIAL_TOKEN", "init")

from bot.config import get_settings  # noqa: E402
from bot.db import configure_engine, init_db, session_scope  # noqa: E402
from bot.models import Item, PurchaseStatus, Referral, ReferralStatus  # noqa: E402
from bot.services.purchases import create_purchase_request  # noqa: E402
from bot.services.referrals import can_reward_referral, mark_referral_rewarded  # noqa: E402


@pytest.mark.asyncio
async def test_purchase_idempotency(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path/'purchases.db'}"
    os.environ["DB_URL"] = db_url
    configure_engine(db_url)
    await init_db()
    async with session_scope() as session:
        session.add(Item(item_id="hat", name="Hat", copies_total=10))
        await session.commit()

    async with session_scope() as session:
        req1 = await create_purchase_request(session, "req1", 10, "hat", "key")
        await session.commit()

    async with session_scope() as session:
        req2 = await create_purchase_request(session, "req2", 10, "hat", "key")
        assert req2.request_id == "req1"


@pytest.mark.asyncio
async def test_referral_cap_enforced(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path/'referrals.db'}"
    os.environ["DB_URL"] = db_url
    configure_engine(db_url)
    await init_db()
    settings = get_settings()
    settings.referral_reward_daily_cap = 1
    async with session_scope() as session:
        referral = Referral(referrer_id=1, referred_id=2)
        session.add(referral)
        await session.commit()

    async with session_scope() as session:
        eligible = await can_reward_referral(session, 1)
        assert eligible
        await mark_referral_rewarded(session, referral.id, 50)
        await session.commit()

    async with session_scope() as session:
        second = Referral(referrer_id=1, referred_id=3, status=ReferralStatus.pending)
        session.add(second)
        await session.commit()

    async with session_scope() as session:
        eligible = await can_reward_referral(session, 1)
        assert not eligible
        flagged = await mark_referral_rewarded(session, second.id, 50)
        await session.commit()
        assert flagged.status == ReferralStatus.flagged