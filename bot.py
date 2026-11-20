import os, re, httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder as AB, ContextTypes as C, CommandHandler as CH, CallbackQueryHandler
from playwright.async_api import async_playwright as ap

T = os.environ.get("BOT_TOKEN")
A = os.environ.get("USER_TOKEN")
D = os.environ.get("USER_DEVICE_ID")
U = os.environ.get("USER_UDI")
G = os.environ.get("GH_PAT")
R = os.environ.get("GITHUB_REPOSITORY")

async def s(v):
    if not R or not G: return "E"
    try:
        u = f"https://api.github.com/repos/{R}/actions/variables/AUTO_UPDATE"
        h = {"Authorization": f"Bearer {G}", "Accept": "application/vnd.github+json"}
        async with httpx.AsyncClient() as c:
            q = await c.get(u, headers=h)
            if q.status_code == 404: 
                await c.post(f"https://api.github.com/repos/{R}/actions/variables", headers=h, json={"name": "AUTO_UPDATE", "value": v})
            else: 
                await c.patch(u, headers=h, json={"name": "AUTO_UPDATE", "value": v})
        return "OK"
    except: 
        return "E"

async def g():
    if not R or not G: return "true"
    try:
        u = f"https://api.github.com/repos/{R}/actions/variables/AUTO_UPDATE"
        h = {"Authorization": f"Bearer {G}", "Accept": "application/vnd.github+json"}
        async with httpx.AsyncClient() as c:
            q = await c.get(u, headers=h)
            if q.status_code == 200: 
                return q.json().get("value", "true")
        return "true"
    except: 
        return "true"

async def st(u: Update, c: C):
    keyboard = [
        [InlineKeyboardButton("تحلیل کل (سریع)", callback_data="all_fast"), InlineKeyboardButton("تحلیل کل (عمیق)", callback_data="all_deep")],
        [InlineKeyboardButton("R1", callback_data="1"), InlineKeyboardButton("R2", callback_data="2"), InlineKeyboardButton("R3", callback_data="3")],
        [InlineKeyboardButton("R4", callback_data="4"), InlineKeyboardButton("R5", callback_data="5"), InlineKeyboardButton("R6", callback_data="6"), InlineKeyboardButton("R7", callback_data="7")]
    ]
    await u.message.reply_text('منوی ربات اسب‌دوانی (یک گزینه را لمس کنید):', reply_markup=InlineKeyboardMarkup(keyboard))

async def on(u: Update, c: C):
    r = await s("true")
    await c.bot.send_message(u.effective_chat.id, "ON" if r == "OK" else r)

async def off(u: Update, c: C):
    r = await s("false")
    await c.bot.send_message(u.effective_chat.id, "OFF" if r == "OK" else r)

