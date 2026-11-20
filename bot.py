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
    try:
        print(f"--> Analyzing Round {r_num}")
        
        # 1. رفتن به تب جدول مسابقات
        try:
            await p.get_by_text("جدول مسابقات", exact=False).first.click(timeout=10000, force=True)
            await p.wait_for_timeout(3000)
        except: pass

        # 2. کلیک روی شماره دور
        try:
            await p.get_by_text(f"R {r_num}", exact=True).first.click(timeout=5000, force=True)
        except:
            try:
                await p.get_by_text(str(r_num), exact=True).first.click(timeout=5000, force=True)
            except:
                print(f"Round {r_num} button not found!")
                return None 
        
        # 3. صبر کردن تا جدول حتما لود شود
        await p.wait_for_timeout(6000)
        
        # 🔴 نکته کلیدی: به جای خواندن کل صفحه، فقط تگ TABLE را می‌خوانیم
        table_text = ""
        try:
            tables = await p.locator('table').all()
            for t in tables:
                txt = await t.inner_text()
                # اگر جدولی پیدا شد که شامل کلمات کلیدی اسب باشد، همان را برمی‌داریم
                if "اسب" in txt or "Kg" in txt or "چابک" in txt:
                    table_text += txt + "\n"
        except Exception as e:
            print(f"Table locator error: {e}")

        # اگر جدول پیدا نشد، برمی‌گردیم به خواندن کل صفحه
        if not table_text:
            table_text = await p.locator('body').inner_text()

        # 4. الگوی جدید و بسیار دقیق‌تر برای استخراج داده‌های اسب
        # این الگو نام، شماره، وزن، IOR، سابقه و چابک‌سوار را شکار می‌کند
        pattern = r'([^\n]+)\n[^\n]*\n(\d{1,2})\s+(\d{2,3}Kg)\n(\d{1,3})\n([\d\-]+)\n([^\n]+)'
        
        matches = re.finditer(pattern, table_text)
        for m in matches:
            name = m.group(1).strip()
            if any(k in name for k in ["مقام", "اسب", "سن", "عملکرد", "وزن", "مالک", "جایزه", "اسبدوانی", "پخش زنده"]): continue
            
            num = int(m.group(2))
            ior = int(m.group(4))
            perf_str = m.group(5)
            jockey = m.group(6).strip()
            
            # محاسبه امتیاز سابقه (بر اساس مقام‌های قبلی: 1=25، 2=15، 3=5)
            perf_score = 0
            s_map = {'1': 25, '2': 15, '3': 5}
            for rank in perf_str.split('-'):
                perf_score += s_map.get(rank.strip(), 0)
                
            horses[num] = {
                "n": name, "num": num, "ior": ior, "j": jockey,
                "perf": perf_str, "perf_sc": perf_score, "w_odd": 50.0
            }
        
        print(f"Found {len(horses)} horses for Round {r_num}")

        # 5. تلاش برای گرفتن ضرایب (Odds)
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
        try:
            screenshot_path = f"debug_{r_num}.png"
            await p.screenshot(path=screenshot_path, full_page=False) 
            page_snippet = table_text[:500] if 'table_text' in locals() else "متن خوانده نشد."
            with open(screenshot_path, "rb") as image_file:
                await c.bot.send_photo(chat_id=chat_id, photo=image_file, caption=f"⚠️ خطای خواندن دور {r_num}.\n\nمتن جدول:\n{page_snippet}")
            os.remove(screenshot_path)
        except Exception as img_e: print(f"Could not send screenshot: {img_e}")
        return None
    
    # 6. فرمول پیشرفته تحلیل و امتیازدهی
    good_jockeys = ["رحمانی", "قهرمانی", "نوروزی", "وحدانی", "مرادی"]
    for num, h in horses.items():
        # امتیاز بازار (هرچه ضریب کمتر، شانس بالاتر)
        market_score = 100 / (h['w_odd'] + 1) if h['w_odd'] > 0 else 0
        # امتیاز چابک‌سوار (اگر از چابک‌سواران مطرح باشد)
        jockey_bonus = 20 if any(gj in h['j'] for gj in good_jockeys) else 0
        
        # ترکیب نهایی (وزن‌دهی به IOR، سابقه، ضریب و چابک‌سوار)
        h['final_sc'] = (h['ior'] * 0.35) + (h['perf_sc'] * 0.30) + (market_score * 0.20) + (jockey_bonus * 0.15)

    # مرتب‌سازی بر اساس امتیاز نهایی
    sorted_horses = sorted(horses.values(), key=lambda y: y['final_sc'], reverse=True)
    
    w = sorted_horses[0] # انتخاب اول
    p_horse = sorted_horses[1] if len(sorted_horses) > 1 else w # انتخاب دوم
    s_horse = max(horses.values(), key=lambda y: y['perf_sc']) # بهترین سابقه
    q = f"{w['num']}-{w['n']} + {p_horse['num']}-{p_horse['n']}"
    
    # گزینه‌های شگفتی‌ساز (اسب‌هایی با ضریب بالا اما IOR خوب)
    surprise_candidates = [h for h in horses.values() if h['w_odd'] >= 8.0 and h['ior'] > 0]
    surprise_candidates = sorted(surprise_candidates, key=lambda y: y['final_sc'], reverse=True)
    su = surprise_candidates[0] if surprise_candidates else sorted_horses[-1]
    su2 = surprise_candidates[1] if len(surprise_candidates) >= 2 else (sorted_horses[-2] if len(sorted_horses) > 1 else w)
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
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
            context = await browser.new_context(viewport={'width': 1440, 'height': 900})
            await context.add_cookies([
                {'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}
            ])
            pg = await context.new_page()
            pg.set_default_timeout(60000)
            
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000, wait_until="domcontentloaded")
            await pg.wait_for_timeout(8000) 

            if "ورود" in await pg.locator('body').inner_text() and "شماره موبایل" in await pg.locator('body').inner_text():
                await c.bot.send_message(chat_id, "⚠️ خطای ورود: کوکی‌ها منقضی شده‌اند.")
                return
                
            rp = "📊 <b>تحلیل جامع مسابقات</b> 📊\n"
            res_all = []
            
            max_r = tg if tg else 1 # پیش‌فرض فقط دور 1 را چک کن
            for r in ([tg] if tg else range(1, max_r + 1)):
                res = await ar(pg, r, c, chat_id)
                
                if res:
                    res_all.append(res)
                    rp += f"\n🏁 <b>دور R{r}</b>\n"
                    rp += f"🥇 <b>انتخاب اول:</b> {res['w']['num']}-{res['w']['n']}\n"
                    rp += f"└ ⭐ IOR: {res['w']['ior']} | 👤 چابک: {res['w']['j']} | 📈 ضریب: {res['w']['w_odd']}\n"
                    rp += f"🥈 <b>انتخاب دوم:</b> {res['p']['num']}-{res['p']['n']}\n"
                    rp += f"└ ⭐ IOR: {res['p']['ior']} | 👤 چابک: {res['p']['j']} | 📈 ضریب: {res['p']['w_odd']}\n"
                    rp += f"🥉 <b>بهترین سابقه:</b> {res['s']['num']}-{res['s']['n']}\n"
                    rp += f"└ 📜 سابقه مقام‌ها: {res['s']['perf']}\n"
                    rp += f"👥 <b>ترکیب دوقلو:</b> {res['q']}\n"
                    rp += f"🎲 <b>شگفتی‌ساز:</b> {res['su']['num']}-{res['su']['n']}\n"
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
