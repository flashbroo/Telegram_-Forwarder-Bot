# bot.py
import logging
import asyncio
from telegram.error import Forbidden
from telegram import Update, BotCommand, BotCommandScopeChat
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import BadRequest
from userbots.auth import start_login, verify_otp, verify_2fa_password, logout
from userbots.manager import restore_logged_in_clients, disconnect_all_clients, ensure_client_started, has_persisted_session

from force_subscribe import is_joined, join_keyboard, get_force_message

import config
import db
import ui
import mappings
from forwarder import process_userbot_queue
import admin_cmds
import subscriptions

from payments_telegram import (
    cmd_buy,
    payment_callback_handler,
)

from db import cleanup_expired_subscriptions, migrate_target_channel_ids


def clear_login_state(context):
    context.user_data.pop("login_step", None)


async def forwarder_loop(app):
    while True:
        try:
            await process_userbot_queue(app)
        except Exception as e:
            print(f"[FORWARDER ERROR] {e}")
        await asyncio.sleep(0.35)


async def send_subscription_reminders(app):
    rows = subscriptions.get_due_expiry_reminders()
    for row in rows:
        days_left = row["days_left"]
        try:
            await app.bot.send_message(
                chat_id=row["user_id"],
                text=(
                    f"Your subscription will expire in {days_left} day(s).\n\n"
                    "Your forwarding and paid features will stop automatically after expiry.\n"
                    "Please recharge or buy a new plan before it ends."
                ),
            )
            subscriptions.mark_reminder_sent(row["user_id"], days_left)
        except Forbidden:
            logger.warning("Could not send expiry reminder to user %s", row["user_id"])
        except Exception:
            logger.exception("Failed to send expiry reminder to user %s", row["user_id"])


async def subscription_reminder_loop(app):
    while True:
        try:
            await send_subscription_reminders(app)
        except Exception:
            logger.exception("Subscription reminder loop failed")
        await asyncio.sleep(3600)


# -----------------------------
# COMMAND MENU (FIX)
# -----------------------------


async def set_commands(app):
    await app.bot.delete_my_commands()

    # -----------------------------
    # USER COMMANDS
    # -----------------------------
    user_commands = [
        BotCommand("start", "Start bot"),
        BotCommand("menu", "Open menu"),
        BotCommand("login", "Login account"),
        BotCommand("logout", "Logout account"),
        BotCommand("status", "Check status"),
        BotCommand("buy", "Buy plan"),
        BotCommand("help", "Help"),
        BotCommand("add_mapping", "Add mapping"),
        BotCommand("add_source", "Add source to target"),
        BotCommand("add_target", "Add target to source"),
        BotCommand("list_mappings", "List mappings"),
        BotCommand("remove_mapping", "Remove mapping"),
        BotCommand("remove_source", "Remove source"),
        BotCommand("remove_target", "Remove target"),
    ]

    # -----------------------------
    # ADMIN COMMANDS
    # -----------------------------
    admin_commands = user_commands + [
        BotCommand("grant_free", "Admin"),
        BotCommand("revoke_free", "Admin"),
        BotCommand("free_on", "Admin"),
        BotCommand("free_off", "Admin"),
        BotCommand("create_plan", "Admin"),
        BotCommand("update_price", "Admin"),
        BotCommand("enable_plan", "Admin"),
        BotCommand("disable_plan", "Admin"),
        BotCommand("list_plans_admin", "Admin"),
        BotCommand("manual_activate", "Admin"),
        BotCommand("list_subscribers", "Admin"),
        BotCommand("list_forwarding_users", "Admin"),
        BotCommand("list_users", "Admin"),
        BotCommand("stats", "Admin"),
        BotCommand("health", "Admin"),
        BotCommand("broadcast", "Admin"),
        BotCommand("export_payments", "Admin"),
        BotCommand("list_force_channels", "Admin"),
        BotCommand("add_force_channel", "Admin"),
        BotCommand("remove_force_channel", "Admin"),
        BotCommand("set_force_message", "Admin"),
        BotCommand("list_admins", "Admin"),
        BotCommand("listadmin", "Admin"),
        BotCommand("addadmin", "Admin"),
        BotCommand("add_admin_id", "Admin"),
        BotCommand("removeadmin", "Admin"),
        BotCommand("remove_admin_id", "Admin"),
    ]

    # -----------------------------
    # SET DEFAULT (ALL USERS)
    # -----------------------------
    await app.bot.set_my_commands(user_commands)

    # -----------------------------
    # SET ADMIN-ONLY COMMANDS
    # -----------------------------
    for admin_id in config.get_admin_ids():
        await app.bot.set_my_commands(
            admin_commands,
            scope=BotCommandScopeChat(chat_id=admin_id)
        )

