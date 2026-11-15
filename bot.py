import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from playwright.async_api import async_playwright

TOKEN = os.environ.get("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "سلام! ربات فعال شد. 👋\nدستور /autoscan را بزنید تا ربات خودش وارد سایت اسب‌دوانی شود و اطلاعات را بخواند."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="این دستور برای تحلیل ضرایب دستی است (فعلا غیرفعال). از /autoscan استفاده کنید.")

async def autoscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="⏳ در حال باز کردن مرورگر مخفی و ورود به سایت اسب‌دوانی... (این کار کمی طول می‌کشد)")
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            await page.goto("https://asbdavani.app", timeout=60000)
            await page.wait_for_timeout(5000) 
            
            await page.screenshot(path="screenshot.png")
            
            with open("screenshot.png", "rb") as photo:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, 
                    photo=photo, 
                    caption="📸 این چیزی است که ربات در سایت می‌بیند."
                )
                
            await browser.close()
            
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطا در خواندن سایت:\n{str(e)}")

application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler('start', start))
application.add_handler(CommandHandler('analyze', analyze))
application.add_handler(CommandHandler('autoscan', autoscan))

print("ربات در حال اجراست...")
application.run_polling(bootstrap_retries=-1)
