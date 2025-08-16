# main.py  — Telegram + Firebase realtime monitor (async, PTB v20+)
# Requirements (already handled earlier):
#   pip3 install python-telegram-bot==20.6 firebase-admin

import asyncio
import threading
import time
import firebase_admin
from firebase_admin import credentials, db
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ---------- CONFIG ----------
cred = credentials.Certificate("adminsdk.json")  # keep this file next to main.py
firebase_admin.initialize_app(cred, {
    "databaseURL": "https://transformer-accidents-default-rtdb.firebaseio.com"
})

FB_ROOT = "transformer_safety"

TELEGRAM_TOKEN = "7438973596:AAEeCc5e31npc0ypraXmGQRnuqB2wAvFIoc"
AUTHORIZED_CHAT_IDS = {"5232865054"}  # set your own Telegram numeric chat id(s) as strings

# Thresholds (match your ESP8266 sketch)
CURRENT_THRESHOLD = 2.0
TEMP_THRESHOLD = 50.0
WARNING_ZONE_CM = 1000.0
DANGER_ZONE_CM = 500.0

# ---------- STATE ----------
last_state = {
    "human_zone": None,     # None | "warning" | "danger"
    "distance_m": None,
    "fault": False,         # overcurrent alert latched
    "temp_high": False      # high temp alert latched
}

# ---------- Helpers ----------
def is_authorized(update: Update) -> bool:
    return str(update.effective_chat.id) in AUTHORIZED_CHAT_IDS

def ref_root():
    return db.reference(FB_ROOT)

def get_data() -> dict:
    return ref_root().get() or {}

def update_data(pairs: dict) -> None:
    ref_root().update(pairs)

async def safe_send(app, chat_id: str, text: str):
    try:
        await app.bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        print("Telegram send error:", e)

def boolish(v, default=False):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return default

# ---------- Telegram commands ----------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Transformer Safety Bot ready.\n"
        "/status\n"
        "/maintenance_on /maintenance_off\n"
        "/relay_open /relay_close\n"
        "/earthrod_on /earthrod_off\n"
        "/temp"
    )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_data()

    dist_cm = data.get("distance_cm")
    temp = data.get("temperature_c")
    curr = data.get("current_a")
    human = boolish(data.get("human_detected", 0))
    current_fault = boolish(data.get("current_fault", 0))
    relay_closed = boolish(data.get("relay_status", 0))
    rod_engaged = boolish(data.get("earth_rod_status", 0))
    maintenance = boolish(data.get("maintenance_mode", 0))

    dist_m = None
    if isinstance(dist_cm, (int, float)) and dist_cm > 0:
        dist_m = dist_cm / 100.0

    msg = "📡 Transformer Status\n"
    msg += f"Distance: {dist_m:.2f} m\n" if dist_m is not None else "Distance: N/A\n"
    msg += f"Human detected: {'YES' if human else 'NO'}\n"
    msg += f"Current: {curr:.2f} A\n" if isinstance(curr, (int, float)) else "Current: N/A\n"
    if isinstance(temp, (int, float)):
        msg += f"Temperature: {temp:.1f} °C"
        if temp >= TEMP_THRESHOLD:
            msg += " (HIGH!)"
        msg += "\n"
    else:
        msg += "Temperature: N/A\n"
    msg += f"Overcurrent: {'YES' if current_fault else 'NO'}\n"
    msg += f"Relay (closed): {'YES' if relay_closed else 'NO'}\n"
    msg += f"Earth rod engaged: {'YES' if rod_engaged else 'NO'}\n"
    msg += f"Maintenance mode: {'ON' if maintenance else 'OFF'}"
    await update.message.reply_text(msg)

async def temp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = get_data().get("temperature_c")
    if isinstance(t, (int, float)):
        txt = f"🌡 Temperature: {float(t):.1f} °C"
        if float(t) >= TEMP_THRESHOLD:
            txt += " (HIGH!)"
        await update.message.reply_text(txt)
    else:
        await update.message.reply_text("No temperature data.")

# ---- Maintenance + Manual controls (mirror your ESP keys) ----
async def maintenance_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await update.message.reply_text("Unauthorized")
    update_data({"maintenance_mode": 1})
    await update.message.reply_text("Maintenance mode ENABLED")

async def maintenance_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await update.message.reply_text("Unauthorized")
    update_data({"maintenance_mode": 0})
    await update.message.reply_text("Maintenance mode DISABLED")

def _require_maintenance() -> bool:
    return boolish(get_data().get("maintenance_mode", 0))

