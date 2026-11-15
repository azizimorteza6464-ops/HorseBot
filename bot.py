import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from playwright.async_api import async_playwright

# خواندن توکن و شماره موبایل از Secrets گیت‌هاب
TOKEN = os.environ.get("BOT_TOKEN")
PHONE = os.environ.get("USER_PHONE")

# متغیرهای سراسری برای نگه داشتن مرورگر در حافظه
g_pw = g_br = g_pg = None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="سلام! برای شروع دستور /autoscan را بفرستید.")

async def autoscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global g_pw, g_br, g_pg
    if not PHONE: 
        await context.bot.send_message(chat_id=update.effective_chat.id, text="شماره موبایل در Secrets تنظیم نشده است.")
        return
    
    await context.bot.send_message(chat_id=update.effective_chat.id, text="⏳ در حال باز کردن مرورگر و درخواست کد...")
    
    try:
        g_pw = await async_playwright().start()
        g_br = await g_pw.chromium.launch(headless=True)
        g_pg = await g_br.new_page()
        
        await g_pg.goto("https://mosharekat.asbdavani.app/", timeout=90000)
        await g_pg.wait_for_timeout(3000)
        
        # وارد کردن شماره موبایل در کادر
        await g_pg.locator('input:visible').first.fill(PHONE)
        await g_pg.wait_for_timeout(1000)
        
        # کلیک روی دکمه ارسال کد
        await g_pg.locator('button:has-text("ارسال کد")').click()
        await g_pg.wait_for_timeout(5000)
        
        # گرفتن عکس از صفحه
        await g_pg.screenshot(path="1.png")
        
        with open("1.png", "rb") as f:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id, 
                photo=f, 
                caption="کد پیامک شده را دقیقاً با این فرمت بفرستید:\n/sendcode 12345"
            )
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"خطا: {str(e)}")

async def sendcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global g_pw, g_br, g_pg
    
    if not g_pg:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ مرورگر باز نیست. اول /autoscan را بزنید.")
        return
    
    # چک کردن اینکه آیا کد همراه دستور فرستاده شده یا نه
    if not context.args:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ لطفا کد را همراه دستور بفرستید. مثال:\n/sendcode 12345")
        return

    try:
        otp = context.args[0]
        
        # کلیک روی کادر و تایپ کد
        await g_pg.locator('input:visible').first.click()
        await g_pg.keyboard.type(otp)
        await g_pg.wait_for_timeout(1500)
        
        # کلیک روی دکمه تایید
        await g_pg.locator('button:has-text("تایید و ورود")').click()
        
        # صبر 5 ثانیه‌ای (به جای 60 ثانیه) برای لود شدن صفحه بعدی
        await g_pg.wait_for_timeout(5000)
        
        # گرفتن عکس از کل صفحه بعد از لاگین
        await g_pg.screenshot(path="2.png", full_page=True)
        
        with open("2.png", "rb") as f:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id, 
                photo=f, 
                caption="📸 نتیجه بعد از وارد کردن کد."
            )
        
        # بستن مرورگر بعد از اتمام کار
        await g_br.close()
        await g_pw.stop()
        g_pg = None
        
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"خطا در تایید کد: {str(e)}")

# راه اندازی ربات
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler('start', start))
app.add_handler(CommandHandler('autoscan', autoscan))
app.add_handler(CommandHandler('sendcode', sendcode))
app.run_polling(bootstrap_retries=-1)
