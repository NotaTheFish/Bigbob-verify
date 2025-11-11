from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Referral, ReferralStatus

settings = get_settings()


async def can_reward_referral(session: AsyncSession, referrer_id: int) -> bool:
    day_start = datetime.utcnow() - timedelta(days=1)
    count = await session.scalar(
        select(func.count(Referral.id)).where(
            Referral.referrer_id == referrer_id,
            Referral.status == ReferralStatus.rewarded,
            Referral.created_at >= day_start,
        )
    )
    return (count or 0) < settings.referral_reward_daily_cap


async def mark_referral_rewarded(session: AsyncSession, referral_id: int, reward_amount: int) -> Optional[Referral]:
    referral = await session.get(Referral, referral_id)
    if not referral:
        return None
    if referral.status != ReferralStatus.pending:
        return referral

    if not await can_reward_referral(session, referral.referrer_id):
        referral.status = ReferralStatus.flagged
        return referral

    referral.status = ReferralStatus.rewarded
    referral.reward_amount = reward_amount
    return referral


async def flag_suspicious_referral(session: AsyncSession, referral_id: int, reason: str) -> Optional[Referral]:
    referral = await session.get(Referral, referral_id)
    if not referral:
        return None
    referral.status = ReferralStatus.flagged
    return referral