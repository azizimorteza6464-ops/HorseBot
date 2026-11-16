import os,re
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from playwright.async_api import async_playwright
TOKEN=os.environ.get("BOT_TOKEN");USER_TOKEN=os.environ.get("USER_TOKEN");USER_DEVICE_ID=os.environ.get("USER_DEVICE_ID");USER_UDI=os.environ.get("USER_UDI")
async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id,text="سلام! /check_odds را بزنید.")
async def analyze_round(page,r):
    try:
        await page.evaluate(f"let el=Array.from(document.querySelectorAll('div,span,button,a'));let tab=el.find(e=>e.textContent.trim()==='{r}'||e.textContent.trim()==='R {r}');if(tab)tab.click();")
        await page.wait_for_timeout(2500)
    except: pass
    txt=await page.locator('body').inner_text();h=[]
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*\n\s*([^\n\d]+)',txt):
        w=float(m.group(1));n=m.group(4).strip()
        if n and 0<w<90: h.append({"name":n,"win":w,"prob":round((1/w)*100,2)})
    h.sort(key=lambda x:x["prob"],reverse=True);return h
async def check_odds(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not USER_TOKEN or not USER_UDI: return await context.bot.send_message(chat_id=update.effective_chat.id,text="❌ توکن تنظیم نشده!")
    await context.bot.send_message(chat_id=update.effective_chat.id,text="⏳ در حال تحلیل...")
    try:
        async with async_playwright() as p:
            b=await p.chromium.launch(headless=True);c=await b.new_context()
            await c.add_cookies([{'name':'auth_token','value':USER_TOKEN,'domain':'.asbdavani.app','path':'/'},{'name':'device_id','value':USER_DEVICE_ID,'domain':'.asbdavani.app','path':'/'},{'name':'_udi','value':USER_UDI,'domain':'.asbdavani.app','path':'/'}])
            pg=await c.new_page();pg.set_default_timeout(30000)
            await pg.goto("https://mosharekat.asbdavani.app/",timeout=90000);await pg.wait_for_timeout(6000)
            if "ورود با کد یکبار مصرف" in await pg.locator('body').inner_text(): return await context.bot.send_message(chat_id=update.effective_chat.id,text="❌ لاگین نشد.")
            await pg.evaluate("let t=Array.from(document.querySelectorAll('span,a,div,button'));let o=t.find(e=>e.textContent.trim()==='آد اسب');if(o)o.click();");await pg.wait_for_timeout(4000)
            rep="🧠 تحلیل نهایی\n\n";p3,p5,p6=[],[],[]
            for r in range(1,8):
                res=await analyze_round(pg,r)
                if res:
                    best=res[0];rep+=f"🏁 R{r}:\n🥇 پیش‌بر: {best['name']} ({best['prob']}%)\n"
                    if len(res)>1: rep+=f"👥 دوقلو: {res[0]['name']} + {res[1]['name']}\n"
                    if r<=6: p6.append(best['name'])
                    if r<=5: p5.append(best['name'])
                    if r in [1,2,3]: p3.append(best['name'])
                    rep+="\n"
                else: rep+=f"🏁 R{r}: اطلاعاتی یافت نشد.\n\n"
            rep+="--- پره‌ها ---\n"
            if p6: rep+=f"🎯 ۶ پره: {', '.join(p6[:6])}\n"
            if p5: rep+=f"🎯 ۵ پره: {', '.join(p5[:5])}\n"
            if p3: rep+=f"🎯 ۳ پره: {', '.join(p3[:3])}\n"
            await context.bot.send_message(chat_id=update.effective_chat.id,text=rep,parse_mode='Markdown');await b.close()
    except Exception as e: await context.bot.send_message(chat_id=update.effective_chat.id,text=f"❌ خطا: {str(e)}")
app=ApplicationBuilder().token(TOKEN).build();app.add_handler(CommandHandler('start',start));app.add_handler(CommandHandler('check_odds',check_odds));app.run_polling(bootstrap_retries=-1)
