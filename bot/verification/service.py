from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update as sa_update

from ..config import get_settings
from ..db import session_scope
from ..models import User, Verification, VerificationStatus

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass(slots=True)
class VerificationCheckResult:
    status: str
    username: str
    telegram_id: int | None = None


@dataclass(slots=True)
class VerificationStatusResult:
    status: str
    username: str


def _generate_code() -> str:
    return f"BB-{secrets.token_hex(3).upper()}"


def _normalize(value: str | None) -> str:
    return (value or "").strip().lower()


def _nicknames_match(expected: str, provided: str | None) -> bool:
    if provided is None:
        return True
    return _normalize(expected) == _normalize(provided)


async def create_verification_request(telegram_id: int, nickname: str) -> Verification:
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=settings.verification_code_ttl_seconds)
    async with session_scope() as session:
        async with session.begin():
            await session.execute(
                sa_update(Verification)
                .where(
                    Verification.telegram_id == telegram_id,
                    Verification.status == VerificationStatus.pending,
                )
                .values(status=VerificationStatus.expired)
            )
            verification = Verification(
                telegram_id=telegram_id,
                roblox_nick=nickname,
                code=_generate_code(),
                status=VerificationStatus.pending,
                expires_at=expires_at,
            )
            session.add(verification)
        logger.info(
            "Issued verification code for telegram_id=%s nickname=%s expires_at=%s",
            telegram_id,
            nickname,
            expires_at.isoformat(),
        )
        return verification


async def get_latest_verification(telegram_id: int) -> Verification | None:
    async with session_scope() as session:
        return await session.scalar(
            select(Verification)
            .where(Verification.telegram_id == telegram_id)
            .order_by(Verification.created_at.desc())
        )


async def expire_verification(verification_id: int) -> None:
    async with session_scope() as session:
        verification = await session.get(Verification, verification_id)
        if verification and verification.status == VerificationStatus.pending:
            verification.status = VerificationStatus.expired
            await session.commit()


async def process_backend_confirmation(
    username: str | None,
    code: str,
    player_id: int,
) -> VerificationCheckResult:
    now = datetime.utcnow()
    async with session_scope() as session:
        verification = await session.scalar(
            select(Verification)
            .where(Verification.code == code)
            .order_by(Verification.created_at.desc())
        )
        if not verification:
            logger.warning("Verification code not found: code=%s", code)
            return VerificationCheckResult(status="not_found", username=username or "")

        stored_username = verification.roblox_nick
        if verification.status == VerificationStatus.used:
            logger.info(
                "Verification code already used: code=%s telegram_id=%s",
                code,
                verification.telegram_id,
            )
            return VerificationCheckResult(status="already_verified", username=stored_username)

        if verification.expires_at < now:
            verification.status = VerificationStatus.expired
            await session.commit()
            logger.info("Verification code expired: code=%s", code)
            return VerificationCheckResult(status="expired", username=stored_username)

        if not _nicknames_match(stored_username, username):
            logger.warning(
                "Verification nickname mismatch: expected=%s provided=%s",
                stored_username,
                username,
            )
            return VerificationCheckResult(status="mismatch", username=stored_username)

        verification.status = VerificationStatus.used
        verification.expires_at = now

        user = await session.scalar(select(User).where(User.telegram_id == verification.telegram_id))
        if not user:
            user = User(telegram_id=verification.telegram_id, roblox_id=player_id, verified_at=now)
            session.add(user)
        else:
            user.roblox_id = player_id
            user.verified_at = now
            user.last_active = now

        await session.commit()
        logger.info(
            "Verification completed for telegram_id=%s roblox_id=%s",
            verification.telegram_id,
            player_id,
        )
        return VerificationCheckResult(
            status="verified",
            username=stored_username,
            telegram_id=verification.telegram_id,
        )


async def fetch_status_for_username(username: str) -> VerificationStatusResult:
    normalized = _normalize(username)
    async with session_scope() as session:
        verification = await session.scalar(
            select(Verification)
            .where(func.lower(Verification.roblox_nick) == normalized)
            .order_by(Verification.created_at.desc())
        )
        if not verification:
            return VerificationStatusResult(status="not_found", username=username)

        if verification.status == VerificationStatus.used:
            return VerificationStatusResult(status="verified", username=verification.roblox_nick)

        now = datetime.utcnow()
        if verification.status == VerificationStatus.pending and verification.expires_at >= now:
            return VerificationStatusResult(status="pending", username=verification.roblox_nick)

        return VerificationStatusResult(status="expired", username=verification.roblox_nick)


__all__ = [
    "VerificationCheckResult",
    "VerificationStatusResult",
    "create_verification_request",
    "expire_verification",
    "fetch_status_for_username",
    "get_latest_verification",
    "process_backend_confirmation",
]