# -----------------------------
# LOGGING
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled bot error: %s", context.error)

# -----------------------------
# SAFE REPLY (GLOBAL)
# -----------------------------
async def safe_reply(message, text, **kwargs):
    try:
        return await message.reply_text(text, **kwargs)
    except Forbidden:
        print(f"[WARNING] Bot blocked by user {message.chat_id}")
        
# -----------------------------
# ACCESS GUARD
# -----------------------------
async def require_active_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user

    if config.is_admin(user.id):
        return True

    state = subscriptions.get_user_access_state(user.id)
    if state in ("PAID", "FREE_ALL", "FREE_USER"):
        return True

    await update.message.reply_text(
        "🔒 *This feature requires an active plan*\n\n"
        "👉 Use /buy to continue.",
        parse_mode="Markdown"
    )
    return False


# -----------------------------
# ADMIN GUARD
# -----------------------------
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not config.is_admin(user.id):
            if update.message:
                await update.message.reply_text("❌ Admin only.")
            return
        return await func(update, context)
    return wrapper


# -----------------------------
# USER TRACKER
# -----------------------------
def track_user(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user:
            db.ensure_user(user.id)
            db.sync_user_profile(
                user.id,
                username=user.username or "",
                first_name=user.first_name or "",
                last_name=user.last_name or "",
            )
            db.touch_user(user.id)
        return await func(update, context)
    return wrapper


# -----------------------------
# START
# -----------------------------
@track_user
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not config.is_admin(user.id) and config.get_force_sub_channels():
        if not await is_joined(context.bot, user.id):
            await update.message.reply_text(
                get_force_message(),
                reply_markup=join_keyboard()
            )
            return

    await update.message.reply_text(
        "🤖 *Auto Forwarder Bot*\n\n"
        "Automatically forward messages between Telegram chats.\n\n"
        "📖 Use /menu to continue.",
        parse_mode="Markdown"
    )


# -----------------------------
# HELP
# -----------------------------
@track_user
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📘 *How to Use*\n\n"
        "Guide:\nhttps://telegra.ph/How-to-Use-Auto-Forwarder-Bot-User-Guide-01-22\n\n"
        "Contact admin:\nhttps://t.me/flash_bro\n\n"
        "Use /menu to continue",
        parse_mode="Markdown"
    )


# -----------------------------
# FORCE JOIN CALLBACK
# -----------------------------
async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user

    db.ensure_user(user.id)
    db.sync_user_profile(
        user.id,
        username=user.username or "",
        first_name=user.first_name or "",
        last_name=user.last_name or "",
    )
    db.touch_user(user.id)

    if not config.is_admin(user.id) and not await is_joined(context.bot, user.id):
        await q.answer("❗ Join required", show_alert=True)
        try:
            await q.edit_message_text(
                get_force_message(),
                reply_markup=join_keyboard()
            )
        except BadRequest:
            pass
        return

    await q.answer()
    text, markup = ui.build_menu_for_user(user.id)
    await q.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")


# -----------------------------
# STATUS
# -----------------------------
@track_user
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = subscriptions.get_user_access_state(update.effective_user.id)
    if state == "PAID":
        sub = subscriptions.get_user_active_subscription(update.effective_user.id)
        await update.message.reply_text(f"⭐ Active\nExpires: {sub['expiry_ts']}")
    elif state.startswith("FREE"):
        await update.message.reply_text("🎁 Free access enabled")
    else:
        await update.message.reply_text("🔒 No active plan\nUse /buy")


