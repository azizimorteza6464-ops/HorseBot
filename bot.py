import os, re, httpx, sys
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder as AB, ContextTypes as C, CommandHandler as CH
from playwright.async_api import async_playwright as ap

# --- تنظیمات اولیه ---
T = os.environ.get("BOT_TOKEN")
A = os.environ.get("USER_TOKEN")
D = os.environ.get("USER_DEVICE_ID")
U = os.environ.get("USER_UDI")
G = os.environ.get("GH_PAT")
R = os.environ.get("GITHUB_REPOSITORY")

# --- توابع ارتباط با API گیت‌هاب ---
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

# --- دستورات ربات ---
async def st(u: Update, c: C): 
    await c.bot.send_message(u.effective_chat.id, "دستورات:\n/on روشن کردن ربات\n/off خاموش کردن ربات\n/check_odds [شماره دور 1-7]")

async def on(u: Update, c: C):
    r = await s("true")
    await c.bot.send_message(u.effective_chat.id, "✅ ربات روشن شد" if r == "OK" else r)

async def off(u: Update, c: C):
    r = await s("false")
    await c.bot.send_message(u.effective_chat.id, "🛑 ربات خاموش شد" if r == "OK" else r)

# --- تابع ارسال پیام طولانی ---
async def send_long_message(c, chat_id, text):
    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await c.bot.send_message(chat_id, text[i:i+4000], parse_mode='HTML')
    else:
        await c.bot.send_message(chat_id, text, parse_mode='HTML')

# --- تابع استخراج داده‌های یک دور خاص ---
async def ar(p, r_num, c, chat_id):
    horses = {}
    x = "" # مقداردهی اولیه
    try:
        print(f"--> Starting scraping for Round {r_num}")
        
        try:
            await p.get_by_text("جدول مسابقات", exact=False).first.click(timeout=10000, force=True)
            await p.wait_for_timeout(3000)
        except Exception as e: 
            print(f"Could not click 'جدول مسابقات': {e}")

        try:
            await p.get_by_text("نمایش دورها", exact=False).first.click(timeout=10000, force=True)
            await p.wait_for_timeout(3000)
        except Exception as e: 
            print(f"Could not click 'نمایش دورها': {e}")

        try:
            await p.get_by_text(f"R {r_num}", exact=True).first.click(timeout=5000, force=True)
            print(f"Clicked R {r_num}")
        except:
            try:
                await p.get_by_text(str(r_num), exact=True).first.click(timeout=5000, force=True)
                print(f"Clicked {r_num}")
            except:
                print(f"Round {r_num} button not found!")
                return None 
        
        await p.wait_for_timeout(8000)
        x = await p.locator('body').inner_text()
        
        pattern = r'([^\n]+)\n[^\n]+\n(\d{1,2})\s+(\d{2,3}Kg)\n(\d{1,3})\n([\d\-]+)\n([^\n]+)'
        
        matches = re.finditer(pattern, x)
        for m in matches:
            name = m.group(1).strip()
            if any(k in name for k in ["مقام", "اسب", "سن", "عملکرد", "وزن", "مالک", "جایزه"]): continue
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
        
        print(f"Found {len(horses)} horses for Round {r_num}")

        try:
            await p.get_by_text("دورهای", exact=False).first.click(timeout=5000, force=True)
            await p.wait_for_timeout(3000)
            await p.get_by_text(str(r_num), exact=True).first.click(timeout=5000, force=True)
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
        except: pass

    except Exception as e:
        print(f"Error in round {r_num}: {e}")
        
    if not horses:
        try:
            screenshot_path = f"debug_{r_num}.png"
            await p.screenshot(path=screenshot_path, full_page=False) 
            page_snippet = x[:500] if x else "متن صفحه خوانده نشد."
            
            with open(screenshot_path, "rb") as image_file:
                await c.bot.send_photo(
                    chat_id=chat_id, 
                    photo=image_file, 
                    caption=f"⚠️ خطای خواندن دور {r_num}.\n\nمتن صفحه:\n{page_snippet}"
                )
            os.remove(screenshot_path)
        except Exception as img_e:
            print(f"Could not send screenshot: {img_e}")
        return None
    
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
    su2 = surprise_candidates[1] if len(surprise_candidates) >= 2 else (sorted_horses[-2] if len(sorted_horses) > 1 else w)
    qs = f"{su['num']}-{su['n']} + {su2['num']}-{su2['n']}"

    return {"w": w, "p": p_horse, "s": s_horse, "q": q, "qs": qs, "su": su}

