from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import config
import subscriptions
from db import get_active_plans, set_setting
from payments_telegram import _market_title, _plan_market


def get_days_left(expiry_ts: str) -> int | None:
    try:
        expiry = datetime.fromisoformat(expiry_ts)
        delta = expiry - datetime.utcnow()
        return max(delta.days, 0)
    except Exception:
        return None


def format_price(price: float, currency: str) -> str:
    if currency.upper() == "INR":
        return f"Rs {int(price)}"
    return f"{price} {currency.upper()}"


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text, markup = build_menu_for_user(user.id)
    await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


def build_menu_for_user(user_id: int):
    state = subscriptions.get_user_access_state(user_id)
    kb = []

    if config.is_admin(user_id):
        kb.append([InlineKeyboardButton("Admin Panel", callback_data="admin_panel")])

    kb.append([InlineKeyboardButton("How to Use Bot", callback_data="how_to_use")])

    if state in ("FREE_ALL", "FREE_USER", "PAID"):
        kb.append([InlineKeyboardButton("Add Mapping", callback_data="menu_add_mapping")])
        kb.append([
            InlineKeyboardButton("Add Source", callback_data="menu_add_source"),
            InlineKeyboardButton("Add Target", callback_data="menu_add_target"),
        ])
        kb.append([InlineKeyboardButton("My Mappings", callback_data="my_mappings")])
        kb.append([
            InlineKeyboardButton("Remove Source", callback_data="menu_remove_source"),
            InlineKeyboardButton("Remove Target", callback_data="menu_remove_target"),
        ])
        kb.append([InlineKeyboardButton("Remove Mapping", callback_data="menu_remove_mapping")])

    if state == "PAID":
        kb.append([InlineKeyboardButton("Subscription Status", callback_data="info_paid")])
        kb.append([InlineKeyboardButton("Extend Plan", callback_data="view_plans")])

    if state == "BLOCKED":
        kb.append([InlineKeyboardButton("Buy Plan", callback_data="view_plans")])

    return (
        "*Auto Forwarder Bot*\n\nChoose an option:",
        InlineKeyboardMarkup(kb),
    )


