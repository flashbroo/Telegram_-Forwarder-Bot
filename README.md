<<<<<<< HEAD
# Telegram_-Forwarder-Bot
I have made telegram bot which is subscription based. Bot forwards the message from public channel to their private channels. 
=======
# FOR ADMIN 

Admin can simply type:
/add_mapping @source @target
/list_mappings
/remove_mapping <id>


These will work because:

Admin is allowed

Handlers exist

No restriction blocks admin from using them

---

# 🚀 Auto Forwarder Telegram Bot

A **subscription-based Telegram bot** that automatically forwards posts from **public channels** to **target channels**, with **admin control**, **free/paid access**, and **plan management**.

Built with:

* Python
* python-telegram-bot (v20+)
* SQLite (lightweight, zero cost)
* Modular, scalable architecture

---

## 📌 What This Bot Does

* Automatically forwards posts from **source channels** → **target channels**
* Supports **text, images, videos, documents, polls**
* Allows **multiple mappings per user**
* Monetized via **subscription plans**
* Supports:

  * Free users
  * Paid users
  * Global free mode (admin controlled)

---

## 🧠 Core Concepts (Very Important)

### 1️⃣ User Access States

Each user always falls into **one** of these states:

| State       | Meaning                               |
| ----------- | ------------------------------------- |
| `BLOCKED`   | No access, must buy a plan            |
| `FREE_USER` | Admin granted free access             |
| `FREE_ALL`  | Bot is free for everyone (admin mode) |
| `PAID`      | User has active subscription          |

👉 **UI, buttons, and permissions are driven entirely by this state**

---

### 2️⃣ Who Can Use What?

#### 👤 Normal User

* Can:

  * Buy a plan
  * Add mappings
  * View mappings
  * Extend plan
* Cannot:

  * See admin controls
  * Change prices
  * Enable free mode

#### 🧑‍💻 Admin

* Can:

  * Enable / disable free mode
  * Create, enable, disable plans
  * Change plan prices
  * Grant free access to users
  * Manually activate subscriptions
  * Broadcast messages
* Sees **all commands and admin UI**

---

## 💳 Subscription Logic

### Plans

* Stored in **database** (not config)
* Each plan has:

  * `plan_key`
  * price
  * duration (default: 30 days)
  * currency
  * active / inactive

### Paid Users

* Can **extend before expiry**
* Can see:

  * expiry date
  * days remaining
* When plan is near expiry (≤ 3 days):

  * Bot shows **⚠️ Expiring Soon**
  * Extend button highlighted

---

## 🧭 How Users Use the Bot

### Step-by-Step Flow

1. User sends `/start`
2. Bot checks:

   * Forced channel join (if enabled)
3. User opens `/menu`
4. Based on access:

   * Free → directly use bot
   * Paid → manage mappings
   * Blocked → buy plan
5. User adds mapping:

   ```
   /add_mapping <source_channel> <target_channel>
   ```
6. Bot automatically forwards new posts

---

## 🔐 Forced Channel Join (Optional)

* Users must join required channels before using the bot
* Controlled via `.env`:

```env
FORCE_SUB_CHANNELS=@channel1,@channel2
```

---

## 🗂 Project Structure

```
.
├── bot.py                 # Entry point
├── config.py              # Environment config
├── db.py                  # SQLite + helpers
├── subscriptions.py       # Access logic
├── payments_telegram.py   # Payment flows
├── admin_cmds.py          # Admin commands
├── ui.py                  # Menus & buttons
├── forwarder.py           # Forwarding engine
├── mappings.py            # Mapping CRUD
├── force_subscribe.py     # Join enforcement
├── utils.py               # Utilities
├── bot.db                 # SQLite database
└── .env                   # Secrets
```

---

## 🧾 Environment Variables (`.env`)

```env
BOT_TOKEN=xxxxxxxxxxxx
ADMIN_ID=123456789
ADMIN_USERNAME=yourusername

DB_PATH=bot.db

UPI_ID=yourupi@bank
PAYPAL_LINK=https://paypal.me/yourlink
TELEGRAM_PROVIDER_TOKEN=xxxxx

FORCE_SUB_CHANNELS=@channel1,@channel2
FORCE_SUB_MESSAGE=Join required channels to use this bot
```

---

## 🖥️ Deployment (Low Cost, India Friendly)

### ✅ Best Option: **Railway.app**

* Cost: **₹0 – ₹300/month**
* Very simple
* Auto restart
* Good for Telegram bots

#### Steps:

