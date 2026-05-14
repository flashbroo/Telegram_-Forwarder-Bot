import logging
import traceback
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from datetime import datetime

import config
import db
import subscriptions

# Razorpay engine
from payments_razorpay import (
    create_razorpay_payment_link,
    verify_and_activate_payment
)

logger = logging.getLogger(__name__)


def _plan_market(plan) -> str:
    audience = (plan["audience"] or "").upper() if "audience" in plan.keys() else ""
    if audience in ("IN", "INTL", "GLOBAL"):
        return audience or "GLOBAL"
    return "IN" if str(plan["currency"]).upper() == "INR" else "INTL"


def _market_title(market: str) -> str:
    return {
        "IN": "India",
        "INTL": "International",
        "GLOBAL": "Global",
    }.get(market, market)


async def _show_market_or_plans(message_or_query, plans, edit: bool = False):
    markets = []
    for plan in plans:
        market = _plan_market(plan)
        if market not in markets:
            markets.append(market)

    if len(markets) > 1:
        kb = [[InlineKeyboardButton(f"{_market_title(m)} Plans", callback_data=f"market_{m}")] for m in markets]
        text = "🌍 *Choose Your Region*\n\nSelect the plan group that fits your payment market."
        if edit:
            await message_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        else:
            await message_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return

    market = markets[0] if markets else "GLOBAL"
    filtered = [p for p in plans if _plan_market(p) == market]
    text = f"📦 *Choose a {_market_title(market)} Plan*\n\n"
    kb = []
    for p in filtered:
        text += (
            f"🔥 *{p['name']}*\n"
            f"💰 {p['price']} {p['currency']}\n"
            f"⏳ {p['duration_days']} days\n\n"
        )
        kb.append([
            InlineKeyboardButton(
                f"{p['name']} — {p['price']} {p['currency']}",
                callback_data=f"razorpay_{p['plan_key']}"
            )
        ])

    if edit:
        await message_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await message_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


# -----------------------------
# BUY COMMAND
# -----------------------------
async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /buy  -> show plans
    /buy <plan_key> -> buy specific plan
    """

    try:
        if update.message.chat.type != "private":
            await update.message.reply_text("❌ Please use /buy in private chat.")
            return

        user = update.effective_user
        state = subscriptions.get_user_access_state(user.id)

        # Already allowed
        if state in ("FREE_ALL", "FREE_USER", "PAID"):
            await update.message.reply_text("✅ You already have access.")
            return

        # -------------------------
        # SHOW PLANS IF NO ARG
        # -------------------------
        if len(context.args) == 0:
            plans = db.fetchall("SELECT * FROM plans WHERE is_active=1 ORDER BY price ASC")

            if not plans:
                await update.message.reply_text("❌ No active plans available.")
                return

            await _show_market_or_plans(update.message, plans, edit=False)
            return

        # -------------------------
        # BUY SPECIFIC PLAN
        # -------------------------
        plan_key = context.args[0].strip()
        plan = db.get_plan(plan_key)

        if not plan:
            await update.message.reply_text("❌ Invalid or inactive plan key.")
            return

        pay_label = "🇮🇳 Pay via Razorpay" if str(plan["currency"]).upper() == "INR" else "🌍 Pay Securely"
        kb = [[InlineKeyboardButton(pay_label, callback_data=f"razorpay_{plan_key}")]]

        msg = (
            f"📦 *{plan['name']}*\n\n"
            f"💰 Price: {plan['price']} {plan['currency']}\n"
            f"⏳ Duration: {plan['duration_days']} days\n\n"
            "Click below to pay securely and activate your subscription."
        )

        await update.message.reply_text(
            msg,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown"
        )

    except Exception:
        logger.error("cmd_buy error:\n%s", traceback.format_exc())
        await update.message.reply_text("⚠️ Internal error. Please contact admin.")


# -----------------------------
# PAYMENT CALLBACK HANDLER
# -----------------------------
async def payment_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    try:
        user = q.from_user
        state = subscriptions.get_user_access_state(user.id)

        if state in ("FREE_ALL", "FREE_USER", "PAID"):
            await q.edit_message_text("✅ You already have access.")
            return

        data = (q.data or "").strip()

        # -----------------
        # CREATE PAYMENT LINK
        # -----------------
        if data.startswith("razorpay_"):
            plan_key = data.replace("razorpay_", "", 1)
            plan = db.get_plan(plan_key)

            if not plan:
                await q.edit_message_text("❌ Plan not found or inactive.")
                return

            if not config.RAZORPAY_KEY_ID or not config.RAZORPAY_KEY_SECRET:
                await q.edit_message_text("⚠️ Razorpay is not configured yet.")
                return

            payment_link = create_razorpay_payment_link(
                user_id=user.id,
                plan_key=plan_key,
                amount=float(plan["price"]),
                description=plan["name"],
                currency=plan["currency"],
            )

            if not payment_link:
                await q.edit_message_text("❌ Failed to create Razorpay payment link.")
                return

            # Fetch exact order_id just created
            order = db.fetchone(
                """
                SELECT order_id FROM razorpay_orders 
                WHERE user_id=? AND plan_key=? 
                ORDER BY created_ts DESC LIMIT 1
                """,
                (user.id, plan_key)
            )

            if not order:
                await q.edit_message_text("❌ Failed to create payment order.")
                return

            order_id = order["order_id"]

            kb = [
                [InlineKeyboardButton("✅ I Paid", callback_data=f"verify_{order_id}")]
            ]

            await q.edit_message_text(
                f"💳 *{plan['name']} Payment*\n\n"
                "Complete payment using the link below:\n\n"
                f"{payment_link}\n\n"
                "After payment, click *I Paid* to activate your subscription.",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="Markdown"
            )
            return

        if data.startswith("market_"):
            market = data.replace("market_", "", 1).upper()
            plans = db.fetchall("SELECT * FROM plans WHERE is_active=1 ORDER BY price ASC")
            filtered = [p for p in plans if _plan_market(p) == market]
            if not filtered:
                await q.edit_message_text("❌ No active plans available for this region.")
                return
            await _show_market_or_plans(q, filtered, edit=True)
            return

        # -----------------
        # VERIFY PAYMENT
        # -----------------
        if data.startswith("verify_"):
            order_id = data.replace("verify_", "", 1)

            ok = verify_and_activate_payment(order_id)

            if not ok:
                now = datetime.utcnow().strftime("%H:%M:%S")

                kb = [
                [InlineKeyboardButton("🔄 Check Payment Again", callback_data=f"verify_{order_id}")]
                    ]
                try:
                    await q.edit_message_text(
                    "⏳ *Payment not completed yet*\n\n"
                    f"Last checked at: `{now}`\n\n"
                    "If you have already paid, tap below again.\n\n" 
                    "If not working even after waiting 10 sec. Use /help to contact admin.",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode="Markdown"
                    )
                    return            
                except BadRequest as e:
                    # Ignore "Message is not modified" error
                    if "Message is not modified" not in str(e):
                        raise

            await q.edit_message_text(
                "✅ *Payment Successful!*\n\n"
                "Your subscription is now active.\n"
                "You can start using the bot.",
                parse_mode="Markdown"
            )
            return

        await q.edit_message_text("Unknown payment action.")

    except Exception:
        logger.error("payment_callback_handler error:\n%s", traceback.format_exc())
        await q.edit_message_text("⚠️ Internal error. Please contact admin.")