# --- دستور اصلی چک کردن ضرایب ---
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
    await c.bot.send_message(chat_id, "⏳ در حال تحلیل (حالت دیباگ)...")
            browser = None
    try:
        async with ap() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
            )
            context = await browser.new_context(
                viewport={'width': 1440, 'height': 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            await context.add_cookies([
                {'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}
            ])
            pg = await context.new_page()
            pg.set_default_timeout(90000)
            
            print("Navigating to website...")
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000, wait_until="domcontentloaded")
            await pg.wait_for_timeout(10000) 

            print("Checking for login status...")
            page_text = await pg.locator('body').inner_text()
            if "ورود" in page_text and "شماره موبایل" in page_text:
                await c.bot.send_message(chat_id, "⚠️ خطای ورود: کوکی‌ها منقضی شده‌اند. لطفا توکن‌های جدید را در سکرت‌های گیت‌هاب جایگزین کنید.")
                return
                
            rp = "📊 <b>تحلیل حرفه‌ای</b> 📊\n"
            res_all = []
            
            max_r = tg if tg else 7
            for r in ([tg] if tg else range(1, max_r + 1)):
                res = await ar(pg, r, c, chat_id)
                
                if res:
                    res_all.append(res)
                    rp += f"\n🏁 <b>دور R{r}</b>\n"
                    rp += f"🥇 پیش‌بر: {res['w']['num']}-{res['w']['n']} (ضریب {res['w']['w_odd']})\n"
                    rp += f"└ ⭐ IOR: {res['w']['ior']} | 👤 چابک: {res['w']['j']}\n"
                    rp += f"🥈 میان‌بر: {res['p']['num']}-{res['p']['n']} (ضریب {res['p']['w_odd']})\n"
                    rp += f"🥉 پی‌بر: {res['s']['num']}-{res['s']['n']} (سابقه: {res['s']['perf']})\n"
                    rp += f"👥 دوقلو: {res['q']}\n"
                    rp += f"🎲 شگفتی: {res['su']['num']}-{res['su']['n']}\n"
                    rp += f"🔥 دوقلو شگفتی: {res['qs']}\n"
                else:
                    res_all.append(None)
                    
            if not tg and len(res_all) > 0:
                valid_rounds = [i+1 for i, res in enumerate(res_all) if res is not None]
                mr = max(valid_rounds) if valid_rounds else 6 
                
                rp += "\n➖➖➖➖➖➖➖➖\n"
                rp += f"🎯 <b>ترکیب‌های پیشنهادی (مسابقه {mr} دوره)</b> 🎯\n"
                
                def get_pick(round_list):
                    names = []
                    for r_num in round_list:
                        if r_num <= len(res_all) and res_all[r_num-1]:
                            res = res_all[r_num-1]
                            names.append(f"{res['w']['num']}-{res['w']['n']}")
                        else:
                            names.append("نامشخص")
                    return " + ".join(names)
                
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
                
            await send_long_message(c, chat_id, rp)
            
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        await c.bot.send_message(chat_id, f"❌ خطای غیرمنتظره: {str(e)}")
    finally:
        if browser:
            await browser.close()
            print("Browser closed.")

if name == 'main':
    app = AB().token(T).build()
    app.add_handler(CH('start', st))
    app.add_handler(CH('on', on))
    app.add_handler(CH('off', off))
    app.add_handler(CH('check_odds', co))

    print("Bot is running...")
    app.run_polling(bootstrap_retries=-1, drop_pending_updates=True)
