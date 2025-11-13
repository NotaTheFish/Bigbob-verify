from __future__ import annotations

import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..models import VerificationStatus
from ..ui import MENU_VERIFICATION, build_main_keyboard
from ..user_state import (
    cache_user_state,
    clear_pending_nickname,
    ensure_user_state,
    load_user,
    send_ban_notice,
)
from .service import create_verification_request, expire_verification, get_latest_verification

logger = logging.getLogger(__name__)

ASK_NICKNAME, CHECK_CODE = range(2)
VERIFICATION_CONVERSATION_TIMEOUT = 120
VERIFICATION_CHECK_CALLBACK = "verification_check"


def verification_instruction() -> str:
    return (
        "Напиши свой Roblox-ник. Мы создадим код подтверждения, который нужно ввести в игре Bigbob на Roblox."
    )


def _pending_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Проверить статус", callback_data=VERIFICATION_CHECK_CALLBACK)]]
    )


async def start_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message or update.effective_message
    user = update.effective_user
    if not message or not user:
        return ConversationHandler.END

    clear_pending_nickname(context)
    verified, is_banned, ban_reason = await ensure_user_state(
        context, user.id, force_refresh=True
    )

    if is_banned:
        await send_ban_notice(message, ban_reason)
        return ConversationHandler.END

    if verified:
        await message.reply_text(
            "Вы уже прошли верификацию.",
            reply_markup=build_main_keyboard(True, context.user_data.get("admin_verified", False)),
        )
        return ConversationHandler.END

    await message.reply_text(verification_instruction())
    return ASK_NICKNAME


async def cancel_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message or update.effective_message
    clear_pending_nickname(context)
    if message:
        verified = bool(context.user_data.get("is_verified"))
        await message.reply_text(
            "Верификация отменена. Используйте /start, чтобы вернуться к главному меню.",
            reply_markup=build_main_keyboard(
                verified,
                context.user_data.get("admin_verified", False),
            ),
        )
    return ConversationHandler.END


async def ask_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    user = update.effective_user
    if not message or not user or not message.text:
        return ConversationHandler.END

    verified, is_banned, ban_reason = await ensure_user_state(
        context, user.id, force_refresh=True
    )

    if is_banned:
        await send_ban_notice(message, ban_reason)
        return ConversationHandler.END

    if verified:
        await message.reply_text(
            "Вы уже прошли верификацию.",
            reply_markup=build_main_keyboard(True, context.user_data.get("admin_verified", False)),
        )
        return ConversationHandler.END

    nickname = message.text.strip()
    if not nickname:
        await message.reply_text("Ник не может быть пустым. Укажите корректный Roblox-ник.")
        return ASK_NICKNAME

    verification = await create_verification_request(user.id, nickname)
    context.user_data["pending_verification_id"] = verification.id

    await message.reply_text(
        (
            "Отлично! Ваш код подтверждения:\n"
            f"<code>{verification.code}</code>\n\n"
            "1. Зайдите в наш Roblox-сервер Bigbob.\n"
            "2. Откройте меню верификации и введите этот код.\n"
            "3. Нажмите \"Проверить статус\" в Telegram, чтобы убедиться, что Roblox подтвердил код."
        ),
        parse_mode="HTML",
        reply_markup=_pending_keyboard(),
    )
    return CHECK_CODE


async def check_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return ConversationHandler.END

    await query.answer()
    message = query.message

    verification = await get_latest_verification(user.id)
    if not verification:
        if message:
            await message.reply_text(
                "Активных запросов не найдено. Отправьте /start, чтобы начать заново.",
                reply_markup=build_main_keyboard(
                    context.user_data.get("is_verified", False),
                    context.user_data.get("admin_verified", False),
                ),
            )
        return ConversationHandler.END

    now = datetime.utcnow()
    if verification.status == VerificationStatus.pending and verification.expires_at < now:
        await expire_verification(verification.id)
        if message:
            await message.reply_text(
                "Код истёк. Запросите новый через /start.",
                reply_markup=build_main_keyboard(
                    context.user_data.get("is_verified", False),
                    context.user_data.get("admin_verified", False),
                ),
            )
        return ConversationHandler.END

    if verification.status == VerificationStatus.pending:
        if message:
            await message.reply_text(
                "Код ещё не подтверждён Roblox. Убедитесь, что вы ввели его правильно в игре.",
                reply_markup=_pending_keyboard(),
            )
        return CHECK_CODE

    if verification.status == VerificationStatus.used:
        context.user_data["is_verified"] = True
        cached_verified, cached_ban, user_record = await load_user(user.id)
        cache_user_state(context, cached_verified, cached_ban, user_record)
        if message:
            await message.reply_text(
                "✅ Вы верифицированы!",
                reply_markup=build_main_keyboard(True, context.user_data.get("admin_verified", False)),
            )
        return ConversationHandler.END

    if message:
        await message.reply_text(
            "Этот код больше недействителен. Запросите новый через /start.",
            reply_markup=build_main_keyboard(
                context.user_data.get("is_verified", False),
                context.user_data.get("admin_verified", False),
            ),
        )
    return ConversationHandler.END


async def verification_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_pending_nickname(context)
    message = update.effective_message if update else None
    if message:
        await message.reply_text("Диалог истёк. Отправьте /start, чтобы начать верификацию заново.")
    return ConversationHandler.END


def build_verification_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("verify", start_verification),
            MessageHandler(filters.Regex(f"^{MENU_VERIFICATION}$"), start_verification),
        ],
        states={
            ASK_NICKNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_nickname)],
            CHECK_CODE: [
                CallbackQueryHandler(
                    check_status,
                    pattern=f"^{VERIFICATION_CHECK_CALLBACK}$",
                )
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, verification_timeout),
                CallbackQueryHandler(verification_timeout),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_verification)],
        conversation_timeout=VERIFICATION_CONVERSATION_TIMEOUT,
    )


__all__ = [
    "ASK_NICKNAME",
    "CHECK_CODE",
    "VERIFICATION_CHECK_CALLBACK",
    "VERIFICATION_CONVERSATION_TIMEOUT",
    "ask_nickname",
    "build_verification_conversation",
    "cancel_verification",
    "check_status",
    "start_verification",
    "verification_instruction",
]