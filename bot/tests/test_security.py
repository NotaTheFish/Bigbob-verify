from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import select

pytest.importorskip("aiosqlite")

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DB_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("HMAC_SECRET", "secret")
os.environ.setdefault("ADMIN_INITIAL_TOKEN", "init")
os.environ.setdefault("ROOT_ADMIN_ID", "5813380332")

from bot.config import get_settings  # noqa: E402
from bot.db import configure_engine, init_db, session_scope  # noqa: E402
from bot.models import Admin, AdminRole  # noqa: E402
from bot.services.security import (  # noqa: E402
    approve_admin_token,
    consume_admin_token,
    create_admin_token,
    ensure_root_admin,
    enforce_role,
    generate_token,
    verify_hmac,
)


@pytest.mark.asyncio
async def test_hmac_verification() -> None:
    configure_engine(os.environ["DB_URL"])
    body = b"{}"
    secret = get_settings().hmac_secret.encode()
    import hmac
    import hashlib

    signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
    assert verify_hmac(body, signature)


@pytest.mark.asyncio
async def test_admin_token_flow(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path/'security.db'}"
    os.environ["DB_URL"] = db_url
    configure_engine(db_url)
    await init_db()
    async with session_scope() as session:
        main_admin = Admin(telegram_id=1, role=AdminRole.main)
        session.add(main_admin)
        await session.commit()
        await session.refresh(main_admin)

    async with session_scope() as session:
        admin = await session.scalar(select(Admin).where(Admin.telegram_id == 1))
        token = await create_admin_token(session, admin.admin_id, AdminRole.support)
        await session.flush()
        await approve_admin_token(session, token.token, admin.admin_id)
        await session.commit()

    async with session_scope() as session:
        admin = await consume_admin_token(session, token.token, 2)
        assert admin is not None
        await session.commit()

    async with session_scope() as session:
        fetched = await session.scalar(select(Admin).where(Admin.telegram_id == 2))
        assert fetched is not None
        assert fetched.role == AdminRole.support


@pytest.mark.asyncio
async def test_root_admin_auto_created(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path/'root-admin.db'}"
    os.environ["DB_URL"] = db_url
    configure_engine(db_url)
    await init_db()

    root_id = get_settings().root_admin_id

    async with session_scope() as session:
        admin, changed = await ensure_root_admin(session)
        assert changed is True
        assert admin.telegram_id == root_id
        assert admin.role == AdminRole.main
        await session.commit()

    async with session_scope() as session:
        admin, changed = await ensure_root_admin(session)
        assert changed is False
        assert admin.telegram_id == root_id

    async with session_scope() as session:
        admin = await enforce_role(session, root_id, AdminRole.support)
        assert admin is not None
        assert admin.role == AdminRole.main