# -----------------------------
# 🔥 FIX LOGIN CHECK (REPLACE)
# -----------------------------
async def is_user_logged_in(uid):
    phone = db.get_user_phone(uid)
    if not phone:
        return False

    try:
        client = await ensure_client_started(uid)
        if await client.is_user_authorized():
            return True

        # Keep the login linked if the session file still exists. This avoids
        # transient connection/restore issues feeling like an automatic logout.
        if has_persisted_session(uid):
            logger.warning("User %s has a persisted session but is not authorized right now; keeping login linked.", uid)
            return True
    except Exception:
        logger.exception("Login check failed for user %s; keeping saved login state.", uid)
        if has_persisted_session(uid):
            return True

    return False


# -----------------------------
# 🔥 FIX MENU
# -----------------------------
@track_user
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not await is_user_logged_in(uid):
        await update.message.reply_text("🔐 Please login first using /login")
        return

    await ui.cmd_menu(update, context)


# -----------------------------
# 🔥 FIX ADD MAPPING
# -----------------------------
async def guarded_add(update, context):
    uid = update.effective_user.id

    if not await is_user_logged_in(uid):
        await update.message.reply_text("🔐 Please login first using /login")
        return

    if await require_active_plan(update, context):
        await mappings.cmd_add_mapping(update, context)


async def guarded_mapping_action(update, context, action):
    uid = update.effective_user.id

    if not await is_user_logged_in(uid):
        await update.message.reply_text("🔐 Please login first using /login")
        return

    if await require_active_plan(update, context):
        await action(update, context)


async def guarded_add_source(update, context):
    await guarded_mapping_action(update, context, mappings.cmd_add_source)


async def guarded_add_target(update, context):
    await guarded_mapping_action(update, context, mappings.cmd_add_target)


async def guarded_remove_source(update, context):
    await guarded_mapping_action(update, context, mappings.cmd_remove_source)


async def guarded_remove_target(update, context):
    await guarded_mapping_action(update, context, mappings.cmd_remove_target)


async def guarded_list_mappings(update, context):
    await guarded_mapping_action(update, context, mappings.cmd_list_mappings)


async def guarded_remove_mapping(update, context):
    await guarded_mapping_action(update, context, mappings.cmd_remove_mapping)


# -----------------------------
# 🔥 FIX LOGIN COMMAND
# -----------------------------
async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # 🔥 CLEAR STUCK STATE
    clear_login_state(context)

    if await is_user_logged_in(uid):
        await update.message.reply_text("✅ Already logged in.")
        return

    context.user_data["login_step"] = "PHONE"
    await update.message.reply_text("📱 Send your phone number in international format, for example: +14155550123")


# -----------------------------
# 🔥 FIX LOGOUT
# -----------------------------
async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not await is_user_logged_in(uid):
        await update.message.reply_text("ℹ️ Not logged in.")
        return

    await logout(uid)
    clear_login_state(context)

    await update.message.reply_text("🚪 Logged out.")

async def login_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("login_step"):
        return await login_message_handler(update, context)


async def private_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if config.is_admin(update.effective_user.id) and context.user_data.get("admin_action"):
        handled = await admin_cmds.handle_admin_input(update, context)
        if handled:
            return
    return await login_router(update, context)
