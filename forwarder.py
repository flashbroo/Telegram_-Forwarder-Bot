# forwarder.py

import json
import logging
import asyncio
import random
import time
from io import BytesIO
from telegram.ext import ContextTypes
from telegram.error import Forbidden, RetryAfter, TimedOut

from db import fetchall, execute, fetchone, mark_forward_usage
from subscriptions import is_user_allowed
from utils import now_iso
from userbots.manager import ensure_client_started

logger = logging.getLogger(__name__)

MAX_FAILURES = 3

# ------------------------------
# ANTI-BAN RATE LIMITER
# ------------------------------

USER_RATE = {}
MAX_PER_MIN = 45

def get_safe_limit(user_id):
    return 20 if user_id not in USER_RATE else MAX_PER_MIN

def can_send(user_id):
    now = time.time()
    window = USER_RATE.get(user_id, [])

    window = [t for t in window if now - t < 60]

    if len(window) >= get_safe_limit(user_id):
        return False

    window.append(now)
    USER_RATE[user_id] = window
    return True

async def human_delay():
    await asyncio.sleep(random.uniform(0.15, 0.45))


# ------------------------------
# HELPERS
# ------------------------------

def unpack_channel(value: str):
    if "|" in value:
        return value.split("|", 1)[0]
    return value


async def load_source_message(user_id: int, payload: dict):
    client = await ensure_client_started(user_id)
    source_ref = payload.get("source_id") or payload.get("source_chat")
    message_id = payload["message_id"]

    if not source_ref:
        raise RuntimeError("Missing source reference")

    if isinstance(source_ref, str) and source_ref.lstrip("-").isdigit():
        source_ref = int(source_ref)

    msg = await client.get_messages(source_ref, ids=message_id)
    if not msg:
        raise RuntimeError("Source message not found")

    return msg


def message_caption(msg):
    return (getattr(msg, "message", None) or "").strip()


def make_upload(data: bytes, filename: str):
    upload = BytesIO(data)
    upload.name = filename
    upload.seek(0)
    return upload


async def resend_via_userbot(user_id: int, target: str, payload: dict):
    client = await ensure_client_started(user_id)
    msg = await load_source_message(user_id, payload)
    caption = message_caption(msg)
    target_ref = int(target) if isinstance(target, str) and target.lstrip("-").isdigit() else target

    if getattr(msg, "photo", None):
        data = await msg.download_media(file=bytes)
        if not data:
            raise RuntimeError("Failed to download photo")
        await client.send_file(
            target_ref,
            file=make_upload(data, f"photo_{msg.id}.jpg"),
            caption=caption or None,
        )
        return

    if getattr(msg, "video", None):
        data = await msg.download_media(file=bytes)
        if not data:
            raise RuntimeError("Failed to download video")
        await client.send_file(
            target_ref,
            file=make_upload(data, getattr(getattr(msg, "file", None), "name", None) or f"video_{msg.id}.mp4"),
            caption=caption or None,
            supports_streaming=True,
        )
        return

    if getattr(msg, "document", None):
        data = await msg.download_media(file=bytes)
        if not data:
            raise RuntimeError("Failed to download document")
        await client.send_file(
            target_ref,
            file=make_upload(data, getattr(getattr(msg, "file", None), "name", None) or f"document_{msg.id}.bin"),
            caption=caption or None,
        )
        return

    text = caption or getattr(msg, "raw_text", None)
    if text:
        await client.send_message(target_ref, text)
        return

    raise RuntimeError("Unsupported message type")


# ------------------------------
# DB LOGGING
# ------------------------------