async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data
    user = q.from_user

    if data == "how_to_use":
        text = (
            "How to Use Auto Forwarder Bot\n\n"
            "This bot forwards new posts from one channel to another automatically.\n\n"
            "Steps:\n"
            "1. Log in your Telegram account with /login\n"
            "2. Tap Add Mapping from the menu\n"
            "3. Pick source channels from your top pinned channels\n"
            "4. Pick target channels from your top pinned channels\n"
            "5. Confirm the mapping\n\n"
            "Notes:\n"
            "- You can add multiple source channels\n"
            "- You can add multiple target channels\n"
            "- You can add or remove sources and targets later from the menu\n\n"
            "Commands:\n"
            "/list_mappings - view mappings\n"
            "/remove_mapping - delete a mapping"
        )
        await q.edit_message_text(text)
        return

    if data == "admin_panel":
        if not config.is_admin(user.id):
            await q.edit_message_text("Admin only.")
            return

        kb = [
            [
                InlineKeyboardButton("Enable Free Mode", callback_data="free_mode_on"),
                InlineKeyboardButton("Disable Free Mode", callback_data="free_mode_off"),
            ],
            [InlineKeyboardButton("Manage Force Sub", callback_data="admin_manage_force")],
            [InlineKeyboardButton("Manage Admins", callback_data="admin_manage_admins")],
            [InlineKeyboardButton("Back to Menu", callback_data="back_to_menu")],
        ]

        await q.edit_message_text(
            "*Admin Panel*\n\nControl bot access:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown",
        )
        return

    if data == "free_mode_on" and config.is_admin(user.id):
        set_setting("FREE_MODE", "1")
        await q.edit_message_text("*Free Mode ENABLED*", parse_mode="Markdown")
        return

    if data == "free_mode_off" and config.is_admin(user.id):
        set_setting("FREE_MODE", "0")
        await q.edit_message_text("*Free Mode DISABLED*", parse_mode="Markdown")
        return

    if data == "admin_manage_force" and config.is_admin(user.id):
        import admin_cmds

        await q.edit_message_text(
            "*Force Subscribe Settings*\n\nManage required join channels and message.",
            reply_markup=admin_cmds.admin_settings_keyboard(),
            parse_mode="Markdown",
        )
        return

    if data == "admin_manage_admins" and config.is_admin(user.id):
        import admin_cmds

        await q.edit_message_text(
            "*Admin Settings*\n\nManage bot admins from here.",
            reply_markup=admin_cmds.admin_settings_keyboard(),
            parse_mode="Markdown",
        )
        return

    if data == "admin_force_channels" and config.is_admin(user.id):
        import admin_cmds

        channels = admin_cmds.get_managed_force_channels()
        message = "No force-subscribe channels are set." if not channels else "Force-subscribe channels:\n" + "\n".join(channels)
        message += f"\n\nCurrent message:\n{admin_cmds.get_force_message_text()}"
        await q.edit_message_text(message, reply_markup=admin_cmds.admin_settings_keyboard())
        return

    if data == "admin_force_add" and config.is_admin(user.id):
        context.user_data["admin_action"] = "ADD_FORCE_CHANNEL"
        await q.edit_message_text("Send the channel username to add, like `@channelname`.", parse_mode="Markdown")
        return

    if data == "admin_force_remove" and config.is_admin(user.id):
        context.user_data["admin_action"] = "REMOVE_FORCE_CHANNEL"
        await q.edit_message_text("Send the channel username to remove, like `@channelname`.", parse_mode="Markdown")
        return

    if data == "admin_force_message" and config.is_admin(user.id):
        context.user_data["admin_action"] = "SET_FORCE_MESSAGE"
        await q.edit_message_text("Send the new force-subscribe message text.")
        return

    if data == "admin_admins_list" and config.is_admin(user.id):
        import admin_cmds

        admin_ids = admin_cmds.get_managed_admin_ids()
        labels = []
        directory = admin_cmds.get_admin_directory()
        for admin_id in admin_ids:
            _, meta = await admin_cmds.refresh_admin_profile(context, admin_id, directory.get(admin_id, {}).get("username", ""))
            directory[admin_id] = meta
            labels.append(f"- {admin_cmds.admin_label(admin_id, directory)}")
        await q.edit_message_text(
            "Admins:\n" + "\n".join(labels),
            reply_markup=admin_cmds.admin_settings_keyboard(),
        )
        return

    if data == "admin_admins_add" and config.is_admin(user.id):
        context.user_data["admin_action"] = "ADD_ADMIN"
        await q.edit_message_text("Send the Telegram username like @username or the numeric user ID to add as admin.")
        return

    if data == "admin_admins_remove" and config.is_admin(user.id):
        context.user_data["admin_action"] = "REMOVE_ADMIN"
        await q.edit_message_text("Send the Telegram username like @username or the numeric user ID to remove from admins.")
        return

    if data == "info_paid":
        sub = subscriptions.get_user_active_subscription(user.id)
        if not sub:
            await q.edit_message_text("Subscription not found.")
            return

        days_left = get_days_left(sub["expiry_ts"])
        msg = (
            "*Subscription Active*\n\n"
            f"Plan: `{sub['plan_key']}`\n"
            f"Expires on:\n`{sub['expiry_ts']}`\n\n"
        )
        if days_left is not None:
            msg += f"Days remaining: *{days_left}*"

        await q.edit_message_text(msg, parse_mode="Markdown")
        return

    if data == "view_plans":
        plans = get_active_plans()
        if not plans:
            await q.edit_message_text("No active plans available.")
            return

        markets = []
        for plan in plans:
            market = _plan_market(plan)
            if market not in markets:
                markets.append(market)

        text = "*Choose a Plan Region*\n\nSelect the pricing group you want to show."
        kb = [
            [InlineKeyboardButton(f"{_market_title(market)} Plans", callback_data=f"market_{market}")]
            for market in markets
        ]
        kb.append([InlineKeyboardButton("Back", callback_data="back_to_menu")])

        await q.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown",
        )
        return

    if (
        data in {
            "my_mappings",
            "menu_add_mapping",
            "menu_add_source",
            "menu_add_target",
            "menu_remove_mapping",
            "menu_remove_source",
            "menu_remove_target",
        }
        and not config.is_admin(user.id)
        and subscriptions.get_user_access_state(user.id) not in ("FREE_ALL", "FREE_USER", "PAID")
    ):
        await q.edit_message_text(
            "This feature requires an active plan.\n\nUse /buy to continue or /status to check your subscription."
        )
        return

    if data == "my_mappings":
        import mappings

        await mappings.cmd_list_mappings(update, context)
        return

    if data == "menu_add_mapping":
        import mappings

        await mappings.cmd_add_mapping_flow(update, context)
        return

    if data == "menu_add_source":
        import mappings

        await mappings.cmd_add_source(update, context)
        return

    if data == "menu_add_target":
        import mappings

        await mappings.cmd_add_target(update, context)
        return

    if data == "menu_remove_mapping":
        import mappings

        await mappings.cmd_remove_mapping(update, context)
        return

    if data == "menu_remove_source":
        import mappings

        await mappings.cmd_remove_source(update, context)
        return

    if data == "menu_remove_target":
        import mappings

        await mappings.cmd_remove_target(update, context)
        return

    if data == "back_to_menu":
        text, markup = build_menu_for_user(user.id)
        await q.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
        return
