
import os
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.environ.get("BOT_TOKEN")
app = Flask(name)
application = Application.builder().token(TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="سلام! ربات اسب‌دوانی آق‌قلا فعال شد. 👋")

async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📊 تحلیل ربات برای دور ۴ (هفته ۷):\n\n"
        "🥇 پیش‌بر (برد): وینر بولتینگ (شماره ۱)\n"
        "🥈 میان‌بر (دوم): لیلی لوئیس (شماره ۴)\n"
        "🥉 پی‌بر (سوم): تکین آی تی سی (۲) یا اوردوز (۸)\n\n"
        "👥 دوقلوی منطقی: وینر بولتینگ (۱) + تکین آی تی سی (۲)\n"
        "🎲 دوقلوی شانسی: هواسپریت (۵) + اوردوز (۸)\n\n"
        "⚠️ توجه: تحلیل بر اساس ضرایب است."
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("analyze", analyze))

@app.route('/', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(application.process_update(update))
    return 'OK', 200

@app.route('/', methods=['GET'])
def home():
    return "Bot is running!", 200

@app.route('/setwebhook', methods=['GET'])
def set_webhook():
    url = request.url_root
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(application.bot.set_webhook(url))
    return f"Webhook set to {url}", 200
