## Staging Checklist

### 1. Install dependencies

Run:

```powershell
pip install -r requirements.txt
```

If you plan to use Razorpay webhooks, confirm `fastapi` and `uvicorn` are installed.

### 2. Verify environment

Required:

```env
BOT_TOKEN=
ADMIN_ID=
USERBOT_API_ID=
USERBOT_API_HASH=
DB_PATH=bot.db
```

For payments:

```env
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
RAZORPAY_WEBHOOK_SECRET=
```

Optional:

```env
FORCE_SUB_CHANNELS=@channel1,@channel2
FORCE_SUB_MESSAGE=Join required channels to use this bot
```

### 3. Start the bot

Run:

```powershell
python bot.py
```

Confirm:

- The bot starts without import errors.
- `/health` works for the admin.
- The bot command menu shows user and admin commands.

### 4. Test login flow

- Open the bot in Telegram.
- Run `/login`.
- Submit a valid phone number.
- Enter the OTP in the required format.
- Confirm `/menu` opens after login.
- Restart the bot and confirm the session is restored.

### 5. Test mappings

- Run `/add_mapping @publicsource -100targetchatid`
- Or use the interactive mapping flow.
- Run `/list_mappings`
- Remove one mapping with `/remove_mapping <id>`
- Re-add the same mapping and confirm duplicates are not created.

### 6. Test forwarding

Setup:

- Source must be a public channel username.
- Target must allow the bot to post.
- The logged-in userbot account must be able to read the source channel.

Tests:

- Post plain text in the source channel.
- Post a photo in the source channel.
- Post a video in the source channel.
- Post a document in the source channel.
- Confirm each message is copied to the target channel.
- Run `/health` and confirm pending forwards return to `0`.

### 7. Test subscription controls

- Create a plan with `/create_plan`
- Run `/list_plans_admin`
- Activate a user manually with `/manual_activate`
- Check `/status` from that user
- Disable and re-enable a plan

### 8. Test payment flow

Manual verification path:

- Create a low-value plan.
- Open `/buy`
- Generate a Razorpay payment link.
- Complete one real payment.
- Press `I Paid`
- Confirm subscription activation.

Webhook path:

Run:

```powershell
uvicorn razorpay_webhook:app --host 0.0.0.0 --port 8000
```

Then:

- Expose the webhook endpoint publicly.
- Register the Razorpay webhook URL.
- Complete a payment.
- Confirm the user receives activation and the subscription row is created.

### 9. Test admin tools

- `/stats`
- `/list_users`
- `/list_subscribers`
- `/list_forwarding_users`
- `/broadcast hello`
- `/export_payments`
- `/health`

### 10. Production readiness checks

- Back up `bot.db` regularly.
- Restrict admin access to the configured `ADMIN_ID`.
- Keep one dedicated Telegram account for userbot monitoring.
- Use a process manager or host auto-restart.
- Monitor failed forwards and pending queue counts with `/health`.
