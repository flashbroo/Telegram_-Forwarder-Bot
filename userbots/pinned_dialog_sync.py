import asyncio
import logging
import time

from telethon import events
from telethon.tl import types
from telethon.tl.functions.messages import GetPinnedDialogsRequest
from telethon.utils import get_peer_id

import db
from utils import now_iso

logger = logging.getLogger(__name__)


PIN_UPDATE_TYPES = (
    types.UpdateDialogPinned,
    types.UpdatePinnedDialogs,
    types.UpdateDialogFilter,
    types.UpdateDialogFilterOrder,
    types.UpdateDialogFilters,
)


class PinnedDialogSyncService:
    def __init__(self):
        self._locks = {}
        self._pending_tasks = {}

    async def sync_user(self, user_id: int, client, reason: str = "manual"):
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            started = time.perf_counter()
            sync_version = int(time.time() * 1000)
            try:
                logger.info("Dialog sync started for user %s (%s)", user_id, reason)
                db.set_dialog_sync_state(user_id, "SYNCING", sync_version=sync_version)

                if not client.is_connected():
                    await client.connect()
                if not await client.is_user_authorized():
                    message = "session is not authorized"
                    db.set_dialog_sync_state(user_id, "FAILED", sync_version=sync_version, error_text=message)
                    logger.warning("Dialog sync failed for user %s: %s", user_id, message)
                    return

                dialogs, stats = await self._collect_pinned_dialogs(client)
                self._validate_dialogs(dialogs)
                written = db.replace_pinned_dialogs(user_id, dialogs, sync_version=sync_version)
                duration = time.perf_counter() - started
                logger.info(
                    "Dialog sync ready for user %s (%s): telegram_dialogs=%s pinned=%s sources=%s targets=%s db_written=%s duration=%.2fs",
                    user_id,
                    reason,
                    stats["dialog_count"],
                    stats["pinned_count"],
                    stats["source_count"],
                    stats["target_count"],
                    written,
                    duration,
                )
            except Exception as exc:
                db.set_dialog_sync_state(user_id, "FAILED", sync_version=sync_version, error_text=str(exc))
                logger.exception("Dialog sync failed for user %s (%s)", user_id, reason)

    def attach(self, user_id: int, client):
        if getattr(client, "_pinned_dialog_sync_attached", False):
            return

        @client.on(events.Raw)
        async def on_raw_update(update):
            if self._is_pin_update(update):
                self.schedule_sync(user_id, client, reason=update.__class__.__name__)

        client._pinned_dialog_sync_attached = True

    def schedule_sync(self, user_id: int, client, reason: str = "update"):
        task = self._pending_tasks.get(user_id)
        if task and not task.done():
            return

        async def delayed_sync():
            try:
                await asyncio.sleep(1)
                await self.sync_user(user_id, client, reason=reason)
            finally:
                self._pending_tasks.pop(user_id, None)

        self._pending_tasks[user_id] = asyncio.create_task(delayed_sync())

    async def periodic_recovery_loop(self, client_provider, interval_seconds: int = 900):
        while True:
            await asyncio.sleep(interval_seconds)
            for user_id, client in list(client_provider().items()):
                try:
                    if client and client.is_connected() and await client.is_user_authorized():
                        logger.info("Dialog recovery sync scheduled for user %s", user_id)
                        self.schedule_sync(user_id, client, reason="periodic_recovery")
                except Exception:
                    logger.exception("Dialog recovery sync scheduling failed for user %s", user_id)

    def _is_pin_update(self, update) -> bool:
        if isinstance(update, PIN_UPDATE_TYPES):
            return True
        updates = getattr(update, "updates", None)
        return any(isinstance(item, PIN_UPDATE_TYPES) for item in updates or [])

    async def _collect_pinned_dialogs(self, client):
        pinned_snapshot = await self._fetch_pinned_snapshot(client)
        pinned_order = {item["dialog_id"]: item["display_order"] for item in pinned_snapshot}
        pinned_keys = set(pinned_order)
        dialog_entities = {}
        iter_pinned_ids = []
        rows = []
        sync_ts = now_iso()
        dialog_count = 0
        target_count = 0

        async for dialog in client.iter_dialogs(ignore_pinned=False):
            dialog_count += 1
            chat = dialog.entity
            dialog_id = self._peer_key(chat)
            dialog_entities.setdefault(
                dialog_id,
                {
                    "entity": chat,
                    "is_pinned": self._dialog_pinned_flag(dialog),
                },
            )
            iter_pinned = self._dialog_pinned_flag(dialog)
            classified_pinned = dialog_id in pinned_keys
            if iter_pinned:
                iter_pinned_ids.append(dialog_id)
            logger.info(
                "Telethon dialog pin debug: id=%s title=%s type=%s pinned_flag=%r flags=%s classified_pinned=%s classification_source=%s",
                dialog_id,
                self._title(chat),
                self._dialog_type(chat),
                iter_pinned,
                self._dialog_flags(dialog),
                classified_pinned,
                "GetPinnedDialogs" if classified_pinned else "not_pinned",
            )

        for item in pinned_snapshot:
            dialog_entry = dialog_entities.get(item["dialog_id"]) or {}
            chat = item.get("entity") or dialog_entry.get("entity")
            if chat is None:
                continue

            row = self._build_dialog_row(chat, item["dialog_id"], item["display_order"], sync_ts)
            if row["can_post"]:
                target_count += 1
            rows.append(row)

        stale_iter_pins = sorted(set(iter_pinned_ids) - pinned_keys)
        snapshot_missing_from_iter = sorted(pinned_keys - set(dialog_entities))
        if stale_iter_pins:
            stale_iter_titles = {
                dialog_id: self._title(dialog_entities[dialog_id]["entity"])
                for dialog_id in stale_iter_pins
                if dialog_id in dialog_entities
            }
            logger.warning(
                "Ignoring iter_dialogs pinned flags absent from GetPinnedDialogs: ids=%s titles=%s",
                stale_iter_pins,
                stale_iter_titles,
            )
        if snapshot_missing_from_iter:
            logger.info(
                "Pinned snapshot contains dialogs not present in iter_dialogs metadata: ids=%s",
                snapshot_missing_from_iter,
            )

        rows.sort(key=lambda row: row["display_order"])
        logger.info(
            "Application detected pinned dialogs from GetPinnedDialogs only: count=%s ids=%s titles=%s",
            len(rows),
            [row["dialog_id"] for row in rows],
            [row["title"] for row in rows],
        )
        stats = {
            "dialog_count": dialog_count,
            "pinned_count": len(rows),
            "source_count": len(rows),
            "target_count": target_count,
        }
        return rows, stats

    def _build_dialog_row(self, chat, dialog_id: str, display_order: int, sync_ts: str) -> dict:
        return {
            "dialog_id": dialog_id,
            "peer_id": dialog_id,
            "dialog_type": self._dialog_type(chat),
            "title": self._title(chat),
            "username": getattr(chat, "username", None) or "",
            "is_pinned": True,
            "can_post": self._can_post(chat),
            "display_order": display_order,
            "last_sync": sync_ts,
        }

    def _dialog_pinned_flag(self, dialog):
        return getattr(dialog, "pinned", None)

    def _dialog_flags(self, dialog) -> dict:
        raw_dialog = getattr(dialog, "dialog", None)
        keys = (
            "pinned",
            "archived",
            "folder_id",
            "is_channel",
            "is_group",
            "is_user",
            "unread_count",
            "unread_mentions_count",
        )
        flags = {key: getattr(dialog, key, None) for key in keys}
        raw_keys = (
            "pinned",
            "folder_id",
            "unread_mark",
            "unread_count",
            "unread_mentions_count",
            "pts",
        )
        flags["raw_dialog_class"] = raw_dialog.__class__.__name__ if raw_dialog is not None else None
        flags["raw_dialog_flags"] = {key: getattr(raw_dialog, key, None) for key in raw_keys}
        return flags

    async def _fetch_pinned_snapshot(self, client):
        result = None
        try:
            result = await client(GetPinnedDialogsRequest(folder_id=0))
        except Exception:
            logger.exception("Failed to fetch pinned dialog snapshot")
            return []

        users_by_id = {getattr(user, "id", None): user for user in getattr(result, "users", []) or []}
        chats_by_id = {getattr(chat, "id", None): chat for chat in getattr(result, "chats", []) or []}
        snapshot = []

        for index, dialog in enumerate(getattr(result, "dialogs", []) or []):
            peer = getattr(dialog, "peer", dialog)
            key = self._dialog_peer_key(dialog)
            if not key:
                continue

            entity = self._entity_for_peer(peer, users_by_id, chats_by_id)
            if entity is None:
                try:
                    entity = await client.get_entity(peer)
                except Exception:
                    logger.exception("Could not resolve pinned dialog entity for peer %s", key)
                    continue

            snapshot.append(
                {
                    "dialog_id": key,
                    "display_order": index,
                    "entity": entity,
                }
            )

        logger.info(
            "Fresh GetPinnedDialogs result: count=%s ids=%s titles=%s",
            len(snapshot),
            [item["dialog_id"] for item in snapshot],
            [self._title(item["entity"]) for item in snapshot],
        )
        return snapshot

    def _entity_for_peer(self, peer, users_by_id: dict, chats_by_id: dict):
        user_id = getattr(peer, "user_id", None)
        if user_id is not None:
            return users_by_id.get(user_id)

        chat_id = getattr(peer, "chat_id", None)
        if chat_id is not None:
            return chats_by_id.get(chat_id)

        channel_id = getattr(peer, "channel_id", None)
        if channel_id is not None:
            return chats_by_id.get(channel_id)

        return None

    def _validate_dialogs(self, dialogs):
        if dialogs is None:
            raise ValueError("Telegram dialog sync returned no result")
        seen = set()
        for index, dialog in enumerate(dialogs):
            dialog_id = dialog.get("dialog_id")
            if not dialog_id:
                raise ValueError(f"Synced dialog at index {index} is missing dialog_id")
            if dialog_id in seen:
                raise ValueError(f"Duplicate synced dialog_id {dialog_id}")
            seen.add(dialog_id)
            if "display_order" not in dialog:
                raise ValueError(f"Synced dialog {dialog_id} is missing display_order")

    def _peer_key(self, chat) -> str:
        try:
            return str(get_peer_id(chat))
        except Exception:
            return str(getattr(chat, "id", ""))

    def _dialog_peer_key(self, dialog) -> str:
        try:
            return str(get_peer_id(getattr(dialog, "peer", dialog)))
        except Exception:
            return ""

    def _title(self, chat) -> str:
        title = getattr(chat, "title", None)
        if title:
            return title
        first_name = getattr(chat, "first_name", "") or ""
        last_name = getattr(chat, "last_name", "") or ""
        name = f"{first_name} {last_name}".strip()
        return name or getattr(chat, "username", None) or self._peer_key(chat)

    def _dialog_type(self, chat) -> str:
        chat_type = chat.__class__.__name__.lower()
        if chat_type == "user":
            return "private"
        if chat_type == "chat":
            return "group"
        if chat_type == "channel":
            return "channel" if getattr(chat, "broadcast", False) else "supergroup"
        return chat_type

    def _is_restricted(self, rights, permission: str) -> bool:
        return bool(rights and getattr(rights, permission, False))

    def _rights_allow_required_content(self, *rights_objects) -> bool:
        media_permissions = ("send_media", "send_photos", "send_videos", "send_docs")
        for rights in rights_objects:
            if self._is_restricted(rights, "send_messages"):
                return False
            for permission in media_permissions:
                if self._is_restricted(rights, permission):
                    return False
        return True

    def _can_send_required_content(self, chat, include_default_rights: bool = True) -> bool:
        rights_objects = [getattr(chat, "banned_rights", None)]
        if include_default_rights:
            rights_objects.append(getattr(chat, "default_banned_rights", None))
        return self._rights_allow_required_content(*rights_objects)

    def _can_post(self, chat) -> bool:
        if self._dialog_type(chat) not in ("channel", "group", "supergroup"):
            return False
        if getattr(chat, "creator", False):
            return True

        admin_rights = getattr(chat, "admin_rights", None)
        if getattr(chat, "broadcast", False):
            return bool(
                admin_rights
                and getattr(admin_rights, "post_messages", False)
                and self._can_send_required_content(chat, include_default_rights=False)
            )
        if admin_rights:
            return self._can_send_required_content(chat, include_default_rights=False)
        return (
            not getattr(chat, "left", False)
            and not getattr(chat, "deactivated", False)
            and self._can_send_required_content(chat, include_default_rights=True)
        )


pinned_dialog_sync = PinnedDialogSyncService()
