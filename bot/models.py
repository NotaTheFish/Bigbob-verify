from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel


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


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    telegram_id: int = Field(index=True, unique=True)
    roblox_id: Optional[int] = Field(default=None, index=True, unique=True)
    username: Optional[str] = Field(default=None, max_length=255)
    verified_at: Optional[datetime] = Field(default=None)
    invited_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    last_active: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    balance: Optional["Balance"] = Relationship(back_populates="user")


class Balance(SQLModel, table=True):
    __tablename__ = "balances"

    user_id: int = Field(foreign_key="users.id", primary_key=True)
    nuts_balance: int = Field(default=0, nullable=False)
    reserved_balance: int = Field(default=0, nullable=False)

    user: User = Relationship(back_populates="balance")


class Verification(SQLModel, table=True):
    __tablename__ = "verifications"
    __table_args__ = (UniqueConstraint("telegram_id", "status", name="uq_verification_active"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    telegram_id: int = Field(index=True, nullable=False)
    roblox_nick: str = Field(nullable=False, max_length=255)
    code: str = Field(nullable=False, max_length=32, index=True)
    status: VerificationStatus = Field(default=VerificationStatus.pending)
    expires_at: datetime = Field(nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class Referral(SQLModel, table=True):
    __tablename__ = "referrals"

    id: Optional[int] = Field(default=None, primary_key=True)
    referrer_id: int = Field(foreign_key="users.id", nullable=False)
    referred_id: int = Field(foreign_key="users.id", nullable=False)
    reward_amount: int = Field(default=0, nullable=False)
    status: ReferralStatus = Field(default=ReferralStatus.pending)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class PromoCodeType(str, enum.Enum):
    nuts = "nuts"
    privilege = "privilege"


class PromoCode(SQLModel, table=True):
    __tablename__ = "promo_codes"

    code: str = Field(primary_key=True)
    type: PromoCodeType = Field(nullable=False)
    value: int = Field(nullable=False)
    activations_left: int = Field(default=0)
    expires_at: Optional[datetime] = Field(default=None)
    creator_admin_id: Optional[int] = Field(default=None, foreign_key="admins.admin_id")


class Item(SQLModel, table=True):
    __tablename__ = "items"

    item_id: str = Field(primary_key=True)
    name: str = Field(nullable=False)
    description: Optional[str] = Field(default=None)
    copies_total: Optional[int] = Field(default=None)
    copies_sold: int = Field(default=0)
    creator_admin: Optional[int] = Field(default=None, foreign_key="admins.admin_id")


class PurchaseRequest(SQLModel, table=True):
    __tablename__ = "purchase_requests"

    request_id: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="users.id", nullable=False)
    item_id: str = Field(foreign_key="items.item_id", nullable=False)
    status: PurchaseStatus = Field(default=PurchaseStatus.pending)
    idempotency_key: str = Field(nullable=False, unique=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    completed_at: Optional[datetime] = Field(default=None)


class Admin(SQLModel, table=True):
    __tablename__ = "admins"

    admin_id: Optional[int] = Field(default=None, primary_key=True)
    telegram_id: int = Field(unique=True, nullable=False)
    role: AdminRole = Field(nullable=False)
    granted_by: Optional[int] = Field(default=None, foreign_key="admins.admin_id")
    granted_at: datetime = Field(default_factory=datetime.utcnow)
    revoked_at: Optional[datetime] = Field(default=None)


class AdminToken(SQLModel, table=True):
    __tablename__ = "admin_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(unique=True, nullable=False, index=True)
    role_requested: AdminRole = Field(nullable=False)
    created_by: int = Field(foreign_key="admins.admin_id")
    approved_by: Optional[int] = Field(default=None, foreign_key="admins.admin_id")
    approved_at: Optional[datetime] = Field(default=None)
    consumed_by: Optional[int] = Field(default=None, foreign_key="admins.admin_id")
    consumed_at: Optional[datetime] = Field(default=None)
    expires_at: datetime = Field(nullable=False)


class AdminActionLog(SQLModel, table=True):
    __tablename__ = "admin_actions_log"

    action_id: Optional[int] = Field(default=None, primary_key=True)
    admin_id: Optional[int] = Field(default=None, foreign_key="admins.admin_id")
    action_type: str = Field(nullable=False)
    target: Optional[str] = Field(default=None)
    details: Optional[str] = Field(default=None)
    ts: datetime = Field(default_factory=datetime.utcnow)


class EventQueue(SQLModel, table=True):
    __tablename__ = "events_queue"

    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: str = Field(nullable=False, unique=True)
    payload: str = Field(nullable=False)
    enqueued_at: datetime = Field(default_factory=datetime.utcnow)
    processed_at: Optional[datetime] = Field(default=None)