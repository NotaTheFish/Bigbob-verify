from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select, update as sa_update
from telegram import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
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

ASK_NICK = 0

MENU_VERIFICATION = "Верификация"
MENU_SHOP = "Магазин"
MENU_PROFILE = "Профиль"
MENU_SUPPORT = "Поддержка"
MENU_ADMIN = "Админ режим"


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


async def _load_user(telegram_id: int) -> tuple[bool, User | None]:
    async with session_scope() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    return bool(user and user.verified_at), user


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

    verified, _ = await _load_user(user.id)
    context.user_data["is_verified"] = verified
    is_admin = context.user_data.get("admin_verified", False)

    if verified:
        await message.reply_text(
            "С возвращением в Bigbob! Выберите действие ниже.",
            reply_markup=build_main_keyboard(True, is_admin),
        )
        return ConversationHandler.END

    await message.reply_text(
        _verification_instruction(), reply_markup=ReplyKeyboardRemove()
    )
    return ASK_NICK


async def start_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message or update.effective_message
    user = update.effective_user
    if not message or not user:
        return ConversationHandler.END

    verified = context.user_data.get("is_verified")
    if verified is None:
        verified, _ = await _load_user(user.id)
        context.user_data["is_verified"] = verified

    if verified:
        await message.reply_text(
            "Вы уже прошли верификацию.",
            reply_markup=build_main_keyboard(True, context.user_data.get("admin_verified", False)),
        )
        return ConversationHandler.END

    await message.reply_text(
        _verification_instruction(), reply_markup=ReplyKeyboardRemove()
    )
    return ASK_NICK


async def handle_menu_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.text:
        return

    selection = message.text.strip()
    user = update.effective_user
    verified = context.user_data.get("is_verified")
    if verified is None and user:
        verified, _ = await _load_user(user.id)
        context.user_data["is_verified"] = verified

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
    elif selection == MENU_VERIFICATION:
        await start_verification(update, context)


