# Auto Forwarder Bot

Telegram forwarding bot with:
- Telethon userbot login
- source-to-target mapping management
- subscription gating
- expiry reminders
- admin management
- optional Razorpay payment activation

## Recommended Production Shape

- Ubuntu VPS
- PostgreSQL
- persistent `sessions/`
- systemd for auto-start and crash restart

## Core Features

- phone + OTP + Telegram 2FA login
- add/list/remove mappings
- add/remove source and target flows
- automatic forwarding for text, photo, video, and document messages
- expired users blocked automatically
- one reminder per 24 hours during the last 5 subscription days
- admin tools for free access, plans, force-subscribe, user list, health, and stats

## Main Files

- [main.py](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/main.py)
- [mappings.py](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/mappings.py)
- [forwarder.py](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/forwarder.py)
- [subscriptions.py](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/subscriptions.py)
- [payments_razorpay.py](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/payments_razorpay.py)
- [razorpay_webhook.py](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/razorpay_webhook.py)
- [DEPLOYMENT.md](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/DEPLOYMENT.md)
- [PRODUCTION_CHECKLIST.md](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/PRODUCTION_CHECKLIST.md)

## Quick Start

1. Copy [.env.example](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/.env.example) to `.env`
2. Fill Telegram credentials
3. Install dependencies from `requirements.txt`
4. Run `python main.py`
5. Test `/health`, `/login`, one mapping, and one real forward

## Payments

You can deploy without Razorpay first.

Later:
- add `RAZORPAY_KEY_ID`
- add `RAZORPAY_KEY_SECRET`
- add `RAZORPAY_WEBHOOK_SECRET`
- start the webhook service
- test one payment in Razorpay test mode

## Deployment

Use:
- [DEPLOYMENT.md](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/DEPLOYMENT.md) for server setup
- [PRODUCTION_CHECKLIST.md](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/PRODUCTION_CHECKLIST.md) before launch

For Railway, deploy the bot as a worker with `python main.py`. Do not use
`uvicorn main:app`; `main.py` is not an ASGI application. If Razorpay webhooks
are enabled, create a separate Railway service with:

```bash
uvicorn razorpay_webhook:app --host 0.0.0.0 --port $PORT
```

To keep users logged in on Railway, attach a persistent volume and set:

```bash
SESSION_DIR=/data/sessions
```
