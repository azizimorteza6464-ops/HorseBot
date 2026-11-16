import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from playwright.async_api import async_playwright
import re

TOKEN = os.environ.get("BOT_TOKEN")
PHONE = os.environ.get("USER_PHONE")
PASSWORD = os.environ.get("USER_PASS")

g_pw = g_br = g_pg = None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="سلام! /login_pass را بزنید تا ربات با رمز عبور وارد شود و ضرایب را بخواند.")

async def login_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global g_pw, g_br, g_pg
    if not PHONE or not PASSWORD: return
    await context.bot.send_message(chat_id=update.effective_chat.id, text="⏳ در حال تلاش برای ورود با رمز عبور (بدون پیامک)...")
    
    try:
        g_pw = await async_playwright().start()
        g_br = await g_pw.chromium.launch(headless=True)
        g_pg = await g_br.new_page()
        
        await g_pg.goto("https://mosharekat.asbdavani.app/", timeout=120000)
        await g_pg.wait_for_timeout(3000)
        
        # کلیک روی تب ورود با رمز عبور
        await g_pg.locator('text="ورود با رمز عبور"').click()
        await g_pg.wait_for_timeout(2000)
        
        # وارد کردن شماره و رمز
        await g_pg.locator('input:visible').first.fill(PHONE)
        await g_pg.wait_for_timeout(1000)
        await g_pg.keyboard.press('Tab')
        await g_pg.keyboard.type(PASSWORD)
        await g_pg.wait_for_timeout(1000)
        
        # کلیک روی دکمه ورود
        await g_pg.locator('button:has-text("ورود")').first.click()
        await g_pg.wait_for_timeout(5000)
        
        # بررسی اینکه آیا لاگین شدیم یا کپچا آمد
        if "ورود با رمز عبور" in await g_pg.locator('body').inner_text():
            await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ متاسفانه سایت کپچا (من ربات نیستم) نشان داد. نیاز به روش کوکی داریم.")
            await g_pg.screenshot(path="captcha.png")
            with open("captcha.png", "rb") as f:
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=f)
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ با موفقیت با رمز عبور وارد شدیم!")
            
            # رفتن به تب آد اسب
            await g_pg.locator('text="آد اسب"').last.click(force=True)
            await g_pg.wait_for_timeout(5000)
            
            # گرفتن متن صفحه آد اسب
            page_text = await g_pg.locator('body').inner_text()
            
            with open("odds_text.txt", "w", encoding="utf-8") as text_file:
                text_file.write(page_text)
                
            with open("odds_text.txt", "rb") as doc:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=doc,
                    caption="📄 فایل متنی ضرایب (آد اسب). لطفاً این را برای من بفرستید."
                )
        
        await g_br.close()
        await g_pw.stop()
        g_pg = None
        
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطا: {str(e)}")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler('start', start))
app.add_handler(CommandHandler('login_pass', login_pass))
app.run_polling(bootstrap_retries=-1)
