import os
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

# --- بخش وب‌سرور برای Render ---
app = Flask(name)

@app.route('/')
def home():
    return "Bot is running!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- بخش ربات تلگرام ---
TOKEN = os.environ.get("BOT_TOKEN", "")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "سلام! ربات اسب‌دوانی آق‌قلا فعال شد. 👋\nبرای دریافت تحلیل دور ۴، دستور /analyze را بزنید."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

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

def run_bot():
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('analyze', analyze))
    print("ربات در حال اجراست...")
    application.run_polling()

if __name__ == '__main__':
    Thread(target=run_web_server).start()
    run_bot()
