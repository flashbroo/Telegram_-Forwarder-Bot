**Recommended Host**
Use a Linux VPS for this bot.

Why:
- `bot.db` is SQLite and stored locally
- `sessions/` contains persistent Telethon session files
- the bot process and webhook process should share the same filesystem

Scale note:
- this setup is good for single-server deployment
- for higher sustained scale, especially 10k+ active users, plan a PostgreSQL migration instead of keeping SQLite long-term

Good options:
- Hostinger VPS
- Contabo VPS
- Hetzner Cloud
- Oracle Cloud Free Tier

**Server Setup**
Example assumes Ubuntu and app path `/opt/telegram-forwarder-bot`.

1. Install packages

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip nginx
```

2. Upload code

```bash
sudo mkdir -p /opt/telegram-forwarder-bot
sudo chown $USER:$USER /opt/telegram-forwarder-bot
git clone <YOUR_REPO_URL> /opt/telegram-forwarder-bot
cd /opt/telegram-forwarder-bot
```

3. Create virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

4. Create `.env`

Copy [.env.example](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/.env.example) to `.env` and fill it.

Important notes:
- `ADMIN_ID` supports multiple admins separated by commas
- `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` come from Razorpay Dashboard
- `RAZORPAY_WEBHOOK_SECRET` is chosen by you when creating the webhook in Razorpay
- leave `DATABASE_URL` empty to use SQLite
- set `DATABASE_URL` to use PostgreSQL, for example `postgresql://dbuser:dbpassword@127.0.0.1:5432/forwarderbot`

5. Test manually

```bash
source /opt/telegram-forwarder-bot/.venv/bin/activate
cd /opt/telegram-forwarder-bot
python bot.py
```

If using webhook:

```bash
uvicorn razorpay_webhook:app --host 127.0.0.1 --port 8000
```

**PostgreSQL Setup**
If you want PostgreSQL instead of SQLite:

1. Install PostgreSQL on your VPS

```bash
sudo apt install -y postgresql postgresql-contrib
```

2. Create database and user

```bash
sudo -u postgres psql
CREATE DATABASE forwarderbot;
CREATE USER forwarder WITH PASSWORD 'strong_password_here';
GRANT ALL PRIVILEGES ON DATABASE forwarderbot TO forwarder;
\q
```

3. Put this in `.env`

```env
DATABASE_URL=postgresql://forwarder:strong_password_here@127.0.0.1:5432/forwarderbot
DB_PATH=bot.db
```

Notes:
- when `DATABASE_URL` is set, the bot now uses PostgreSQL
- `DB_PATH` can stay present as fallback, but PostgreSQL becomes the active backend
- if you use a managed provider like Neon, Supabase, Railway Postgres, or Render Postgres, they give you the full `DATABASE_URL` directly in their dashboard

**SQLite to PostgreSQL Migration**
If you already have data in `bot.db`, migrate it before switching production to PostgreSQL.

1. Keep a backup of `bot.db`

2. Set `DATABASE_URL` in `.env`

3. Run the migration script

```bash
python migration_sqlite_to_postgres.py --sqlite-path bot.db --database-url "postgresql://forwarder:strong_password_here@127.0.0.1:5432/forwarderbot"
```

4. After successful migration, start the bot normally

```bash
python bot.py
```

Notes:
- by default the migration clears PostgreSQL tables before importing
- use a fresh PostgreSQL database if possible
- the script migrates the main bot tables, settings, mappings, plans, subscriptions, payments, and saved channels

**Systemd Setup**
Copy the service files:

- [telegram-forwarder-bot.service](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/deploy/telegram-forwarder-bot.service)
- [telegram-forwarder-webhook.service](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/deploy/telegram-forwarder-webhook.service)

Install them:

```bash
sudo cp deploy/telegram-forwarder-bot.service /etc/systemd/system/
sudo cp deploy/telegram-forwarder-webhook.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable telegram-forwarder-bot
sudo systemctl start telegram-forwarder-bot
```

If you have Razorpay webhook enabled:

```bash
sudo systemctl enable telegram-forwarder-webhook
sudo systemctl start telegram-forwarder-webhook
```

Check status:

