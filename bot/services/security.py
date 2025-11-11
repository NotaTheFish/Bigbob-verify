from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Admin, AdminRole, AdminToken, AdminActionLog

settings = get_settings()


def generate_token(prefix: str = "BB") -> str:
    return f"{prefix}-{secrets.token_urlsafe(16)}"


def verify_hmac(message: bytes, signature: str) -> bool:
    expected = hmac.new(settings.hmac_secret.encode(), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def create_admin_token(
    session: AsyncSession,
    created_by: int,
    role: AdminRole,
    expires_in: Optional[int] = None,
) -> AdminToken:
    ttl = expires_in or settings.admin_token_ttl_seconds
    token = AdminToken(
        token=generate_token("ADM"),
        role_requested=role,
        created_by=created_by,
        expires_at=datetime.utcnow() + timedelta(seconds=ttl),
    )
    session.add(token)
    session.add(
        AdminActionLog(
            admin_id=created_by,
            action_type="admin_token_created",
            target=token.token,
            details=f"Role={role.value}",
        )
    )
    await session.flush()
    return token


async def consume_admin_token(
    session: AsyncSession,
    token_value: str,
    consumer_telegram_id: int,
) -> Optional[Admin]:
    token = await session.scalar(select(AdminToken).where(AdminToken.token == token_value))
    if not token:
        return None
    if token.consumed_at or token.expires_at < datetime.utcnow():
        return None
    if not token.approved_at or not token.approved_by:
        return None

    existing_admin = await session.scalar(select(Admin).where(Admin.telegram_id == consumer_telegram_id))
    if existing_admin:
        return None

    admin = Admin(
        telegram_id=consumer_telegram_id,
        role=token.role_requested,
        granted_by=token.approved_by,
    )
    session.add(admin)
    await session.flush()

    token.consumed_by = admin.admin_id
    token.consumed_at = datetime.utcnow()

    session.add(
        AdminActionLog(
            admin_id=token.approved_by,
            action_type="admin_onboarded",
            target=str(admin.telegram_id),
            details=f"Role={admin.role.value}",
        )
    )

    return admin


async def approve_admin_token(session: AsyncSession, token_value: str, approver_id: int) -> bool:
    token = await session.scalar(select(AdminToken).where(AdminToken.token == token_value))
    if not token:
        return False
    if token.expires_at < datetime.utcnow():
        return False
    if token.approved_at:
        return False

    approver = await session.get(Admin, approver_id)
    if not approver:
        return False

    token.approved_at = datetime.utcnow()
    token.approved_by = approver_id
    session.add(
        AdminActionLog(
            admin_id=approver_id,
            action_type="admin_token_approved",
            target=token.token,
            details=f"Role={token.role_requested.value}",
        )
    )
    return True


async def enforce_role(session: AsyncSession, telegram_id: int, *allowed_roles: AdminRole) -> Optional[Admin]:
    admin = await session.scalar(select(Admin).where(Admin.telegram_id == telegram_id, Admin.revoked_at.is_(None)))
    if not admin:
        return None
    if admin.role not in allowed_roles:
        return None
    return admin