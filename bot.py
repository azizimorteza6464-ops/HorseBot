import os,re
from telegram import Update
from telegram.ext import ApplicationBuilder,ContextTypes,CommandHandler
from playwright.async_api import async_playwright
T=os.environ.get("BOT_TOKEN");A=os.environ.get("USER_TOKEN");D=os.environ.get("USER_DEVICE_ID");U=os.environ.get("USER_UDI")
async def st(u,c):await c.bot.send_message(u.effective_chat.id,"سلام! /check_odds")
async def ar(p,rd):
    try:await p.evaluate(f"let a=document.querySelectorAll('div,span,button,a');let t=Array.from(a).find(e=>e.textContent.trim()==='{rd}'||e.textContent.trim()==='R {rd}');if(t)t.click();");await p.wait_for_timeout(2000)
    except:pass
    x=await p.locator('body').inner_text();h=[]
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*\n\s*([^\n\d]+)',x):
        w,pl,sh=float(m.group(1)),float(m.group(2)),float(m.group(3));n=m.group(4).strip()
        if n and 0<w<90 and not any(k in n for k in ["مقام","شماره","نام","پیش","میان","پی","چابک"]):h.append({"n":n,"w":w,"p":pl,"s":sh,"pr":(1/w)*40+(1/pl)*30+(1/sh)*30})
    if not h:return
    o=sorted(h,key=lambda y:y["w"]);b=o[0];p2=min(h,key=lambda y:y["p"]);s2=min(h,key=lambda y:y["s"]);r2=max(h,key=lambda y:y["pr"])
    q=f"{b['n']}+{o[1]['n']}" if len(o)>1 else "-"
    t=[y['n'] for y in o[:3]];m=[y for y in h if y['n'] not in t];su=max(m,key=lambda y:y["pr"]) if m else o[-1]
    return{"w":b,"p":p2,"s":s2,"q":q,"pr":r2,"su":su}
async def co(u,c):
    if not A or not U:return await c.bot.send_message(u.effective_chat.id,"❌ تنظیم نشده!")
    await c.bot.send_message(u.effective_chat.id,"⏳ تحلیل...")
    try:
        async with async_playwright() as p:
            b=await p.chromium.launch(headless=True);ct=await b.new_context()
            await ct.add_cookies([{'name':'auth_token','value':A,'domain':'.asbdavani.app','path':'/'},{'name':'device_id','value':D,'domain':'.asbdavani.app','path':'/'},{'name':'_udi','value':U,'domain':'.asbdavani.app','path':'/'}])
            pg=await ct.new_page();pg.set_default_timeout(30000)
            await pg.goto("https://mosharekat.asbdavani.app/",timeout=90000);await pg.wait_for_timeout(5000)
            if "ورود با کد یکبار مصرف" in await pg.locator('body').inner_text():return await c.bot.send_message(u.effective_chat.id,"❌ لاگین نشد.")
            await pg.evaluate("let a=document.querySelectorAll('span,a,div,button');let o=Array.from(a).find(e=>e.textContent.trim()==='آد اسب');if(o)o.click();");await pg.wait_for_timeout(3000)
            r7=await pg.locator("text='R 7'").count()>0 or await pg.locator("text='7'").count()>0;mr=7 if r7 else 6
            rp="🧠 تحلیل\n";p3,p5,p6=[],[],[]
            for r in range(1,mr+1):
                res=await ar(pg,r)
                if res:
                    rp+=f"\nR{r}:\n🥇 {res['w']['n']} ({res['w']['w']})\n🥈 {res['p']['n']} ({res['p']['p']})\n🥉 {res['s']['n']} ({res['s']['s']})\n👥 {res['q']}\n🎯 {res['pr']['n']}\n🎲 {res['su']['n']}\n"
                    if r<=6:p6.append(res['pr']['n'])
                    if r<=5:p5.append(res['pr']['n'])
                    if r in[1,2,3]:p3.append(res['pr']['n'])
                else:rp+=f"\nR{r}: -\n"
            rp+="\n--- پره‌ها ---\n"
            if p6:rp+=f"🎯 ۶: {', '.join(p6[:6])}\n"
            if p5:rp+=f"🎯 ۵: {', '.join(p5[:5])}\n"
            if p3:rp+=f"🎯 ۳: {', '.join(p3[:3])}\n"
            await c.bot.send_message(u.effective_chat.id,rp,parse_mode='Markdown');await b.close()
    except Exception as e:await c.bot.send_message(u.effective_chat.id,f"❌ {str(e)}")
app=ApplicationBuilder().token(T).build();app.add_handler(CommandHandler('start',st));app.add_handler(CommandHandler('check_odds',co));app.run_polling(bootstrap_retries=-1)
