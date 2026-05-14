import json
import logging
import os

from telethon import TelegramClient, events
from telethon.utils import get_peer_id

import db
from config import USERBOT_API_HASH, USERBOT_API_ID

logger = logging.getLogger(__name__)

clients = {}


def get_client(user_id):
    return clients.get(user_id)


async def drop_client(user_id):
    client = clients.pop(user_id, None)
    if not client:
        return

    try:
        if client.is_connected():
            await client.disconnect()
    except Exception:
        logger.exception("Failed to disconnect existing userbot client for user %s", user_id)


def get_session_paths(user_id):
    session_base = os.path.join("sessions", str(user_id))
    return f"{session_base}.session", f"{session_base}.session-journal"


def has_persisted_session(user_id):
    session_file, session_journal = get_session_paths(user_id)
    return os.path.exists(session_file) or os.path.exists(session_journal)


def _normalize_source_key(username: str) -> str:
    username = (username or "").strip().lstrip("@").lower()
    return f"@{username}" if username else ""


def _build_fetch_payload(source_chat: str, source_id: str, message_id: int) -> str:
    return json.dumps(
        {
            "type": "fetch",
            "source_chat": source_chat,
            "source_id": source_id,
            "message_id": message_id,
        }
    )


def _attach_channel_listener(user_id, client):
    if getattr(client, "_forward_listener_attached", False):
        return

    @client.on(events.NewMessage)
    async def on_new_message(event):
        if not event.is_channel:
            return

        chat = await event.get_chat()
        source_key = _normalize_source_key(getattr(chat, "username", None))
        source_id = str(get_peer_id(chat))
        malformed_source_id = f"@{source_id}" if source_id else ""

        if not source_key and not source_id:
            return

        mappings = db.fetchall(
            """
            SELECT mapping_id
            FROM mappings
            WHERE user_id=? AND active=1 AND LOWER(source_channel) IN (?, ?, ?)
            """,
            (
                user_id,
                source_key.lower() if source_key else "",
                source_id.lower(),
                malformed_source_id.lower(),
            ),
        )

        if not mappings:
            return

        source_ref = source_key or source_id
        payload = _build_fetch_payload(source_ref, source_id, event.message.id)
        for mapping in mappings:
            db.add_incoming_message(mapping["mapping_id"], source_ref, event.message.id, payload)

    client._forward_listener_attached = True


def create_client(user_id):
    existing = get_client(user_id)
    if existing:
        return existing

    os.makedirs("sessions", exist_ok=True)
    session_name = os.path.join("sessions", str(user_id))
    client = TelegramClient(session_name, USERBOT_API_ID, USERBOT_API_HASH)
    _attach_channel_listener(user_id, client)
    clients[user_id] = client
    return client


async def ensure_client_started(user_id):
    client = get_client(user_id) or create_client(user_id)
    if not client.is_connected():
        await client.connect()
    return client


async def restore_logged_in_clients():
    if not USERBOT_API_ID or not USERBOT_API_HASH:
        logger.warning("Userbot API credentials are missing; login and source monitoring are disabled.")
        return

    for user_id in db.get_logged_in_user_ids():
        try:
            client = await ensure_client_started(user_id)
            if await client.is_user_authorized():
                logger.info("Restored userbot session for user %s", user_id)
            else:
                logger.warning("Session for user %s is not authorized anymore", user_id)
        except Exception:
            logger.exception("Failed to restore userbot session for user %s", user_id)


async def disconnect_all_clients():
    for user_id, client in list(clients.items()):
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            logger.exception("Failed to disconnect userbot for user %s", user_id)
    clients.clear()
