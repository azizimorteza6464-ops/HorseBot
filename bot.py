import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from playwright.async_api import async_playwright

TOKEN = os.environ.get("BOT_TOKEN")
USER_TOKEN = os.environ.get("USER_TOKEN")
USER_DEVICE_ID = os.environ.get("USER_DEVICE_ID")
USER_UDI = os.environ.get("USER_UDI")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="سلام! /check_odds را بزنید.")

async def check_odds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not USER_TOKEN or not USER_UDI:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ توکن یا کوکی‌ها در Secrets تنظیم نشده‌اند!")
        return

    await context.bot.send_message(chat_id=update.effective_chat.id, text="⏳ در حال ورود به سایت با کوکی‌ها (بدون کپچا)...")
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context_browser = await browser.new_context()
            
            # تزریق هر ۳ کوکی
            cookies = [
                {'name': 'auth_token', 'value': USER_TOKEN, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': 'device_id', 'value': USER_DEVICE_ID, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': '_udi', 'value': USER_UDI, 'domain': '.asbdavani.app', 'path': '/'}
            ]
                
            await context_browser.add_cookies(cookies)
            
            page = await context_browser.new_page()
            await page.goto("https://mosharekat.asbdavani.app/", timeout=90000)
            
            # صبر برای باز شدن کامل داشبورد
            await page.wait_for_timeout(8000)
            
            # بررسی لاگین بودن
            content = await page.locator('body').inner_text()
            if "ورود با کد یکبار مصرف" in content:
                await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ متاسفانه لاگین نشد. (شاید توکن منقضی شده یا کوکی‌ها اشتباهند).")
                await browser.close()
                return
                
            await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ ورود با موفقیت انجام شد! در حال رفتن به تب آد اسب...")
            
            # پیدا کردن و کلیک روی آد اسب
            try:
                await page.locator('text="آد اسب"').wait_for(state="visible", timeout=15000)
                await page.locator('text="آد اسب"').last.click(force=True)
                await page.wait_for_timeout(6000)
            except Exception as e:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚠️ آد اسب پیدا نشد: {e}")
            
            # گرفتن عکس و متن
            await page.screenshot(path="odds.png")
            page_text = await page.locator('body').inner_text()
            with open("odds_text.txt", "w", encoding="utf-8") as text_file:
                text_file.write(page_text)
                
            with open("odds_text.txt", "rb") as doc:
                await context.bot.send_document(chat_id=update.effective_chat.id, document=doc, caption="📄 متن ضرایب")
            
            await browser.close()
            
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطا: {str(e)}")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler('start', start))
app.add_handler(CommandHandler('check_odds', check_odds))
app.run_polling(bootstrap_retries=-1)
