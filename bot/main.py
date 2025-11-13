from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select, update as sa_update
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .config import get_settings
from .db import init_db, session_scope
from .models import (
    Admin,
    AdminActionLog,
    AdminRole,
    EventQueue,
    User,
    Verification,
    VerificationStatus,
)
from .services.purchases import create_purchase_request
from .services.roblox import (
    RobloxProfileNotFound,
    RobloxServiceError,
    contains_verification_code,
    fetch_profile_by_nickname,
)
from .services.queue import enqueue_event
from .services.security import (
    approve_admin_token,
    consume_admin_token,
    create_admin_token,
    enforce_role,
    ensure_root_admin,
    generate_token,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

ASK_NICK, CONFIRM_NICK = range(2)

VERIFICATION_CONVERSATION_TIMEOUT = 120
VERIFICATION_CHECK_CALLBACK = "verification_check"

MENU_VERIFICATION = "Верификация"
MENU_SHOP = "Магазин"
MENU_PROFILE = "Профиль"
MENU_SUPPORT = "Поддержка"
MENU_ADMIN = "Админ режим"


def _plural_ru(value: int, forms: tuple[str, str, str]) -> str:
    value = abs(value)
    if value % 10 == 1 and value % 100 != 11:
        return forms[0]
    if 2 <= value % 10 <= 4 and not (12 <= value % 100 <= 14):
        return forms[1]
    return forms[2]


def build_main_keyboard(verified: bool, is_admin: bool) -> ReplyKeyboardMarkup:
    if not verified:
        return ReplyKeyboardMarkup(
            [[KeyboardButton(MENU_VERIFICATION)]],
            resize_keyboard=True,
            one_time_keyboard=False,
        )

    buttons = [
        [KeyboardButton(MENU_SHOP), KeyboardButton(MENU_PROFILE)],
        [KeyboardButton(MENU_SUPPORT)],
    ]
    if is_admin:
        buttons.append([KeyboardButton(MENU_ADMIN)])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=False)


async def _load_user(telegram_id: int) -> tuple[bool, bool, User | None]:
    async with session_scope() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    is_verified = bool(user and user.verified_at)
    is_banned = bool(user and user.is_banned)
    return is_verified, is_banned, user


def _cache_user_state(
    context: ContextTypes.DEFAULT_TYPE, verified: bool, is_banned: bool, user: User | None
) -> None:
    context.user_data["is_verified"] = verified
    context.user_data["is_banned"] = is_banned
    context.user_data["ban_reason"] = user.ban_reason if user and user.ban_reason else None
    context.user_data["_user_state_cached_at"] = datetime.utcnow()