async def ask_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nickname = update.message.text.strip()
    code = f"BB-{secrets.token_hex(3)}"
    expires_at = datetime.utcnow() + timedelta(seconds=settings.verification_code_ttl_seconds)
    async with session_scope() as session:
        await session.execute(
            sa_update(Verification)
            .where(
                Verification.telegram_id == update.effective_user.id,
                Verification.status == VerificationStatus.pending,
            )
            .values(status=VerificationStatus.expired)
        )
        verification = Verification(
            telegram_id=update.effective_user.id,
            roblox_nick=nickname,
            code=code,
            status=VerificationStatus.pending,
            expires_at=expires_at,
        )
        session.add(verification)
        await session.commit()
    context.user_data["verification_code"] = code
    context.user_data["is_verified"] = False
    await update.message.reply_text(
        "Place this code in your Roblox profile description within 10 minutes and wait for confirmation: "
        f"`{code}`",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def admin_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /admin_login <token>")
        return
    token = context.args[0]
    async with session_scope() as session:
        admin = await consume_admin_token(session, token, update.effective_user.id)
        if not admin:
            await session.rollback()
            await update.message.reply_text("Invalid or unapproved token.")
            return
        session.add(
            AdminActionLog(
                admin_id=admin.admin_id,
                action_type="admin_login",
                target=str(update.effective_user.id),
                details="Onboarding completed",
            )
        )
        await session.commit()
    await update.message.reply_text(f"Welcome, {admin.role.value} admin! Use /admin_menu.")
    context.user_data["admin_verified"] = True


async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        admin = await enforce_role(session, update.effective_user.id, AdminRole.main, AdminRole.manager, AdminRole.support)
        if not admin:
            await update.message.reply_text("You are not an active admin.")
            return
    await update.message.reply_text(
        "Admin commands:\n"
        "- /admin_token <role>\n"
        "- /admin_logs\n"
        "- /admin_approve <token> (main only)",
    )


async def admin_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /admin_token <role>")
        return
    role = context.args[0]
    if role not in settings.allowed_admin_roles:
        await update.message.reply_text("Role not allowed.")
        return
    async with session_scope() as session:
        admin = await enforce_role(session, update.effective_user.id, AdminRole.main)
        if not admin:
            await update.message.reply_text("Only main admin can create tokens.")
            return
        try:
            requested_role = AdminRole(role)
        except ValueError:
            await update.message.reply_text("Unknown role.")
            return
        token = await create_admin_token(session, admin.admin_id, requested_role)
        await approve_admin_token(session, token.token, admin.admin_id)
        await session.commit()
    await update.message.reply_text(f"Token created and auto-approved: {token.token}")


async def bigbob_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message or update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    if user.id != settings.root_admin_id:
        await message.reply_text("Only the root admin can generate access codes.")
        return

    role_value = context.args[0].lower() if context.args else AdminRole.support.value
    if role_value not in settings.allowed_admin_roles:
        await message.reply_text(
            "Role not allowed. Available roles: " + ", ".join(settings.allowed_admin_roles)
        )
        return
    try:
        requested_role = AdminRole(role_value)
    except ValueError:
        await message.reply_text("Unknown role specified.")
        return

    async with session_scope() as session:
        admin, _ = await ensure_root_admin(session)
        token = await create_admin_token(session, admin.admin_id, requested_role)
        await approve_admin_token(session, token.token, admin.admin_id)
        await session.commit()

    ttl_seconds = settings.admin_token_ttl_seconds
    await message.reply_text(
        "One-time admin code generated:\n"
        f"`{token.token}`\n"
        f"Valid for {ttl_seconds // 60 if ttl_seconds % 60 == 0 else ttl_seconds} "
        f"{'minutes' if ttl_seconds % 60 == 0 else 'seconds'}.",
        parse_mode="Markdown",
    )


async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /admin_approve <token>")
        return
    token_value = context.args[0]
    async with session_scope() as session:
        admin = await enforce_role(session, update.effective_user.id, AdminRole.main)
        if not admin:
            await update.message.reply_text("Only main admin can approve tokens.")
            return
        if not await approve_admin_token(session, token_value, admin.admin_id):
            await session.rollback()
            await update.message.reply_text("Unable to approve token.")
            return
        await session.commit()
    await update.message.reply_text("Token approved.")


async def admin_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        admin = await enforce_role(session, update.effective_user.id, AdminRole.main)
        if not admin:
            await update.message.reply_text("Only main admin can view logs.")
            return
        result = await session.execute(
            select(AdminActionLog).order_by(AdminActionLog.ts.desc()).limit(10)
        )
        rows = result.scalars().all()
    text = "\n".join(
        f"{row.ts.isoformat()} {row.action_type}: {row.details or ''}" for row in rows
    )
    await update.message.reply_text(text or "No logs available.")


async def purchase(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /buy <item_id> <idempotency_key>")
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
            await update.message.reply_text(f"Purchase failed: {exc}")
            return
    await enqueue_event({"type": "purchase", "request_id": request.request_id})
    await update.message.reply_text(
        "Purchase request submitted. You will be notified once confirmed."
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Exception while handling update: %s", context.error)


async def admin_init(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /admin_init <token>")
        return
    token_value = context.args[0]
    if token_value != settings.admin_initial_token:
        await update.message.reply_text("Invalid bootstrap token.")
        return
    async with session_scope() as session:
        existing_main = await session.scalar(
            select(Admin).where(Admin.role == AdminRole.main, Admin.revoked_at.is_(None))
        )
        if existing_main:
            await update.message.reply_text("Main admin already initialized.")
            return
        admin = Admin(telegram_id=update.effective_user.id, role=AdminRole.main)
        session.add(admin)
        await session.flush()
        session.add(
            AdminActionLog(
                admin_id=admin.admin_id,
                action_type="admin_init",
                target=str(update.effective_user.id),
                details="Bootstrap main admin",
            )
        )
        await session.commit()
    await update.message.reply_text("Bootstrap complete. You are the main admin.")


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
            CommandHandler("start", start),
            MessageHandler(filters.Regex(f"^{MENU_VERIFICATION}$"), start_verification),
        ],
        states={
            ASK_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_nickname)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    application.add_handler(conv)
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