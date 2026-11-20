import os, re, httpx
from telegram import Update
from telegram.ext import ApplicationBuilder as AB, ContextTypes as C, CommandHandler as CH
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
    except: return "E"

async def g():
    if not R or not G: return "true"
    try:
        u = f"https://api.github.com/repos/{R}/actions/variables/AUTO_UPDATE"
        h = {"Authorization": f"Bearer {G}", "Accept": "application/vnd.github+json"}
        async with httpx.AsyncClient() as c:
            q = await c.get(u, headers=h)
            if q.status_code == 200: return q.json().get("value", "true")
        return "true"
    except: return "true"

async def st(u: Update, c: C): await c.bot.send_message(u.effective_chat.id, "دستورات:\n/on روشن\n/off خاموش\n/check_odds [1-7]")
async def on(u: Update, c: C):
    r = await s("true")
    await c.bot.send_message(u.effective_chat.id, "✅ ربات روشن شد" if r == "OK" else r)
async def off(u: Update, c: C):
    r = await s("false")
    await c.bot.send_message(u.effective_chat.id, "🛑 ربات خاموش شد" if r == "OK" else r)

# تابع دیباگ برای ارسال متن خام
async def send_debug(c, chat_id, round_num, text):
    if not text: text = "متن خالی است."
    # فقط 500 کاراکتر اول را می فرستیم تا پیام طولانی نشود
    debug_msg = f"⚠️ خطای خواندن دور {round_num}\nمتن دریافتی از سایت:\n\n{text[:800]}..."
    await c.bot.send_message(chat_id, debug_msg)

async def ar(p, r_num, c, chat_id):
    horses = {}
    try:
        # 1. رفتن به تب "جدول مسابقات"
        await p.evaluate("let a=document.querySelectorAll('a,div,span,button');let o=Array.from(a).find(e=>e.textContent.includes('جدول مسا'));if(o)o.click();")
        await p.wait_for_timeout(4000)
        
        # 2. کلیک روی "نمایش دورها"
        try:
            await p.evaluate("let a=document.querySelectorAll('a,div,span,button');let o=Array.from(a).find(e=>e.textContent.includes('نمایش دورها'));if(o)o.click();")
            await p.wait_for_timeout(3000)
        except: pass
        
        # 3. کلیک روی دور مورد نظر (R1, R2, ...)
        clicked = await p.evaluate(f"""
            let elements = Array.from(document.querySelectorAll('div,span,button,a'));
            let btn = elements.find(e => e.textContent.trim() === 'R {r_num}' || e.textContent.trim() === '{r_num}');
            if(btn) {{ btn.click(); return true; }}
            return false;
        """)
        
        if not clicked: 
            await c.bot.send_message(chat_id, f"❌ دکمه دور {r_num} پیدا نشد.")
            return None
            
        # صبر بیشتر برای لود کامل جدول
        await p.wait_for_timeout(6000) 
        
        x = await p.locator('body').inner_text()
        
        # الگوی خواندن اطلاعات
        pattern = r'([^\n]+)\n[^\n]+\n(\d{1,2})\s+(\d{2,3}Kg)\n(\d{1,3})\n([\d\-]+)\n([^\n]+)'
        
        found = False
        for m in re.finditer(pattern, x):
            name = m.group(1).strip()
            if any(k in name for k in ["مقام", "اسب", "سن", "عملکرد", "وزن", "مالک", "جایزه", "دور"]): continue
                found = True
            num = int(m.group(2))
            ior = int(m.group(4))
            perf_str = m.group(5)
            jockey = m.group(6).strip()
            
            perf_score = 0
            s_map = {'1': 25, '2': 15, '3': 5}
            for rank in perf_str.split('-'):
                perf_score += s_map.get(rank, 0)
                
            horses[num] = {
                "n": name, "num": num, "ior": ior, "j": jockey,
                "perf": perf_str, "perf_sc": perf_score, "w_odd": 50.0
            }

        if not found:
            # اگر الگو پیدا نشد، متن خام را برای دیباگ می فرستیم
            await send_debug(c, chat_id, r_num, x)
            return None

        # 4. رفتن به تب "دورهای" برای خواندن ضرایب
        await p.evaluate("let a=document.querySelectorAll('a,div,span,button');let o=Array.from(a).find(e=>e.textContent.includes('دورهای'));if(o)o.click();")
        await p.wait_for_timeout(3000)
        
        clicked_odds = await p.evaluate(f"""
            let elements = Array.from(document.querySelectorAll('div,span,button,a'));
            let btn = elements.find(e => e.textContent.trim() === 'R {r_num}' || e.textContent.trim() === '{r_num}');
            if(btn) {{ btn.click(); return true; }}
            return false;
        """)
        
        if clicked_odds:
            await p.wait_for_timeout(4000)
            x_odds = await p.locator('body').inner_text()
            odds_pattern = r'([^\n]+)\n(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)'
            for m in re.finditer(odds_pattern, x_odds):
                name = m.group(1).strip()
                w_odd = float(m.group(2))
                for num, h in horses.items():
                    if name in h['n'] or h['n'] in name:
                        horses[num]['w_odd'] = w_odd
                        break

    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطای داخلی در دور {r_num}: {str(e)}")
        
    if not horses: return None
    
    good_jockeys = ["رحمانی", "قهرمانی", "نوروزی", "وحدانی", "مرادی"]
    for num, h in horses.items():
        market_score = 100 / (h['w_odd'] + 1) if h['w_odd'] > 0 else 0
        jockey_bonus = 15 if any(gj in h['j'] for gj in good_jockeys) else 0
        h['final_sc'] = (h['ior'] * 0.4) + (h['perf_sc'] * 0.25) + (market_score * 0.2) + (jockey_bonus * 0.15)

    sorted_horses = sorted(horses.values(), key=lambda y: y['final_sc'], reverse=True)
    
    w = sorted_horses[0]
    p_horse = sorted_horses[1] if len(sorted_horses) > 1 else w
    s_horse = max(horses.values(), key=lambda y: y['perf_sc'])
    
    q = f"{w['num']}-{w['n']} + {p_horse['num']}-{p_horse['n']}"
    
    surprise_candidates = [h for h in horses.values() if h['w_odd'] >= 8.0 and h['ior'] > 0]
    surprise_candidates = sorted(surprise_candidates, key=lambda y: y['final_sc'], reverse=True)

    su = surprise_candidates[0] if surprise_candidates else sorted_horses[-1]

    if len(surprise_candidates) >= 2:
        su2 = surprise_candidates[1]
    else:
        others = [h for h in sorted_horses if h['num'] != w['num'] and h['num'] != p_horse['num']]
        su2 = others[0] if others else w

    qs = f"{su['num']}-{su['n']} + {su2['num']}-{su2['n']}"

    return {"w": w, "p": p_horse, "s": s_horse, "q": q, "qs": qs, "su": su}

