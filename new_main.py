import threading
from telegram_bot import main as run_telegram_bot
from razorpay_webhook import app

def start_bot_thread():
    bot_thread = threading.Thread(target=run_telegram_bot, daemon=True)
    bot_thread.start()

@app.on_event("startup")
async def startup_event():
    start_bot_thread()
