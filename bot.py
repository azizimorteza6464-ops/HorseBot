import os,re,httpx
from telegram import Update
from telegram.ext import ApplicationBuilder as AB,ContextTypes as C,CommandHandler as CH
from playwright.async_api import async_playwright as ap
T=os.environ.get("BOT_TOKEN");A=os.environ.get("USER_TOKEN");D=os.environ.get("USER_DEVICE_ID");U=os.environ.get("USER_UDI");G=os.environ.get("GH_PAT");R=os.environ.get("GITHUB_REPOSITORY")
async def s(v):
 if not R or not G: return"E"
 try:
  u=f"https://api.github.com/repos/{R}/actions/variables/AUTO_UPDATE"
  h={"Authorization":f"Bearer {G}","Accept":"application/vnd.github+json"}
  async with httpx.AsyncClient() as c:
   q=await c.get(u,headers=h)
   if q.status_code==404: await c.post(f"https://api.github.com/repos/{R}/actions/variables",headers=h,json={"name":"AUTO_UPDATE","value":v})
   else: await c.patch(u,headers=h,json={"name":"AUTO_UPDATE","value":v})
  return"OK"
 except: return"E"
async def g():
 if not R or not G: return"true"
 try:
  u=f"https://api.github.com/repos/{R}/actions/variables/AUTO_UPDATE"
  h={"Authorization":f"Bearer {G}","Accept":"application/vnd.github+json"}
  async with httpx.AsyncClient() as c:
   q=await c.get(u,headers=h)
   if q.status_code==200: return q.json().get("value","true")
  return"true"
 except: return"true"
async def st(u:Update,c:C): await c.bot.send_message(u.effective_chat.id,"/on /off /check_odds [1-7]")
async def on(u:Update,c:C):
 r=await s("true")
 await c.bot.send_message(u.effective_chat.id,"ON"if r=="OK"else r)
async def off(u:Update,c:C):
 r=await s("false")
 await c.bot.send_message(u.effective_chat.id,"OFF"if r=="OK"else r)
async def ar(p,r):
 try:
  await p.evaluate(f"let a=document.querySelectorAll('div,span,button,a');let t=Array.from(a).find(e=>e.textContent.trim()==='{r}'||e.textContent.trim()==='R {r}');if(t)t.click();")
  await p.wait_for_timeout(3000)
 except: pass
 x=await p.locator('body').inner_text()
 h=[]
 for m in re.finditer(r'(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*\n\s*([^\n\d]+)',x):
  w,pl,sh=float(m.group(1)),float(m.group(2)),float(m.group(3))
  n=m.group(4).strip()
  if n and 0<w<90 and not any(k in n for k in["مقام","شماره","نام","پیش","میان","پی","چابک"]):
   score=(1/w)*50+(1/pl)*30+(1/sh)*20
   h.append({"n":n,"w":w,"p":pl,"s":sh,"sc":score})
 if not h: return
 for idx,item in enumerate(h): item["num"]=idx+1
 o=sorted(h,key=lambda y:y["sc"],reverse=True)
 b=o[0]
 p2=min(h,key=lambda y:y["p"])
 s2=min(h,key=lambda y:y["s"])
 q=f"{o[0]['num']}-{o[0]['n']} + {o[1]['num']}-{o[1]['n']}" if len(o)>1 else "-"
 t=[y['n'] for y in o[:3]]
 m=[y for y in h if y['n'] not in t]
 su=max(m,key=lambda y:y["sc"]) if m else o[-1]
 return{"w":b,"p":p2,"s":s2,"q":q,"pr":b,"su":su}
async def co(u:Update,c:C):
 if not A or not U:
  await c.bot.send_message(u.effective_chat.id,"Err Auth")
  return
 cst=await g()
 if cst=="false":
  await c.bot.send_message(u.effective_chat.id,"Bot is OFF")
  return
 tg=int(c.args[0]) if c.args and c.args[0].isdigit() else None
 await c.bot.send_message(u.effective_chat.id,"Wait...")
 try:
  async with ap() as p:
   b=await p.chromium.launch(headless=True)
   ct=await b.new_context()
   await ct.add_cookies([{'name':'auth_token','value':A,'domain':'.asbdavani.app','path':'/'},{'name':'device_id','value':D,'domain':'.asbdavani.app','path':'/'},{'name':'_udi','value':U,'domain':'.asbdavani.app','path':'/'}])
   pg=await ct.new_page()
   pg.set_default_timeout(30000)
   await pg.goto("https://mosharekat.asbdavani.app/",timeout=90000)
   await pg.wait_for_timeout(4000)
   if "ورود"in await pg.locator('body').inner_text():
    await c.bot.send_message(u.effective_chat.id,"Login Err")
    return
   await pg.evaluate("let a=document.querySelectorAll('span,a,div,button');let o=Array.from(a).find(e=>e.textContent.trim()==='آد اسب');if(o)o.click();")
      await pg.wait_for_timeout(4000)
   r7=await pg.locator("text='R 7'").count()>0 or await pg.locator("text='7'").count()>0
   mr=7 if r7 else 6
   rp="📊 تحلیل جامع مسابقات\n"
   res_all=[]
   for r in ([tg] if tg else range(1,mr+1)):
    res=await ar(pg,r)
    if res:
     res_all.append((r,res))
     rp+=f"\n🏁 دور R{r}\n"
     rp+=f"🥇 پیش‌بر: {res['w']['num']}-{res['w']['n']} (ضریب {res['w']['w']})\n"
     rp+=f"🥈 میان‌بر: {res['p']['num']}-{res['p']['n']} (ضریب {res['p']['p']})\n"
     rp+=f"🥉 پی‌بر: {res['s']['num']}-{res['s']['n']} (ضریب {res['s']['s']})\n"
     rp+=f"👥 دوقلو: {res['q']}\n"
     rp+=f"🎲 اسب شگفتی: {res['su']['num']}-{res['su']['n']}\n"
    else:
     rp+=f"\n🏁 دور R{r}: اطلاعاتی یافت نشد.\n"
     res_all.append((r,None))
   if not tg:
    rp+="\n➖➖➖➖➖➖➖➖\n"
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
    if p6: rp+=f"🎯 ۶ پره: {p6}\n"
    if p5: rp+=f"🎯 ۵ پره: {p5}\n"
    if p3_1: rp+=f"🎯 ۳ پره (اول): {p3_1}\n"
    if p3_2: rp+=f"🎯 ۳ پره (دوم): {p3_2}\n"
   await c.bot.send_message(u.effective_chat.id,rp,parse_mode='Markdown')
   await b.close()
 except Exception as e:
  await c.bot.send_message(u.effective_chat.id,f"E:{str(e)}")
app=AB().token(T).build()
app.add_handler(CH('start',st))
app.add_handler(CH('on',on))
app.add_handler(CH('off',off))
app.add_handler(CH('check_odds',co))
app.run_polling(bootstrap_retries=-1)
