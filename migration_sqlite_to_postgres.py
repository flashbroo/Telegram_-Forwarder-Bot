import argparse
import os
import sqlite3
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor


TABLES = [
    ("users", "user_id"),
    ("subscriptions", "sub_id"),
    ("payments", "payment_id"),
    ("razorpay_orders", "order_id"),
    ("razorpay_payments", "payment_id"),
    ("mappings", "mapping_id"),
    ("settings", "key"),
    ("plans", "plan_key"),
    ("admin_logs", "id"),
    ("forward_logs", "log_id"),
    ("incoming_messages", "id"),
    ("saved_channels", "id"),
    ("userbot_queue", "id"),
    ("pinned_chats", "chat_id"),
    ("userbots", "userbot_id"),
    ("userbot_phones", "phone"),
]

SEQUENCE_COLUMNS = {
    "subscriptions": ("sub_id", "subscriptions_sub_id_seq"),
    "payments": ("payment_id", "payments_payment_id_seq"),
    "mappings": ("mapping_id", "mappings_mapping_id_seq"),
    "admin_logs": ("id", "admin_logs_id_seq"),
    "forward_logs": ("log_id", "forward_logs_log_id_seq"),
    "incoming_messages": ("id", "incoming_messages_id_seq"),
    "saved_channels": ("id", "saved_channels_id_seq"),
    "userbot_queue": ("id", "userbot_queue_id_seq"),
}


def parse_args():
    parser = argparse.ArgumentParser(description="Migrate bot data from SQLite to PostgreSQL.")
    parser.add_argument("--sqlite-path", default="bot.db", help="Path to the SQLite bot database.")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", "").strip(),
        help="PostgreSQL DATABASE_URL. Defaults to the environment variable.",
    )
    parser.add_argument(
        "--skip-truncate",
        action="store_true",
        help="Do not clear PostgreSQL tables before importing.",
    )
    return parser.parse_args()


def create_postgres_schema(database_url: str):
    os.environ["DATABASE_URL"] = database_url
    import db  # noqa: F401


def sqlite_connect(path: str):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def postgres_connect(database_url: str):
    return psycopg2.connect(database_url, cursor_factory=RealDictCursor)


def fetch_sqlite_rows(conn, table: str):
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM {table}")
    return [dict(row) for row in cur.fetchall()]


def get_postgres_columns(cur, table: str):
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    )
    return [row["column_name"] for row in cur.fetchall()]


def truncate_target(cur):
    table_names = ", ".join(table for table, _ in TABLES)
    cur.execute(f"TRUNCATE TABLE {table_names} RESTART IDENTITY")


def insert_rows(cur, table: str, rows: list[dict]):
    if not rows:
        return

    columns = get_postgres_columns(cur, table)
    present_columns = [column for column in columns if column in rows[0]]
    placeholders = ", ".join(["%s"] * len(present_columns))
    column_sql = ", ".join(present_columns)

    sql = f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})"
    values = [[row.get(column) for column in present_columns] for row in rows]
    cur.executemany(sql, values)


def reset_sequences(cur):
    for table, (column, sequence) in SEQUENCE_COLUMNS.items():
        cur.execute(
            f"SELECT setval(%s, COALESCE((SELECT MAX({column}) FROM {table}), 1), true)",
            (sequence,),
        )


def main():
    args = parse_args()

    sqlite_path = Path(args.sqlite_path)
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite database not found: {sqlite_path}")

    if not args.database_url:
        raise SystemExit("DATABASE_URL is required. Pass --database-url or set it in the environment.")

    print(f"Preparing PostgreSQL schema for {args.database_url}")
    create_postgres_schema(args.database_url)

    sqlite_conn = sqlite_connect(str(sqlite_path))
    pg_conn = postgres_connect(args.database_url)

    try:
        with pg_conn:
            with pg_conn.cursor() as cur:
                if not args.skip_truncate:
                    print("Clearing PostgreSQL tables before import...")
                    truncate_target(cur)

                for table, _ in TABLES:
                    rows = fetch_sqlite_rows(sqlite_conn, table)
                    print(f"Migrating {table}: {len(rows)} row(s)")
                    insert_rows(cur, table, rows)

                reset_sequences(cur)
        print("Migration completed successfully.")
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()