def _clear_pending_nickname(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("pending_nickname", None)


async def _ensure_user_state(
    context: ContextTypes.DEFAULT_TYPE,
    telegram_id: int,
    force_refresh: bool = False,
) -> tuple[bool, bool, str | None]:
    verified = context.user_data.get("is_verified")
    is_banned = context.user_data.get("is_banned")
    ban_reason = context.user_data.get("ban_reason")
    if force_refresh or verified is None or is_banned is None:
        verified, is_banned, user = await _load_user(telegram_id)
        _cache_user_state(context, verified, is_banned, user)
        ban_reason = context.user_data.get("ban_reason")
    return bool(verified), bool(is_banned), ban_reason


def _ban_notice_text(reason: str | None) -> str:
    text = (
        "Ваш доступ к Bigbob ограничен. Вы не можете пользоваться ботом, пока блокировка не будет снята."
    )
    if reason:
        text += f"\nПричина: {reason}"
    text += "\nЕсли считаете блокировку ошибочной, напишите в поддержку @BigbobSupport."
    return text


async def _send_ban_notice(message, reason: str | None) -> None:
    await message.reply_text(_ban_notice_text(reason), reply_markup=ReplyKeyboardRemove())


def _verification_instruction() -> str:
    return (
        "Привет! Мы ещё не подтвердили твою верификацию.\n"
        "Напиши свой Roblox-ник в следующем сообщении, чтобы получить код подтверждения.\n"
        "После этого добавь выданный код в описание своего Roblox-профиля и дождись проверки."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message or update.effective_message
    user = update.effective_user
    if not message or not user:
        return ConversationHandler.END

    _clear_pending_nickname(context)
    verified, is_banned, user_record = await _load_user(user.id)
    _cache_user_state(context, verified, is_banned, user_record)
    is_admin = context.user_data.get("admin_verified", False)

    if is_banned:
        await _send_ban_notice(message, context.user_data.get("ban_reason"))
        return ConversationHandler.END

    if verified:
        await message.reply_text(
            "С возвращением в Bigbob! Выберите действие ниже.",
            reply_markup=build_main_keyboard(True, is_admin),
        )
        return ConversationHandler.END

    await message.reply_text(
        _verification_instruction(),
        reply_markup=build_main_keyboard(False, is_admin),
    )
    return ConversationHandler.END


async def start_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message or update.effective_message
    user = update.effective_user
    if not message or not user:
        return ConversationHandler.END

    _clear_pending_nickname(context)
    verified, is_banned, ban_reason = await _ensure_user_state(
        context, user.id, force_refresh=True
    )

    if is_banned:
        await _send_ban_notice(message, ban_reason)
        return ConversationHandler.END

    if verified:
        await message.reply_text(
            "Вы уже прошли верификацию.",
            reply_markup=build_main_keyboard(True, context.user_data.get("admin_verified", False)),
        )
        return ConversationHandler.END

    await message.reply_text(
        _verification_instruction(),
        reply_markup=build_main_keyboard(
            False, context.user_data.get("admin_verified", False)
        ),
    )
    return ASK_NICK


async def cancel_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message or update.effective_message
    _clear_pending_nickname(context)
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


async def handle_menu_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.text:
        return

    selection = message.text.strip()
    user = update.effective_user
    verified = context.user_data.get("is_verified")
    is_banned = context.user_data.get("is_banned")
    ban_reason = context.user_data.get("ban_reason")
    if user and (verified is None or is_banned is None):
        verified, is_banned, ban_reason = await _ensure_user_state(context, user.id)

    if is_banned:
        await _send_ban_notice(message, ban_reason)
        return

    if selection == MENU_SHOP:
        await message.reply_text("Магазин пока в разработке. Используйте /start для возврата.")
    elif selection == MENU_PROFILE:
        await message.reply_text(
            "Раздел профиля появится позже. Мы покажем баланс и реферальную ссылку."
        )
    elif selection == MENU_SUPPORT:
        await message.reply_text("Поддержка: напишите @BigbobSupport или используйте /start.")
    elif selection == MENU_ADMIN:
        if context.user_data.get("admin_verified"):
            await admin_menu(update, context)
        else:
            await message.reply_text("Для доступа к админ-режиму используйте /admin_login <token>.")


async def _issue_verification_code(
    update: Update, context: ContextTypes.DEFAULT_TYPE, nickname: str
) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    code = f"BB-{secrets.token_hex(3)}"
    expires_at = datetime.utcnow() + timedelta(seconds=settings.verification_code_ttl_seconds)
    async with session_scope() as session:
        async with session.begin():
            expire_stmt = (
                sa_update(Verification)
                .where(
                    Verification.telegram_id == user.id,
                    Verification.status == VerificationStatus.pending,
                )
                .values(status=VerificationStatus.expired)
                .execution_options(synchronize_session=False)
            )
            expire_result = await session.execute(expire_stmt)
            expired_rows = expire_result.rowcount or 0
            if expired_rows > 1:
                logger.warning(
                    "Expired multiple pending verifications for telegram_id=%s: %s",
                    user.id,
                    expired_rows,
                )

            verification = Verification(
                telegram_id=user.id,
                roblox_nick=nickname,
                code=code,
                status=VerificationStatus.pending,
                expires_at=expires_at,
            )
            session.add(verification)
    context.user_data["verification_code"] = code
    context.user_data["is_verified"] = False
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Проверить", callback_data=VERIFICATION_CHECK_CALLBACK)]]
    )
    await message.reply_text(
        "Добавьте этот код в описание своего Roblox-профиля в течение 10 минут и нажмите \"Проверить\", когда будете готовы: "
        f"`{code}`",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def ask_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    user = update.effective_user
    if not message or not user or not message.text:
        return ConversationHandler.END

    verified, is_banned, ban_reason = await _ensure_user_state(
        context, user.id, force_refresh=True
    )

    if is_banned:
        await _send_ban_notice(message, ban_reason)
        return ConversationHandler.END

    nickname = message.text.strip()
    context.user_data["pending_nickname"] = nickname
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Да", callback_data="confirm_nick_yes"),
                InlineKeyboardButton("Нет", callback_data="confirm_nick_no"),
            ]
        ]
    )
    await message.reply_text(
        f"Ты указал Roblox-ник <b>{nickname}</b>. Всё верно?",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    return CONFIRM_NICK


async def confirm_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return ConversationHandler.END

    await query.answer()
    data = query.data or ""
    if data == "confirm_nick_yes":
        nickname = context.user_data.get("pending_nickname")
        if not nickname:
            await query.edit_message_text(
                "Не удалось определить ник. Пожалуйста, отправьте его ещё раз."
            )
            return ASK_NICK
        verified, is_banned, ban_reason = await _ensure_user_state(
            context, user.id, force_refresh=True
        )
        if is_banned:
            if query.message:
                await _send_ban_notice(query.message, ban_reason)
            _clear_pending_nickname(context)
            return ConversationHandler.END
        if verified:
            await query.edit_message_text("Вы уже прошли верификацию.")
            _clear_pending_nickname(context)
            return ConversationHandler.END
        await query.edit_message_reply_markup(reply_markup=None)
        await _issue_verification_code(update, context, nickname)
        _clear_pending_nickname(context)
        return ConversationHandler.END

    if data == "confirm_nick_no":
        _clear_pending_nickname(context)
        await query.edit_message_text("Хорошо, отправьте правильный Roblox-ник.")
        if query.message:
            await query.message.reply_text("Напишите свой Roblox-ник ещё раз.")
        return ASK_NICK

    return ConversationHandler.END


async def verification_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_pending_nickname(context)
    message = update.effective_message if update else None
    if message:
        await message.reply_text("Диалог истёк. Отправьте /start, чтобы начать верификацию заново.")
    return ConversationHandler.END


async def check_verification_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    message = query.message if query else update.effective_message
    if not query or not user or not message:
        return

    await query.answer()

    async with session_scope() as session:
        verification = await session.scalar(
            select(Verification)
            .where(
                Verification.telegram_id == user.id,
                Verification.status == VerificationStatus.pending,
            )
            .order_by(Verification.created_at.desc())
        )
        if not verification:
            await message.reply_text(
                "Активных запросов на верификацию не найдено. Отправьте /start, чтобы получить новый код."
            )
            return

        if verification.expires_at < datetime.utcnow():
            verification.status = VerificationStatus.expired
            await session.commit()
            await message.reply_text(
                "Этот код уже истёк. Запросите новый через /start и попробуйте снова."
            )
            return

        try:
            profile = await fetch_profile_by_nickname(verification.roblox_nick)
        except RobloxProfileNotFound:
            await message.reply_text(
                "Не удалось найти Roblox-профиль с таким ником. Проверьте написание ника и запросите новый код."
            )
            return
        except RobloxServiceError:
            await message.reply_text(
                "Не получилось связаться с Roblox. Попробуйте ещё раз через минуту."
            )
            return

        combined_text = " ".join(
            part for part in (profile.description, profile.status) if part
        )
        if contains_verification_code(combined_text, verification.code):
            verification.status = VerificationStatus.used
            verification.expires_at = datetime.utcnow()
            user_record = await session.scalar(
                select(User).where(User.telegram_id == verification.telegram_id)
            )
            if not user_record:
                user_record = User(
                    telegram_id=verification.telegram_id,
                    roblox_id=profile.user_id,
                    verified_at=datetime.utcnow(),
                )
                session.add(user_record)
            else:
                user_record.roblox_id = profile.user_id
                user_record.verified_at = datetime.utcnow()
            await session.commit()
            context.user_data["is_verified"] = True
            await message.reply_text(
                "Код найден! Верификация завершена.",
                reply_markup=build_main_keyboard(
                    True, context.user_data.get("admin_verified", False)
                ),
            )
            return

        await message.reply_text(
            "Код пока не найден в описании/статусе. Убедитесь, что вы сохранили профиль и попробуйте ещё раз."
        )


async def admin_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /admin_login <token>")
        return
    token = context.args[0]
    async with session_scope() as session:
        admin = await consume_admin_token(session, token, update.effective_user.id)
        if not admin:
            await session.rollback()
            await update.message.reply_text("Токен недействителен или ещё не подтверждён.")
            return
        session.add(
            AdminActionLog(
                admin_id=admin.admin_id,
                action_type="admin_login",
                target=str(update.effective_user.id),
                details="Онбординг завершён",
            )
        )
        await session.commit()
    await update.message.reply_text(f"Добро пожаловать, администратор с ролью {admin.role.value}! Введите /admin_menu.")
    context.user_data["admin_verified"] = True


async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        admin = await enforce_role(session, update.effective_user.id, AdminRole.main, AdminRole.manager, AdminRole.support)
        if not admin:
            await update.message.reply_text("У вас нет активного доступа администратора.")
            return
    await update.message.reply_text(
        "Команды администратора:\n"
        "- /admin_token <role>\n"
        "- /admin_logs\n"
        "- /admin_approve <token> (только для main)",
    )


async def admin_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /admin_token <role>")
        return
    role = context.args[0]
    if role not in settings.allowed_admin_roles:
        await update.message.reply_text("Эта роль недоступна.")
        return
    async with session_scope() as session:
        admin = await enforce_role(session, update.effective_user.id, AdminRole.main)
        if not admin:
            await update.message.reply_text("Создавать токены может только главный администратор.")
            return
        try:
            requested_role = AdminRole(role)
        except ValueError:
            await update.message.reply_text("Неизвестная роль.")
            return
        token = await create_admin_token(session, admin.admin_id, requested_role)
        await approve_admin_token(session, token.token, admin.admin_id)
        await session.commit()
    await update.message.reply_text(f"Токен создан и автоматически подтверждён: {token.token}")


async def bigbob_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message or update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    if user.id != settings.root_admin_id:
        await message.reply_text("Коды доступа может создавать только главный администратор.")
        return

    role_value = context.args[0].lower() if context.args else AdminRole.support.value
    if role_value not in settings.allowed_admin_roles:
        await message.reply_text(
            "Эта роль недоступна. Доступные роли: " + ", ".join(settings.allowed_admin_roles)
        )
        return
    try:
        requested_role = AdminRole(role_value)
    except ValueError:
        await message.reply_text("Указана неизвестная роль.")
        return

    async with session_scope() as session:
        admin, _ = await ensure_root_admin(session)
        token = await create_admin_token(session, admin.admin_id, requested_role)
        await approve_admin_token(session, token.token, admin.admin_id)
        await session.commit()

    ttl_seconds = settings.admin_token_ttl_seconds
    if ttl_seconds % 60 == 0:
        ttl_value = ttl_seconds // 60
        ttl_unit = _plural_ru(ttl_value, ("минута", "минуты", "минут"))
    else:
        ttl_value = ttl_seconds
        ttl_unit = _plural_ru(ttl_value, ("секунда", "секунды", "секунд"))
    await message.reply_text(
        "Сформирован одноразовый админский код:\n"
        f"`{token.token}`\n"
        f"Действителен {ttl_value} {ttl_unit}.",
        parse_mode="Markdown",
    )


async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /admin_approve <token>")
        return
    token_value = context.args[0]
    async with session_scope() as session:
        admin = await enforce_role(session, update.effective_user.id, AdminRole.main)
        if not admin:
            await update.message.reply_text("Подтверждать токены может только главный администратор.")
            return
        if not await approve_admin_token(session, token_value, admin.admin_id):
            await session.rollback()
            await update.message.reply_text("Не удалось подтвердить токен.")
            return
        await session.commit()
    await update.message.reply_text("Токен подтверждён.")


async def admin_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        admin = await enforce_role(session, update.effective_user.id, AdminRole.main)
        if not admin:
            await update.message.reply_text("Просматривать логи может только главный администратор.")
            return
        result = await session.execute(
            select(AdminActionLog).order_by(AdminActionLog.ts.desc()).limit(10)
        )
        rows = result.scalars().all()
    text = "\n".join(
        f"{row.ts.isoformat()} {row.action_type}: {row.details or ''}" for row in rows
    )
    await update.message.reply_text(text or "Логи отсутствуют.")


async def purchase(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /buy <item_id> <idempotency_key>")
        return
    item_id, idempotency_key = context.args[:2]
    request_id = generate_token("REQ")
    async with session_scope() as session:
        try:
            request = await create_purchase_request(
                session, request_id, update.effective_user.id, item_id, idempotency_key
            )
            event = {"type": "purchase", "request_id": request.request_id}
            session.add(EventQueue(event_id=f"purchase:{request.request_id}", payload=json.dumps(event)))
            await session.commit()
        except ValueError as exc:
            await session.rollback()
            await update.message.reply_text(f"Не удалось оформить покупку: {exc}")
            return
    await enqueue_event({"type": "purchase", "request_id": request.request_id})
    await update.message.reply_text(
        "Запрос на покупку отправлен. Мы уведомим, когда он будет подтверждён."
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Exception while handling update: %s", context.error)


async def admin_init(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /admin_init <token>")
        return
    token_value = context.args[0]
    if token_value != settings.admin_initial_token:
        await update.message.reply_text("Неверный начальный токен.")
        return
    async with session_scope() as session:
        existing_main = await session.scalar(
            select(Admin).where(Admin.role == AdminRole.main, Admin.revoked_at.is_(None))
        )
        if existing_main:
            await update.message.reply_text("Главный администратор уже создан.")
            return
        admin = Admin(telegram_id=update.effective_user.id, role=AdminRole.main)
        session.add(admin)
        await session.flush()
        session.add(
            AdminActionLog(
                admin_id=admin.admin_id,
                action_type="admin_init",
                target=str(update.effective_user.id),
                details="Инициализация главного администратора",
            )
        )
        await session.commit()
    await update.message.reply_text("Инициализация завершена. Вы назначены главным администратором.")


async def build_application() -> Application:
    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .rate_limiter(AIORateLimiter())
        .concurrent_updates(True)
        .build()
    )

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("verify", start_verification),
            MessageHandler(filters.Regex(f"^{MENU_VERIFICATION}$"), start_verification),
        ],
        states={
            ASK_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_nickname)],
            CONFIRM_NICK: [
                CallbackQueryHandler(
                    confirm_nickname, pattern="^confirm_nick_(yes|no)$"
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

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv)
    application.add_handler(CommandHandler("cancel", cancel_verification))
    application.add_handler(
        CallbackQueryHandler(
            check_verification_status, pattern=f"^{VERIFICATION_CHECK_CALLBACK}$"
        )
    )
    application.add_handler(CommandHandler("admin_login", admin_login))
    application.add_handler(CommandHandler("admin_init", admin_init))
    application.add_handler(CommandHandler("admin_menu", admin_menu))
    application.add_handler(CommandHandler("admin_token", admin_token))
    application.add_handler(CommandHandler("admin_approve", admin_approve))
    application.add_handler(CommandHandler("admin_logs", admin_logs))
    application.add_handler(CommandHandler("bigbob_code", bigbob_code))
    application.add_handler(CommandHandler("buy", purchase))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_selection)
    )
    application.add_error_handler(error_handler)

    return application


async def run_bot() -> None:
    await init_db()
    application = await build_application()
    await application.initialize()
    await application.start()
    logger.info("Bot started")
    await application.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    asyncio.run(run_bot())