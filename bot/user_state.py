from __future__ import annotations

from datetime import datetime
from typing import Any, Tuple

from sqlalchemy import select
from telegram import ReplyKeyboardRemove
from telegram.ext import ContextTypes

from .db import session_scope
from .models import User


async def load_user(telegram_id: int) -> Tuple[bool, bool, User | None]:
    async with session_scope() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    is_verified = bool(user and user.verified_at)
    is_banned = bool(user and user.is_banned)
    return is_verified, is_banned, user


def cache_user_state(
    context: ContextTypes.DEFAULT_TYPE, verified: bool, is_banned: bool, user: User | None
) -> None:
    context.user_data["is_verified"] = verified
    context.user_data["is_banned"] = is_banned
    context.user_data["ban_reason"] = user.ban_reason if user and user.ban_reason else None
    context.user_data["_user_state_cached_at"] = datetime.utcnow()


async def ensure_user_state(
    context: ContextTypes.DEFAULT_TYPE,
    telegram_id: int,
    force_refresh: bool = False,
) -> Tuple[bool, bool, str | None]:
    verified = context.user_data.get("is_verified")
    is_banned = context.user_data.get("is_banned")
    ban_reason = context.user_data.get("ban_reason")
    if force_refresh or verified is None or is_banned is None:
        verified, is_banned, user = await load_user(telegram_id)
        cache_user_state(context, verified, is_banned, user)
        ban_reason = context.user_data.get("ban_reason")
    return bool(verified), bool(is_banned), ban_reason


def ban_notice_text(reason: str | None) -> str:
    text = (
        "Ваш доступ к Bigbob ограничен. Вы не можете пользоваться ботом, пока блокировка не будет снята."
    )
    if reason:
        text += f"\nПричина: {reason}"
    text += "\nЕсли считаете блокировку ошибочной, напишите в поддержку @BigbobSupport."
    return text


async def send_ban_notice(message: Any, reason: str | None) -> None:
    await message.reply_text(ban_notice_text(reason), reply_markup=ReplyKeyboardRemove())


def clear_pending_nickname(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("pending_nickname", None)


__all__ = [
    "ban_notice_text",
    "cache_user_state",
    "clear_pending_nickname",
    "ensure_user_state",
    "load_user",
    "send_ban_notice",
]