import json

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

import config
import subscriptions
from utils import now_iso
from db import get_setting, set_setting, fetchone, fetchall, execute


# =====================================================
# ADMIN AUDIT LOG
# =====================================================

def write_admin_audit(admin_id: int, action: str, payload: str = ""):
    execute(
        """
        INSERT INTO admin_logs (admin_id, action, payload, ts)
        VALUES (?,?,?,?)
        """,
        (admin_id, action, payload, now_iso())
    )


# =====================================================
# FREE ACCESS (PER USER)
# =====================================================

async def cmd_grant_free(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        return await update.message.reply_text("Usage: /grant_free <user_id>")

    try:
        uid = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("User ID must be a number.")

    execute(
        """
        INSERT INTO users (user_id, free_access, created_at, updated_at)
        VALUES (?, 1, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            free_access=1,
            updated_at=excluded.updated_at
        """,
        (uid, now_iso(), now_iso())
    )

    write_admin_audit(update.effective_user.id, "GRANT_FREE", f"user_id={uid}")
    await update.message.reply_text(f"✅ Free access granted to user {uid}")


async def cmd_revoke_free(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        return await update.message.reply_text("Usage: /revoke_free <user_id>")

    try:
        uid = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("User ID must be a number.")

    execute(
        "UPDATE users SET free_access=0, updated_at=? WHERE user_id=?",
        (now_iso(), uid)
    )

    write_admin_audit(update.effective_user.id, "REVOKE_FREE", f"user_id={uid}")
    await update.message.reply_text(f"❌ Free access revoked for user {uid}")


# =====================================================
# GLOBAL FREE MODE
# =====================================================

async def cmd_enable_free_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting("FREE_MODE", "1")
    write_admin_audit(update.effective_user.id, "FREE_MODE_ON")
    await update.message.reply_text("🔓 Free Mode ENABLED\nBot is now free for all users.")


async def cmd_disable_free_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_setting("FREE_MODE", "0")
    write_admin_audit(update.effective_user.id, "FREE_MODE_OFF")
    await update.message.reply_text("🔒 Free Mode DISABLED\nSubscription is now required.")


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _normalize_force_channel(channel: str) -> str:
    channel = channel.strip()
    if not channel:
        return ""
    return channel if channel.startswith("@") else f"@{channel}"


def get_managed_force_channels() -> list[str]:
    raw = get_setting("FORCE_SUB_CHANNELS", ",".join(config.FORCE_SUB_CHANNELS))
    return [_normalize_force_channel(ch) for ch in _split_csv(raw)]


def save_managed_force_channels(channels: list[str]):
    cleaned = []
    seen = set()
    for channel in channels:
        normalized = _normalize_force_channel(channel)
        if normalized and normalized not in seen:
            seen.add(normalized)
            cleaned.append(normalized)
    set_setting("FORCE_SUB_CHANNELS", ",".join(cleaned))


def get_managed_admin_ids() -> list[int]:
    stored = get_setting("ADMIN_IDS", "")
    ids = []
    seen = set()
    for admin_id in config.get_admin_ids() + [int(x) for x in _split_csv(stored) if x.isdigit()]:
        if admin_id not in seen:
            seen.add(admin_id)
            ids.append(admin_id)
    return ids


def save_extra_admin_ids(admin_ids: list[int]):
    env_admins = set(config.ADMIN_IDS)
    dynamic_ids = [str(admin_id) for admin_id in admin_ids if admin_id not in env_admins]
    set_setting("ADMIN_IDS", ",".join(dynamic_ids))


def get_admin_directory() -> dict[int, dict[str, str]]:
    raw = get_setting("ADMIN_DIRECTORY", "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    directory: dict[int, dict[str, str]] = {}
    for key, value in data.items():
        try:
            admin_id = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            directory[admin_id] = {
                "username": (value.get("username") or "").strip(),
                "display_name": (value.get("display_name") or "").strip(),
            }
    return directory


def save_admin_directory(directory: dict[int, dict[str, str]]):
    payload = {
        str(admin_id): {
            "username": (meta.get("username") or "").strip(),
            "display_name": (meta.get("display_name") or "").strip(),
        }
        for admin_id, meta in directory.items()
    }
    set_setting("ADMIN_DIRECTORY", json.dumps(payload, ensure_ascii=True, sort_keys=True))


def admin_label(admin_id: int, directory: dict[int, dict[str, str]] | None = None) -> str:
    if directory is None:
        directory = get_admin_directory()
    meta = directory.get(admin_id, {})
    username = (meta.get("username") or "").strip()
    display_name = (meta.get("display_name") or "").strip()

    if username and display_name:
        return f"{display_name} (@{username})"
    if username:
        return f"@{username}"
    if display_name:
        return display_name
    return str(admin_id)


async def refresh_admin_profile(
    context: ContextTypes.DEFAULT_TYPE,
    admin_id: int,
    known_username: str = "",
):
    directory = get_admin_directory()
    meta = directory.get(admin_id, {})

    username = known_username.lstrip("@").strip() if known_username else (meta.get("username") or "").strip()
    display_name = (meta.get("display_name") or "").strip()

    try:
        chat = await context.bot.get_chat(admin_id)
        username = (chat.username or username or "").strip()
        display_name = " ".join(part for part in [chat.first_name, chat.last_name] if part).strip() or (chat.title or display_name)
    except TelegramError:
        pass

    directory[admin_id] = {
        "username": username,
        "display_name": display_name,
    }
    save_admin_directory(directory)
    return admin_id, directory[admin_id]


async def resolve_admin_reference(
    context: ContextTypes.DEFAULT_TYPE,
    raw_value: str,
) -> tuple[int | None, dict[str, str] | None, str | None]:
    value = (raw_value or "").strip()
    if not value:
        return None, None, "Send a Telegram username like @username or a numeric user ID."

    chat_ref = value
    if value.isdigit():
        chat_ref = int(value)
    elif not value.startswith("@"):
        chat_ref = f"@{value}"

    directory = get_admin_directory()

    if isinstance(chat_ref, str) and chat_ref.startswith("@"):
        wanted_username = chat_ref.lstrip("@").lower()
        for admin_id, meta in directory.items():
            if (meta.get("username") or "").strip().lower() == wanted_username:
                return admin_id, meta, None

    try:
        chat = await context.bot.get_chat(chat_ref)
    except BadRequest:
        return None, None, "I could not find that Telegram user. Ask them to set a public username or send their numeric user ID."
    except TelegramError:
        return None, None, "I could not verify that Telegram user right now. Please try again."

    if getattr(chat, "type", None) not in ("private", "bot"):
        return None, None, "That username does not belong to a Telegram user."

    display_name = " ".join(part for part in [chat.first_name, chat.last_name] if part).strip() or "Unknown User"
    metadata = {
        "username": (chat.username or "").strip(),
        "display_name": display_name,
    }
    return chat.id, metadata, None


def get_force_message_text() -> str:
    return get_setting("FORCE_SUB_MESSAGE", config.FORCE_SUB_MESSAGE)


def admin_settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Force Channels", callback_data="admin_force_channels")],
        [InlineKeyboardButton("Add Force Channel", callback_data="admin_force_add")],
        [InlineKeyboardButton("Remove Force Channel", callback_data="admin_force_remove")],
        [InlineKeyboardButton("Set Force Message", callback_data="admin_force_message")],
        [InlineKeyboardButton("List Admins", callback_data="admin_admins_list")],
        [InlineKeyboardButton("Add Admin", callback_data="admin_admins_add")],
        [InlineKeyboardButton("Remove Admin", callback_data="admin_admins_remove")],
        [InlineKeyboardButton("Back to Admin Panel", callback_data="admin_panel")],
    ])


async def cmd_list_force_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = get_managed_force_channels()
    if not channels:
        await update.message.reply_text("No force-subscribe channels are set.")
        return
    await update.message.reply_text("Force-subscribe channels:\n" + "\n".join(channels))


async def cmd_add_force_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /add_force_channel @channelusername")
        return
    channel = _normalize_force_channel(context.args[0])
    channels = get_managed_force_channels()
    if channel in channels:
        await update.message.reply_text("That force-subscribe channel already exists.")
        return
    channels.append(channel)
    save_managed_force_channels(channels)
    write_admin_audit(update.effective_user.id, "ADD_FORCE_CHANNEL", channel)
    await update.message.reply_text(f"Added force-subscribe channel: {channel}")


async def cmd_remove_force_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /remove_force_channel @channelusername")
        return
    channel = _normalize_force_channel(context.args[0])
    channels = [item for item in get_managed_force_channels() if item != channel]
    save_managed_force_channels(channels)
    write_admin_audit(update.effective_user.id, "REMOVE_FORCE_CHANNEL", channel)
    await update.message.reply_text(f"Removed force-subscribe channel: {channel}")


async def cmd_set_force_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /set_force_message <text>")
        return
    message = " ".join(context.args).strip()
    set_setting("FORCE_SUB_MESSAGE", message)
    write_admin_audit(update.effective_user.id, "SET_FORCE_MESSAGE", message)
    await update.message.reply_text("Force-subscribe message updated.")


async def cmd_list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_ids = get_managed_admin_ids()
    directory = get_admin_directory()
    labels = []
    for admin_id in admin_ids:
        _, meta = await refresh_admin_profile(context, admin_id, directory.get(admin_id, {}).get("username", ""))
        directory[admin_id] = meta
        labels.append(f"- {admin_label(admin_id, directory)}")
    await update.message.reply_text("Admins:\n" + "\n".join(labels))


async def cmd_add_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /add_admin_id <@username_or_user_id>")
        return
    admin_id, metadata, error = await resolve_admin_reference(context, context.args[0])
    if error or admin_id is None or metadata is None:
        await update.message.reply_text(error or "Could not resolve that Telegram user.")
        return
    admin_ids = get_managed_admin_ids()
    if admin_id in admin_ids:
        await update.message.reply_text("That admin already exists.")
        return
    admin_ids.append(admin_id)
    save_extra_admin_ids(admin_ids)
    directory = get_admin_directory()
    directory[admin_id] = metadata
    save_admin_directory(directory)
    write_admin_audit(update.effective_user.id, "ADD_ADMIN", str(admin_id))
    await update.message.reply_text(f"Added admin: {admin_label(admin_id, directory)}")


async def cmd_remove_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /remove_admin_id <@username_or_user_id>")
        return
    admin_id, metadata, error = await resolve_admin_reference(context, context.args[0])
    if error or admin_id is None:
        await update.message.reply_text(error or "Could not resolve that Telegram user.")
        return
    admin_ids = [item for item in get_managed_admin_ids() if item != admin_id]
    if not admin_ids:
        await update.message.reply_text("At least one admin must remain.")
        return
    save_extra_admin_ids(admin_ids)
    directory = get_admin_directory()
    directory.pop(admin_id, None)
    save_admin_directory(directory)
    write_admin_audit(update.effective_user.id, "REMOVE_ADMIN", str(admin_id))
    label = admin_label(admin_id, {admin_id: metadata or {}}) if metadata else str(admin_id)
    await update.message.reply_text(f"Removed admin: {label}")


async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    action = context.user_data.get("admin_action")
    if not action:
        return False

    text = (update.message.text or "").strip()
    uid = update.effective_user.id

    if action == "ADD_FORCE_CHANNEL":
        channel = _normalize_force_channel(text)
        if not channel:
            await update.message.reply_text("Send a valid channel username like @channelname.")
            return True
        channels = get_managed_force_channels()
        if channel not in channels:
            channels.append(channel)
            save_managed_force_channels(channels)
            write_admin_audit(uid, "ADD_FORCE_CHANNEL", channel)
        context.user_data.pop("admin_action", None)
        await update.message.reply_text(f"Added force-subscribe channel: {channel}")
        return True

    if action == "REMOVE_FORCE_CHANNEL":
        channel = _normalize_force_channel(text)
        channels = [item for item in get_managed_force_channels() if item != channel]
        save_managed_force_channels(channels)
        context.user_data.pop("admin_action", None)
        write_admin_audit(uid, "REMOVE_FORCE_CHANNEL", channel)
        await update.message.reply_text(f"Removed force-subscribe channel: {channel}")
        return True

    if action == "SET_FORCE_MESSAGE":
        set_setting("FORCE_SUB_MESSAGE", text)
        context.user_data.pop("admin_action", None)
        write_admin_audit(uid, "SET_FORCE_MESSAGE", text)
        await update.message.reply_text("Force-subscribe message updated.")
        return True

    if action == "ADD_ADMIN":
        admin_id, metadata, error = await resolve_admin_reference(context, text)
        if error or admin_id is None or metadata is None:
            await update.message.reply_text(error or "Send a Telegram username like @username or a numeric user ID.")
            return True
        admin_ids = get_managed_admin_ids()
        if admin_id not in admin_ids:
            admin_ids.append(admin_id)
            save_extra_admin_ids(admin_ids)
            directory = get_admin_directory()
            directory[admin_id] = metadata
            save_admin_directory(directory)
            write_admin_audit(uid, "ADD_ADMIN", str(admin_id))
        context.user_data.pop("admin_action", None)
        await update.message.reply_text(f"Added admin: {admin_label(admin_id)}")
        return True

    if action == "REMOVE_ADMIN":
        admin_id, metadata, error = await resolve_admin_reference(context, text)
        if error or admin_id is None:
            await update.message.reply_text(error or "Send a Telegram username like @username or a numeric user ID.")
            return True
        admin_ids = [item for item in get_managed_admin_ids() if item != admin_id]
        if not admin_ids:
            await update.message.reply_text("At least one admin must remain.")
            return True
        save_extra_admin_ids(admin_ids)
        directory = get_admin_directory()
        directory.pop(admin_id, None)
        save_admin_directory(directory)
        context.user_data.pop("admin_action", None)
        write_admin_audit(uid, "REMOVE_ADMIN", str(admin_id))
        label = admin_label(admin_id, {admin_id: metadata or {}}) if metadata else str(admin_id)
        await update.message.reply_text(f"Removed admin: {label}")
        return True

    return False


# =====================================================
# PLAN MANAGEMENT
# =====================================================

async def cmd_create_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /create_plan <plan_key> <price> <currency> <duration_days> <name...>
    """
    if len(context.args) < 5:
        return await update.message.reply_text(
            "Usage: /create_plan <plan_key> <price> <currency> <duration_days> <name>"
            " Example: pro 199 INR 30 Pro Plan"
        )

    plan_key = context.args[0]

    try:
        price = float(context.args[1])
        currency = context.args[2].upper()
        duration = int(context.args[3])
    except ValueError:
        return await update.message.reply_text("Invalid price or duration.")

    name = " ".join(context.args[4:])

    now = now_iso()
    audience = "IN" if currency == "INR" else "INTL"
    execute(
        """
        INSERT INTO plans
        (plan_key, name, price, currency, duration_days, audience, provider, is_active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'razorpay', 1, ?, ?)
        ON CONFLICT(plan_key) DO UPDATE SET
            name=excluded.name,
            price=excluded.price,
            currency=excluded.currency,
            duration_days=excluded.duration_days,
            audience=excluded.audience,
            provider=excluded.provider,
            is_active=1,
            updated_at=excluded.updated_at
        """,
        (plan_key, name, price, currency, duration, audience, now, now)
    )

    write_admin_audit(update.effective_user.id, "CREATE_PLAN", plan_key)
    await update.message.reply_text(
        f"✅ Plan `{plan_key}` created/updated\nDuration: {duration} days",
        parse_mode="Markdown"
    )


async def cmd_list_plans_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = fetchall("SELECT * FROM plans ORDER BY price ASC")
    if not rows:
        return await update.message.reply_text("No plans found.")

    msg = "📦 *All Plans*\n\n"
    for p in rows:
        msg += (
            f"*{p['name']}* (`{p['plan_key']}`)\n"
            f"Price: {p['price']} {p['currency']}\n"
            f"Duration: {p['duration_days']} days\n"
            f"Audience: {p['audience']}\n"
            f"Provider: {p['provider']}\n"
            f"Active: {bool(p['is_active'])}\n\n"
        )

    await update.message.reply_text(msg, parse_mode="Markdown")


# =====================================================
# MANUAL SUBSCRIPTION (ADMIN)
# =====================================================

async def cmd_manual_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /manual_activate <user_id> <plan_key> [days]
    """

    if len(context.args) < 2:
        return await update.message.reply_text(
            "Usage: /manual_activate <user_id> <plan_key> [days]"
        )

    try:
        uid = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("User ID must be numeric.")

    plan_key = context.args[1]
    custom_days = None

    if len(context.args) == 3:
        try:
            custom_days = int(context.args[2])
        except ValueError:
            return await update.message.reply_text("Days must be numeric.")

    ok = subscriptions.manual_activate(uid, plan_key, custom_days)

    if not ok:
        return await update.message.reply_text("❌ Activation failed. Invalid plan.")

    write_admin_audit(
        update.effective_user.id,
        "MANUAL_ACTIVATE",
        f"user={uid} plan={plan_key} days={custom_days}"
    )

    await update.message.reply_text(
        f"✅ User `{uid}` activated for `{plan_key}`",
        parse_mode="Markdown"
    )


# =====================================================
# BUSINESS ANALYTICS
# =====================================================

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_users = fetchone("SELECT COUNT(*) as c FROM users")["c"]
    free_users = fetchone("SELECT COUNT(*) as c FROM users WHERE free_access=1")["c"]
    paid_users = fetchone("SELECT COUNT(DISTINCT user_id) as c FROM subscriptions WHERE status='active'")["c"]
    forwarding_users = fetchone("SELECT COUNT(DISTINCT user_id) as c FROM forward_logs")["c"]

    msg = (
        "📊 *Bot Business Stats*\n\n"
        f"👥 Total Registered Users: {total_users}\n"
        f"🎁 Free Users: {free_users}\n"
        f"💳 Paid Users: {paid_users}\n"
        f"🚀 Forwarding Users: {forwarding_users}\n"
    )

    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = fetchall("SELECT * FROM users ORDER BY updated_at DESC")
    if not rows:
        return await update.message.reply_text("No users found.")

    msg = "👥 *Registered Users*\n\n"
    for r in rows:
        username = (r.get("username") or "").strip()
        first_name = (r.get("first_name") or "").strip()
        last_name = (r.get("last_name") or "").strip()

        if not username and not first_name and not last_name:
            try:
                chat = await context.bot.get_chat(r["user_id"])
                username = (chat.username or "").strip()
                first_name = (chat.first_name or "").strip()
                last_name = (chat.last_name or "").strip()
                execute(
                    "UPDATE users SET username=?, first_name=?, last_name=?, updated_at=? WHERE user_id=?",
                    (username or None, first_name or None, last_name or None, now_iso(), r["user_id"]),
                )
            except TelegramError:
                pass

        display_name = " ".join(part for part in [first_name, last_name] if part).strip()
        identity = f"@{username}" if username else (display_name or str(r["user_id"]))
        msg += (
            f"{identity} | id={r['user_id']} | "
            f"free={bool(r['free_access'])} | "
            f"cmds={r['total_commands']} | "
            f"fwds={r['total_forwards']}\n"
        )

    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_list_forwarding_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = fetchall("SELECT DISTINCT user_id FROM forward_logs ORDER BY ts DESC")

    if not rows:
        return await update.message.reply_text("No forwarding users yet.")

    msg = "🚀 *Forwarding Users*\n\n"
    for r in rows:
        msg += f"{r['user_id']}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")
