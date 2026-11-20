import os,re,httpx
from telegram import Update
from telegram.ext import ApplicationBuilder as AB,ContextTypes as C,CommandHandler as CH
from playwright.async_api import async_playwright as ap

T=os.environ.get("BOT_TOKEN")
A=os.environ.get("USER_TOKEN")
D=os.environ.get("USER_DEVICE_ID")
U=os.environ.get("USER_UDI")
G=os.environ.get("GH_PAT")
R=os.environ.get("GITHUB_REPOSITORY")

async def s(v):
    if not R or not G: return "E"
    try:
        u=f"https://api.github.com/repos/{R}/actions/variables/AUTO_UPDATE"
        h={"Authorization":f"Bearer {G}","Accept":"application/vnd.github+json"}
        async with httpx.AsyncClient() as c:
            q=await c.get(u,headers=h)
            if q.status_code==404: await c.post(f"https://api.github.com/repos/{R}/actions/variables",headers=h,json={"name":"AUTO_UPDATE","value":v})
            else: await c.patch(u,headers=h,json={"name":"AUTO_UPDATE","value":v})
        return "OK"
    except: return "E"

async def g():
    if not R or not G: return "true"
    try:
        u=f"https://api.github.com/repos/{R}/actions/variables/AUTO_UPDATE"
        h={"Authorization":f"Bearer {G}","Accept":"application/vnd.github+json"}
        async with httpx.AsyncClient() as c:
            q=await c.get(u,headers=h)
            if q.status_code==200: return q.json().get("value","true")
        return "true"
    except: return "true"

async def st(u:Update,c:C): await c.bot.send_message(u.effective_chat.id,"Commands:\n/on \n/off \n/check_odds [1-7]")

async def on(u:Update,c:C):
    r=await s("true")
    await c.bot.send_message(u.effective_chat.id,"Robot ON" if r=="OK" else r)

async def off(u:Update,c:C):
    r=await s("false")
    await c.bot.send_message(u.effective_chat.id,"Robot OFF" if r=="OK" else r)

async def ar(p,r):
    try:
        await p.evaluate(f"let a=document.querySelectorAll('div,span,button,a');let t=Array.from(a).find(e=>e.textContent.trim()==='{r}'||e.textContent.trim()==='R {r}');if(t)t.click();")
        await p.wait_for_timeout(3000)
    except: pass
    
    x=await p.locator('body').inner_text()
    horses=[]
    
    for m in re.finditer(r'(?:^|\n)\s*(?:(\d+)[\s\-\.]*)?([^\n\d]+?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)', x):
        num_str = m.group(1)
        name = m.group(2).strip()
        w = float(m.group(3))
        pl = float(m.group(4))
        sh = float(m.group(5))
        
        if name and 0 < w < 90 and not any(k in name for k in ["مقام","شماره","نام","پیش","میان","پی","چابک"]):
            score = (1/w)*50 + (1/pl)*30 + (1/sh)*20
            actual_num = int(num_str) if num_str else None
            horses.append({"n": name, "num": actual_num, "w": w, "p": pl, "s": sh, "sc": score})
            
    if not horses: return
    
    for idx, item in enumerate(horses):
        if item["num"] is None:
            item["num"] = idx + 1

    o = sorted(horses, key=lambda y: y["sc"], reverse=True)
    b = o[0]
    p2 = min(horses, key=lambda y: y["p"])
    s2 = min(horses, key=lambda y: y["s"])
    q = f"{o[0]['num']}-{o[0]['n']} + {o[1]['num']}-{o[1]['n']}" if len(o) > 1 else "-"
    t_names = [y['n'] for y in o[:3]]
    others = [y for y in horses if y['n'] not in t_names]
    su = max(others, key=lambda y: y["sc"]) if others else o[-1]
    
    return {"w": b, "p": p2, "s": s2, "q": q, "pr": b, "su": su}

