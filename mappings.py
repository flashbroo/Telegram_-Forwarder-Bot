import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from telethon.utils import get_peer_id

import config
import subscriptions
from db import execute, fetchall, fetchone, get_dialog_sync_state, get_pinned_dialogs
from utils import now_iso
from userbots.manager import schedule_pinned_dialog_sync_for_user

logger = logging.getLogger(__name__)


def _has_mapping_access(user_id: int) -> bool:
    return config.is_admin(user_id) or subscriptions.is_user_allowed(user_id)


def _normalize_source_channel(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("@") and value[1:].lstrip("-").isdigit():
        return value[1:]
    if value.lstrip("-").isdigit():
        return value
    if not value.startswith("@"): 
        value = f"@{value.lstrip('@')}"
    return value.lower()


def _normalize_target_channel(value: str) -> str:
    return (value or "").strip()


def _extract_channel_key(chat, role: str) -> str:
    if role == "source":
        username = getattr(chat, "username", None)
        if username:
            return _normalize_source_channel(username)
    try:
        return _normalize_target_channel(str(get_peer_id(chat)))
    except Exception:
        return _normalize_target_channel(str(chat.id))


def save_channel(uid, channel_key, title, role):
    execute(
        """
        INSERT INTO saved_channels (user_id, channel_key, title, role, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (uid, channel_key, title, role, now_iso()),
    )


def get_saved_channel_title(uid, channel_key, role):
    candidates = []
    key = str(channel_key)
    candidates.append(key)

    if key.startswith("-100"):
        candidates.extend([key[4:], f"@{key}"])
    elif key.isdigit():
        candidates.extend([f"-100{key}", f"@-100{key}"])
    elif key.startswith("@-100"):
        candidates.append(key[1:])

    seen = set()
    ordered = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)

    for candidate in ordered:
        row = fetchone(
            """
            SELECT title
            FROM saved_channels
            WHERE user_id=? AND channel_key=? AND role=?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (uid, candidate, role),
        )
        if row:
            return row["title"]

    for candidate in ordered:
        row = fetchone(
            """
            SELECT title
            FROM saved_channels
            WHERE user_id=? AND channel_key=?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (uid, candidate),
        )
        if row:
            return row["title"]

    return None


def _channel_display(uid, key, role):
    return get_saved_channel_title(uid, key, role) or key


def _mapping_display(uid, row):
    return f"{_channel_display(uid, row['source_channel'], 'source')} -> {_channel_display(uid, row['target_channel'], 'target')}"


def synced_dialog_rows(uid: int, role: str):
    rows = get_pinned_dialogs(uid, role)
    logger.info(
        "Pinned dialog UI query: user_id=%s role=%s count=%s ids=%s titles=%s",
        uid,
        role,
        len(rows),
        [row["dialog_id"] for row in rows],
        [row["title"] for row in rows],
    )
    return [(row["dialog_id"], row["title"] or row["dialog_id"]) for row in rows]


def trigger_dialog_resync(uid: int, reason: str):
    try:
        asyncio.create_task(schedule_pinned_dialog_sync_for_user(uid, reason=reason))
    except RuntimeError:
        pass


async def ready_synced_dialog_rows(uid: int, role: str):
    state = get_dialog_sync_state(uid)
    if not state:
        trigger_dialog_resync(uid, reason=f"{role}_missing_state")
        logger.info("Pinned dialog UI blocked: user_id=%s role=%s sync_state=NOT_STARTED", uid, role)
        return None, "Your Telegram chats are syncing now. Please try again in a few seconds."

    sync_state = state["sync_state"]
    if sync_state == "SYNCING":
        logger.info("Pinned dialog UI blocked: user_id=%s role=%s sync_state=SYNCING", uid, role)
        return None, "Your Telegram chats are still syncing. Please try again in a few seconds."

    if sync_state == "FAILED":
        trigger_dialog_resync(uid, reason=f"{role}_failed_retry")
        logger.info(
            "Pinned dialog UI blocked: user_id=%s role=%s sync_state=FAILED error=%s",
            uid,
            role,
            state["error_text"],
        )
        return None, "Chat sync failed earlier, so I started a fresh sync. Please try again shortly."

    if sync_state != "READY":
        trigger_dialog_resync(uid, reason=f"{role}_unknown_state")
        logger.info("Pinned dialog UI blocked: user_id=%s role=%s sync_state=%s", uid, role, sync_state)
        return None, "Chat sync is being refreshed. Please try again in a few seconds."

    return synced_dialog_rows(uid, role), ""


async def reply_sync_message(carrier, text: str):
    if hasattr(carrier, "message") and carrier.message:
        await carrier.message.reply_text(text)
    elif hasattr(carrier, "edit_text"):
        await carrier.edit_text(text)
    else:
        await carrier.reply_text(text)


async def cmd_debug_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = get_dialog_sync_state(uid)
    source_rows = synced_dialog_rows(uid, "source")
    target_rows = synced_dialog_rows(uid, "target")

    def preview(rows):
        if not rows:
            return "None"
        return "\n".join(f"- {title}" for _, title in rows[:10])

    await update.message.reply_text(
        "Chat diagnostic for your logged-in Telegram account:\n\n"
        f"Sync state: {state['sync_state'] if state else 'NOT_STARTED'}\n"
        f"Last sync: {state['last_sync_at'] if state else 'Never'}\n"
        f"Synced pinned dialogs: {len(source_rows)}\n"
        f"Source eligible shown: {len(source_rows)}\n"
        f"Target eligible shown: {len(target_rows)}\n\n"
        f"Source preview:\n{preview(source_rows)}\n\n"
        f"Target preview:\n{preview(target_rows)}"
    )


def build_buttons(role, selected_ids, pinned, saved):
    buttons = []
    seen = set()
    for cid, title in pinned + [(row["channel_key"], row["title"]) for row in saved]:
        if cid in seen:
            continue
        seen.add(cid)
        prefix = "[x]" if cid in selected_ids else "[ ]"
        buttons.append([InlineKeyboardButton(f"{prefix} {title}"[:60], callback_data=f"pick_{role}_{cid}")])
    return buttons


async def cmd_add_mapping_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    message = update.message or update.callback_query.message

    if not _has_mapping_access(uid):
        await message.reply_text("This feature requires an active plan. Use /buy to continue.")
        return

    context.user_data.clear()
    context.user_data["map_state"] = "COLLECT_SOURCES"
    context.user_data["sources"] = []
    context.user_data["targets"] = []

    rows, sync_message = await ready_synced_dialog_rows(uid, "source")
    if rows is None:
        await message.reply_text(sync_message)
        return

    if not rows:
        await message.reply_text("No pinned chats found for your logged-in Telegram account. Pin chats in Telegram, then try again after sync.")
        return

    buttons = build_buttons("source", [], rows, [])
    buttons.append([
        InlineKeyboardButton("Done", callback_data="map_source_done"),
        InlineKeyboardButton("Cancel", callback_data="map_cancel"),
    ])

    await message.reply_text(
        f"Select SOURCE pinned chat\n\nFound {len(rows)} synced pinned chats. Up to 15 pinned chats are shown.\nSelected: 0",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_forwarded_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = update.effective_user.id
    if not msg or not msg.forward_from_chat:
        return
    if not _has_mapping_access(uid):
        await msg.reply_text("This feature requires an active plan. Use /buy to continue.")
        return

    chat = msg.forward_from_chat
    if chat.type not in ("channel", "supergroup"):
        return

    state = context.user_data.get("map_state")
    if state not in ("COLLECT_SOURCES", "COLLECT_TARGETS"):
        return

    role = "source" if state == "COLLECT_SOURCES" else "target"
    channel_key = _extract_channel_key(chat, role)
    if role == "target":
        rows, sync_message = await ready_synced_dialog_rows(uid, "target")
        if rows is None:
            await msg.reply_text(sync_message)
            return
        allowed_targets = {key for key, _ in rows}
        if channel_key not in allowed_targets:
            await msg.reply_text("Target must be a channel or group where your logged-in account can post text and media.")
            return
    save_channel(uid, channel_key, chat.title or channel_key, role)
    key = f"{role}s"
    context.user_data.setdefault(key, [])
    if channel_key not in context.user_data[key]:
        context.user_data[key].append(channel_key)
    await msg.reply_text(f"Added: {chat.title or channel_key}")


def _selected_channel_names(uid, keys, role):
    names = [_channel_display(uid, key, role) for key in keys]
    return ", ".join(names[:3]) if names else "None"


async def mapping_flow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    uid = q.from_user.id
    if not _has_mapping_access(uid):
        context.user_data.clear()
        await q.message.edit_text("This feature requires an active plan. Use /buy to continue.")
        return

    data = q.data
    manage_mode = context.user_data.get("manage_mode")

    context.user_data.setdefault("sources", [])
    context.user_data.setdefault("targets", [])

    if data.startswith("pick_"):
        _, role, cid = data.split("_", 2)
        key = "sources" if role == "source" else "targets"
        selected = context.user_data[key]
        if cid in selected:
            selected.remove(cid)
        else:
            selected.append(cid)

        rows, sync_message = await ready_synced_dialog_rows(uid, role)
        if rows is None:
            await q.message.edit_text(sync_message)
            return
        buttons = build_buttons(role, selected, rows, [])
        buttons.append([
            InlineKeyboardButton("Done", callback_data=f"map_{role}_done"),
            InlineKeyboardButton("Cancel", callback_data="map_cancel"),
        ])

        if role == "target":
            text = (
                f"Select TARGET channel/group\n\nSelected sources: {_selected_channel_names(uid, context.user_data['sources'], 'source')}\n"
                f"Found {len(rows)} synced pinned targets where you can post.\nSelected targets: {len(selected)}"
            )
        else:
            text = (
                f"Select SOURCE pinned chat\n\nFound {len(rows)} synced pinned chats. Up to 15 pinned chats are shown.\nSelected: {len(selected)}"
            )
        await q.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data == "map_source_done":
        if not context.user_data["sources"]:
            await q.answer("Select at least one source channel first.", show_alert=True)
            return

        if manage_mode == "ADD_SOURCES_TO_TARGET":
            target = context.user_data.get("selected_target")
            created = 0
            source_titles = dict(synced_dialog_rows(uid, "source"))
            for src in context.user_data["sources"]:
                before = fetchone(
                    "SELECT 1 FROM mappings WHERE user_id=? AND source_channel=? AND target_channel=?",
                    (uid, src, target),
                )
                create_mapping(uid, src, target)
                save_channel(uid, src, source_titles.get(src, src), "source")
                if not before:
                    created += 1
            context.user_data.clear()
            await q.message.edit_text(f"Added {created} new source mapping(s) to:\n{_channel_display(uid, target, 'target')}")
            return

        context.user_data["map_state"] = "COLLECT_TARGETS"
        rows, sync_message = await ready_synced_dialog_rows(uid, "target")
        if rows is None:
            await q.message.edit_text(sync_message)
            return
        if not rows:
            await q.message.edit_text("No pinned target channels/groups found where your logged-in account can post text and media.")
            return
        buttons = build_buttons("target", [], rows, [])
        buttons.append([
            InlineKeyboardButton("Done", callback_data="map_target_done"),
            InlineKeyboardButton("Cancel", callback_data="map_cancel"),
        ])
        await q.message.edit_text(
            f"Select TARGET channel/group\n\nSelected sources: {_selected_channel_names(uid, context.user_data['sources'], 'source')}\n"
            f"Found {len(rows)} synced pinned targets where you can post. Up to 15 pinned targets are shown.\nSelected targets: 0",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data == "map_target_done":
        if not context.user_data["targets"]:
            await q.answer("Select at least one target channel first.", show_alert=True)
            return

        if manage_mode == "ADD_TARGETS_TO_SOURCE":
            source = context.user_data.get("selected_source")
            created = 0
            target_titles = dict(synced_dialog_rows(uid, "target"))
            for tgt in context.user_data["targets"]:
                before = fetchone(
                    "SELECT 1 FROM mappings WHERE user_id=? AND source_channel=? AND target_channel=?",
                    (uid, source, tgt),
                )
                create_mapping(uid, source, tgt)
                save_channel(uid, tgt, target_titles.get(tgt, tgt), "target")
                if not before:
                    created += 1
            context.user_data.clear()
            await q.message.edit_text(f"Added {created} new target mapping(s) for:\n{_channel_display(uid, source, 'source')}")
            return

        await q.message.edit_text(
            "Confirm mapping?",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Confirm", callback_data="map_confirm"), InlineKeyboardButton("Cancel", callback_data="map_cancel")]]),
        )
        return

    if data == "map_confirm":
        created = 0
        source_titles = dict(synced_dialog_rows(uid, "source"))
        target_titles = dict(synced_dialog_rows(uid, "target"))
        for src in context.user_data.get("sources", []):
            for tgt in context.user_data.get("targets", []):
                before = fetchone(
                    "SELECT 1 FROM mappings WHERE user_id=? AND source_channel=? AND target_channel=?",
                    (uid, src, tgt),
                )
                create_mapping(uid, src, tgt)
                save_channel(uid, src, source_titles.get(src, src), "source")
                save_channel(uid, tgt, target_titles.get(tgt, tgt), "target")
                if not before:
                    created += 1
        context.user_data.clear()
        await q.message.edit_text(f"Mapping created. Added {created} rule(s).")
        return

    if data == "map_cancel":
        context.user_data.clear()
        await q.message.edit_text("Cancelled")


def create_mapping(user_id: int, source_channel: str, target_channel: str) -> bool:
    source_channel = _normalize_source_channel(source_channel)
    target_channel = _normalize_target_channel(target_channel)
    if not source_channel or not target_channel:
        return False
    execute(
        """
        INSERT OR IGNORE INTO mappings
        (user_id, source_channel, target_channel, active, created_at)
        VALUES (?, ?, ?, 1, ?)
        """,
        (user_id, source_channel, target_channel, now_iso()),
    )
    return True


async def cmd_add_mapping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if len(context.args) == 2:
        source = _normalize_source_channel(context.args[0])
        target = _normalize_target_channel(context.args[1])
        rows, sync_message = await ready_synced_dialog_rows(uid, "target")
        if rows is None:
            await update.message.reply_text(sync_message)
            return
        allowed_targets = {key for key, _ in rows}
        if target not in allowed_targets:
            await update.message.reply_text("Target must be a channel or group where your logged-in account can post text and media.")
            return
        before = fetchone(
            "SELECT 1 FROM mappings WHERE user_id=? AND source_channel=? AND target_channel=?",
            (uid, source, target),
        )
        create_mapping(uid, source, target)
        await update.message.reply_text("This mapping already exists." if before else "Mapping created.")
        return

    if context.args:
        await update.message.reply_text("Usage: /add_mapping @source_channel <target_channel>")
        return

    await cmd_add_mapping_flow(update, context)


async def cmd_list_mappings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    message = update.message or update.callback_query.message
    if not _has_mapping_access(uid):
        await message.reply_text("This feature requires an active plan. Use /buy to continue.")
        return
    rows = fetchall(
        """
        SELECT mapping_id, source_channel, target_channel, active
        FROM mappings
        WHERE user_id=?
        ORDER BY mapping_id DESC
        """,
        (uid,),
    )
    if not rows:
        await message.reply_text("No mappings found.")
        return

    lines = [f"{_mapping_display(uid, row)}\nActive: {'Yes' if row['active'] else 'No'}" for row in rows]
    await message.reply_text("\n\n".join(lines))


async def cmd_remove_mapping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    message = update.message or update.callback_query.message
    if not _has_mapping_access(uid):
        await message.reply_text("This feature requires an active plan. Use /buy to continue.")
        return
    if len(context.args) == 1:
        try:
            mapping_id = int(context.args[0])
        except ValueError:
            await message.reply_text("Mapping ID must be a number.")
            return
        mapping = fetchone("SELECT mapping_id FROM mappings WHERE mapping_id=? AND user_id=?", (mapping_id, uid))
        if not mapping:
            await message.reply_text("Mapping not found.")
            return
        execute("DELETE FROM mappings WHERE mapping_id=? AND user_id=?", (mapping_id, uid))
        await message.reply_text("Mapping removed.")
        return

    rows = fetchall(
        """
        SELECT mapping_id, source_channel, target_channel
        FROM mappings
        WHERE user_id=?
        ORDER BY mapping_id DESC
        """,
        (uid,),
    )
    if not rows:
        await message.reply_text("No mappings found.")
        return

    buttons = [[InlineKeyboardButton(_mapping_display(uid, row)[:60], callback_data=f"mm_del_{row['mapping_id']}")] for row in rows[:20]]
    await message.reply_text("Select a mapping to remove:", reply_markup=InlineKeyboardMarkup(buttons))


def _distinct_channels(uid: int, role: str):
    column = "source_channel" if role == "source" else "target_channel"
    rows = fetchall(
        f"""
        SELECT DISTINCT {column} AS channel_key
        FROM mappings
        WHERE user_id=? AND active=1
        ORDER BY {column} ASC
        """,
        (uid,),
    )
    return [(row["channel_key"], _channel_display(uid, row["channel_key"], role)) for row in rows]


async def _pinned_or_saved_channels(uid: int, role: str):
    rows, _ = await ready_synced_dialog_rows(uid, role)
    return rows


async def _show_existing_channel_picker(update_or_query, uid: int, role: str, action: str):
    if action in ("add_source", "add_target"):
        rows, sync_message = await ready_synced_dialog_rows(uid, role)
        if rows is None:
            await reply_sync_message(update_or_query, sync_message)
            return
    else:
        rows = _distinct_channels(uid, role)
    if not rows:
        if action in ("add_source", "add_target"):
            message = (
                "No accessible source chats found. Please make sure this Telegram account is logged in and has joined chats."
                if role == "source"
                else "No target channels/groups found where this Telegram account can post text and media."
            )
        else:
            message = "No mapped sources found." if role == "source" else "No mapped targets found."
        if hasattr(update_or_query, "message"):
            await update_or_query.message.reply_text(message)
        else:
            await update_or_query.reply_text(message)
        return

    prefix = "mp_addsrc_target_" if action == "add_source" else "mp_addtgt_source_"
    if action == "remove_source":
        prefix = "mp_rmsrc_"
    elif action == "remove_target":
        prefix = "mp_rmtgt_"

    buttons = [[InlineKeyboardButton(label[:60], callback_data=f"{prefix}{key}")] for key, label in rows]
    text = {
        ("target", "add_source"): "Select the target channel that should receive the new source:",
        ("source", "add_target"): "Select the source channel that should send to the new target:",
        ("source", "remove_source"): "Select the source channel to remove from all mappings:",
        ("target", "remove_target"): "Select the target channel to remove from all mappings:",
    }[(role, action)]

    if hasattr(update_or_query, "message"):
        await update_or_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def mapping_manage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid = q.from_user.id

    if not _has_mapping_access(uid):
        context.user_data.clear()
        await q.message.edit_text("This feature requires an active plan. Use /buy to continue.")
        return

    if data.startswith("mm_del_"):
        mid = int(data.split("_")[-1])
        row = fetchone(
            """
            SELECT mapping_id, source_channel, target_channel
            FROM mappings
            WHERE mapping_id=? AND user_id=?
            """,
            (mid, uid),
        )
        if not row:
            await q.message.reply_text("Mapping not found.")
            return
        label = _mapping_display(uid, row)
        execute("DELETE FROM mappings WHERE mapping_id=? AND user_id=?", (mid, uid))
        await q.message.reply_text(f"Removed mapping:\n{label}")
        return

    if data.startswith("mp_addsrc_target_"):
        target_key = data.replace("mp_addsrc_target_", "", 1)
        context.user_data.clear()
        context.user_data["map_state"] = "COLLECT_SOURCES"
        context.user_data["manage_mode"] = "ADD_SOURCES_TO_TARGET"
        context.user_data["sources"] = []
        context.user_data["selected_target"] = target_key
        rows, sync_message = await ready_synced_dialog_rows(uid, "source")
        if rows is None:
            await q.message.reply_text(sync_message)
            return
        buttons = build_buttons("source", [], rows, [])
        buttons.append([InlineKeyboardButton("Done", callback_data="map_source_done"), InlineKeyboardButton("Cancel", callback_data="map_cancel")])
        await q.message.reply_text(
            f"Select new source chat for:\n{_channel_display(uid, target_key, 'target')}\n\nUp to 15 synced pinned chats are shown.",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith("mp_addtgt_source_"):
        source_key = data.replace("mp_addtgt_source_", "", 1)
        context.user_data.clear()
        context.user_data["map_state"] = "COLLECT_TARGETS"
        context.user_data["manage_mode"] = "ADD_TARGETS_TO_SOURCE"
        context.user_data["targets"] = []
        context.user_data["selected_source"] = source_key
        rows, sync_message = await ready_synced_dialog_rows(uid, "target")
        if rows is None:
            await q.message.reply_text(sync_message)
            return
        buttons = build_buttons("target", [], rows, [])
        buttons.append([InlineKeyboardButton("Done", callback_data="map_target_done"), InlineKeyboardButton("Cancel", callback_data="map_cancel")])
        await q.message.reply_text(
            f"Select new target channel/group for:\n{_channel_display(uid, source_key, 'source')}\n\nOnly pinned channels/groups where your account can post text and media are shown. Up to 15 targets are shown.",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data.startswith("mp_rmsrc_"):
        source_key = data.replace("mp_rmsrc_", "", 1)
        label = _channel_display(uid, source_key, "source")
        execute("DELETE FROM mappings WHERE user_id=? AND source_channel=?", (uid, source_key))
        await q.message.reply_text(f"Removed source from all mappings:\n{label}")
        return

    if data.startswith("mp_rmtgt_"):
        target_key = data.replace("mp_rmtgt_", "", 1)
        label = _channel_display(uid, target_key, "target")
        execute("DELETE FROM mappings WHERE user_id=? AND target_channel=?", (uid, target_key))
        await q.message.reply_text(f"Removed target from all mappings:\n{label}")


async def cmd_add_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    carrier = update.message or update.callback_query
    await _show_existing_channel_picker(carrier, update.effective_user.id, "target", "add_source")


async def cmd_add_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    carrier = update.message or update.callback_query
    await _show_existing_channel_picker(carrier, update.effective_user.id, "source", "add_target")


async def cmd_remove_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    carrier = update.message or update.callback_query
    await _show_existing_channel_picker(carrier, update.effective_user.id, "source", "remove_source")


async def cmd_remove_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    carrier = update.message or update.callback_query
    await _show_existing_channel_picker(carrier, update.effective_user.id, "target", "remove_target")
