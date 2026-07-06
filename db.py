import sqlite3
import threading
import logging
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL, DB_PATH

IS_POSTGRES = bool(DATABASE_URL)
logger = logging.getLogger(__name__)


class RowAdapter(dict):
    def __getattr__(self, item):
        return self[item]


_lock = threading.Lock()


def now_iso():
    return datetime.utcnow().isoformat()


def _translate_query(query: str) -> str:
    translated = query

    replacements = {
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)": (
            "INSERT INTO settings (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value"
        ),
        "INSERT OR IGNORE INTO incoming_messages": "INSERT INTO incoming_messages",
        "INSERT OR IGNORE INTO mappings": "INSERT INTO mappings",
    }

    for source, target in replacements.items():
        if source in translated:
            translated = translated.replace(source, target)

    translated = translated.replace("?", "%s")

    if "INSERT INTO incoming_messages" in translated and "ON CONFLICT" not in translated:
        translated += " ON CONFLICT (mapping_id, source_channel, message_id) DO NOTHING"

    if "INSERT INTO mappings" in translated and "ON CONFLICT" not in translated and "VALUES (%s, %s, %s, 1, %s)" in translated:
        translated += " ON CONFLICT (user_id, source_channel, target_channel) DO NOTHING"

    return translated


if IS_POSTGRES:
    _conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    _conn.autocommit = False
    _cur = _conn.cursor()
else:
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _cur = _conn.cursor()
    _cur.execute("PRAGMA journal_mode=WAL")
    _cur.execute("PRAGMA synchronous=NORMAL")
    _cur.execute("PRAGMA foreign_keys=ON")


