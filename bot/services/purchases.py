from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Balance, Item, PurchaseRequest, PurchaseStatus, AdminActionLog


async def create_purchase_request(
    session: AsyncSession,
    request_id: str,
    user_id: int,
    item_id: str,
    idempotency_key: str,
) -> PurchaseRequest:
    existing = await session.scalar(select(PurchaseRequest).where(PurchaseRequest.idempotency_key == idempotency_key))
    if existing:
        return existing

    item = await session.get(Item, item_id)
    if not item:
        raise ValueError("Item not found")

    if item.copies_total is not None and item.copies_sold >= item.copies_total:
        raise ValueError("Item sold out")

    request = PurchaseRequest(
        request_id=request_id,
        user_id=user_id,
        item_id=item_id,
        idempotency_key=idempotency_key,
    )
    session.add(request)
    await session.flush()

    session.add(
        AdminActionLog(
            admin_id=0,
            action_type="purchase_request_created",
            target=str(request.request_id),
            details=f"user={user_id},item={item_id}",
        )
    )

    return request


async def confirm_purchase(session: AsyncSession, request_id: str) -> Optional[PurchaseRequest]:
    request = await session.get(PurchaseRequest, request_id)
    if not request:
        return None
    if request.status == PurchaseStatus.confirmed:
        return request
    if request.status != PurchaseStatus.pending:
        return None

    item = await session.get(Item, request.item_id)
    if not item:
        return None

    balance = await session.get(Balance, request.user_id)
    if not balance:
        balance = Balance(user_id=request.user_id)
        session.add(balance)
        await session.flush()

    if item.copies_total is not None and item.copies_sold >= item.copies_total:
        request.status = PurchaseStatus.cancelled
        return request

    request.status = PurchaseStatus.confirmed
    request.completed_at = datetime.utcnow()
    item.copies_sold += 1

    session.add(
        AdminActionLog(
            admin_id=0,
            action_type="purchase_confirmed",
            target=request.request_id,
            details=f"user={request.user_id},item={request.item_id}",
        )
    )

    return request


async def cancel_purchase(session: AsyncSession, request_id: str, reason: str) -> Optional[PurchaseRequest]:
    request = await session.get(PurchaseRequest, request_id)
    if not request:
        return None
    if request.status != PurchaseStatus.pending:
        return request

    request.status = PurchaseStatus.cancelled

    session.add(
        AdminActionLog(
            admin_id=0,
            action_type="purchase_cancelled",
            target=request.request_id,
            details=reason,
        )
    )
    return request