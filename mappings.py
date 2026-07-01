from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from telethon.tl.functions.messages import GetPinnedDialogsRequest
from telethon.utils import get_peer_id

import config
import subscriptions
from db import execute, fetchall, fetchone
from userbots.manager import get_client
from utils import now_iso


def _has_mapping_access(user_id: int) -> bool:
    return config.is_admin(user_id) or subscriptions.is_user_allowed(user_id)


async def fetch_all_dialogs(uid):
    client = get_client(uid)
    if not client:
        return []

    try:
        if not client.is_connected():
            await client.connect()
        if not await client.is_user_authorized():
            return []

        pinned_order = await fetch_pinned_dialog_order(client)
        pinned_keys = set(pinned_order)
        dialogs = []
        async for dialog in client.iter_dialogs():
            chat = dialog.entity
            if hasattr(chat, "title") and chat.title:
                name = chat.title
            elif hasattr(chat, "first_name") and chat.first_name:
                name = chat.first_name
            else:
                name = "Unknown"
            peer_key = _peer_sort_key(chat)
            is_pinned = peer_key in pinned_keys or bool(getattr(dialog, "pinned", False))
            dialogs.append((str(chat.id), name, chat, is_pinned, pinned_order.get(peer_key, 10_000)))
        return sorted(dialogs, key=lambda item: (0 if item[3] else 1, item[4]))
    except Exception:
        return []


def _peer_sort_key(chat) -> str:
    try:
        return str(get_peer_id(chat))
    except Exception:
        return str(getattr(chat, "id", ""))


def _dialog_peer_sort_key(peer) -> str:
    try:
        raw_peer = getattr(peer, "peer", peer)
        return str(get_peer_id(raw_peer))
    except Exception:
        return ""


async def fetch_pinned_dialog_order(client) -> dict[str, int]:
    pinned = {}
    try:
        result = await client(GetPinnedDialogsRequest(folder_id=0))
    except Exception:
        return pinned

    for index, dialog in enumerate(getattr(result, "dialogs", []) or []):
        key = _dialog_peer_sort_key(dialog)
        if key:
            pinned.setdefault(key, index)
    return pinned


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