def _has_column(table: str, column: str) -> bool:
    if IS_POSTGRES:
        row = _cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name=%s AND column_name=%s
            LIMIT 1
            """,
            (table, column),
        )
        _conn.commit()
        return bool(_cur.fetchone())

    rows = _cur.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def init_db():
    with _lock:
        if IS_POSTGRES:
            statements = [
                """
                CREATE TABLE IF NOT EXISTS users(
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    free_access INTEGER DEFAULT 0,
                    total_commands INTEGER DEFAULT 0,
                    total_forwards INTEGER DEFAULT 0,
                    last_seen TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS subscriptions(
                    sub_id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT,
                    plan_key TEXT,
                    start_ts TEXT,
                    expiry_ts TEXT,
                    status TEXT,
                    provider TEXT,
                    provider_payment_id TEXT,
                    amount DOUBLE PRECISION
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS payments(
                    payment_id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT,
                    provider TEXT,
                    payload TEXT,
                    status TEXT,
                    ts TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS razorpay_orders(
                    order_id TEXT PRIMARY KEY,
                    user_id BIGINT,
                    plan_key TEXT,
                    amount DOUBLE PRECISION,
                    currency TEXT,
                    status TEXT,
                    receipt TEXT,
                    created_ts TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS razorpay_payments(
                    payment_id TEXT PRIMARY KEY,
                    order_id TEXT,
                    user_id BIGINT,
                    amount DOUBLE PRECISION,
                    status TEXT,
                    method TEXT,
                    payload TEXT,
                    ts TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS mappings(
                    mapping_id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT,
                    source_channel TEXT,
                    target_channel TEXT,
                    filters TEXT,
                    watermark INTEGER DEFAULT 0,
                    active INTEGER DEFAULT 1,
                    created_at TEXT
                )
                """,
                "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)",
                """
                CREATE TABLE IF NOT EXISTS plans (
                    plan_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    price DOUBLE PRECISION NOT NULL,
                    currency TEXT NOT NULL,
                    duration_days INTEGER NOT NULL,
                    features TEXT,
                    audience TEXT DEFAULT 'GLOBAL',
                    provider TEXT DEFAULT 'razorpay',
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS admin_logs (
                    id BIGSERIAL PRIMARY KEY,
                    admin_id BIGINT,
                    action TEXT,
                    payload TEXT,
                    ts TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS forward_logs (
                    log_id BIGSERIAL PRIMARY KEY,
                    mapping_id BIGINT,
                    user_id BIGINT,
                    message_id BIGINT,
                    source_channel TEXT,
                    target_channel TEXT,
                    status TEXT,
                    error_text TEXT,
                    ts TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS incoming_messages (
                    id BIGSERIAL PRIMARY KEY,
                    mapping_id BIGINT,
                    source_channel TEXT,
                    message_id BIGINT,
                    payload TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS saved_channels (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT,
                    channel_key TEXT,
                    title TEXT,
                    role TEXT,
                    created_at TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS userbot_queue (
                    id BIGSERIAL PRIMARY KEY,
                    mapping_id BIGINT,
                    user_id BIGINT,
                    source_channel TEXT,
                    target_channel TEXT,
                    payload TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS pinned_chats (
                    chat_id TEXT PRIMARY KEY,
                    title TEXT,
                    username TEXT,
                    chat_type TEXT,
                    is_pinned INTEGER DEFAULT 1,
                    updated_at TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS pinned_dialogs (
                    user_id BIGINT,
                    dialog_id TEXT,
                    peer_id TEXT,
                    dialog_type TEXT,
                    title TEXT,
                    username TEXT,
                    is_pinned INTEGER DEFAULT 1,
                    can_post INTEGER DEFAULT 0,
                    display_order INTEGER DEFAULT 0,
                    last_sync TEXT,
                    PRIMARY KEY (user_id, dialog_id)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS dialog_sync_state (
                    user_id BIGINT PRIMARY KEY,
                    sync_state TEXT NOT NULL,
                    last_sync_at TEXT,
                    sync_version BIGINT DEFAULT 0,
                    error_text TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS userbots (
                    userbot_id BIGINT PRIMARY KEY,
                    name TEXT,
                    active INTEGER DEFAULT 1,
                    created_at TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS userbot_phones (
                    phone TEXT PRIMARY KEY,
                    user_id BIGINT,
                    created_at TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS subscription_reminders (
                    user_id BIGINT PRIMARY KEY,
                    last_days_left INTEGER,
                    last_sent_ts TEXT
                )
                """,
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_mappings_unique_pair ON mappings(user_id, source_channel, target_channel)",
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_incoming_unique_message ON incoming_messages(mapping_id, source_channel, message_id)",
                "CREATE INDEX IF NOT EXISTS idx_incoming_status_id ON incoming_messages(status, id)",
                "CREATE INDEX IF NOT EXISTS idx_pinned_dialogs_user_order ON pinned_dialogs(user_id, is_pinned, display_order)",
                "CREATE INDEX IF NOT EXISTS idx_pinned_dialogs_user_target_order ON pinned_dialogs(user_id, can_post, display_order)",
            ]
            for statement in statements:
                _cur.execute(statement)

            if not _has_column("users", "username"):
                _cur.execute("ALTER TABLE users ADD COLUMN username TEXT")
            if not _has_column("users", "first_name"):
                _cur.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
            if not _has_column("users", "last_name"):
                _cur.execute("ALTER TABLE users ADD COLUMN last_name TEXT")
            _conn.commit()
            return

        statements = [
            """
            CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                free_access INTEGER DEFAULT 0,
                total_commands INTEGER DEFAULT 0,
                total_forwards INTEGER DEFAULT 0,
                last_seen TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS subscriptions(
                sub_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                plan_key TEXT,
                start_ts TEXT,
                expiry_ts TEXT,
                status TEXT,
                provider TEXT,
                provider_payment_id TEXT,
                amount REAL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS payments(
                payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                provider TEXT,
                payload TEXT,
                status TEXT,
                ts TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS razorpay_orders(
                order_id TEXT PRIMARY KEY,
                user_id INTEGER,
                plan_key TEXT,
                amount REAL,
                currency TEXT,
                status TEXT,
                receipt TEXT,
                created_ts TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS razorpay_payments(
                payment_id TEXT PRIMARY KEY,
                order_id TEXT,
                user_id INTEGER,
                amount REAL,
                status TEXT,
                method TEXT,
                payload TEXT,
                ts TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS mappings(
                mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                source_channel TEXT,
                target_channel TEXT,
                filters TEXT,
                watermark INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                created_at TEXT
            )
            """,
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)",
            """
            CREATE TABLE IF NOT EXISTS plans (
                plan_key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                currency TEXT NOT NULL,
                duration_days INTEGER NOT NULL,
                features TEXT,
                audience TEXT DEFAULT 'GLOBAL',
                provider TEXT DEFAULT 'razorpay',
                is_active INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action TEXT,
                payload TEXT,
                ts TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS forward_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mapping_id INTEGER,
                user_id INTEGER,
                message_id INTEGER,
                source_channel TEXT,
                target_channel TEXT,
                status TEXT,
                error_text TEXT,
                ts TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS incoming_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mapping_id INTEGER,
                source_channel TEXT,
                message_id INTEGER,
                payload TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS saved_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                channel_key TEXT,
                title TEXT,
                role TEXT,
                created_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS userbot_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mapping_id INTEGER,
                user_id INTEGER,
                source_channel TEXT,
                target_channel TEXT,
                payload TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
            """,
                """
                CREATE TABLE IF NOT EXISTS pinned_chats (
                    chat_id TEXT PRIMARY KEY,
                    title TEXT,
                    username TEXT,
                chat_type TEXT,
                is_pinned INTEGER DEFAULT 1,
                    updated_at TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS pinned_dialogs (
                    user_id INTEGER,
                    dialog_id TEXT,
                    peer_id TEXT,
                    dialog_type TEXT,
                    title TEXT,
                    username TEXT,
                    is_pinned INTEGER DEFAULT 1,
                    can_post INTEGER DEFAULT 0,
                    display_order INTEGER DEFAULT 0,
                    last_sync TEXT,
                    PRIMARY KEY (user_id, dialog_id)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS dialog_sync_state (
                    user_id INTEGER PRIMARY KEY,
                    sync_state TEXT NOT NULL,
                    last_sync_at TEXT,
                    sync_version INTEGER DEFAULT 0,
                    error_text TEXT
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS userbots (
                    userbot_id INTEGER PRIMARY KEY,
                    name TEXT,
                    active INTEGER DEFAULT 1,
                created_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS userbot_phones (
                phone TEXT PRIMARY KEY,
                user_id INTEGER,
                created_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS subscription_reminders (
                user_id INTEGER PRIMARY KEY,
                last_days_left INTEGER,
                last_sent_ts TEXT
            )
            """,
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_mappings_unique_pair ON mappings(user_id, source_channel, target_channel)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_incoming_unique_message ON incoming_messages(mapping_id, source_channel, message_id)",
            "CREATE INDEX IF NOT EXISTS idx_incoming_status_id ON incoming_messages(status, id)",
            "CREATE INDEX IF NOT EXISTS idx_pinned_dialogs_user_order ON pinned_dialogs(user_id, is_pinned, display_order)",
            "CREATE INDEX IF NOT EXISTS idx_pinned_dialogs_user_target_order ON pinned_dialogs(user_id, can_post, display_order)",
        ]
        for statement in statements:
            _cur.execute(statement)

        if not _has_column("plans", "audience"):
            _cur.execute("ALTER TABLE plans ADD COLUMN audience TEXT DEFAULT 'GLOBAL'")
        if not _has_column("plans", "provider"):
            _cur.execute("ALTER TABLE plans ADD COLUMN provider TEXT DEFAULT 'razorpay'")
        if not _has_column("users", "username"):
            _cur.execute("ALTER TABLE users ADD COLUMN username TEXT")
        if not _has_column("users", "first_name"):
            _cur.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
        if not _has_column("users", "last_name"):
            _cur.execute("ALTER TABLE users ADD COLUMN last_name TEXT")

        _conn.commit()


def _normalize_result(row):
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        return RowAdapter(dict(row))
    if isinstance(row, dict):
        return RowAdapter(row)
    return row


def execute(query, params=()):
    with _lock:
        translated = _translate_query(query) if IS_POSTGRES else query
        _cur.execute(translated, params)
        _conn.commit()
        return _cur


def fetchone(query, params=()):
    with _lock:
        translated = _translate_query(query) if IS_POSTGRES else query
        _cur.execute(translated, params)
        return _normalize_result(_cur.fetchone())


def fetchall(query, params=()):
    with _lock:
        translated = _translate_query(query) if IS_POSTGRES else query
        _cur.execute(translated, params)
        rows = _cur.fetchall()
        return [_normalize_result(row) for row in rows]


def ensure_user(user_id: int):
    row = fetchone("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if not row:
        execute(
            """
            INSERT INTO users (user_id, created_at, updated_at, last_seen)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, now_iso(), now_iso(), now_iso()),
        )


def touch_user(user_id: int):
    ensure_user(user_id)
    execute(
        """
        UPDATE users
        SET total_commands = total_commands + 1,
            last_seen = ?,
            updated_at = ?
        WHERE user_id = ?
        """,
        (now_iso(), now_iso(), user_id),
    )


def sync_user_profile(user_id: int, username: str = "", first_name: str = "", last_name: str = ""):
    ensure_user(user_id)
    execute(
        """
        UPDATE users
        SET username = ?,
            first_name = ?,
            last_name = ?,
            updated_at = ?
        WHERE user_id = ?
        """,
        (
            (username or "").strip() or None,
            (first_name or "").strip() or None,
            (last_name or "").strip() or None,
            now_iso(),
            user_id,
        ),
    )


def mark_forward_usage(user_id: int):
    ensure_user(user_id)
    execute(
        """
        UPDATE users
        SET total_forwards = total_forwards + 1,
            last_seen = ?,
            updated_at = ?
        WHERE user_id = ?
        """,
        (now_iso(), now_iso(), user_id),
    )


def get_setting(key, default=None):
    row = fetchone("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


def set_setting(key, value):
    if IS_POSTGRES:
        execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
            """,
            (key, str(value)),
        )
        return
    execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))


def get_active_plans(audience: str | None = None):
    if audience:
        return fetchall(
            """
            SELECT *
            FROM plans
            WHERE is_active=1 AND (audience=? OR audience='GLOBAL')
            ORDER BY price ASC
            """,
            (audience.upper(),),
        )
    return fetchall("SELECT * FROM plans WHERE is_active=1 ORDER BY price ASC")


def get_plan(plan_key: str):
    return fetchone("SELECT * FROM plans WHERE plan_key=? AND is_active=1", (plan_key,))


def cleanup_expired_subscriptions():
    execute(
        "UPDATE subscriptions SET status='expired' WHERE status='active' AND expiry_ts < ?",
        (now_iso(),),
    )


def normalize_phone(phone: str):
    return phone.strip().replace(" ", "")


def get_phone_owner(phone: str):
    phone = normalize_phone(phone)
    row = fetchone("SELECT user_id FROM userbot_phones WHERE phone=?", (phone,))
    return row["user_id"] if row else None


def assign_phone(phone: str, user_id: int):
    phone = normalize_phone(phone)
    if IS_POSTGRES:
        execute(
            """
            INSERT INTO userbot_phones (phone, user_id, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT (phone) DO UPDATE SET user_id=EXCLUDED.user_id, created_at=EXCLUDED.created_at
            """,
            (phone, user_id, now_iso()),
        )
        return
    execute(
        """
        INSERT OR REPLACE INTO userbot_phones (phone, user_id, created_at)
        VALUES (?, ?, ?)
        """,
        (phone, user_id, now_iso()),
    )


def remove_phone(user_id: int):
    execute("DELETE FROM userbot_phones WHERE user_id=?", (user_id,))


def get_user_phone(user_id: int):
    row = fetchone("SELECT phone FROM userbot_phones WHERE user_id=?", (user_id,))
    return row["phone"] if row else None


def get_logged_in_user_ids():
    rows = fetchall(
        """
        SELECT DISTINCT user_id
        FROM userbot_phones
        WHERE user_id IS NOT NULL
        ORDER BY user_id ASC
        """
    )
    return [row["user_id"] for row in rows]


def set_dialog_sync_state(user_id: int, sync_state: str, sync_version: int | None = None, error_text: str = ""):
    timestamp = now_iso()
    current = get_dialog_sync_state(user_id)
    version = sync_version if sync_version is not None else ((current["sync_version"] if current else 0) + 1)

    if IS_POSTGRES:
        execute(
            """
            INSERT INTO dialog_sync_state (user_id, sync_state, last_sync_at, sync_version, error_text)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (user_id) DO UPDATE SET
                sync_state=EXCLUDED.sync_state,
                last_sync_at=EXCLUDED.last_sync_at,
                sync_version=EXCLUDED.sync_version,
                error_text=EXCLUDED.error_text
            """,
            (user_id, sync_state, timestamp, version, error_text[:1000]),
        )
        return

    execute(
        """
        INSERT OR REPLACE INTO dialog_sync_state (user_id, sync_state, last_sync_at, sync_version, error_text)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, sync_state, timestamp, version, error_text[:1000]),
    )


def get_dialog_sync_state(user_id: int):
    return fetchone(
        """
        SELECT user_id, sync_state, last_sync_at, sync_version, error_text
        FROM dialog_sync_state
        WHERE user_id=?
        """,
        (user_id,),
    )


def replace_pinned_dialogs(user_id: int, dialogs: list[dict], sync_version: int | None = None) -> int:
    sync_version = sync_version if sync_version is not None else int(datetime.utcnow().timestamp() * 1000)

    if dialogs is None:
        raise ValueError("dialogs cannot be None")
    for index, dialog in enumerate(dialogs):
        if not dialog.get("dialog_id"):
            raise ValueError(f"dialog at index {index} is missing dialog_id")
        if "display_order" not in dialog:
            raise ValueError(f"dialog at index {index} is missing display_order")

    if IS_POSTGRES:
        insert_query = _translate_query(
            """
            INSERT INTO pinned_dialogs
            (user_id, dialog_id, peer_id, dialog_type, title, username, is_pinned, can_post, display_order, last_sync)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (user_id, dialog_id)
            DO UPDATE SET
                peer_id=EXCLUDED.peer_id,
                dialog_type=EXCLUDED.dialog_type,
                title=EXCLUDED.title,
                username=EXCLUDED.username,
                is_pinned=EXCLUDED.is_pinned,
                can_post=EXCLUDED.can_post,
                display_order=EXCLUDED.display_order,
                last_sync=EXCLUDED.last_sync
            """
        )
        state_query = _translate_query(
            """
            INSERT INTO dialog_sync_state (user_id, sync_state, last_sync_at, sync_version, error_text)
            VALUES (?, 'READY', ?, ?, '')
            ON CONFLICT (user_id) DO UPDATE SET
                sync_state=EXCLUDED.sync_state,
                last_sync_at=EXCLUDED.last_sync_at,
                sync_version=EXCLUDED.sync_version,
                error_text=EXCLUDED.error_text
            """
        )
    else:
        insert_query = """
            INSERT OR REPLACE INTO pinned_dialogs
            (user_id, dialog_id, peer_id, dialog_type, title, username, is_pinned, can_post, display_order, last_sync)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        state_query = """
            INSERT OR REPLACE INTO dialog_sync_state (user_id, sync_state, last_sync_at, sync_version, error_text)
            VALUES (?, 'READY', ?, ?, '')
        """

    with _lock:
        try:
            delete_query = _translate_query("DELETE FROM pinned_dialogs WHERE user_id=?") if IS_POSTGRES else "DELETE FROM pinned_dialogs WHERE user_id=?"
            before_query = (
                _translate_query("SELECT dialog_id, title FROM pinned_dialogs WHERE user_id=? ORDER BY display_order ASC")
                if IS_POSTGRES
                else "SELECT dialog_id, title FROM pinned_dialogs WHERE user_id=? ORDER BY display_order ASC"
            )
            _cur.execute(before_query, (user_id,))
            before_rows = _cur.fetchall()
            before_ids = [row["dialog_id"] for row in before_rows]

            logger.info(
                "Pinned dialog DB update start: user_id=%s before_count=%s before_ids=%s incoming_count=%s incoming_ids=%s",
                user_id,
                len(before_rows),
                before_ids,
                len(dialogs),
                [dialog["dialog_id"] for dialog in dialogs],
            )

            _cur.execute(delete_query, (user_id,))
            for dialog in dialogs:
                _cur.execute(
                    insert_query,
                    (
                        user_id,
                        dialog["dialog_id"],
                        dialog.get("peer_id") or dialog["dialog_id"],
                        dialog.get("dialog_type") or "unknown",
                        dialog.get("title") or dialog["dialog_id"],
                        dialog.get("username") or "",
                        1 if dialog.get("is_pinned") else 0,
                        1 if dialog.get("can_post") else 0,
                        dialog["display_order"],
                        dialog.get("last_sync") or now_iso(),
                    ),
                )
            _cur.execute(state_query, (user_id, now_iso(), sync_version))
            _cur.execute(before_query, (user_id,))
            after_rows = _cur.fetchall()
            logger.info(
                "Pinned dialog DB update success: user_id=%s after_count=%s after_ids=%s sync_version=%s",
                user_id,
                len(after_rows),
                [row["dialog_id"] for row in after_rows],
                sync_version,
            )
            _conn.commit()
            return len(dialogs)
        except Exception:
            _conn.rollback()
            raise


def get_pinned_dialogs(user_id: int, role: str = "source", limit: int = 15):
    if role == "target":
        total_row = fetchone(
            """
            SELECT COUNT(*) AS c
            FROM pinned_dialogs
            WHERE user_id=? AND is_pinned=1 AND can_post=1 AND dialog_type IN ('channel', 'group', 'supergroup')
            """,
            (user_id,),
        )
        rows = fetchall(
            """
            SELECT *
            FROM pinned_dialogs
            WHERE user_id=? AND is_pinned=1 AND can_post=1 AND dialog_type IN ('channel', 'group', 'supergroup')
            ORDER BY display_order ASC
            LIMIT ?
            """,
            (user_id, limit),
        )
        logger.info(
            "Pinned dialog repository return: user_id=%s role=%s total_matching=%s limit=%s returned=%s ids=%s",
            user_id,
            role,
            total_row["c"] if total_row else 0,
            limit,
            len(rows),
            [row["dialog_id"] for row in rows],
        )
        return rows

    total_row = fetchone(
        """
        SELECT COUNT(*) AS c
        FROM pinned_dialogs
        WHERE user_id=? AND is_pinned=1
        """,
        (user_id,),
    )
    rows = fetchall(
        """
        SELECT *
        FROM pinned_dialogs
        WHERE user_id=? AND is_pinned=1
        ORDER BY display_order ASC
        LIMIT ?
        """,
        (user_id, limit),
    )
    logger.info(
        "Pinned dialog repository return: user_id=%s role=%s total_matching=%s limit=%s returned=%s ids=%s",
        user_id,
        role,
        total_row["c"] if total_row else 0,
        limit,
        len(rows),
        [row["dialog_id"] for row in rows],
    )
    return rows


def add_incoming_message(mapping_id: int, source_channel: str, message_id: int, payload: str):
    if IS_POSTGRES:
        execute(
            """
            INSERT INTO incoming_messages
            (mapping_id, source_channel, message_id, payload, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            ON CONFLICT (mapping_id, source_channel, message_id) DO NOTHING
            """,
            (mapping_id, source_channel, message_id, payload, now_iso()),
        )
        return
    execute(
        """
        INSERT OR IGNORE INTO incoming_messages
        (mapping_id, source_channel, message_id, payload, status, created_at)
        VALUES (?, ?, ?, ?, 'pending', ?)
        """,
        (mapping_id, source_channel, message_id, payload, now_iso()),
    )


def migrate_target_channel_ids():
    rows = fetchall(
        """
        SELECT mapping_id, user_id, target_channel
        FROM mappings
        WHERE target_channel NOT LIKE '-%'
        """
    )

    for row in rows:
        target = str(row["target_channel"])
        candidate = f"-100{target}" if target.isdigit() else None
        if not candidate:
            continue

        saved = fetchone(
            """
            SELECT 1
            FROM saved_channels
            WHERE user_id=? AND channel_key=?
            LIMIT 1
            """,
            (row["user_id"], candidate),
        )
        if saved:
            execute("UPDATE mappings SET target_channel=? WHERE mapping_id=?", (candidate, row["mapping_id"]))


init_db()
