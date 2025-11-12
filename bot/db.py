from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings
from .models import Base

_settings = get_settings()
_engine: AsyncEngine | None = None
_async_session: async_sessionmaker[AsyncSession] | None = None


def configure_engine(db_url: str | None = None) -> None:
    global _engine, _async_session
    url = db_url or _settings.db_url
    _engine = create_async_engine(url, echo=False, future=True)
    _async_session = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


try:
    configure_engine()
except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency handling
    if exc.name != "aiosqlite":
        raise


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    assert _async_session is not None
    async with _async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    assert _engine is not None
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def get_engine() -> AsyncEngine:
    assert _engine is not None
    return _engine