# -----------------------------
# 🔥 FIX LOGIN FLOW
# -----------------------------
async def login_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    step = context.user_data.get("login_step")

    if step == "PHONE":
        result = await start_login(uid, text)

        if result == "PHONE_ALREADY_REGISTERED":
            await update.message.reply_text("❌ Number already linked.")

        elif result == "PHONE_IN_USE":
            await update.message.reply_text("❌ Number in use.")

        elif result == "OTP_SENT":   # ✅ FIXED
            context.user_data["login_step"] = "OTP"
            await update.message.reply_text(
                "🔐 OTP sent.\n\nSend the OTP digits directly.\nYou can also still use the old format like:\n12345abc"
            )

        elif result == "FLOOD":
            await update.message.reply_text(
                "⏳ Too many attempts.\nWait a few minutes."
            )

        elif result == "PHONE_FLOOD":
            await update.message.reply_text(
                "⏳ Telegram is temporarily rate-limiting this phone number.\nPlease wait a while and try /login again."
            )

        elif result == "INVALID_PHONE":
            await update.message.reply_text(
                "❌ Invalid phone number.\nUse international format like +14155550123"
            )

        elif result == "PHONE_BANNED":
            await update.message.reply_text(
                "❌ This phone number cannot be used for Telegram login.\nPlease use another Telegram account."
            )

        elif result == "USERBOT_CONFIG_MISSING":
            await update.message.reply_text(
                "❌ Telegram login is not configured on this bot yet.\nAdmin must set USERBOT_API_ID and USERBOT_API_HASH in .env."
            )

        elif result == "API_ID_INVALID":
            await update.message.reply_text(
                "❌ Telegram API credentials are invalid.\nAdmin should re-check USERBOT_API_ID and USERBOT_API_HASH."
            )

        elif result == "API_ID_PUBLISHED_FLOOD":
            await update.message.reply_text(
                "❌ Telegram has limited this API key right now.\nAdmin should use a different Telegram API app or wait and try again later."
            )

        elif result == "AUTH_RESTART":
            await update.message.reply_text(
                "ℹ️ Telegram asked to restart the login process.\nPlease use /login again."
            )

        elif result == "CONNECT_ERROR":
            await update.message.reply_text(
                "❌ Could not connect to Telegram for OTP delivery.\nPlease try again in a moment."
            )

        else:
            await update.message.reply_text("❌ Failed to send OTP.")

    elif step == "OTP":
        result = await verify_otp(uid, text)

        if result == "LOGGED_IN":
            clear_login_state(context)
            await update.message.reply_text("✅ Login successful!")

        elif result == "INVALID_FORMAT":
            await update.message.reply_text("❌ Send the OTP digits directly, or use the old format like 12345abc")

        elif result == "TOO_MANY_ATTEMPTS":
            clear_login_state(context)
            await update.message.reply_text(
                "❌ Too many attempts.\nUse /login again."
            )

        elif result == "2FA_REQUIRED":
            context.user_data["login_step"] = "PASSWORD"
            await update.message.reply_text(
                "🔒 Telegram Two-Step Verification is enabled.\n\nSend your Telegram password to continue."
            )

        elif result == "INVALID_OTP":
            clear_login_state(context)
            await update.message.reply_text(
                "❌ OTP expired or incorrect.\nUse /login again."
            )

        else:
            await update.message.reply_text("❌ Invalid OTP.")

    elif step == "PASSWORD":
        result = await verify_2fa_password(uid, text)

        if result == "LOGGED_IN":
            clear_login_state(context)
            await update.message.reply_text("✅ Login successful!")

        elif result == "TOO_MANY_ATTEMPTS":
            clear_login_state(context)
            await update.message.reply_text(
                "❌ Too many attempts.\nUse /login again."
            )

        elif result == "INVALID_PASSWORD":
            await update.message.reply_text(
                "❌ Incorrect Telegram password.\nPlease try again."
            )

        else:
            clear_login_state(context)
            await update.message.reply_text("❌ Login failed. Use /login again.")

    else:
        await update.message.reply_text("ℹ️ Use /login first.")

async def startup(app):
    await set_commands(app)
    await restore_logged_in_clients()
    app.create_task(forwarder_loop(app))
    app.create_task(subscription_reminder_loop(app))


async def shutdown(app):
    await disconnect_all_clients()


    



