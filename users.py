# users.py

from typing import Optional, List, Dict
from db import execute, fetchone, fetchall
from utils import now_iso


# ------------------------------
# USER REGISTRATION
# ------------------------------

def ensure_user_exists(user_id: int) -> None:
    """
    Creates user row if it does not exist.
    Idempotent.
    """
    row = fetchone("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if not row:
        execute(
            """
            INSERT INTO users (user_id, free_access, created_at, updated_at)
            VALUES (?,?,?,?)
            """,
            (user_id, 0, now_iso(), now_iso())
        )


def create_user_if_missing(user_id: int) -> None:
    """
    Alias helper for consistency.
    """
    ensure_user_exists(user_id)


# ------------------------------
# USER QUERIES
# ------------------------------

def get_user(user_id: int) -> Optional[Dict]:
    row = fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))
    return dict(row) if row else None


def list_users() -> List[Dict]:
    rows = fetchall("SELECT * FROM users ORDER BY created_at DESC")
    return [dict(r) for r in rows]


def count_users() -> int:
    row = fetchone("SELECT COUNT(*) AS total FROM users")
    return int(row["total"]) if row else 0


# ------------------------------
# ACCESS MANAGEMENT
# ------------------------------

def set_free_access(user_id: int, value: int) -> None:
    """
    Grants or revokes free access for user.
    """
    ensure_user_exists(user_id)

    execute(
        "UPDATE users SET free_access=?, updated_at=? WHERE user_id=?",
        (int(bool(value)), now_iso(), user_id)
    )


def revoke_free_access(user_id: int) -> None:
    set_free_access(user_id, 0)


def grant_free_access(user_id: int) -> None:
    set_free_access(user_id, 1)


# ------------------------------
# USER METADATA (FUTURE READY)
# ------------------------------

def update_last_seen(user_id: int) -> None:
    """
    Optional helper for analytics.
    """
    ensure_user_exists(user_id)
    execute(
        "UPDATE users SET updated_at=? WHERE user_id=?",
        (now_iso(), user_id)
    )