```bash
sudo systemctl status telegram-forwarder-bot
sudo systemctl status telegram-forwarder-webhook
journalctl -u telegram-forwarder-bot -f
journalctl -u telegram-forwarder-webhook -f
```

**Nginx Setup**
Use [nginx-telegram-forwarder.conf](/D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/deploy/nginx-telegram-forwarder.conf) as a base.

1. Copy config

```bash
sudo cp deploy/nginx-telegram-forwarder.conf /etc/nginx/sites-available/telegram-forwarder
```

2. Edit domain

Replace `your-domain.com` with your real domain.

3. Enable config

```bash
sudo ln -s /etc/nginx/sites-available/telegram-forwarder /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

4. Add HTTPS with Let's Encrypt

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

**Razorpay Setup**
You can deploy without Razorpay first.

Without Razorpay:
- bot login works
- mapping works
- forwarding works
- paid plan purchase does not work

To enable Razorpay:
1. Create/login to Razorpay Dashboard
2. Generate Test Mode API keys
3. Put them in `.env`
4. Create a webhook URL like `https://your-domain.com/webhook`
5. Enter your own random webhook secret in Razorpay
6. Put the same value in `RAZORPAY_WEBHOOK_SECRET`

Recommended random secret example:

```text
forwarder_webhook_2026_super_secret_random_value
```

**Backups**
Back up these regularly:
- `bot.db`
- `sessions/`
- `.env`

**First Production Checklist**
1. Run `python bot.py`
2. Test `/health`
3. Test `/login`
4. Test one mapping
5. Test one real forward
6. Then enable Razorpay test mode
7. Then test one payment




**WHICH SHOULD BE UPLOAD**
This bot is a nice fit for a simple VPS deploy, and the easiest safe path is: deploy the whole project folder to one Ubuntu server and run two services from it.

**What to deploy**
Deploy the whole project folder, not just one file. The important runtime files/folders are:
- [bot.py](D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/bot.py)
- [razorpay_webhook.py](D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/razorpay_webhook.py)
- [requirements.txt](D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/requirements.txt)
- [config.py](D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/config.py)
- [db.py](D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/db.py)
- [userbots](D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/userbots)
- [sessions](D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/sessions)
- [.env.example](D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/.env.example)
- [deploy](D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/deploy)
- [DEPLOYMENT.md](D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/DEPLOYMENT.md)

Do not deploy:
- `venv`
- `bot-env`
- `__pycache__`
- `node_modules`
- temporary smoke/test DB files

**Best hosting**
Use one Ubuntu VPS. Easiest realistic options:
1. Hostinger VPS
2. Contabo VPS
3. Hetzner Cloud
4. Oracle Cloud Free Tier if you want cheapest and can tolerate a bit more setup

For zero-knowledge deployment, I’d recommend `Hostinger VPS` or `Contabo VPS`.

**What you need before starting**
You need:
- Telegram bot token from BotFather
- your Telegram numeric user ID for `ADMIN_ID`
- Telegram `USERBOT_API_ID` and `USERBOT_API_HASH` from `my.telegram.org`
- a domain name only if you want Razorpay webhook later
- Razorpay keys only when you are ready for payments

**Your `.env`**
Create `.env` from [.env.example](D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/.env.example).

Use this as a practical starting point:

```env
BOT_TOKEN=your_bot_token_here
ADMIN_ID=111111111,222222222
ADMIN_USERNAME=your_username

USERBOT_API_ID=12345678
USERBOT_API_HASH=your_api_hash_here
USERBOT_SESSION=userbot

DB_PATH=bot.db
FORWARD_LOG_FILE=forward_logs.txt

DEFAULT_CURRENCY=INR

RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
RAZORPAY_WEBHOOK_SECRET=
RAZORPAY_BASE_URL=https://api.razorpay.com

FORCE_SUB_CHANNELS=@flashbro_bot_updates,@second_channel
FORCE_SUB_MESSAGE=You must join our channel(s) to use this bot.
```

If you do not have Razorpay yet, leave those 3 Razorpay values blank for now.

**Step-by-step VPS deploy**
Assumption:
- Ubuntu VPS
- project path will be `/opt/telegram-forwarder-bot`

