from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class VerificationStatus(str, enum.Enum):
    pending = "pending"
    used = "used"
    expired = "expired"


class PurchaseStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    cancelled = "cancelled"


class ReferralStatus(str, enum.Enum):
    pending = "pending"
    rewarded = "rewarded"
    flagged = "flagged"


class AdminRole(str, enum.Enum):
    main = "main"
    manager = "manager"
    support = "support"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(index=True, unique=True)
    roblox_id: Mapped[Optional[int]] = mapped_column(index=True, unique=True, nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), nullable=True)
    invited_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=datetime.utcnow, nullable=False)
    last_active: Mapped[datetime] = mapped_column(DateTime(), default=datetime.utcnow, nullable=False)

    balance: Mapped[Optional["Balance"]] = relationship(back_populates="user", uselist=False)


class Balance(Base):
    __tablename__ = "balances"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    nuts_balance: Mapped[int] = mapped_column(default=0, nullable=False)
    reserved_balance: Mapped[int] = mapped_column(default=0, nullable=False)

    user: Mapped["User"] = relationship(back_populates="balance")


class Verification(Base):
    __tablename__ = "verifications"
    __table_args__ = (UniqueConstraint("telegram_id", "status", name="uq_verification_active"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(index=True, nullable=False)
    roblox_nick: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[VerificationStatus] = mapped_column(SAEnum(VerificationStatus), default=VerificationStatus.pending)
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=datetime.utcnow, nullable=False)


class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    referred_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    reward_amount: Mapped[int] = mapped_column(default=0, nullable=False)
    status: Mapped[ReferralStatus] = mapped_column(SAEnum(ReferralStatus), default=ReferralStatus.pending)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=datetime.utcnow, nullable=False)


class PromoCodeType(str, enum.Enum):
    nuts = "nuts"
    privilege = "privilege"


class PromoCode(Base):
    __tablename__ = "promo_codes"

    code: Mapped[str] = mapped_column(primary_key=True)
    type: Mapped[PromoCodeType] = mapped_column(SAEnum(PromoCodeType), nullable=False)
    value: Mapped[int] = mapped_column(nullable=False)
    activations_left: Mapped[int] = mapped_column(default=0)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), nullable=True)
    creator_admin_id: Mapped[Optional[int]] = mapped_column(ForeignKey("admins.admin_id"), nullable=True)


class Item(Base):
    __tablename__ = "items"

    item_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    copies_total: Mapped[Optional[int]] = mapped_column(nullable=True)
    copies_sold: Mapped[int] = mapped_column(default=0)
    creator_admin: Mapped[Optional[int]] = mapped_column(ForeignKey("admins.admin_id"), nullable=True)


class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"

    request_id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.item_id"), nullable=False)
    status: Mapped[PurchaseStatus] = mapped_column(SAEnum(PurchaseStatus), default=PurchaseStatus.pending)
    idempotency_key: Mapped[str] = mapped_column(nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=datetime.utcnow, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), nullable=True)


class Admin(Base):
    __tablename__ = "admins"

    admin_id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(unique=True, nullable=False)
    role: Mapped[AdminRole] = mapped_column(SAEnum(AdminRole), nullable=False)
    granted_by: Mapped[Optional[int]] = mapped_column(ForeignKey("admins.admin_id"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(), default=datetime.utcnow)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), nullable=True)


class AdminToken(Base):
    __tablename__ = "admin_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    role_requested: Mapped[AdminRole] = mapped_column(SAEnum(AdminRole), nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("admins.admin_id"))
    approved_by: Mapped[Optional[int]] = mapped_column(ForeignKey("admins.admin_id"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), nullable=True)
    consumed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("admins.admin_id"), nullable=True)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), nullable=Tru)
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)


class AdminActionLog(Base):
    __tablename__ = "admin_actions_log"

    action_id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[Optional[int]] = mapped_column(ForeignKey("admins.admin_id"), nullable=True)
    action_type: Mapped[str] = mapped_column(nullable=False)
    target: Mapped[Optional[str]] = mapped_column(nullable=True)
    details: Mapped[Optional[str]] = mapped_column(nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(), default=datetime.utcnow)


class EventQueue(Base):
    __tablename__ = "events_queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(nullable=False, unique=True)
    payload: Mapped[str] = mapped_column(nullable=False)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(), default=datetime.utcnow)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(), nullable=True)