async def ar(p, r, is_deep=False):
    try:
        await p.evaluate(f"let a=document.querySelectorAll('div,span,button,a');let t=Array.from(a).find(e=>e.textContent.trim()==='{r}'||e.textContent.trim()==='R {r}');if(t)t.click();")
        await p.wait_for_timeout(3000)
    except: 
        pass
    x = await p.locator('body').inner_text()
    h = []
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*\n\s*([^\n\d]+)', x):
        w, pl, sh = float(m.group(1)), float(m.group(2)), float(m.group(3))
        n = m.group(4).strip()
        if n and 0 < w < 90 and not any(k in n for k in ["مقام", "شماره", "نام", "پیش", "میان", "پی", "چابک"]):
            base_score = (1/w)*50 + (1/pl)*30 + (1/sh)*20
            hist_score = 50 
            final_score = base_score*0.7 + hist_score*0.3 if is_deep else base_score
            h.append({"n": n, "w": w, "p": pl, "s": sh, "sc": final_score, "prob": round((1/w)*100, 1)})
    if not h: 
        return
    for idx, item in enumerate(h): 
        item["num"] = idx + 1
    o = sorted(h, key=lambda y: y["sc"], reverse=True)
    b = o[0]
    p2 = min(h, key=lambda y: y["p"])
    s2 = min(h, key=lambda y: y["s"])
    q = f"{o[0]['num']}-{o[0]['n']} + {o[1]['num']}-{o[1]['n']}" if len(o) > 1 else "-"
    t = [y['n'] for y in o[:3]]
    m = [y for y in h if y['n'] not in t]
    su = max(m, key=lambda y: y["sc"]) if m else o[-1]
    return {"w": b, "p": p2, "s": s2, "q": q, "pr": b, "su": su}
    async def co(u: Update, c: C, tg=None, is_deep=False):
        if not A or not U: 
        return await c.bot.send_message(u.effective_chat.id, "Err Auth")
        if await g() == "false": 
        return await c.bot.send_message(u.effective_chat.id, "Bot is OFF")
    
    await c.bot.send_message(u.effective_chat.id, "⏳ در حال تحلیل...")
    try:
        async with ap() as p:
            b = await p.chromium.launch(headless=True)
            ct = await b.new_context()
            await ct.add_cookies([
                {'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}
            ])
            pg = await ct.new_page()
            pg.set_default_timeout(30000)
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000)
            await pg.wait_for_timeout(3000)
            if "ورود" in await pg.locator('body').inner_text(): 
                return await c.bot.send_message(u.effective_chat.id, "Login Err")
            
            await pg.evaluate("let a=document.querySelectorAll('span,a,div,button');let o=Array.from(a).find(e=>e.textContent.trim()==='آد اسب');if(o)o.click();")
            await pg.wait_for_timeout(2000)
            
            r7 = await pg.locator("text='R 7'").count() > 0 or await pg.locator("text='7'").count() > 0
            mr = 7 if r7 else 6
            rp = "📊 تحلیل " + ("عمیق" if is_deep else "سریع") + "\n"
            res_all = []
            
            for r in ([tg] if tg else range(1, mr+1)):
                res = await ar(pg, r, is_deep)
                if res:
                    res_all.append((r, res))
                    bet_advice = "💰 پیشنهاد: ۱۰٪ سرمایه" if res['w']['prob'] > 40 else "💰 پیشنهاد: شرط کم"
                    rp += f"\n🏁 دور R{r}\n"
                    rp += f"🥇 پیش‌بر: {res['w']['num']}-{res['w']['n']} (شانس: {res['w']['prob']}%)\n"
                    rp += f"🥈 میان‌بر: {res['p']['num']}-{res['p']['n']}\n"
                    rp += f"🥉 پی‌بر: {res['s']['num']}-{res['s']['n']}\n"
                    rp += f"👥 دوقلو: {res['q']}\n"
                    rp += f"🎲 شگفتی: {res['su']['num']}-{res['su']['n']}\n"
                    rp += f"{bet_advice}\n"
                else: 
                    rp += f"\n🏁 دور R{r}: اطلاعاتی یافت نشد.\n"
                    res_all.append((r, None))
            
            if not tg:
                rp += "\n➖➖➖➖➖➖➖➖\n"
                def get_p(rng):
                    names = []
                    for r_num, res in res_all:
                        if r_num in rng and res: 
                            names.append(f"{res['pr']['num']}-{res['pr']['n']}")
                    return ", ".join(names)
                
                if mr == 7:
                    rp += f"🎯 ۶ پره (R2-R7): {get_p(range(2,8))}\n"
                    rp += f"🎯 ۵ پره (R2-R6): {get_p(range(2,7))}\n"
                    rp += f"🎯 ۳ پره اول (R1-R3): {get_p(range(1,4))}\n"
                    rp += f"🎯 ۳ پره دوم (R5-R7): {get_p(range(5,8))}\n"
                else:
                    rp += f"🎯 ۶ پره (R1-R6): {get_p(range(1,7))}\n"
                    rp += f"🎯 ۵ پره (R2-R6): {get_p(range(2,7))}\n"
                    rp += f"🎯 ۳ پره اول (R1-R3): {get_p(range(1,4))}\n"
                    rp += f"🎯 ۳ پره دوم (R4-R6): {get_p(range(4,7))}\n"
            
            await c.bot.send_message(u.effective_chat.id, rp, parse_mode='Markdown')
            await b.close()
    except Exception as e: 
        await c.bot.send_message(u.effective_chat.id, f"E:{str(e)}")

async def cmd_co(u: Update, c: C):
    is_deep = bool(c.args and c.args[0] == "deep")
    tg = int(c.args[1]) if is_deep and len(c.args) > 1 else (int(c.args[0]) if not is_deep and c.args else None)
    await co(u, c, tg, is_deep)
    async def btn_handler(u: Update, c: C):
    q = u.callback_query
    await q.answer()
    data = q.data
    if data == "all_fast": 
        await co(q, c, None, False)
    elif data == "all_deep": 
        await co(q, c, None, True)
    else: 
        await co(q, c, int(data), False)

app = AB().token(T).build()
app.add_handler(CH('start', st))
app.add_handler(CH('menu', st))
app.add_handler(CH('on', on))
app.add_handler(CH('off', off))
app.add_handler(CH('check_odds', cmd_co))
app.add_handler(CallbackQueryHandler(btn_handler))
app.run_polling(bootstrap_retries=-1)
