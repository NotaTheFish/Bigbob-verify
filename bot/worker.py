from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select

from .db import session_scope
from .models import EventQueue, PurchaseStatus, User, Verification, VerificationStatus
from .services.purchases import confirm_purchase
from .services.queue import dequeue_event

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def mark_event_processed(event_id: str) -> None:
    if not event_id:
        return
    async with session_scope() as session:
        db_event = await session.scalar(select(EventQueue).where(EventQueue.event_id == event_id))
        if db_event:
            db_event.processed_at = datetime.utcnow()
            await session.commit()


async def handle_verification(payload: dict) -> None:
    code = payload["code"]
    player_id = payload["playerId"]
    async with session_scope() as session:
        verification = await session.scalar(
            select(Verification).where(Verification.code == code, Verification.status == VerificationStatus.pending)
        )
        if not verification:
            await session.commit()
            return
        if verification.expires_at < datetime.utcnow():
            verification.status = VerificationStatus.expired
            await session.commit()
            return
        verification.status = VerificationStatus.used
        verification.expires_at = datetime.utcnow()
        user = await session.scalar(select(User).where(User.telegram_id == verification.telegram_id))
        if not user:
            user = User(telegram_id=verification.telegram_id, roblox_id=player_id, verified_at=datetime.utcnow())
            session.add(user)
        else:
            user.roblox_id = player_id
            user.verified_at = datetime.utcnow()
        await session.commit()


async def handle_purchase(event: dict) -> None:
    request_id = event["request_id"]
    async with session_scope() as session:
        request = await confirm_purchase(session, request_id)
        if request and request.status == PurchaseStatus.confirmed:
            await session.commit()
        else:
            await session.rollback()


async def worker_loop() -> None:
    while True:
        event = await dequeue_event()
        if not event:
            await asyncio.sleep(0.5)
            continue
        event_type = event.get("type")
        try:
            if event_type == "verification":
                await handle_verification(event["payload"])
                await mark_event_processed(event["payload"].get("eventId", event.get("event_id")))
            elif event_type == "purchase":
                await handle_purchase(event)
                await mark_event_processed(f"purchase:{event['request_id']}")
            else:
                logger.warning("Unknown event type: %s", event_type)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to process event: %s", event)
            await asyncio.sleep(1)


def main() -> None:
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()