import os,re,httpx
from telegram import Update
from telegram.ext import ApplicationBuilder as AB,ContextTypes as C,CommandHandler as CH
from playwright.async_api import async_playwright as ap
T=os.environ.get("BOT_TOKEN");A=os.environ.get("USER_TOKEN");D=os.environ.get("USER_DEVICE_ID");U=os.environ.get("USER_UDI");G=os.environ.get("GH_PAT");R=os.environ.get("GITHUB_REPOSITORY")
async def s(v):
    if not R or not G:return"E"
    u=f"https://api.github.com/repos/{R}/actions/variables/AUTO_UPDATE";h={"Authorization":f"Bearer {G}","Accept":"application/vnd.github+json"}
    async with httpx.AsyncClient() as c:
        q=await c.get(u,headers=h)
        if q.status_code==404:await c.post(f"https://api.github.com/repos/{R}/actions/variables",headers=h,json={"name":"AUTO_UPDATE","value":v})
        else:await c.patch(u,headers=h,json={"name":"AUTO_UPDATE","value":v})
    return"OK"
async def st(u:Update,c:C):await c.bot.send_message(u.effective_chat.id,"/on /off /check_odds [1-7]")
async def on(u:Update,c:C):r=await s("true");await c.bot.send_message(u.effective_chat.id,"ON"if r=="OK"else r)
async def off(u:Update,c:C):r=await s("false");await c.bot.send_message(u.effective_chat.id,"OFF"if r=="OK"else r)
    import os,re,httpx
from telegram import Update
from telegram.ext import ApplicationBuilder as AB,ContextTypes as C,CommandHandler as CH
from playwright.async_api import async_playwright as ap
T=os.environ.get("BOT_TOKEN");A=os.environ.get("USER_TOKEN");D=os.environ.get("USER_DEVICE_ID");U=os.environ.get("USER_UDI");G=os.environ.get("GH_PAT");R=os.environ.get("GITHUB_REPOSITORY")
async def s(v):
    if not R or not G:return"E"
    u=f"https://api.github.com/repos/{R}/actions/variables/AUTO_UPDATE";h={"Authorization":f"Bearer {G}","Accept":"application/vnd.github+json"}
    async with httpx.AsyncClient() as c:
        q=await c.get(u,headers=h)
        if q.status_code==404:await c.post(f"https://api.github.com/repos/{R}/actions/variables",headers=h,json={"name":"AUTO_UPDATE","value":v})
        else:await c.patch(u,headers=h,json={"name":"AUTO_UPDATE","value":v})
    return"OK"
async def st(u:Update,c:C):await c.bot.send_message(u.effective_chat.id,"/on /off /check_odds [1-7]")
async def on(u:Update,c:C):r=await s("true");await c.bot.send_message(u.effective_chat.id,"ON"if r=="OK"else r)
async def off(u:Update,c:C):r=await s("false");await c.bot.send_message(u.effective_chat.id,"OFF"if r=="OK"else r)
async def co(u:Update,c:C):
    if not A or not U:return await c.bot.send_message(u.effective_chat.id,"Err")
    if os.environ.get("AUTO_UPDATE")=="false":return await c.bot.send_message(u.effective_chat.id,"Off")
    tg=int(c.args[0])if c.args and c.args[0].isdigit()else None
    await c.bot.send_message(u.effective_chat.id,"Wait...")
    try:
        async with ap() as p:
            b=await p.chromium.launch(headless=True);ct=await b.new_context()
            await ct.add_cookies([{'name':'auth_token','value':A,'domain':'.asbdavani.app','path':'/'},{'name':'device_id','value':D,'domain':'.asbdavani.app','path':'/'},{'name':'_udi','value':U,'domain':'.asbdavani.app','path':'/'}])
            pg=await ct.new_page();pg.set_default_timeout(30000)
            await pg.goto("https://mosharekat.asbdavani.app/",timeout=90000);await pg.wait_for_timeout(3000)
            if "ورود"in await pg.locator('body').inner_text():return await c.bot.send_message(u.effective_chat.id,"Login Err")
            await pg.evaluate("let a=document.querySelectorAll('span,a,div,button');let o=Array.from(a).find(e=>e.textContent.trim()==='آد اسب');if(o)o.click();");await pg.wait_for_timeout(2000)
            r7=await pg.locator("text='R 7'").count()>0 or await pg.locator("text='7'").count()>0;mr=7 if r7 else 6
            rp="";p3,p5,p6=[],[],[]
            for r in([tg]if tg else range(1,mr+1)):
                res=await ar(pg,r)
                if res:
                    rp+=f"R{r}:1.{res['w']['n']}({res['w']['w']})2.{res['p']['n']}({res['p']['p']})3.{res['s']['n']}({res['s']['s']})Q:{res['q']}P:{res['pr']['n']}S:{res['su']['n']}\n"
                    if not tg:
                        if r<=6:p6.append(res['pr']['n'])
                        if r<=5:p5.append(res['pr']['n'])
                        if r in[1,2,3]:p3.append(res['pr']['n'])
                else:rp+=f"R{r}:-\n"
            if not tg:
                if p6:rp+=f"P6:{','.join(p6[:6])}\n"
                if p5:rp+=f"P5:{','.join(p5[:5])}\n"
                if p3:rp+=f"P3:{','.join(p3[:3])}\n"
            await c.bot.send_message(u.effective_chat.id,rp,parse_mode='Markdown');await b.close()
    except Exception as e:await c.bot.send_message(u.effective_chat.id,f"E:{str(e)}")
app=AB().token(T).build();app.add_handler(CH('start',st));app.add_handler(CH('on',on));app.add_handler(CH('off',off));app.add_handler(CH('check_odds',co));app.run_polling(bootstrap_retries=-1)    
