import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from playwright.async_api import async_playwright

TOKEN = os.environ.get("BOT_TOKEN")
PHONE = os.environ.get("USER_PHONE")
PASSWORD = os.environ.get("USER_PASS")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "سلام! ربات فعال شد. 👋\nدستور /autoscan را بزنید تا ربات وارد سایت شود."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

async def autoscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="⏳ در حال باز کردن مرورگر و لاگین به سایت...")
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            await page.goto("https://mosharekat.asbdavani.app/", timeout=90000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000) 
            
            try:
                # تایپ کردن شماره موبایل و رمز عبور
                await page.fill('input[type="text"]', PHONE)
                await page.fill('input[type="password"]', PASSWORD)
                
                # کلیک روی دکمه ورود
                await page.click('button:has-text("ورود")')
                
                # صبر می‌کنیم تا صفحه داشبورد بعد از لاگین باز شود
                await page.wait_for_timeout(7000) 
            except Exception as e:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚠️ خطا در وارد کردن اطلاعات: {e}")
            
            await page.screenshot(path="screenshot.png")
            
            with open("screenshot.png", "rb") as photo:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, 
                    photo=photo, 
                    caption="📸 این صفحه بعد از لاگین است."
                )
                
            await browser.close()
            
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطای کلی:\n{str(e)}")

application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler('start', start))
application.add_handler(CommandHandler('autoscan', autoscan))

print("ربات در حال اجراست...")
application.run_polling(bootstrap_retries=-1)
