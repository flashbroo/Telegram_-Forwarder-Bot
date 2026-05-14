# utils.py

from datetime import datetime, timedelta
import csv
import io
from typing import Iterable, Dict, Any


# ------------------------------
# TIME HELPERS
# ------------------------------

def now_iso() -> str:
    return datetime.utcnow().isoformat()


def add_days_iso(days: int) -> str:
    return (datetime.utcnow() + timedelta(days=days)).isoformat()


def parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


# ------------------------------
# CSV EXPORT HELPERS
# ------------------------------

def export_rows_to_csv(
    rows: Iterable[Dict[str, Any]],
    headers: list[str],
) -> io.BytesIO:
    """
    Generic CSV exporter for DB rows.
    Accepts list of sqlite Row or dict.
    Returns BytesIO for Telegram upload.
    """

    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow(headers)

    for r in rows:
        row = dict(r)
        writer.writerow([row.get(h, "") for h in headers])

    out = io.BytesIO(buf.getvalue().encode("utf-8"))
    out.seek(0)
    return out


# ------------------------------
# REPORT HELPERS
# ------------------------------

def export_forward_logs_csv(rows):
    headers = [
        "log_id",
        "mapping_id",
        "user_id",
        "message_id",
        "source_channel",
        "target_channel",
        "status",
        "error_text",
        "ts",
    ]
    return export_rows_to_csv(rows, headers)


def export_payments_csv(rows):
    headers = [
        "payment_id",
        "user_id",
        "provider",
        "payload",
        "status",
        "ts",
    ]
    return export_rows_to_csv(rows, headers)


def export_subscriptions_csv(rows):
    headers = [
        "sub_id",
        "user_id",
        "plan_key",
        "start_ts",
        "expiry_ts",
        "status",
        "provider",
        "provider_payment_id",
        "amount",
    ]
    return export_rows_to_csv(rows, headers)


def export_users_csv(rows):
    headers = [
        "user_id",
        "free_access",
        "created_at",
        "updated_at",
    ]
    return export_rows_to_csv(rows, headers)