1. Buy/start VPS
You’ll receive:
- server IP
- username, often `root`
- password

2. Connect to server
From Windows PowerShell:

```powershell
ssh root@YOUR_SERVER_IP
```

3. Install required software

```bash
apt update
apt install -y git python3 python3-venv python3-pip nginx
```

4. Create app folder

```bash
mkdir -p /opt/telegram-forwarder-bot
cd /opt/telegram-forwarder-bot
```

5. Upload your project
Simplest way:
- zip your full project folder on Windows
- upload with WinSCP to `/opt/telegram-forwarder-bot`

Alternative if using GitHub:

```bash
git clone YOUR_GITHUB_REPO_URL /opt/telegram-forwarder-bot
cd /opt/telegram-forwarder-bot
```

6. Create Python virtual environment

```bash
cd /opt/telegram-forwarder-bot
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

7. Add `.env`
Put your real `.env` in:

```bash
/opt/telegram-forwarder-bot/.env
```

8. First manual run test

```bash
cd /opt/telegram-forwarder-bot
source .venv/bin/activate
python bot.py
```

If startup is successful:
- bot should print config loaded
- bot should stay running
- open Telegram and test `/start`, `/menu`, `/health`

Stop it with `Ctrl+C`.

**Run as permanent background service**
You already have service templates:
- [telegram-forwarder-bot.service](D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/deploy/telegram-forwarder-bot.service)
- [telegram-forwarder-webhook.service](D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/deploy/telegram-forwarder-webhook.service)

9. Install bot service

```bash
cp /opt/telegram-forwarder-bot/deploy/telegram-forwarder-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable telegram-forwarder-bot
systemctl start telegram-forwarder-bot
systemctl status telegram-forwarder-bot
```

10. View logs

```bash
journalctl -u telegram-forwarder-bot -f
```

**Webhook service for Razorpay**
Only do this when you’re ready for payments.

11. Start webhook service

```bash
cp /opt/telegram-forwarder-bot/deploy/telegram-forwarder-webhook.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable telegram-forwarder-webhook
systemctl start telegram-forwarder-webhook
systemctl status telegram-forwarder-webhook
```

**Nginx setup**
Only needed for webhook/public HTTPS.

12. Install Nginx config

```bash
cp /opt/telegram-forwarder-bot/deploy/nginx-telegram-forwarder.conf /etc/nginx/sites-available/telegram-forwarder
ln -s /etc/nginx/sites-available/telegram-forwarder /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

Then edit the config and replace `your-domain.com` with your real domain in [nginx-telegram-forwarder.conf](D:/PROJECT-IMPORTANT/AUTOMATED_FORWARDED_BOT/deploy/nginx-telegram-forwarder.conf).

13. Add HTTPS

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d your-domain.com
```

**What happens after deploy**
- `bot.py` runs the Telegram bot and forwarding engine
- `razorpay_webhook.py` handles payment webhooks
- `bot.db` stores your data
- `sessions/` stores userbot sessions
- `.env` stores secrets/config

**Important file/folder backup**
Back up these regularly:
- `/opt/telegram-forwarder-bot/bot.db`
- `/opt/telegram-forwarder-bot/sessions/`
- `/opt/telegram-forwarder-bot/.env`

**What to do if you don’t have Razorpay yet**
That’s completely okay. Deploy without it first.
You can still test:
- `/start`
- `/menu`
- `/login`
- mapping
- forwarding
- admin panel
- force subscribe
- multi admin

Payments can be enabled later.

**Very simple beginner checklist**
1. Get VPS
2. Upload whole project folder
3. Create `.env`
4. Install Python requirements
5. Run `python bot.py`
6. Test bot in Telegram
7. Enable `systemd` bot service
8. Later set up domain + webhook + Razorpay

**My practical recommendation**
Do it in this order:
1. Deploy bot only, no Razorpay yet
2. Verify login + mapping + forwarding
3. Then enable Razorpay
4. Then configure webhook/domain

If you want, I can do the next easiest help for you: give you a copy-paste exact `.env` template with placeholder names and a copy-paste exact VPS command list in the order you should run them.

**REMEMBER .env.example should have the format not real values or important values.**