def get_saved(uid, role):
    return fetchall(
        """
        SELECT channel_key, title
        FROM saved_channels
        WHERE user_id=? AND role=?
        ORDER BY created_at DESC
        """,
        (uid, role),
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


def _is_group_or_channel(chat) -> bool:
    return chat.__class__.__name__.lower() in ("channel", "chat")


def _is_accessible_source_chat(chat) -> bool:
    return chat.__class__.__name__.lower() in ("channel", "chat", "user")


def _is_restricted(rights, permission: str) -> bool:
    return bool(rights and getattr(rights, permission, False))


def _rights_allow_required_content(*rights_objects) -> bool:
    media_permissions = ("send_media", "send_photos", "send_videos", "send_docs")
    for rights in rights_objects:
        if _is_restricted(rights, "send_messages"):
            return False
        for permission in media_permissions:
            if _is_restricted(rights, permission):
                return False
    return True


def _can_send_required_content(chat, include_default_rights: bool = True) -> bool:
    rights_objects = [getattr(chat, "banned_rights", None)]
    if include_default_rights:
        rights_objects.append(getattr(chat, "default_banned_rights", None))

    return _rights_allow_required_content(*rights_objects)


def _can_post_to_target(chat) -> bool:
    if not _is_group_or_channel(chat):
        return False

    if getattr(chat, "creator", False):
        return True

    admin_rights = getattr(chat, "admin_rights", None)
    if getattr(chat, "broadcast", False):
        return bool(
            admin_rights
            and getattr(admin_rights, "post_messages", False)
            and _can_send_required_content(chat, include_default_rights=False)
        )

    if admin_rights:
        return _can_send_required_content(chat, include_default_rights=False)

    return (
        not getattr(chat, "left", False)
        and not getattr(chat, "deactivated", False)
        and _can_send_required_content(chat, include_default_rights=True)
    )


def filter_dialogs(dialogs, role):
    pinned = []
    remaining = []
    seen = set()

    for _, name, chat, is_pinned, *_ in dialogs:
        if role == "source" and not _is_accessible_source_chat(chat):
            continue

        if role == "target" and not _can_post_to_target(chat):
            continue

        key = _extract_channel_key(chat, role)
        if not key or key in seen:
            continue
        seen.add(key)

        item = (key, name)
        if is_pinned:
            pinned.append(item)
        else:
            remaining.append(item)

    return (pinned + remaining)[:10]


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


async def refresh_saved_channel_titles(uid):
    dialogs = await fetch_all_dialogs(uid)
    for _, name, chat, *_ in dialogs:
        source_key = _extract_channel_key(chat, "source")
        target_key = _extract_channel_key(chat, "target")
        if source_key:
            save_channel(uid, source_key, name, "source")
        if target_key:
            save_channel(uid, target_key, name, "target")


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

    dialogs = await fetch_all_dialogs(uid)
    context.user_data["dialogs_cache"] = dialogs

    pinned = filter_dialogs(dialogs, "source")
    buttons = build_buttons("source", [], pinned, [])
    buttons.append([
        InlineKeyboardButton("Done", callback_data="map_source_done"),
        InlineKeyboardButton("Cancel", callback_data="map_cancel"),
    ])

    await message.reply_text(
        f"Select SOURCE chat\n\nFound {len(pinned)} accessible chats. Pinned chats are shown first, then recent chats.\nMaximum 10 chats are shown.\nSelected: 0",
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
        dialogs = await fetch_all_dialogs(uid)
        allowed_targets = {key for key, _ in filter_dialogs(dialogs, "target")}
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
    dialogs = context.user_data.get("dialogs_cache", [])
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

        pinned = filter_dialogs(dialogs, role)
        buttons = build_buttons(role, selected, pinned, [])
        buttons.append([
            InlineKeyboardButton("Done", callback_data=f"map_{role}_done"),
            InlineKeyboardButton("Cancel", callback_data="map_cancel"),
        ])

        if role == "target":
            text = (
                f"Select TARGET channel/group\n\nSelected sources: {_selected_channel_names(uid, context.user_data['sources'], 'source')}\n"
                f"Found {len(pinned)} target channels/groups where you can post. Pinned targets are shown first, then recent targets.\nMaximum 10 targets are shown.\nSelected targets: {len(selected)}"
            )
        else:
            text = (
                f"Select SOURCE chat\n\nFound {len(pinned)} accessible chats. Pinned chats are shown first, then recent chats.\nMaximum 10 chats are shown.\nSelected: {len(selected)}"
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
            source_titles = {key: name for key, name in filter_dialogs(dialogs, "source")}
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
        pinned = filter_dialogs(dialogs, "target")
        buttons = build_buttons("target", [], pinned, [])
        buttons.append([
            InlineKeyboardButton("Done", callback_data="map_target_done"),
            InlineKeyboardButton("Cancel", callback_data="map_cancel"),
        ])
        await q.message.edit_text(
            f"Select TARGET channel/group\n\nSelected sources: {_selected_channel_names(uid, context.user_data['sources'], 'source')}\n"
            f"Found {len(pinned)} target channels/groups where you can post. Pinned targets are shown first, then recent targets.\nMaximum 10 targets are shown.\nSelected targets: 0",
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
            target_titles = {key: name for key, name in filter_dialogs(dialogs, "target")}
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
        source_titles = {key: name for key, name in filter_dialogs(dialogs, "source")}
        target_titles = {key: name for key, name in filter_dialogs(dialogs, "target")}
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
        dialogs = await fetch_all_dialogs(uid)
        allowed_targets = {key for key, _ in filter_dialogs(dialogs, "target")}
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
    await refresh_saved_channel_titles(uid)
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


def _saved_channel_rows(uid: int, role: str):
    rows = fetchall(
        """
        SELECT channel_key, title
        FROM saved_channels
        WHERE user_id=? AND role=?
        ORDER BY created_at DESC
        """,
        (uid, role),
    )
    return [(row["channel_key"], row["title"] or row["channel_key"]) for row in rows]


async def _pinned_or_saved_channels(uid: int, role: str):
    dialogs = await fetch_all_dialogs(uid)
    return filter_dialogs(dialogs, role)


async def _show_existing_channel_picker(update_or_query, uid: int, role: str, action: str):
    await refresh_saved_channel_titles(uid)
    rows = await _pinned_or_saved_channels(uid, role) if action in ("add_source", "add_target") else _distinct_channels(uid, role)
    if not rows:
        if action in ("add_source", "add_target"):
            message = "No accessible source chats found." if role == "source" else "No target channels/groups found where your account can post."
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
        dialogs = await fetch_all_dialogs(uid)
        context.user_data["dialogs_cache"] = dialogs
        pinned = filter_dialogs(dialogs, "source")
        buttons = build_buttons("source", [], pinned, [])
        buttons.append([InlineKeyboardButton("Done", callback_data="map_source_done"), InlineKeyboardButton("Cancel", callback_data="map_cancel")])
        await q.message.reply_text(
            f"Select new source chat for:\n{_channel_display(uid, target_key, 'target')}\n\nPinned chats are shown first, then recent chats. Maximum 10 chats are shown.",
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
        dialogs = await fetch_all_dialogs(uid)
        context.user_data["dialogs_cache"] = dialogs
        pinned = filter_dialogs(dialogs, "target")
        buttons = build_buttons("target", [], pinned, [])
        buttons.append([InlineKeyboardButton("Done", callback_data="map_target_done"), InlineKeyboardButton("Cancel", callback_data="map_cancel")])
        await q.message.reply_text(
            f"Select new target channel/group for:\n{_channel_display(uid, source_key, 'source')}\n\nOnly channels/groups where your account can post text and media are shown. Pinned targets appear first, then recent targets. Maximum 10 targets are shown.",
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
