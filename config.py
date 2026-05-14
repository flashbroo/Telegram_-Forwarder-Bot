# config.py

import os
import sqlite3
import time
from dotenv import load_dotenv

load_dotenv()

# --------------------
# BASIC CONFIG
# --------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip()


def _parse_admin_ids(raw: str) -> list[int]:
    admin_ids = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            admin_ids.append(int(part))
        except ValueError:
            continue
    return admin_ids


ADMIN_IDS = _parse_admin_ids(ADMIN_ID_RAW)
ADMIN_ID = ADMIN_IDS[0] if ADMIN_IDS else 0

# --------------------
# PAYMENT CONFIG
# --------------------

# Existing (kept for backward compatibility)
UPI_ID = os.getenv("UPI_ID", "").strip()                  # e.g. flashbro@ybl (manual UPI - optional now)
PAYPAL_LINK = os.getenv("PAYPAL_LINK", "").strip()        # PayPal.me/xyz
TELEGRAM_PROVIDER_TOKEN = os.getenv("TELEGRAM_PROVIDER_TOKEN", "").strip()
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "INR").strip()

# --------------------
# RAZORPAY (INDIA UPI AUTO PAY)
# --------------------

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()
RAZORPAY_BASE_URL = os.getenv("RAZORPAY_BASE_URL", "https://api.razorpay.com").strip()

# --------------------
# DATABASE
# --------------------

DB_PATH = os.getenv("DB_PATH", "bot.db").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# --------------------
# LOGGING
# --------------------

FORWARD_LOG_FILE = os.getenv("FORWARD_LOG_FILE", "forward_logs.txt").strip()

# --------------------
# FORCE SUBSCRIBE
# --------------------

FORCE_SUB_CHANNELS = [
    ch.strip()
    for ch in os.getenv("FORCE_SUB_CHANNELS", "").split(",")
    if ch.strip()
]

FORCE_SUB_MESSAGE = os.getenv(
    "FORCE_SUB_MESSAGE",
    "You must join required channels to use this bot."
).strip()

_SETTINGS_CACHE: dict[str, tuple[float, str]] = {}
_SETTINGS_TTL_SECONDS = 5.0


def _get_runtime_setting(key: str, default: str = "") -> str:
    now = time.time()
    cached = _SETTINGS_CACHE.get(key)
    if cached and now - cached[0] < _SETTINGS_TTL_SECONDS:
        return cached[1]

    if DATABASE_URL:
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor

            conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
            cur = conn.cursor()
            cur.execute("SELECT value FROM settings WHERE key=%s", (key,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            value = row["value"] if row and row["value"] is not None else default
            _SETTINGS_CACHE[key] = (now, value)
            return value
        except Exception:
            return default

    if not DB_PATH or not os.path.exists(DB_PATH):
        return default
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
        value = row["value"] if row and row["value"] is not None else default
        _SETTINGS_CACHE[key] = (now, value)
        return value
    except Exception:
        return default


def get_admin_ids() -> list[int]:
    stored_ids = _parse_admin_ids(_get_runtime_setting("ADMIN_IDS", ""))
    merged = []
    seen = set()
    for admin_id in ADMIN_IDS + stored_ids:
        if admin_id not in seen:
            seen.add(admin_id)
            merged.append(admin_id)
    return merged


def get_primary_admin_id() -> int:
    admin_ids = get_admin_ids()
    return admin_ids[0] if admin_ids else 0


def get_force_sub_channels() -> list[str]:
    raw = _get_runtime_setting("FORCE_SUB_CHANNELS", ",".join(FORCE_SUB_CHANNELS))
    return [ch.strip() for ch in raw.split(",") if ch.strip()]


def get_force_sub_message() -> str:
    return _get_runtime_setting("FORCE_SUB_MESSAGE", FORCE_SUB_MESSAGE).strip() or FORCE_SUB_MESSAGE


# --------------------
# CONFIG VALIDATION
# --------------------

def validate_config():
    """
    This prevents the bot from starting with broken or unsafe configuration.
    """

    if not BOT_TOKEN:
        raise RuntimeError("❌ BOT_TOKEN is missing in .env")

    if not get_primary_admin_id():
        raise RuntimeError("❌ ADMIN_ID is missing or invalid in .env")

    # Razorpay is required for India auto-pay
    if not RAZORPAY_KEY_ID:
        print("⚠️  RAZORPAY_KEY_ID not set (Razorpay payments disabled)")

    if not RAZORPAY_KEY_SECRET:
        print("⚠️  RAZORPAY_KEY_SECRET not set (Razorpay payments disabled)")

    if not RAZORPAY_WEBHOOK_SECRET:
        print("⚠️  RAZORPAY_WEBHOOK_SECRET not set (Webhook verification disabled)")

    print("✅ Config loaded successfully")

def is_admin(user_id: int) -> bool:
    return user_id in get_admin_ids()


# --------------------
# USERBOT CONFIG
# --------------------

USERBOT_API_ID = int(os.getenv("USERBOT_API_ID", "0"))
USERBOT_API_HASH = os.getenv("USERBOT_API_HASH", "").strip()
USERBOT_SESSION="userbot"
