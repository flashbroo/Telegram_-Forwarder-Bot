# razorpay_webhook.py

import hmac
import hashlib
import json
import requests
from fastapi import FastAPI, Request, Header
from datetime import datetime

import config
import db
import subscriptions

app = FastAPI()


def _requests_session():
    session = requests.Session()
    session.trust_env = False
    return session


# ----------------------------
# Verify Razorpay Signature
# ----------------------------

def verify_signature(body: bytes, signature: str) -> bool:
    if not config.RAZORPAY_WEBHOOK_SECRET or not signature:
        return False
    secret = config.RAZORPAY_WEBHOOK_SECRET.encode()
    digest = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature)


# ----------------------------
# Send Telegram Notification
# ----------------------------

def send_telegram_message(user_id: int, text: str):
    """
    Sends Telegram message directly using Bot API.
    Works from webhook (outside bot process).
    """
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": user_id,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        _requests_session().post(url, json=payload, timeout=10)
    except Exception as e:
        print("Failed to send Telegram notification:", e)


# ----------------------------
# Webhook Endpoint
# ----------------------------

@app.post("/razorpay/webhook")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(None)
):
    body = await request.body()

    # ----------------------------
    # Verify webhook signature
    # ----------------------------
    if not verify_signature(body, x_razorpay_signature):
        return {"status": "invalid signature"}

    payload = json.loads(body)
    event = payload.get("event")

    # Only process successful payment links
    if event != "payment_link.paid":
        return {"status": "ignored"}

    payment = payload["payload"]["payment"]["entity"]
    payment_link = payload["payload"]["payment_link"]["entity"]

    order_id = payment_link["id"]
    status = payment["status"]

    if status != "captured":
        return {"status": "payment not captured"}

    # ----------------------------
    # Fetch order from DB
    # ----------------------------

    row = db.fetchone(
        "SELECT * FROM razorpay_orders WHERE order_id=?",
        (order_id,)
    )

    if not row:
        return {"status": "order not found"}

    user_id = row["user_id"]
    plan_key = row["plan_key"]
    amount = payment["amount"] / 100


    # ----------------------------
    # Idempotency check (prevent double activation)
    # ----------------------------

    existing = db.fetchone(
        "SELECT 1 FROM razorpay_payments WHERE payment_id=?",
        (payment["id"],)
    )

    if existing:
        return {"status": "already processed"}


    # ----------------------------
    # Save Payment
    # ----------------------------

    db.execute(
        """
        INSERT OR REPLACE INTO razorpay_payments
        (payment_id, order_id, user_id, amount, status, method, payload, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payment["id"],
            order_id,
            user_id,
            amount,
            payment["status"],
            payment["method"],
            json.dumps(payment),
            datetime.utcnow().isoformat()
        )
    )

    db.execute(
        """
        INSERT INTO payments (user_id, provider, payload, status, ts)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            user_id,
            "razorpay",
            json.dumps(
                {
                    "event": "webhook_payment_link_paid",
                    "order_id": order_id,
                    "payment_id": payment["id"],
                    "plan_key": plan_key,
                    "amount": amount,
                }
            ),
            "paid",
            datetime.utcnow().isoformat(),
        )
    )


    # ----------------------------
    # Activate Subscription
    # ----------------------------

    subscriptions.activate_subscription(
        user_id=user_id,
        plan_key=plan_key,
        provider="razorpay",
        provider_payment_id=payment["id"],
        amount=amount
    )


    # ----------------------------
    # Mark Order Paid
    # ----------------------------

    db.execute(
        "UPDATE razorpay_orders SET status='paid' WHERE order_id=?",
        (order_id,)
    )


    # ----------------------------
    # Notify User on Telegram
    # ----------------------------

    send_telegram_message(
        user_id,
        "🎉 *Payment Successful!*\n\n"
        f"Your *{plan_key}* subscription is now active.\n\n"
        "You can start using the bot now.\n"
        "Type /menu to continue."
    )


    return {"status": "subscription activated"}
