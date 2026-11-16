import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from playwright.async_api import async_playwright

TOKEN = os.environ.get("BOT_TOKEN")
USER_TOKEN = os.environ.get("USER_TOKEN") # توکن جدید که از مرورگر گرفتیم

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="سلام! /check_odds را بزنید تا با توکن وارد شوم و ضرایب را بخوانم.")

async def check_odds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not USER_TOKEN:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ توکن در Secrets تنظیم نشده!")
        return

    await context.bot.send_message(chat_id=update.effective_chat.id, text="⏳ در حال تزریق توکن و ورود به سایت...")
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context_browser = await browser.new_context()
            
            # تزریق کوکی (توکن) به مرورگر
            await context_browser.add_cookies([{
                'name': 'auth_token', # احتمالا نام کوکی auth_token است
                'value': USER_TOKEN,
                'domain': 'mosharekat.asbdavani.app',
                'path': '/'
            }])
            
            page = await context_browser.new_page()
            
            # رفتن مستقیم به صفحه اصلی
            await page.goto("https://mosharekat.asbdavani.app/", timeout=90000)
            await page.wait_for_timeout(3000)
            
            # بررسی اینکه آیا وارد شده‌ایم (اگر کلمه خوش آمدید نبود یعنی موفق بوده)
            content = await page.locator('body').inner_text()
            if "خوش آمدید" in content and "ورود با کد یکبار مصرف" in content:
                await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ توکن منقضی شده یا نام کوکی اشتباه است. (لطفا دوباره از مرورگر بگیرید).")
                await browser.close()
                return
                
            await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ ورود با توکن موفق بود! در حال رفتن به صفحه آد اسب...")
            
            # کلیک روی تب آد اسب
            try:
                await page.locator('text="آد اسب"').last.click(force=True)
                await page.wait_for_timeout(5000)
            except Exception as e:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚠️ خطا در باز کردن آد اسب: {e}")
            
            # گرفتن عکس و متن
            await page.screenshot(path="odds.png", full_page=True)
            with open("odds.png", "rb") as f:
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=f, caption="📸 صفحه آد اسب")
                
            page_text = await page.locator('body').inner_text()
            with open("odds_text.txt", "w", encoding="utf-8") as text_file:
                text_file.write(page_text)
                
            with open("odds_text.txt", "rb") as doc:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id, document=doc, 
                    caption="📄 متن ضرایب. (به زودی اینجا تحلیل می‌نویسم!)"
                )
            
            await browser.close()
            
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطا: {str(e)}")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler('start', start))
app.add_handler(CommandHandler('check_odds', check_odds))
app.run_polling(bootstrap_retries=-1)
