import os, re, httpx, sys
from telegram import Update
from telegram.ext import ApplicationBuilder as AB, ContextTypes as C, CommandHandler as CH
from playwright.async_api import async_playwright as ap

# --- تنظیمات اولیه ---
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

async def st(u: Update, c: C): 
    await c.bot.send_message(u.effective_chat.id, "دستورات:\n/on روشن کردن ربات\n/off خاموش کردن ربات\n/check_odds [شماره دور]")

async def on(u: Update, c: C):
    r = await s("true")
    await c.bot.send_message(u.effective_chat.id, "✅ ربات روشن شد" if r == "OK" else r)

async def off(u: Update, c: C):
    r = await s("false")
    await c.bot.send_message(u.effective_chat.id, "🛑 ربات خاموش شد" if r == "OK" else r)

async def send_long_message(c, chat_id, text):
    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await c.bot.send_message(chat_id, text[i:i+4000], parse_mode='HTML')
    else:
        await c.bot.send_message(chat_id, text, parse_mode='HTML')

async def ar(p, r_num, c, chat_id):
    horses = {}
    table_text = ""
    try:
        try:
            await p.get_by_text("جدول مسابقات", exact=False).first.click(timeout=10000, force=True)
            await p.wait_for_timeout(3000)
        except: pass

        try:
            await p.get_by_text(f"R {r_num}", exact=True).first.click(timeout=5000, force=True)
        except:
            try:
                await p.get_by_text(str(r_num), exact=True).first.click(timeout=5000, force=True)
            except: return None 
        
        await p.wait_for_timeout(7000)
        table_text = await p.locator('body').inner_text()
        
        pattern = r'([^\n]+)\n[^\n]*\n(\d{1,2})\s+(\d{2,3}Kg)\n(\d{1,3})\n([\d\-]+)\n([^\n]+)'
        
        matches = re.finditer(pattern, table_text)
        for m in matches:
            name = m.group(1).strip()
            if any(k in name for k in ["مقام", "اسب", "سن", "عملکرد", "وزن", "مالک", "جایزه", "اسبدوانی", "پخش زنده", "مشارکت", "آد اسب"]): continue
            
            num = int(m.group(2))
            ior = int(m.group(4))
            perf_str = m.group(5)
            jockey = m.group(6).strip()
            
            perf_score = 0
            s_map = {'1': 25, '2': 15, '3': 5}
            for rank in perf_str.split('-'):
                perf_score += s_map.get(rank.strip(), 0)
                
            horses[num] = {
                "n": name, "num": num, "ior": ior, "j": jockey,
                "perf": perf_str, "perf_sc": perf_score, "w_odd": 50.0
            }

        try:
            await p.get_by_text("دورهای", exact=False).first.click(timeout=5000, force=True)
            await p.wait_for_timeout(3000)
            await p.get_by_text(str(r_num), exact=True).first.click(timeout=5000, force=True)
            await p.wait_for_timeout(4000)
            odds_text = await p.locator('body').inner_text()
            
            odds_pattern = r'([^\n]+)\n(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)'
            for m in re.finditer(odds_pattern, odds_text):
                name = m.group(1).strip()
                w_odd = float(m.group(2))
                for num, h in horses.items():
                    if name in h['n'] or h['n'] in name:
                        horses[num]['w_odd'] = w_odd
                        break
        except: pass

    except Exception as e:
        print(f"Error in round {r_num}: {e}")
        
    if not horses:
        return None
    
    good_jockeys = ["رحمانی", "قهرمانی", "نوروزی", "وحدانی", "مرادی"]
    for num, h in horses.items():
        market_score = 100 / (h['w_odd'] + 1) if h['w_odd'] > 0 else 0
        jockey_bonus = 20 if any(gj in h['j'] for gj in good_jockeys) else 0
        h['final_sc'] = (h['ior'] * 0.35) + (h['perf_sc'] * 0.30) + (market_score * 0.20) + (jockey_bonus * 0.15)

    sorted_horses = sorted(horses.values(), key=lambda y: y['final_sc'], reverse=True)
    
    # --- منطق انتخاب بدون تکرار ---
    w = sorted_horses[0]
    
    remaining_2 = [h for h in sorted_horses if h['num'] != w['num']]
    p_horse = remaining_2[0] if remaining_2 else w
    
    remaining_3 = [h for h in remaining_2 if h['num'] != p_horse['num']]
    s_horse = remaining_3[0] if remaining_3 else p_horse
    
    q = f"{w['num']}-{w['n']} + {p_horse['num']}-{p_horse['n']}"
    
    surprise_candidates = [h for h in horses.values() if h['w_odd'] >= 8.0 and h['ior'] > 0]
    surprise_candidates = sorted(surprise_candidates, key=lambda y: y['final_sc'], reverse=True)
    
    if surprise_candidates:
        su = surprise_candidates[0]
        remaining_su = [h for h in surprise_candidates if h['num'] != su['num']]
        su2 = remaining_su[0] if remaining_su else (sorted_horses[-1] if sorted_horses[-1]['num'] != su['num'] else sorted_horses[0])
    else:
        su = sorted_horses[-1]
        su2 = sorted_horses[-2] if len(sorted_horses) > 1 else sorted_horses[0]
        
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
    await c.bot.send_message(chat_id, "⏳ در حال تحلیل...")
    
    browser = None
    try:
        async with ap() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-blink-features=AutomationControlled'])
            
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            await context.add_cookies([
                {'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}
            ])
            pg = await context.new_page()
            pg.set_default_timeout(60000)
            
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000, wait_until="domcontentloaded")
            await pg.wait_for_timeout(10000)
            if "ورود" in await pg.locator('body').inner_text() and "شماره موبایل" in await pg.locator('body').inner_text():
                await c.bot.send_message(chat_id, "⚠️ خطای ورود: کوکی‌ها منقضی شده‌اند.")
                return
                
            rp = "📊 <b>تحلیل جامع مسابقات</b> 📊\n"
            res_all = []
            
            max_r = tg if tg else 4 
            for r in ([tg] if tg else range(1, max_r + 1)):
                res = await ar(pg, r, c, chat_id)
                
                if res:
                    res_all.append(res)
                    rp += f"\n🏁 <b>دور R{r}</b>\n"
                    rp += f"🥇 پیش‌بر: {res['w']['num']}-{res['w']['n']} (ضریب {res['w']['w_odd']})\n"
                    rp += f"🥈 میان‌بر: {res['p']['num']}-{res['p']['n']} (ضریب {res['p']['w_odd']})\n"
                    rp += f"🥉 پی‌بر: {res['s']['num']}-{res['s']['n']} (ضریب {res['s']['w_odd']})\n"
                    rp += f"👥 دوقلو: {res['q']}\n"
                    rp += f"🎲 اسب شگفتی: {res['su']['num']}-{res['su']['n']}\n"
                    rp += f"🔥 دوقلوی شگفتی: {res['qs']}\n"
                else:
                    res_all.append(None)
                    
            await send_long_message(c, chat_id, rp)
            
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطای غیرمنتظره: {str(e)}")
    finally:
        if browser: await browser.close()

if __name__ == '__main__':
    app = AB().token(T).build()
    app.add_handler(CH('start', st))
    app.add_handler(CH('on', on))
    app.add_handler(CH('off', off))
    app.add_handler(CH('check_odds', co))

    print("Bot is running...")
    app.run_polling(bootstrap_retries=-1, drop_pending_updates=True)