def write_forward_log(
    mapping_id,
    user_id,
    message_id,
    source,
    target,
    status,
    error_text=""
):
    execute(
        """
        INSERT INTO forward_logs
        (mapping_id, user_id, message_id, source_channel, target_channel, status, error_text, ts)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            mapping_id,
            user_id,
            message_id,
            source,
            target,
            status,
            error_text,
            now_iso()
        )
    )


# ------------------------------
# FAILURE COUNTER
# ------------------------------

def recent_failure_count(mapping_id):
    row = fetchone(
        """
        SELECT COUNT(*) AS c FROM (
            SELECT 1 FROM forward_logs
            WHERE mapping_id=? AND status='FAILED'
            ORDER BY ts DESC
            LIMIT ?
        )
        """,
        (mapping_id, MAX_FAILURES)
    )
    return row["c"] if row else 0


# ------------------------------
# USERBOT → BOT DELIVERY PIPELINE
# ------------------------------

async def process_userbot_queue(context: ContextTypes.DEFAULT_TYPE):
    rows = fetchall(
        """
        SELECT *
        FROM incoming_messages
        WHERE status='pending'
        ORDER BY id ASC
        LIMIT 20
        """
    )

    for r in rows:
        msg_row_id = r["id"]
        mapping_id = r["mapping_id"]
        source = r["source_channel"]
        payload = json.loads(r["payload"])

        mapping = fetchone(
            "SELECT * FROM mappings WHERE mapping_id=? AND active=1",
            (mapping_id,)
        )

        if not mapping:
            execute(
                "UPDATE incoming_messages SET status='skipped' WHERE id=?",
                (msg_row_id,)
            )
            continue

        user_id = mapping["user_id"]
        target = unpack_channel(mapping["target_channel"])

        if not is_user_allowed(user_id):
            write_forward_log(
                mapping_id,
                user_id,
                r["message_id"],
                source,
                target,
                "SKIPPED",
                "Subscription inactive"
            )
            execute(
                "UPDATE incoming_messages SET status='blocked' WHERE id=?",
                (msg_row_id,)
            )
            continue

        try:
            # RATE LIMIT CHECK
            if not can_send(user_id):
                await asyncio.sleep(1)
                continue

            await human_delay()

            if payload["type"] in ("copy", "fetch"):
                await resend_via_userbot(user_id, target, payload)

            elif payload["type"] == "text":
                client = await ensure_client_started(user_id)
                await client.send_message(target, payload["text"])

            elif payload["type"] == "photo":
                raise RuntimeError("Legacy bot photo forwarding is no longer supported")

            elif payload["type"] == "video":
                raise RuntimeError("Legacy bot video forwarding is no longer supported")

            elif payload["type"] == "document":
                raise RuntimeError("Legacy bot document forwarding is no longer supported")

            write_forward_log(
                mapping_id,
                user_id,
                r["message_id"],
                source,
                target,
                "OK"
            )

            execute(
                "UPDATE incoming_messages SET status='sent' WHERE id=?",
                (msg_row_id,)
            )
            mark_forward_usage(user_id)

        except Forbidden:
            write_forward_log(
                mapping_id,
                user_id,
                r["message_id"],
                source,
                target,
                "FAILED",
                "Target post forbidden"
            )

            failures = recent_failure_count(mapping_id)

            if failures >= MAX_FAILURES:
                execute(
                    "UPDATE mappings SET active=0 WHERE mapping_id=?",
                    (mapping_id,)
                )
                logger.warning(f"Mapping {mapping_id} auto-disabled due to failures")

            execute(
                "UPDATE incoming_messages SET status='failed' WHERE id=?",
                (msg_row_id,)
            )

        except RetryAfter as e:
            wait_time = int(e.retry_after) + random.randint(2, 5)
            logger.warning(f"Flood detected. Sleeping {wait_time}s")
            await asyncio.sleep(wait_time)
            continue

        except TimedOut:
            await asyncio.sleep(1)
            continue

        except Exception as e:
            write_forward_log(
                mapping_id,
                user_id,
                r["message_id"],
                source,
                target,
                "FAILED",
                str(e)
            )
            execute(
                "UPDATE incoming_messages SET status='failed' WHERE id=?",
                (msg_row_id,)
            )

        # GLOBAL SAFETY DELAY
        await asyncio.sleep(random.uniform(0.05, 0.2))
