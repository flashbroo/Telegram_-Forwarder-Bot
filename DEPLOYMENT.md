**Recommended Host**
Use a Linux VPS with persistent disk.

Best fit:
- Hostinger VPS
- Contabo VPS
- Hetzner Cloud

Recommended OS:
- Ubuntu 22.04 or 24.04

**Production Recommendation**
- use PostgreSQL in production
- use SQLite only for local development, smoke testing, or very small temporary launches
- keep `sessions/` on persistent disk
- do not use ephemeral-storage hosting for the userbot session files

**Railway Login Persistence**
Telegram user logins are stored as Telethon session files. On Railway, attach a
persistent volume and set:

```bash
SESSION_DIR=/data/sessions
```

Without a persistent `SESSION_DIR`, users may need to login again after every
redeploy or service restart.

**What To Upload**
Upload only the project source and deployment files.

Do upload:
- source code files
- `deploy/`
- `.env.example`
- `requirements.txt`

Do not upload:
- real `.env`
- `bot.db`, `bot.db-shm`, `bot.db-wal`
- `sessions/`
- `venv/`, `bot-env/`
- `node_modules/`
- `__pycache__/`
- `logs/`
- `smoke_tmp/`, `smoke_test.db*`, `test_session.session`

Create the real `.env` directly on the server.

**Server Setup**
Example app path: `/opt/telegram-forwarder-bot`

1. Install packages

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip nginx postgresql postgresql-contrib
```

2. Create dedicated app user

```bash
sudo useradd --system --create-home --home /opt/telegram-forwarder-bot --shell /bin/bash telegrambot
sudo mkdir -p /opt/telegram-forwarder-bot
sudo chown telegrambot:telegrambot /opt/telegram-forwarder-bot
```

3. Upload code

If using git:

```bash
sudo -u telegrambot git clone <YOUR_REPO_URL> /opt/telegram-forwarder-bot
```

If uploading from Windows:
- upload the cleaned project folder to `/opt/telegram-forwarder-bot`
- then run:

```bash
sudo chown -R telegrambot:telegrambot /opt/telegram-forwarder-bot
```

4. Create Python virtual environment

```bash
sudo -u telegrambot bash -lc 'cd /opt/telegram-forwarder-bot && python3 -m venv .venv && source .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt'
```

**PostgreSQL Setup**
1. Create database and user

```bash
sudo -u postgres psql
```

```sql
CREATE DATABASE forwarderbot;
CREATE USER forwarder WITH PASSWORD 'strong_password_here';
GRANT ALL PRIVILEGES ON DATABASE forwarderbot TO forwarder;
\q
```

2. Use this in `.env`

```env
DATABASE_URL=postgresql://forwarder:strong_password_here@127.0.0.1:5432/forwarderbot
DB_PATH=bot.db
```

Notes:
- when `DATABASE_URL` is set, the bot uses PostgreSQL
- `DB_PATH` can stay present as a fallback value, but PostgreSQL becomes the active backend

**Environment File**
Copy `.env.example` to `.env` on the server and fill it.

Minimum required:

```env
BOT_TOKEN=
ADMIN_ID=
USERBOT_API_ID=
USERBOT_API_HASH=
DATABASE_URL=postgresql://forwarder:strong_password_here@127.0.0.1:5432/forwarderbot
DB_PATH=bot.db
FORWARD_LOG_FILE=forward_logs.txt
```

Optional at first launch:

```env
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
RAZORPAY_WEBHOOK_SECRET=
```

**Manual Test**

```bash
sudo -u telegrambot bash -lc 'cd /opt/telegram-forwarder-bot && source .venv/bin/activate && python main.py'
```

Check:
- `/health`
- `/login`
- `/menu`
- one mapping
- one real forward

Stop with `Ctrl+C` after the manual test succeeds.

**Systemd Setup**
This repo includes service templates:
- [telegram-forwarder-bot.service](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/deploy/telegram-forwarder-bot.service)
- [telegram-forwarder-webhook.service](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/deploy/telegram-forwarder-webhook.service)

Install them:

```bash
cd /opt/telegram-forwarder-bot
sudo cp deploy/telegram-forwarder-bot.service /etc/systemd/system/
sudo cp deploy/telegram-forwarder-webhook.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable telegram-forwarder-bot
sudo systemctl start telegram-forwarder-bot
```

Check:

```bash
sudo systemctl status telegram-forwarder-bot
journalctl -u telegram-forwarder-bot -f
```

The bot service is configured to:
- restart automatically if it crashes
- start automatically after server reboot

**Razorpay Webhook Service**
Only enable this when payments are ready.

```bash
sudo systemctl enable telegram-forwarder-webhook
sudo systemctl start telegram-forwarder-webhook
sudo systemctl status telegram-forwarder-webhook
journalctl -u telegram-forwarder-webhook -f
```

**Nginx Setup**
Use [nginx-telegram-forwarder.conf](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/deploy/nginx-telegram-forwarder.conf) as the base config.

```bash
sudo cp deploy/nginx-telegram-forwarder.conf /etc/nginx/sites-available/telegram-forwarder
sudo ln -s /etc/nginx/sites-available/telegram-forwarder /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

Then replace `your-domain.com` with your real domain and add HTTPS:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

**Razorpay Setup**
You can launch without Razorpay first.

Without Razorpay:
- bot login works
- mapping works
- forwarding works
- paid plan purchase does not work

When ready:
1. create Razorpay test keys
2. put them in `.env`
3. create a webhook URL like `https://your-domain.com/razorpay/webhook`
4. set your webhook secret in Razorpay
5. use the same value in `RAZORPAY_WEBHOOK_SECRET`
6. run one full test payment before enabling live mode

**Backups**
Back up regularly:
- PostgreSQL database
- `sessions/`
- `.env`

If you are still temporarily using SQLite:
- `bot.db`
- `sessions/`
- `.env`

**Launch Order**
1. deploy bot without Razorpay
2. verify login, mappings, forwarding, expiry blocking, and reminders
3. then add Razorpay in test mode
4. then run one test payment
5. only then switch to live payment mode