# =====================================================
# LIST ACTIVE SUBSCRIBERS (PAID USERS)
# =====================================================

async def cmd_list_subscribers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = fetchall(
        "SELECT * FROM subscriptions WHERE status='active' ORDER BY expiry_ts DESC"
    )

    if not rows:
        return await update.message.reply_text("No active subscribers.")

    msg = "⭐ *Active Subscribers*\n\n"

    for r in rows:
        msg += (
            f"User: `{r['user_id']}`\n"
            f"Plan: `{r['plan_key']}`\n"
            f"Expires: `{r['expiry_ts']}`\n\n"
        )

    await update.message.reply_text(msg, parse_mode="Markdown")
# =====================================================
# EXPORT PAYMENTS CSV
# =====================================================

async def cmd_export_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = fetchall("SELECT * FROM payments ORDER BY ts DESC")

    if not rows:
        return await update.message.reply_text("No payments found.")

    from utils import export_payments_csv

    buffer = export_payments_csv(rows)

    await update.message.reply_document(
        buffer,
        filename="payments.csv",
        caption="💳 All Payments Export"
    )


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = fetchone("SELECT COUNT(*) AS c FROM users")["c"]
    plans = fetchone("SELECT COUNT(*) AS c FROM plans WHERE is_active=1")["c"]
    mappings_count = fetchone("SELECT COUNT(*) AS c FROM mappings WHERE active=1")["c"]
    pending = fetchone("SELECT COUNT(*) AS c FROM incoming_messages WHERE status='pending'")["c"]
    failed = fetchone("SELECT COUNT(*) AS c FROM incoming_messages WHERE status='failed'")["c"]

    msg = (
        "🩺 *Health Check*\n\n"
        f"Users: {users}\n"
        f"Active plans: {plans}\n"
        f"Active mappings: {mappings_count}\n"
        f"Pending forwards: {pending}\n"
        f"Failed forwards: {failed}\n"
        f"Time: `{now_iso()}`"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
# =====================================================
# BROADCAST MESSAGE TO ALL USERS
# =====================================================

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Usage: /broadcast <message>")

    text = " ".join(context.args)

    users = fetchall("SELECT user_id FROM users")

    if not users:
        return await update.message.reply_text("No users found.")

    sent = 0
    failed = 0

    for u in users:
        try:
            await context.bot.send_message(u["user_id"], text)
            sent += 1
        except Exception:
            failed += 1

    write_admin_audit(
        update.effective_user.id,
        "BROADCAST",
        f"sent={sent} failed={failed}"
    )

    await update.message.reply_text(
        f"📢 Broadcast finished\n\n✅ Sent: {sent}\n❌ Failed: {failed}"
    )
# =====================================================
# UPDATE PLAN PRICE
# =====================================================

async def cmd_update_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        return await update.message.reply_text("Usage: /update_price <plan_key> <new_price>")

    plan_key = context.args[0]

    try:
        new_price = float(context.args[1])
    except ValueError:
        return await update.message.reply_text("Price must be a number.")

    plan = fetchone("SELECT plan_key FROM plans WHERE plan_key=?", (plan_key,))
    if not plan:
        return await update.message.reply_text("❌ Plan not found.")

    execute(
        "UPDATE plans SET price=?, updated_at=? WHERE plan_key=?",
        (new_price, now_iso(), plan_key)
    )

    write_admin_audit(update.effective_user.id, "UPDATE_PRICE", f"{plan_key} → {new_price}")

    await update.message.reply_text(
        f"💰 Price updated for `{plan_key}` → {new_price}",
        parse_mode="Markdown"
    )
# =====================================================
# ENABLE / DISABLE PLAN
# =====================================================

async def cmd_enable_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        return await update.message.reply_text("Usage: /enable_plan <plan_key>" 
        "\n Example: 'pro' should be in small only"
                                               )

    plan_key = context.args[0]

    execute(
        "UPDATE plans SET is_active=1, updated_at=? WHERE plan_key=?",
        (now_iso(), plan_key)
    )

    write_admin_audit(update.effective_user.id, "ENABLE_PLAN", plan_key)

    await update.message.reply_text(
        f"✅ Plan `{plan_key}` enabled",
        parse_mode="Markdown"
    )


async def cmd_disable_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        return await update.message.reply_text("Usage: /disable_plan <plan_key>")

    plan_key = context.args[0]

    execute(
        "UPDATE plans SET is_active=0, updated_at=? WHERE plan_key=?",
        (now_iso(), plan_key)
    )

    write_admin_audit(update.effective_user.id, "DISABLE_PLAN", plan_key)

    await update.message.reply_text(
        f"🚫 Plan `{plan_key}` disabled",
        parse_mode="Markdown"
    )
