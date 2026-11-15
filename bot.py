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
    if not PHONE or not PASSWORD:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ خطا: شماره یا رمز در Secrets تنظیم نشده است!")
        return

    await context.bot.send_message(chat_id=update.effective_chat.id, text="⏳ در حال ورود به سایت و استخراج جدول...")
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            # افزایش زمان انتظار به ۹۰ ثانیه برای جلوگیری از خطای Timed out
            page.set_default_timeout(90000) 
            
            await page.goto("https://mosharekat.asbdavani.app/", timeout=90000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000) 
            
            try:
                # کلیک روی تب ورود با رمز
                await page.locator('text="ورود با رمز عبور"').click()
                await page.wait_for_timeout(2000) 
                
                await page.locator('input:visible').first.fill(PHONE)
                await page.wait_for_timeout(1000)
                
                await page.keyboard.press('Tab')
                await page.wait_for_timeout(500)
                await page.keyboard.type(PASSWORD)
                await page.wait_for_timeout(500)
                
                await page.keyboard.press('Enter')
                
                # صبر میکنیم تا جدول حتما لود شود (منتظر دیدن کلمه R1 می‌مانیم)
                await page.wait_for_selector('text="R1"', timeout=60000)
                await page.wait_for_timeout(3000) 
            except Exception as e:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚠️ هشدار (ممکن است صفحه کند بوده باشد): {e}")
            
            # گرفتن عکس از کل صفحه (Full Page)
            await page.screenshot(path="screenshot.png", full_page=True)
            
            with open("screenshot.png", "rb") as photo:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, 
                    photo=photo, 
                    caption="📸 ورود موفق! جدول مسابقات استخراج شد."
                )
                
            await browser.close()
            
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطای کلی:\n{str(e)}")

application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler('start', start))
application.add_handler(CommandHandler('autoscan', autoscan))

print("ربات در حال اجراست...")
application.run_polling(bootstrap_retries=-1)
