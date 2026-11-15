import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

TOKEN = os.environ.get("BOT_TOKEN")

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

application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler('start', start))
application.add_handler(CommandHandler('analyze', analyze))

print("ربات در حال اجراست...")
application.run_polling()
