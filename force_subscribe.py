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

async def is_joined(bot, user_id: int) -> bool:
    now = time.time()
    cached = _JOIN_CACHE.get(user_id)

    if cached and now - cached["ts"] < _JOIN_CACHE_TTL:
        return cached["ok"]

    channels = get_force_channels()
    if not channels:
        return True

    for ch in channels:
        try:
            member = await bot.get_chat_member(ch, user_id)
            if member.status not in ALLOWED:
                _JOIN_CACHE[user_id] = {"ok": False, "ts": now}
                return False
        except Exception:
            _JOIN_CACHE[user_id] = {"ok": False, "ts": now}
            return False

    _JOIN_CACHE[user_id] = {"ok": True, "ts": now}
    return True



# -----------------------------
# JOIN KEYBOARD
# -----------------------------

def join_keyboard():
    channels = get_force_channels()
    kb = []

    for ch in channels:
        url = f"https://t.me/{ch.lstrip('@')}"
        kb.append([InlineKeyboardButton(f"Join {ch}", url=url)])

    kb.append([InlineKeyboardButton("✅ I Joined", callback_data="check_join")])

    return InlineKeyboardMarkup(kb)


def get_force_message() -> str:
    return config.get_force_sub_message()