async def co(u: Update, c: C):
    chat_id = u.effective_chat.id
    if not A or not U:
        await c.bot.send_message(chat_id, "⚠️ خطا: توکن‌های ورود یافت نشد.")
        return
    
    cst = await g()
    if cst == "false":
        await c.bot.send_message(chat_id, "🛑 ربات خاموش است. /on را بزنید.")
        return
        
    tg = int(c.args[0]) if c.args and c.args[0].isdigit() else None
    await c.bot.send_message(chat_id, "⏳ در حال تحلیل فنی و بررسی ضرایب...")
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
            pg.set_default_timeout(60000)
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000)
            await pg.wait_for_timeout(6000)
            
            if "ورود" in await pg.locator('body').inner_text():
                await c.bot.send_message(chat_id, "⚠️ خطای ورود: کوکی‌ها منقضی شده‌اند.")
                return
                
            rp = "📊 *تحلیل حرفه‌ای (فنی + ضرایب)* 📊\n"
            res_all = []
            
            max_r = tg if tg else 7
            for r in ([tg] if tg else range(1, max_r + 1)):
                # ارسال پیام وضعیت برای اینکه بدانید کدام دور گیر کرده
                await c.bot.send_message(chat_id, f"🔎 در حال بررسی دور R{r}...")
                res = await ar(pg, r, c, chat_id)
                
                if res:
                    res_all.append(res)
                    rp += f"\n🏁 *دور R{r}*\n"
                    rp += f"🥇 پیش‌بر: {res['w']['num']}-{res['w']['n']} (ضریب {res['w']['w_odd']})\n"
                    rp += f"└ ⭐ IOR: {res['w']['ior']} | 👤 چابک: {res['w']['j']}\n"
                    rp += f"🥈 میان‌بر: {res['p']['num']}-{res['p']['n']} (ضریب {res['p']['w_odd']})\n"
                    rp += f"🥉 پی‌بر: {res['s']['num']}-{res['s']['n']} (سابقه: {res['s']['perf']})\n"
                    rp += f"👥 دوقلو: {res['q']}\n"
                    rp += f"🎲 شگفتی: {res['su']['num']}-{res['su']['n']}\n"
                    rp += f"🔥 دوقلو شگفتی: {res['qs']}\n"
                else:
                    if tg:
                        rp += f"\n🏁 دور R{r}: اطلاعاتی یافت نشد.\n"
                    res_all.append(None)
                    
            if not tg and len(res_all) > 0:
                valid_rounds = [i+1 for i, res in enumerate(res_all) if res is not None]
                mr = max(valid_rounds) if valid_rounds else 6 
                
                rp += "\n➖➖➖➖➖➖➖➖\n"
                rp += f"🎯 *ترکیب‌های پیشنهادی (مسابقه {mr} دوره)* 🎯\n"
                
                def get_pick(round_list):
                    names = []
                    for r_num in round_list:
                        if r_num <= len(res_all) and res_all[r_num-1]:
                            res = res_all[r_num-1]
                            names.append(f"{res['w']['num']}-{res['w']['n']}")
                    return " + ".join(names) if names else "نامشخص"
                
                if mr >= 7:
                    rp += f"🔹 ۶ پره (دور ۲ تا ۷): {get_pick([2,3,4,5,6,7])}\n"
                    rp += f"🔹 ۵ پره (دور ۳ تا ۷): {get_pick([3,4,5,6,7])}\n"
                    rp += f"🔹 ۳ پره اول (دور ۱ تا ۳): {get_pick([1,2,3])}\n"
                    rp += f"🔹 ۳ پره دوم (دور ۵ تا ۷): {get_pick([5,6,7])}\n"
                else:
                    rp += f"🔹 ۶ پره (دور ۱ تا ۶): {get_pick([1,2,3,4,5,6])}\n"
                    rp += f"🔹 ۵ پره (دور ۲ تا ۶): {get_pick([2,3,4,5,6])}\n"
                    rp += f"🔹 ۳ پره اول (دور ۱ تا ۳): {get_pick([1,2,3])}\n"
                    rp += f"🔹 ۳ پره دوم (دور ۴ تا ۶): {get_pick([4,5,6])}\n"
                
            await c.bot.send_message(chat_id, rp, parse_mode='HTML')
            await b.close()
            
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطای غیرمنتظره: {str(e)}")

app = AB().token(T).build()
app.add_handler(CH('start', st))
app.add_handler(CH('on', on))
app.add_handler(CH('off', off))
app.add_handler(CH('check_odds', co))

app.run_polling(bootstrap_retries=-1, drop_pending_updates=True)