async def relay_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await update.message.reply_text("Unauthorized")
    if not _require_maintenance():
        return await update.message.reply_text("Enable maintenance mode first.")
    update_data({"relay_on": 0, "relay_status": 0})
    await update.message.reply_text("Relay opened (circuit open).")

async def relay_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await update.message.reply_text("Unauthorized")
    if not _require_maintenance():
        return await update.message.reply_text("Enable maintenance mode first.")
    update_data({"relay_on": 1, "relay_status": 1})
    await update.message.reply_text("Relay closed (circuit closed).")

async def earthrod_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await update.message.reply_text("Unauthorized")
    if not _require_maintenance():
        return await update.message.reply_text("Enable maintenance mode first.")
    update_data({"earth_rod_status": 1})
    await update.message.reply_text("Earth rod ENGAGED (manual).")

async def earthrod_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return await update.message.reply_text("Unauthorized")
    if not _require_maintenance():
        return await update.message.reply_text("Enable maintenance mode first.")
    update_data({"earth_rod_status": 0})
    await update.message.reply_text("Earth rod RETRACTED (manual).")

# ---------- Firebase polling ----------
async def poll_firebase(app):
    global last_state
    while True:
        data = get_data()

        dist_cm = data.get("distance_cm")
        temp = data.get("temperature_c")
        current = data.get("current_a")

        # Determine zone by distance
        zone = "none"
        dist_m = None
        if isinstance(dist_cm, (int, float)) and dist_cm > 0:
            dist_m = dist_cm / 100.0
            if dist_cm <= DANGER_ZONE_CM:
                zone = "danger"
            elif dist_cm <= WARNING_ZONE_CM:
                zone = "warning"

        # Human proximity notifications (send only on zone change)
        if zone == "warning" and last_state["human_zone"] != "warning":
            for chat in AUTHORIZED_CHAT_IDS:
                await safe_send(app, chat, f"⚠ Warning: human at {dist_m:.2f} m — Buzzer ON")
            last_state["human_zone"] = "warning"
            last_state["distance_m"] = dist_m
        elif zone == "danger" and last_state["human_zone"] != "danger":
            for chat in AUTHORIZED_CHAT_IDS:
                await safe_send(app, chat, f"🚨 DANGER: human at {dist_m:.2f} m — Relay OPEN & Earth rod ENGAGED")
            last_state["human_zone"] = "danger"
            last_state["distance_m"] = dist_m
        elif zone == "none" and last_state["human_zone"] is not None:
            for chat in AUTHORIZED_CHAT_IDS:
                await safe_send(app, chat, "✅ Area clear: no human detected in warning/danger zone.")
            last_state["human_zone"] = None
            last_state["distance_m"] = None

        # Overcurrent alert (edge-triggered)
        if isinstance(current, (int, float)) and current > CURRENT_THRESHOLD and not last_state["fault"]:
            for chat in AUTHORIZED_CHAT_IDS:
                await safe_send(app, chat, f"⚡ Fault: Overcurrent {float(current):.2f} A — Relay opened")
            last_state["fault"] = True
        elif isinstance(current, (int, float)) and current <= CURRENT_THRESHOLD and last_state["fault"]:
            for chat in AUTHORIZED_CHAT_IDS:
                await safe_send(app, chat, "✅ Current back to normal.")
            last_state["fault"] = False

        # Temperature alert (edge-triggered)
        if isinstance(temp, (int, float)) and temp >= TEMP_THRESHOLD and not last_state["temp_high"]:
            for chat in AUTHORIZED_CHAT_IDS:
                await safe_send(app, chat, f"🔥 HIGH TEMP: {float(temp):.1f} °C")
            last_state["temp_high"] = True
        elif isinstance(temp, (int, float)) and temp < TEMP_THRESHOLD and last_state["temp_high"]:
            for chat in AUTHORIZED_CHAT_IDS:
                await safe_send(app, chat, "✅ Temperature back to normal.")
            last_state["temp_high"] = False

        await asyncio.sleep(2)  # poll interval

# ---------- Main ----------
async def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("temp", temp_cmd))

    app.add_handler(CommandHandler("maintenance_on", maintenance_on))
    app.add_handler(CommandHandler("maintenance_off", maintenance_off))
    app.add_handler(CommandHandler("relay_open", relay_open))
    app.add_handler(CommandHandler("relay_close", relay_close))
    app.add_handler(CommandHandler("earthrod_on", earthrod_on))
    app.add_handler(CommandHandler("earthrod_off", earthrod_off))

    # Start Firebase polling in the background
    asyncio.create_task(poll_firebase(app))

    # Run the bot
    await app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    asyncio.run(main())
