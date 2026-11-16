import os,re
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from playwright.async_api import async_playwright
T=os.environ.get("BOT_TOKEN");U_T=os.environ.get("USER_TOKEN");U_D=os.environ.get("USER_DEVICE_ID");U_U=os.environ.get("USER_UDI")
async def st(update:Update,context:ContextTypes.DEFAULT_TYPE): await context.bot.send_message(chat_id=update.effective_chat.id,text="سلام! /check_odds")
async def ar(pg,r):
    try:
        await pg.evaluate(f"let el=Array.from(document.querySelectorAll('div,span,button,a'));let t=el.find(e=>e.textContent.trim()==='{r}'||e.textContent.trim()==='R {r}');if(t)t.click();")
        await pg.wait_for_timeout(2500)
    except: pass
    txt=await pg.locator('body').inner_text();h=[]
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*\n\s*([^\n\d]+)',txt):
        w,p,s=float(m.group(1)),float(m.group(2)),float(m.group(3));n=m.group(4).strip()
        if n and 0<w<90 and not any(x in n for x in ["مقام","شماره","نام","پیش","میان","پی","چابک"]):
            h.append({"n":n,"w":w,"p":p,"s":s,"pr":(1/w)*40+(1/p)*30+(1/s)*30})
    if not h: return None
    bw=min(h,key=lambda x:x["w"]);bp=min(h,key=lambda x:x["p"]);bs=min(h,key=lambda x:x["s"]);bpr=max(h,key=lambda x:x["pr"]);t=sorted(h,key=lambda x:x["w"])[:2]
    q=f"{t[0]['n']} + {t[1]['n']}" if len(t)>1 else "-"
    return {"w":bw,"p":bp,"s":bs,"q":q,"pr":bpr}
async def co(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not U_T or not U_U: return await context.bot.send_message(chat_id=update.effective_chat.id,text="❌ توکن تنظیم نشده!")
    await context.bot.send_message(chat_id=update.effective_chat.id,text="⏳ تحلیل...")
    try:
        async with async_playwright() as p:
            b=await p.chromium.launch(headless=True);c=await b.new_context()
            await c.add_cookies([{'name':'auth_token','value':U_T,'domain':'.asbdavani.app','path':'/'},{'name':'device_id','value':U_D,'domain':'.asbdavani.app','path':'/'},{'name':'_udi','value':U_U,'domain':'.asbdavani.app','path':'/'}])
            pg=await c.new_page();pg.set_default_timeout(30000)
            await pg.goto("https://mosharekat.asbdavani.app/",timeout=90000);await pg.wait_for_timeout(6000)
            if "ورود با کد یکبار مصرف" in await pg.locator('body').inner_text(): return await context.bot.send_message(chat_id=update.effective_chat.id,text="❌ لاگین نشد.")
            await pg.evaluate("let t=Array.from(document.querySelectorAll('span,a,div,button'));let o=t.find(e=>e.textContent.trim()==='آد اسب');if(o)o.click();");await pg.wait_for_timeout(4000)
            rep="🧠 تحلیل جامع\n\n";p3,p5,p6=[],[],[]
            for r in range(1,8):
                res=await ar(pg,r)
                if res:
                    rep+=f"R{r}:\n🥇 {res['w']['n']} ({res['w']['w']})\n🥈 {res['p']['n']} ({res['p']['p']})\n🥉 {res['s']['n']} ({res['s']['s']})\n👥 {res['q']}\n🎯 {res['pr']['n']}\n\n"
                    if r<=6: p6.append(res['pr']['n'])
                    if r<=5: p5.append(res['pr']['n'])
                    if r in [1,2,3]: p3.append(res['pr']['n'])
                else: rep+=f"R{r}: - \n\n"
            rep+="--- پره‌ها ---\n"
            if p6: rep+=f"🎯 ۶: {', '.join(p6[:6])}\n"
            if p5: rep+=f"🎯 ۵: {', '.join(p5[:5])}\n"
            if p3: rep+=f"🎯 ۳: {', '.join(p3[:3])}\n"
            await context.bot.send_message(chat_id=update.effective_chat.id,text=rep,parse_mode='Markdown');await b.close()
    except Exception as e: await context.bot.send_message(chat_id=update.effective_chat.id,text=f"❌ خطا: {str(e)}")
app=ApplicationBuilder().token(T).build();app.add_handler(CommandHandler('start',st));app.add_handler(CommandHandler('check_odds',co));app.run_polling(bootstrap_retries=-1)
