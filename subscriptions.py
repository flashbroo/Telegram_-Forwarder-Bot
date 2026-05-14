import math
from datetime import datetime, timedelta
from typing import Optional

from db import execute, fetchall, fetchone, get_plan, get_setting


# ------------------------------
# USER REGISTRATION (AUTO)
# ------------------------------

def register_user(user_id: int):
    """
    Ensures user is registered in DB.
    Called on any real usage (commands, forwarding, payment, etc)
    """
    execute(
        """
        INSERT INTO users (user_id, free_access, created_at, updated_at)
        VALUES (?, 0, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            updated_at=excluded.updated_at
        """,
        (user_id, datetime.utcnow().isoformat(), datetime.utcnow().isoformat())
    )


# ------------------------------
# ACTIVATE SUBSCRIPTION (PAID)
# ------------------------------

def activate_subscription(
    user_id: int,
    plan_key: str,
    provider: str,
    provider_payment_id: str,
    amount: float,
) -> bool:
    """
    Activates (or extends) a subscription safely.
    - Extends from existing expiry
    - Idempotent by payment id
    """

    register_user(user_id)

    plan = get_plan(plan_key)
    if not plan:
        return False

    # Prevent double credit
    if provider_payment_id:
        dup = fetchone(
            "SELECT 1 FROM subscriptions WHERE provider=? AND provider_payment_id=?",
            (provider, provider_payment_id),
        )
        if dup:
            return True

    duration_days = int(plan["duration_days"])

    # Check current active subscription
    row = fetchone(
        "SELECT * FROM subscriptions WHERE user_id=? AND status='active' "
        "ORDER BY expiry_ts DESC LIMIT 1",
        (user_id,),
    )

    now = datetime.utcnow()

    if row and row["expiry_ts"]:
        try:
            current_expiry = datetime.fromisoformat(row["expiry_ts"])
        except Exception:
            current_expiry = now

        start = current_expiry if current_expiry > now else now
    else:
        start = now

    expiry = (start + timedelta(days=duration_days)).isoformat()

    execute(
        """
        INSERT INTO subscriptions
        (user_id, plan_key, start_ts, expiry_ts, status, provider, provider_payment_id, amount)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            user_id,
            plan_key,
            now.isoformat(),
            expiry,
            "active",
            provider,
            provider_payment_id,
            float(amount),
        ),
    )

    return True


# ------------------------------
# MANUAL ACTIVATE (ADMIN)
# ------------------------------

def manual_activate(user_id: int, plan_key: str, days: Optional[int] = None) -> bool:
    register_user(user_id)

    plan = get_plan(plan_key)

    if not plan and not days:
        return False

    duration_days = int(days) if days is not None else int(plan["duration_days"])

    now = datetime.utcnow()
    expiry = (now + timedelta(days=duration_days)).isoformat()

    execute(
        """
        INSERT INTO subscriptions
        (user_id, plan_key, start_ts, expiry_ts, status, provider, provider_payment_id, amount)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            user_id,
            plan_key,
            now.isoformat(),
            expiry,
            "active",
            "manual",
            "manual",
            0.0,
        ),
    )
    return True


# ------------------------------
# QUERY HELPERS
# ------------------------------

def get_user_active_subscription(user_id: int) -> Optional[dict]:
    row = fetchone(
        "SELECT * FROM subscriptions WHERE user_id=? AND status='active' "
        "ORDER BY expiry_ts DESC LIMIT 1",
        (user_id,),
    )
    return dict(row) if row else None


def mark_expired_subscriptions():
    """
    Marks expired subscriptions as 'expired'
    """
    execute(
        "UPDATE subscriptions SET status='expired' "
        "WHERE status='active' AND expiry_ts < ?",
        (datetime.utcnow().isoformat(),),
    )


# ------------------------------
# ACCESS STATE (SINGLE SOURCE)
# ------------------------------

def get_user_access_state(user_id: int) -> str:
    """
    Returns:
    FREE_ALL   -> bot free for everyone
    FREE_USER  -> user manually granted free
    PAID       -> active paid user
    BLOCKED    -> no access
    """

    register_user(user_id)
    mark_expired_subscriptions()

    # Global free mode
    free_mode = get_setting("FREE_MODE", "0")
    if free_mode == "1":
        return "FREE_ALL"

    # Per-user free access
    user = fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))
    if user and user["free_access"]:
        return "FREE_USER"

    # Paid subscription

    row = fetchone(
        "SELECT * FROM subscriptions WHERE user_id=? AND status='active' "
        "ORDER BY expiry_ts DESC LIMIT 1",
        (user_id,),
    )

    if row and row["expiry_ts"]:
        try:
            expiry = datetime.fromisoformat(row["expiry_ts"])
            if expiry > datetime.utcnow():
                return "PAID"
        except Exception:
            pass

    return "BLOCKED"


def is_user_allowed(user_id: int) -> bool:
    """
    Boolean helper used across the app.
    """
    return get_user_access_state(user_id) in ("FREE_ALL", "FREE_USER", "PAID")


def days_left_until(expiry_ts: str) -> int | None:
    try:
        expiry = datetime.fromisoformat(expiry_ts)
    except Exception:
        return None

    seconds_left = (expiry - datetime.utcnow()).total_seconds()
    if seconds_left <= 0:
        return 0
    return max(1, math.ceil(seconds_left / 86400))


def get_due_expiry_reminders() -> list[dict]:
    mark_expired_subscriptions()

    rows = fetchall(
        """
        SELECT user_id, plan_key, expiry_ts
        FROM subscriptions
        WHERE status='active'
        ORDER BY user_id ASC, expiry_ts DESC
        """
    )

    latest_by_user = {}
    for row in rows:
        if row["user_id"] not in latest_by_user:
            latest_by_user[row["user_id"]] = dict(row)

    due = []
    now = datetime.utcnow()

    for row in latest_by_user.values():
        days_left = days_left_until(row["expiry_ts"])
        if days_left is None or days_left < 1 or days_left > 5:
            continue

        reminder = fetchone(
            "SELECT last_days_left, last_sent_ts FROM subscription_reminders WHERE user_id=?",
            (row["user_id"],),
        )

        if reminder:
            if reminder["last_days_left"] == days_left:
                continue
            if reminder["last_sent_ts"]:
                try:
                    last_sent = datetime.fromisoformat(reminder["last_sent_ts"])
                    if now - last_sent < timedelta(hours=24):
                        continue
                except Exception:
                    pass

        row["days_left"] = days_left
        due.append(row)

    return due


def mark_reminder_sent(user_id: int, days_left: int):
    existing = fetchone(
        "SELECT user_id FROM subscription_reminders WHERE user_id=?",
        (user_id,),
    )
    if existing:
        execute(
            """
            UPDATE subscription_reminders
            SET last_days_left=?, last_sent_ts=?
            WHERE user_id=?
            """,
            (days_left, datetime.utcnow().isoformat(), user_id),
        )
        return

    execute(
        """
        INSERT INTO subscription_reminders (user_id, last_days_left, last_sent_ts)
        VALUES (?, ?, ?)
        """,
        (user_id, days_left, datetime.utcnow().isoformat()),
    )
