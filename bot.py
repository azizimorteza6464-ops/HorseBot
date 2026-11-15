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

    await context.bot.send_message(chat_id=update.effective_chat.id, text="⏳ در حال باز کردن مرورگر و لاگین با رمز عبور...")
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            await page.goto("https://mosharekat.asbdavani.app/", timeout=90000, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000) 
            
            try:
                # ۱. کلیک روی تب "ورود با نام کاربری" (یا رمز عبور)
                # اگر متن دکمه دقیقا این نبود، ممکن است نیاز به تغییر جزئی داشته باشد
                await page.get_by_text("ورود با نام کاربری").click()
                await page.wait_for_timeout(2000) # صبر برای عوض شدن فرم
                
                # ۲. پر کردن شماره موبایل
                await page.locator('input:visible').first.fill(PHONE)
                await page.wait_for_timeout(1000)
                
                # ۳. رفتن به کادر بعدی و تایپ رمز عبور
                await page.keyboard.press('Tab')
                await page.wait_for_timeout(500)
                await page.keyboard.type(PASSWORD)
                await page.wait_for_timeout(500)
                
                # ۴. زدن Enter برای ورود
                await page.keyboard.press('Enter')
                
                # صبر برای باز شدن صفحه بعد از لاگین
                await page.wait_for_timeout(8000) 
            except Exception as e:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚠️ خطا در وارد کردن اطلاعات: {e}")
            
            await page.screenshot(path="screenshot.png")
            
            with open("screenshot.png", "rb") as photo:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, 
                    photo=photo, 
                    caption="📸 این صفحه بعد از تلاش برای لاگین با رمز است."
                )
                
            await browser.close()
            
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطای کلی:\n{str(e)}")

application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler('start', start))
application.add_handler(CommandHandler('autoscan', autoscan))

print("ربات در حال اجراست...")
application.run_polling(bootstrap_retries=-1)
