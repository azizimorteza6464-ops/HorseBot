import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from playwright.async_api import async_playwright

TOKEN = os.environ.get("BOT_TOKEN")
PHONE = os.environ.get("USER_PHONE")

g_pw = g_br = g_pg = None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="سلام! /autoscan را بزنید.")

async def autoscan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global g_pw, g_br, g_pg
    if not PHONE: return
    await context.bot.send_message(chat_id=update.effective_chat.id, text="⏳ در حال لود سایت...")
    try:
        g_pw = await async_playwright().start()
        g_br = await g_pw.chromium.launch(headless=True)
        g_pg = await g_br.new_page()
        await g_pg.goto("https://mosharekat.asbdavani.app/", timeout=120000, wait_until="domcontentloaded")
        await g_pg.wait_for_timeout(3000)
        await g_pg.locator('input:visible').first.fill(PHONE)
        await g_pg.wait_for_timeout(1000)
        await g_pg.locator('button:has-text("ارسال کد")').click()
        await g_pg.wait_for_timeout(5000)
        await g_pg.screenshot(path="1.png")
        with open("1.png", "rb") as f:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=f, caption="کد پیامک شده را بفرستید: /sendcode 12345")
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=str(e))

async def sendcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global g_pw, g_br, g_pg
    if not g_pg: return
    try:
        otp = context.args[0]
        await g_pg.locator('input:visible').first.click()
        await g_pg.keyboard.type(otp)
        await g_pg.wait_for_timeout(1500)
        
        # ۱. کلیک اول روی تایید
        await g_pg.locator('button:has-text("تایید و ورود")').click()
        await g_pg.wait_for_timeout(4000) 
        
        # ۲. کلیک روی اولین دستگاه (Chrome) برای بستن نشست
        try:
            await g_pg.locator('div:has-text("Chrome")').first.click(force=True)
            await g_pg.wait_for_timeout(2000)
        except Exception:
            pass
            
        # ۳. کلیک روی دکمه نهایی "ورود (بستن 1 نشست)"
        try:
            # دکمه‌ای که کلمه "ورود" در آن است را پیدا و کلیک می‌کند
            await g_pg.locator('button:has-text("ورود")').last.click()
        except Exception as e:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"خطا در کلیک دکمه نهایی: {e}")
            
        await g_pg.wait_for_timeout(8000)
        await g_pg.screenshot(path="2.png", full_page=True)
        with open("2.png", "rb") as f:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=f, caption="📸 نتیجه نهایی (اگر جدول را دیدیم، موفق شدیم!)")
        
        await g_br.close()
        await g_pw.stop()
        g_pg = None
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=str(e))

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler('start', start))
app.add_handler(CommandHandler('autoscan', autoscan))
app.add_handler(CommandHandler('sendcode', sendcode))
app.run_polling(bootstrap_retries=-1)
