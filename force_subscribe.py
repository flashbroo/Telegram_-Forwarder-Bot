# force_subscribe.py

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import logging
import time
import config

logger = logging.getLogger(__name__)

ALLOWED = ("member", "administrator", "creator")


# -----------------------------
# VALIDATION
# -----------------------------

def normalize_channel(ch: str) -> str:
    ch = ch.strip()
    if not ch:
        return ""
    if ch.startswith("-") and ch[1:].isdigit():
        return ch
    if not ch.startswith("@"):
        ch = "@" + ch
    return ch


def get_force_channels():
    valid = []
    for ch in config.get_force_sub_channels():
        ch = normalize_channel(ch)
        if ch:
            valid.append(ch)
    return valid


# -----------------------------
# JOIN CHECK
# -----------------------------

_JOIN_CACHE = {}
_JOIN_CACHE_TTL = 30  # seconds

async def check_join_status(bot, user_id: int, use_cache: bool = True) -> dict:
    now = time.time()
    cached = _JOIN_CACHE.get(user_id)

    if use_cache and cached and cached["ok"] and now - cached["ts"] < _JOIN_CACHE_TTL:
        return cached

    channels = get_force_channels()
    if not channels:
        return {"ok": True, "missing": [], "checked": [], "ts": now}

    missing = []
    checked = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(ch, user_id)
            checked.append({"channel": ch, "status": member.status, "error": ""})
            if member.status not in ALLOWED:
                missing.append({"channel": ch, "status": member.status, "error": ""})
        except Exception as exc:
            logger.warning(
                "Force-subscribe membership check failed: user_id=%s channel=%s error=%s",
                user_id,
                ch,
                exc,
            )
            checked.append({"channel": ch, "status": "unknown", "error": str(exc)})
            missing.append({"channel": ch, "status": "unknown", "error": str(exc)})

    status = {"ok": not missing, "missing": missing, "checked": checked, "ts": now}
    if status["ok"]:
        _JOIN_CACHE[user_id] = status
    else:
        _JOIN_CACHE.pop(user_id, None)
    return status


async def is_joined(bot, user_id: int) -> bool:
    return (await check_join_status(bot, user_id))["ok"]


def missing_channels_text(status: dict) -> str:
    missing = status.get("missing") or []
    if not missing:
        return ""

    lines = ["\n\nStill missing:"]
    for item in missing:
        channel = item.get("channel", "")
        detail = item.get("status") or "not joined"
        lines.append(f"- {channel} ({detail})")
    return "\n".join(lines)



# -----------------------------
# JOIN KEYBOARD
# -----------------------------

def join_keyboard():
    channels = get_force_channels()
    kb = []

    for ch in channels:
        url = f"https://t.me/{ch.lstrip('@')}"
        kb.append([InlineKeyboardButton(f"Join {ch}", url=url)])

    kb.append([InlineKeyboardButton("I Joined", callback_data="check_join")])

    return InlineKeyboardMarkup(kb)


def get_force_message() -> str:
    return config.get_force_sub_message()


