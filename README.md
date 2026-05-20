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

- [bot.py](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/bot.py)
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
4. Run `python bot.py`
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
