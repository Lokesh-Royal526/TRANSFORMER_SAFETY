import os
import firebase_admin
from firebase_admin import credentials, db
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext

# ===============================
# Telegram Token from Environment
# ===============================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN not set in environment variables")

# ===============================
# Firebase Initialization
# ===============================
cred = credentials.Certificate("src/firebase_config.json")
firebase_admin.initialize_app(cred, {
    "databaseURL": "https://your-db.firebaseio.com/"
})

# ===============================
# Telegram Bot Setup
# ===============================
updater = Updater(TELEGRAM_TOKEN, use_context=True)
dispatcher = updater.dispatcher

# Test Command
def start(update: Update, context: CallbackContext):
    update.message.reply_text("⚡ Transformer Safety Bot is Active!")

dispatcher.add_handler(CommandHandler("start", start))

# ===============================
# Start Bot
# ===============================
if __name__ == "__main__":
    print("✅ Bot is running...")
    updater.start_polling()
    updater.idle()
