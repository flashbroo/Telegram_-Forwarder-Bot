## Production Checklist

### 1. Repository hygiene

Before deployment:
- keep the real `.env` out of git
- do not upload local DB files from your Windows machine unless you intentionally need migration data
- do not upload local `sessions/` unless you intentionally want to carry over test sessions
- remove or ignore `venv/`, `bot-env/`, `node_modules/`, `__pycache__/`, `logs/`, and smoke-test files

### 2. Infrastructure choice

Recommended:
- Ubuntu VPS
- persistent disk
- Python 3.11+ or 3.12+
- PostgreSQL for production

Avoid:
- ephemeral filesystem hosting for live userbot sessions
- SQLite for sustained multi-user production traffic

### 3. Required secrets and config

Required:

```env
BOT_TOKEN=
ADMIN_ID=
USERBOT_API_ID=
USERBOT_API_HASH=
DATABASE_URL=
DB_PATH=bot.db
```

Optional at first launch:

```env
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
RAZORPAY_WEBHOOK_SECRET=
```

### 4. Database decision

Use PostgreSQL in production.

Recommended example:

```env
DATABASE_URL=postgresql://forwarder:strong_password_here@127.0.0.1:5432/forwarderbot
```

SQLite should be treated as:
- local development
- smoke testing
- temporary low-risk launch only

### 5. Must-pass staging tests

Run these before public launch:
- `/health`
- `/login`
- OTP flow
- 2FA flow if applicable
- bot restart and session restore
- `/add_mapping`
- `/list_mappings`
- one text forward
- one photo forward
- one video forward
- one document forward
- expired subscription blocks forwarding
- expired subscription blocks mapping actions
- final 5-day reminder sends once per 24 hours

### 6. Process supervision

Use:
- `systemd` for the bot
- `systemd` for webhook when Razorpay is enabled

Confirm:
- service restarts automatically on crash
- logs are visible through `journalctl`

### 7. Backups

Back up regularly:
- PostgreSQL database or `bot.db`
- `sessions/`
- `.env`

### 8. Payments rollout

Safe rollout order:
1. launch bot without Razorpay
2. verify login, mappings, forwarding, subscription expiry logic
3. add Razorpay test keys
4. run one test payment
5. only then enable live payment mode

### 9. Launch criteria

Call this production-ready only when:
- login survives restart
- forwarding works for real channels
- expired users are blocked automatically
- reminders are sent correctly
- bot restarts cleanly after service restart
- payment activation works end to end if payments are enabled