# -----------------------------
# MAIN
# -----------------------------
def main():
    config.validate_config()
    db.init_db()
    cleanup_expired_subscriptions()
    migrate_target_channel_ids()
    asyncio.set_event_loop(asyncio.new_event_loop())

    
    app = Application.builder().token(config.BOT_TOKEN).job_queue(None).build()

    app.post_init = startup
    app.post_shutdown = shutdown

    # app.add_handler(CallbackQueryHandler(mappings.mapping_flow_callback, pattern="^map_"))
    app.add_handler(
        CallbackQueryHandler(
            mappings.mapping_flow_callback,
            pattern="^(pick_|map_)"
        )
    )

    # FORCE JOIN
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))

    # UI
    app.add_handler(CallbackQueryHandler(
        ui.admin_callback_handler,
        pattern="^(admin_|free_mode_|info_|view_plans|my_mappings|how_to_use|back_to_menu|menu_)"
    ))

    # PAYMENTS
    app.add_handler(CallbackQueryHandler(payment_callback_handler, pattern="^(razorpay_|verify_|market_)"))



    
    app.add_handler(CallbackQueryHandler(mappings.mapping_manage_callback, pattern="^(mm_|mp_)"))

    # LOGIN
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("logout", cmd_logout))

        # MAPPINGS
    app.add_handler(CommandHandler("add_mapping", guarded_add))
    app.add_handler(CommandHandler("add_source", guarded_add_source))
    app.add_handler(CommandHandler("add_target", guarded_add_target))
    app.add_handler(CommandHandler("list_mappings", guarded_list_mappings))
    app.add_handler(CommandHandler("remove_mapping", guarded_remove_mapping))
    app.add_handler(CommandHandler("remove_source", guarded_remove_source))
    app.add_handler(CommandHandler("remove_target", guarded_remove_target))
       # USER
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("buy", cmd_buy))

     # ADMIN
    app.add_handler(CommandHandler("grant_free", admin_only(admin_cmds.cmd_grant_free)))
    app.add_handler(CommandHandler("revoke_free", admin_only(admin_cmds.cmd_revoke_free)))
    app.add_handler(CommandHandler("free_on", admin_only(admin_cmds.cmd_enable_free_mode)))
    app.add_handler(CommandHandler("free_off", admin_only(admin_cmds.cmd_disable_free_mode)))
    app.add_handler(CommandHandler("create_plan", admin_only(admin_cmds.cmd_create_plan)))
    app.add_handler(CommandHandler("update_price", admin_only(admin_cmds.cmd_update_price)))
    app.add_handler(CommandHandler("enable_plan", admin_only(admin_cmds.cmd_enable_plan)))
    app.add_handler(CommandHandler("disable_plan", admin_only(admin_cmds.cmd_disable_plan)))
    app.add_handler(CommandHandler("list_plans_admin", admin_only(admin_cmds.cmd_list_plans_admin)))
    app.add_handler(CommandHandler("manual_activate", admin_only(admin_cmds.cmd_manual_activate)))
    app.add_handler(CommandHandler("list_subscribers", admin_only(admin_cmds.cmd_list_subscribers)))
    app.add_handler(CommandHandler("list_forwarding_users", admin_only(admin_cmds.cmd_list_forwarding_users)))
    app.add_handler(CommandHandler("list_users", admin_only(admin_cmds.cmd_list_users)))
    app.add_handler(CommandHandler("stats", admin_only(admin_cmds.cmd_stats)))
    app.add_handler(CommandHandler("health", admin_only(admin_cmds.cmd_health)))
    app.add_handler(CommandHandler("broadcast", admin_only(admin_cmds.cmd_broadcast)))
    app.add_handler(CommandHandler("export_payments", admin_only(admin_cmds.cmd_export_payments)))
    app.add_handler(CommandHandler("list_force_channels", admin_only(admin_cmds.cmd_list_force_channels)))
    app.add_handler(CommandHandler("add_force_channel", admin_only(admin_cmds.cmd_add_force_channel)))
    app.add_handler(CommandHandler("remove_force_channel", admin_only(admin_cmds.cmd_remove_force_channel)))
    app.add_handler(CommandHandler("set_force_message", admin_only(admin_cmds.cmd_set_force_message)))
    app.add_handler(CommandHandler("list_admins", admin_only(admin_cmds.cmd_list_admins)))
    app.add_handler(CommandHandler("listadmin", admin_only(admin_cmds.cmd_list_admins)))
    app.add_handler(CommandHandler("addadmin", admin_only(admin_cmds.cmd_add_admin_id)))
    app.add_handler(CommandHandler("add_admin_id", admin_only(admin_cmds.cmd_add_admin_id)))
    app.add_handler(CommandHandler("removeadmin", admin_only(admin_cmds.cmd_remove_admin_id)))
    app.add_handler(CommandHandler("remove_admin_id", admin_only(admin_cmds.cmd_remove_admin_id)))
    
      # FORWARDED POSTS
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.FORWARDED,
            mappings.handle_forwarded_channel
        )
    )


    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (~filters.COMMAND),
            private_message_router
        )
    )
    app.add_error_handler(on_error)
    print("🚀 Bot running…")
    app.run_polling()


if __name__ == "__main__":
    main()
