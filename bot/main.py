from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select, update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
from .models import Admin, AdminActionLog, AdminRole, EventQueue, Verification, VerificationStatus
from .services.purchases import create_purchase_request
from .services.queue import enqueue_event
from .services.security import (
    approve_admin_token,
    consume_admin_token,
    create_admin_token,
    enforce_role,
    generate_token,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

ASK_NICK = 0


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Verification", callback_data="verification")],
            [InlineKeyboardButton("Shop", callback_data="shop")],
            [InlineKeyboardButton("Profile", callback_data="profile")],
            [InlineKeyboardButton("Support", callback_data="support")],
            [InlineKeyboardButton("Admin", callback_data="admin")],
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message or update.effective_message
    if not message:
        return
    await message.reply_text(
        "Welcome to Bigbob! Choose an option below.",
        reply_markup=main_menu_keyboard(),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "verification":
        await query.edit_message_text("Please send your Roblox nickname.")
        return ASK_NICK
    if data == "shop":
        await query.edit_message_text("Shop is under construction. Use /start to return.")
    elif data == "profile":
        await query.edit_message_text("Profile view coming soon. We'll show balances and referral link.")
    elif data == "support":
        await query.edit_message_text("Support: Contact @BigbobSupport or use /start to go back.")
    elif data == "admin":
        await query.edit_message_text("Send /admin_login <token> to begin onboarding.")
    return ConversationHandler.END


async def ask_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nickname = update.message.text.strip()
    code = f"BB-{secrets.token_hex(3)}"
    expires_at = datetime.utcnow() + timedelta(seconds=settings.verification_code_ttl_seconds)
    async with session_scope() as session:
        await session.execute(
            update(Verification)
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
        entry_points=[CallbackQueryHandler(handle_callback)],
        states={
            ASK_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_nickname)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv)
    application.add_handler(CommandHandler("admin_login", admin_login))
    application.add_handler(CommandHandler("admin_init", admin_init))
    application.add_handler(CommandHandler("admin_menu", admin_menu))
    application.add_handler(CommandHandler("admin_token", admin_token))
    application.add_handler(CommandHandler("admin_approve", admin_approve))
    application.add_handler(CommandHandler("admin_logs", admin_logs))
    application.add_handler(CommandHandler("buy", purchase))
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