async def co(u:Update,c:C):
    if not A or not U:
        await c.bot.send_message(u.effective_chat.id,"Err Auth: Check Secrets.")
        return
    
    cst=await g()
    if cst=="false":
        await c.bot.send_message(u.effective_chat.id,"Robot is OFF.")
        return
        
    tg=int(c.args[0]) if c.args and c.args[0].isdigit() else None
    await c.bot.send_message(u.effective_chat.id,"Please wait...")
    
    try:
        async with ap() as p:
            b=await p.chromium.launch(headless=True)
            ct=await b.new_context()
            await ct.add_cookies([
                {'name':'auth_token','value':A,'domain':'.asbdavani.app','path':'/'},
                {'name':'device_id','value':D,'domain':'.asbdavani.app','path':'/'},
                {'name':'_udi','value':U,'domain':'.asbdavani.app','path':'/'}
            ])
            pg=await ct.new_page()
            pg.set_default_timeout(30000)
            await pg.goto("https://mosharekat.asbdavani.app/",timeout=90000)
            await pg.wait_for_timeout(4000)
            
            if "ورود" in await pg.locator('body').inner_text():
                await c.bot.send_message(u.effective_chat.id,"Login Err: Update Cookies.")
                return
                
            await pg.evaluate("let a=document.querySelectorAll('span,a,div,button');let o=Array.from(a).find(e=>e.textContent.trim()==='آد اسب');if(o)o.click();")
            await pg.wait_for_timeout(4000)
            
            r7=await pg.locator("text='R 7'").count()>0 or await pg.locator("text='7'").count()>0
            mr=7 if r7 else 6
            rp="Analysis:\n"
            res_all=[]
            
            for r in ([tg] if tg else range(1,mr+1)):
                res=await ar(pg,r)
                if res:
                    res_all.append((r,res))
                    rp+=f"\nR{r}\n"
                    rp+=f"Win: {res['w']['num']}-{res['w']['n']} ({res['w']['w']})\n"
                    rp+=f"Place: {res['p']['num']}-{res['p']['n']} ({res['p']['p']})\n"
                    rp+=f"Show: {res['s']['num']}-{res['s']['n']} ({res['s']['s']})\n"
                    rp+=f"Quin: {res['q']}\n"
                    rp+=f"Surp: {res['su']['num']}-{res['su']['n']}\n"
                else:
                    rp+=f"\nR{r}: No data.\n"
                    res_all.append((r,None))
                    
            if not tg:
                rp+="\n----------\n"
                def get_p(list_r):
                    names=[]
                    for r_num,res in list_r:
                        if r_num in range_num and res:
                            names.append(f"{res['pr']['num']}-{res['pr']['n']}")
                    return ", ".join(names)
                    
                if mr==7:
                    range_num=range(2,8); p6=get_p(res_all)
                    range_num=range(2,7); p5=get_p(res_all)
                    range_num=range(1,4); p3_1=get_p(res_all)
                    range_num=range(5,8); p3_2=get_p(res_all)
                else:
                    range_num=range(1,7); p6=get_p(res_all)
                    range_num=range(2,7); p5=get_p(res_all)
                    range_num=range(1,4); p3_1=get_p(res_all)
                    range_num=range(4,7); p3_2=get_p(res_all)
                    
                if p6: rp+=f"6: {p6}\n"
                if p5: rp+=f"5: {p5}\n"
                if p3_1: rp+=f"3(1): {p3_1}\n"
                if p3_2: rp+=f"3(2): {p3_2}\n"
                
            await c.bot.send_message(u.effective_chat.id,rp)
            await b.close()
            
    except Exception as e:
        await c.bot.send_message(u.effective_chat.id,f"E: {str(e)}")

app=AB().token(T).build()
app.add_handler(CH('start',st))
app.add_handler(CH('on',on))
app.add_handler(CH('off',off))
app.add_handler(CH('check_odds',co))

app.run_polling(bootstrap_retries=-1, drop_pending_updates=True)