1. Push code to GitHub
2. Go to [https://railway.app](https://railway.app)
3. New Project → Deploy from GitHub
4. Add environment variables
5. Start command:

   ```
   python bot.py
   ```

---

### ✅ Cheapest VPS Option: **Oracle Cloud Free Tier**

* Cost: **₹0**
* 24 GB RAM (free)
* Best long-term option

#### Setup:

```bash
sudo apt update
sudo apt install python3 python3-pip -y
pip install -r requirements.txt
python bot.py
```

Use `screen` or `pm2` to keep bot running.

---

### ⚠️ NOT Recommended

* Shared hosting
* Replit (unstable)
* Heroku (no longer free)

---

## 🔒 Security Notes

* Admin actions are **hard-guarded by ADMIN_ID**
* Payments verified before activation
* No sensitive data logged
* SQLite safe for low–medium traffic

---

## 📈 Scalability

When traffic grows:

* Replace SQLite → PostgreSQL
* Add Redis (optional)
* Split forwarder into worker process

Current design already supports this cleanly.

---

## ✅ Summary

This bot is:

* Monetizable
* Admin-controlled
* User-friendly
* Cheap to run
* Easy to extend

You’ve built a **real SaaS-style Telegram product**, not a hobby bot.

---

Below is a **clear, short, copy-paste ready section** you can directly add to your **README**.
It explains **commands for BOTH users and admin**, without confusion.

I’ll also explain **why some commands show / don’t show**, because that confused you earlier.

---

## 🧾 Commands – How to Use

---

# 👤 User Commands (Normal Users)

These commands are visible and usable by **all users**.

### `/start`

* Starts the bot
* Checks forced channel join (if enabled)
* Shows a short intro message

**Use when:** First time or after joining required channels

---

### `/menu`

* Opens the main menu with buttons
* Buttons change based on your access:

  * Free
  * Paid
  * Blocked

**Use when:** You want to manage everything via buttons

---

### `/buy <plan_key>`

* Buy a subscription plan
* Example:

```text
/buy basic
```

**Use when:**

* You are blocked
* OR you want to extend your current plan

---

### `/status`

* Shows your subscription status
* Shows expiry date if paid

**Use when:** Check how many days are left

---

### `/add_mapping <source_channel> <target_channel>`

* Creates auto-forward rule

Example:

```text
/add_mapping @sourcechannel @targetchannel
```

⚠️ Rules:

* Source must be **public**
* Bot must be **admin in target channel**

---

### `/list_mappings`

* Shows all your active mappings

---

### `/remove_mapping <mapping_id>`

* Removes a mapping

Example:

```text
/remove_mapping 3
```

---

# 🧑‍💻 Admin Commands (ADMIN_ID only)

⚠️ These commands **exist for everyone**, but **only ADMIN_ID can execute them**.

---

## 🔓 Access Control

### `/grant_free <user_id>`

Give free access to a user

```text
/grant_free 123456789
```

---

### `/revoke_free <user_id>`

Remove free access

---

### `/free_on`

Enable **Free Mode for ALL users**

---

### `/free_off`

Disable Free Mode
→ Bot becomes **paid-only**

---

## 💳 Plan Management

### `/create_plan <key> <name> <price> <currency> [days]`

Example:

```text
/create_plan basic Basic 60 INR 30
```

If days not given → defaults to **30 days**

---

### `/update_price <plan_key> <new_price>`

```text
/update_price basic 80
```

---

### `/enable_plan <plan_key>`

```text
/enable_plan basic
```

---

### `/disable_plan <plan_key>`

```text
/disable_plan basic
```

---

### `/list_plans_admin`

Lists all plans (active + inactive)

---

## 🛠 Subscription Control

### `/manual_activate <user_id> <plan_key> [days]`

Manually activate subscription

```text
/manual_activate 123456789 basic 15
```

---

### `/list_subscribers`

Shows all active subscribers

---

### `/list_users`

Lists all users and free status

---

## 📢 Communication

### `/broadcast <message>`

Send message to all users

```text
/broadcast Bot will be down for maintenance tonight.
```

---

### `/export_payments`

Exports all payments as CSV

---

## 🤔 Common Confusions (Important)

### ❓ Why admin commands don’t show in menu?

* Menu is **button-based**
* Admin commands are **slash commands**
* This is intentional (clean UI)

---

### ❓ Why normal users don’t see admin buttons?

* UI checks:

```python
if user.id == ADMIN_ID
```

* Security by design

---

### ❓ When does “Buy Plan” button show?

* Only when:

  * User is `BLOCKED`
  * OR user clicks **Extend Plan**
  * OR plan is expired / near expiry

---

### ❓ How paid users extend plan?

* Menu shows:

```
🔁 Extend Plan
```

* Clicking it → shows plans → `/buy <plan_key>`

---

## ✅ Quick Mental Model

* **Buttons = user UX**
* **Slash commands = power & admin**
* **Access state controls everything**
* **Paid users never get locked out**



>>>>>>> 5431de8 (Initial telegrambot project setup)
