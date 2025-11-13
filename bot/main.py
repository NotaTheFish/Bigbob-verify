from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy import select
from telegram import Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .config import get_settings
from .db import init_db, session_scope
from .models import Admin, AdminActionLog, AdminRole, EventQueue
from .services.purchases import create_purchase_request
from .services.queue import enqueue_event
from .services.security import (
    approve_admin_token,
    consume_admin_token,
    create_admin_token,
    enforce_role,
    ensure_root_admin,
    generate_token,
)
from .ui import (
    MENU_ADMIN,
    MENU_PROFILE,
    MENU_SHOP,
    MENU_SUPPORT,
    MENU_VERIFICATION,
    build_main_keyboard,
)
from .user_state import (
    cache_user_state,
    clear_pending_nickname,
    ensure_user_state,
    load_user,
    send_ban_notice,
)
from .verification.handlers import build_verification_conversation, verification_instruction

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()


def _plural_ru(value: int, forms: tuple[str, str, str]) -> str:
    value = abs(value)
    if value % 10 == 1 and value % 100 != 11:
        return forms[0]
    if 2 <= value % 10 <= 4 and not (12 <= value % 100 <= 14):
        return forms[1]
    return forms[2]
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message or update.effective_message
    user = update.effective_user
    if not message or not user:
        return ConversationHandler.END

    clear_pending_nickname(context)
    verified, is_banned, user_record = await load_user(user.id)
    cache_user_state(context, verified, is_banned, user_record)
    is_admin = context.user_data.get("admin_verified", False)

    if is_banned:
        await send_ban_notice(message, context.user_data.get("ban_reason"))
        return ConversationHandler.END

    if verified:
        await message.reply_text(
            "С возвращением в Bigbob! Выберите действие ниже.",
            reply_markup=build_main_keyboard(True, is_admin),
        )
        return ConversationHandler.END

    await message.reply_text(
        verification_instruction(),
        reply_markup=build_main_keyboard(False, is_admin),
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
        verified, is_banned, ban_reason = await ensure_user_state(context, user.id)

    if is_banned:
        await send_ban_notice(message, ban_reason)
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

    application.add_handler(CommandHandler("start", start))
    application.add_handler(build_verification_conversation())
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