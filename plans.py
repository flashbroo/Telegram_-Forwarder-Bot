# plans.py

from telegram import Update
from telegram.ext import ContextTypes
from typing import Optional

from db import fetchall, fetchone, upsert_plan
from utils import now_iso


# ------------------------------
# VALIDATION HELPERS
# ------------------------------

def parse_price(value: str) -> Optional[float]:
    try:
        price = float(value)
        if price <= 0:
            return None
        return price
    except Exception:
        return None


def parse_days(value: str) -> Optional[int]:
    try:
        days = int(value)
        if days <= 0:
            return None
        return days
    except Exception:
        return None


# ------------------------------
# LIST PLANS (ADMIN)
# ------------------------------

async def cmd_list_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = fetchall("SELECT * FROM plans ORDER BY price ASC")

    if not rows:
        await update.message.reply_text("No plans found.")
        return

    msg = "📦 *Plans*\n\n"
    for r in rows:
        status = "✅ Active" if r["is_active"] else "❌ Disabled"
        msg += (
            f"*{r['name']}*\n"
            f"Key: `{r['plan_key']}`\n"
            f"Price: {r['price']} {r['currency']}\n"
            f"Duration: {r['duration_days']} days\n"
            f"Status: {status}\n"
            f"Features: {r['features'] or '—'}\n\n"
        )

    await update.message.reply_text(msg, parse_mode="Markdown")


# ------------------------------
# CREATE / UPDATE PLAN
# ------------------------------
# /create_plan <plan_key> <name> <price> <currency> <days> <features>

async def cmd_create_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 6:
        await update.message.reply_text(
            "Usage:\n"
            "/create_plan <plan_key> <name> <price> <currency> <days> <features>"
        )
        return

    plan_key = context.args[0].strip()
    name = context.args[1].strip()
    price = parse_price(context.args[2])
    currency = context.args[3].upper().strip()
    days = parse_days(context.args[4])
    features = " ".join(context.args[5:]).strip()

    if not plan_key:
        await update.message.reply_text("Plan key cannot be empty.")
        return

    if not name:
        await update.message.reply_text("Plan name cannot be empty.")
        return

    if not price:
        await update.message.reply_text("Invalid price.")
        return

    if not days:
        await update.message.reply_text("Invalid duration (days).")
        return

    if not currency:
        await update.message.reply_text("Currency is required.")
        return

    upsert_plan(
        plan_key=plan_key,
        name=name,
        price=price,
        currency=currency,
        duration_days=days,
        features=features,
        is_active=1,
    )

    await update.message.reply_text(
        f"✅ Plan `{plan_key}` created / updated successfully.",
        parse_mode="Markdown"
    )


# ------------------------------
# SET PRICE
# ------------------------------
# /set_price <plan_key> <new_price>

async def cmd_set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /set_price <plan_key> <new_price>")
        return

    plan_key = context.args[0].strip()
    new_price = parse_price(context.args[1])

    if not new_price:
        await update.message.reply_text("Invalid price.")
        return

    plan = fetchone("SELECT * FROM plans WHERE plan_key=?", (plan_key,))
    if not plan:
        await update.message.reply_text("❌ Plan not found.")
        return

    upsert_plan(
        plan_key=plan_key,
        name=plan["name"],
        price=new_price,
        currency=plan["currency"],
        duration_days=plan["duration_days"],
        features=plan["features"],
        is_active=plan["is_active"],
    )

    await update.message.reply_text(
        f"💰 Price updated for `{plan_key}` → {new_price}",
        parse_mode="Markdown"
    )


# ------------------------------
# ENABLE / DISABLE PLAN
# ------------------------------

async def cmd_disable_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /disable_plan <plan_key>")
        return

    plan_key = context.args[0].strip()

    plan = fetchone("SELECT * FROM plans WHERE plan_key=?", (plan_key,))
    if not plan:
        await update.message.reply_text("❌ Plan not found.")
        return

    upsert_plan(
        plan_key=plan_key,
        name=plan["name"],
        price=plan["price"],
        currency=plan["currency"],
        duration_days=plan["duration_days"],
        features=plan["features"],
        is_active=0,
    )

    await update.message.reply_text(
        f"🚫 Plan `{plan_key}` disabled.",
        parse_mode="Markdown"
    )


async def cmd_enable_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /enable_plan <plan_key>")
        return

    plan_key = context.args[0].strip()

    plan = fetchone("SELECT * FROM plans WHERE plan_key=?", (plan_key,))
    if not plan:
        await update.message.reply_text("❌ Plan not found.")
        return

    upsert_plan(
        plan_key=plan_key,
        name=plan["name"],
        price=plan["price"],
        currency=plan["currency"],
        duration_days=plan["duration_days"],
        features=plan["features"],
        is_active=1,
    )

    await update.message.reply_text(
        f"✅ Plan `{plan_key}` enabled.",
        parse_mode="Markdown"
    )
