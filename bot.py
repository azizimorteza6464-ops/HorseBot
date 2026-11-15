Azizi Morteza:
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from playwright.async_api import async_playwright

TOKEN = os.environ.get("BOT_TOKEN")
PHONE = os.environ.get("USER_PHONE")

# متغیرهای سراسری برای نگه داشتن مرورگر در حافظه
global_browser = None
global_page = None
global_playwright = None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "سلام! ربات فعال شد. 👋\nدستور /autoscan را بزنید تا کد تایید برای شما پیامک شود."
    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)

async def autoscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global global_browser, global_page, global_playwright
    
    if not PHONE:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ خطا: شماره موبایل در Secrets تنظیم نشده است!")
        return

    await context.bot.send_message(chat_id=update.effective_chat.id, text="⏳ در حال باز کردن مرورگر و درخواست کد تایید...")
    
    try:
        global_playwright = await async_playwright().start()
        global_browser = await global_playwright.chromium.launch(headless=True)
        global_page = await global_browser.new_page()
        
        await global_page.goto("https://mosharekat.asbdavani.app/", timeout=90000)
        await global_page.wait_for_timeout(3000) 
        
        # پر کردن شماره موبایل در تب پیش‌فرض (کد یکبار مصرف)
        await global_page.locator('input:visible').first.fill(PHONE)
        await global_page.wait_for_timeout(1000)
        
        # کلیک روی دکمه ارسال کد
        await global_page.locator('button:has-text("ارسال کد")').click()
        await global_page.wait_for_timeout(5000)
        
        await global_page.screenshot(path="otp_screen.png")
        
        with open("otp_screen.png", "rb") as photo:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id, 
                photo=photo, 
                caption="📸 کد به گوشی شما پیامک شد.\n\nلطفاً کد را با این فرمت بفرستید:\n/sendcode 12345"
            )
            
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطا در درخواست کد:\n{str(e)}")

async def sendcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global global_browser, global_page, global_playwright
    
    if not global_page:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ مرورگر باز نیست. اول /autoscan را بزنید.")
        return

    try:
        otp_code = context.args[0]
    except IndexError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ فرمت اشتباه است. بنویسید: /sendcode 12345")
        return

    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"⏳ در حال وارد کردن کد {otp_code}...")
    
    try:
        # کلیک روی اولین کادر و تایپ کد (سایت خودش به کادر بعدی می‌رود)
        await global_page.locator('input:visible').first.click()
        await global_page.keyboard.type(otp_code)
        await global_page.wait_for_timeout(1500)
        
        # کلیک روی دکمه تایید و ورود
        await global_page.locator('button:has-text("تایید و ورود")').click()
        
        # صبر برای لود شدن کامل جدول
        await global_page.wait_for_selector('text="R1"', timeout=60000)
        await global_page.wait_for_timeout(3000) 
        
        await global_page.screenshot(path="dashboard_success.png", full_page=True)
        
        with open("dashboard_success.png", "rb") as photo:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id, 
                photo=photo, 
                caption="🎉 ورود موفق! این جدول مسابقات است."
            )
            
        # بستن مرورگر برای آزاد کردن منابع
        await global_browser.close()
        await global_playwright.stop()
        global_page = None
        
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطا در تایید کد:\n{str(e)}")

application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler('start', start))
application.add_handler(CommandHandler('autoscan', autoscan))
application.add_handler(CommandHandler('sendcode', sendcode))

print("ربات در حال اجراست...")
application.run_polling(bootstrap_retries=-1)
