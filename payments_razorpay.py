import requests
import base64
import json
import logging
from typing import Optional
from datetime import datetime

import config
import db
import subscriptions

logger = logging.getLogger(__name__)


def _requests_session():
    session = requests.Session()
    session.trust_env = False
    return session


def _write_payment_event(user_id: int, provider: str, payload: dict, status: str):
    db.execute(
        """
        INSERT INTO payments (user_id, provider, payload, status, ts)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, provider, json.dumps(payload), status, datetime.utcnow().isoformat())
    )


# -----------------------------
# Razorpay API helpers
# -----------------------------

def _get_auth_header():
    auth = f"{config.RAZORPAY_KEY_ID}:{config.RAZORPAY_KEY_SECRET}"
    encoded = base64.b64encode(auth.encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json"
    }


def _razorpay_api(url: str, method="POST", payload=None):
    headers = _get_auth_header()
    session = _requests_session()

    try:
        if method == "POST":
            r = session.post(url, headers=headers, json=payload, timeout=20)
        else:
            r = session.get(url, headers=headers, timeout=20)

        if r.status_code not in (200, 201):
            logger.error("Razorpay API error: %s %s", r.status_code, r.text)
            return None

        return r.json()

    except Exception:
        logger.exception("Razorpay API request failed")
        return None


# -----------------------------
# Create Razorpay Payment Link
# -----------------------------

def create_razorpay_payment_link(
    user_id: int,
    plan_key: str,
    amount: float,
    description: str,
    currency: str,
) -> Optional[str]:
    """
    Creates Razorpay payment link with UPI intent support.
    Returns short payment URL.
    """

    if not config.RAZORPAY_KEY_ID or not config.RAZORPAY_KEY_SECRET:
        logger.warning("Razorpay keys not configured")
        return None

    amount_paise = int(amount * 100)
    receipt = f"user_{user_id}_{plan_key}_{int(datetime.utcnow().timestamp())}"

    payload = {
        "amount": amount_paise,
        "currency": currency.upper(),
        "accept_partial": False,
        "description": description,
        "reference_id": receipt,
        "notes": {
            "user_id": str(user_id),
            "plan_key": plan_key
        },
        "customer": {
            "name": f"User {user_id}"
        },
        "notify": {
            "sms": False,
            "email": False
        },
        "reminder_enable": False
    }

    url = f"{config.RAZORPAY_BASE_URL}/v1/payment_links"

    result = _razorpay_api(url, payload=payload)

    if not result:
        return None

    payment_link = result.get("short_url")
    order_id = result.get("id")

    if not payment_link or not order_id:
        logger.error("Invalid Razorpay response: %s", result)
        return None

    # Save Razorpay order
    db.execute(
        """
        INSERT OR REPLACE INTO razorpay_orders
        (order_id, user_id, plan_key, amount, currency, status, receipt, created_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            user_id,
            plan_key,
            amount,
            currency.upper(),
            result.get("status", "created"),
            receipt,
            datetime.utcnow().isoformat()
        )
    )
    _write_payment_event(
        user_id,
        "razorpay",
        {
            "event": "payment_link_created",
            "order_id": order_id,
            "plan_key": plan_key,
            "amount": amount,
            "currency": currency.upper(),
        },
        result.get("status", "created"),
    )

    return payment_link


# -----------------------------
# Fetch payment status from Razorpay
# -----------------------------

def fetch_payment_link_status(order_id: str):
    url = f"{config.RAZORPAY_BASE_URL}/v1/payment_links/{order_id}"
    return _razorpay_api(url, method="GET")


# -----------------------------
# Verify & Activate Subscription
# -----------------------------

def verify_and_activate_payment(order_id: str) -> bool:
    """
    Confirms payment from Razorpay and activates subscription.
    This is the only trusted activation path.
    """

    data = fetch_payment_link_status(order_id)
    if not data:
        logger.warning("Failed to fetch Razorpay order: %s", order_id)
        return False

    status = data.get("status")
    payment_id = data.get("payment_id")

    # Only allow fully paid orders
    if status != "paid" or not payment_id:
        logger.info("Payment not completed yet: %s status=%s", order_id, status)
        return False

    # Fetch order from DB
    order = db.fetchone(
        "SELECT * FROM razorpay_orders WHERE order_id=?",
        (order_id,)
    )

    if not order:
        logger.error("Order not found in DB: %s", order_id)
        return False

    user_id = order["user_id"]
    plan_key = order["plan_key"]
    amount = order["amount"]

    # Idempotency check
    dup = db.fetchone(
        "SELECT 1 FROM subscriptions WHERE provider='razorpay' AND provider_payment_id=?",
        (payment_id,)
    )
    if dup:
        logger.info("Payment already processed: %s", payment_id)
        return True

    # Store payment
    db.execute(
        """
        INSERT OR REPLACE INTO razorpay_payments
        (payment_id, order_id, user_id, amount, status, method, payload, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payment_id,
            order_id,
            user_id,
            amount,
            "paid",
            data.get("method", "upi"),
            json.dumps(data),
            datetime.utcnow().isoformat()
        )
    )
    _write_payment_event(
        user_id,
        "razorpay",
        {
            "event": "payment_verified",
            "order_id": order_id,
            "payment_id": payment_id,
            "plan_key": plan_key,
            "amount": amount,
            "currency": order["currency"],
        },
        "paid",
    )

    # Activate subscription
    ok = subscriptions.activate_subscription(
        user_id=user_id,
        plan_key=plan_key,
        provider="razorpay",
        provider_payment_id=payment_id,
        amount=amount
    )

    if not ok:
        logger.error("Subscription activation failed for user %s", user_id)
        return False

    # Update order status
    db.execute(
        "UPDATE razorpay_orders SET status='paid' WHERE order_id=?",
        (order_id,)
    )

    logger.info("Payment verified & subscription activated: user=%s plan=%s", user_id, plan_key)

    return True
