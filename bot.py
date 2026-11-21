import os, re, httpx, sys, asyncio
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

# 🔴 آیدی عددی شما (مدیر اصلی ربات)
MY_ID = "184149177" 

# --- توابع ارتباط با گیت‌هاب (برای ذخیره لیست کاربران) ---
async def get_github_var(var_name, default_val=""):
    if not R or not G: return default_val
    try:
        url = f"https://api.github.com/repos/{R}/actions/variables/{var_name}"
        headers = {"Authorization": f"Bearer {G}", "Accept": "application/vnd.github+json"}
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                return res.json().get("value", default_val)
        return default_val
    except:
        return default_val

async def set_github_var(var_name, val):
    if not R or not G: return False
    try:
        url = f"https://api.github.com/repos/{R}/actions/variables/{var_name}"
        headers = {"Authorization": f"Bearer {G}", "Accept": "application/vnd.github+json"}
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 404:
                await client.post(f"https://api.github.com/repos/{R}/actions/variables", headers=headers, json={"name": var_name, "value": val})
            else:
                await client.patch(url, headers=headers, json={"name": var_name, "value": val})
        return True
    except:
        return False

# --- توابع مدیریت دسترسی ---
async def get_allowed_users():
    # لیست کاربران را از گیت‌هاب می‌خواند. اگر خالی بود، فقط آیدی شما را برمی‌گرداند.
    users_str = await get_github_var("ALLOWED_USERS", MY_ID)
    return [u.strip() for u in users_str.split(",") if u.strip()]

async def is_authorized(u: Update, c: C) -> bool:
    if str(u.effective_chat.id) == MY_ID:
        return True # مدیر همیشه دسترسی دارد
    
    allowed = await get_allowed_users()
    if str(u.effective_chat.id) not in allowed:
        await c.bot.send_message(u.effective_chat.id, "⛔ شما اجازه استفاده از این ربات را ندارید.")
        return False
    return True

# --- دستورات ربات ---
async def st(u: Update, c: C): 
    if not await is_authorized(u, c): return
    msg = "دستورات کاربری:\n/on روشن\n/off خاموش\n/check_odds [دور]\n\n"
    if str(u.effective_chat.id) == MY_ID:
        msg += "👑 دستورات مدیریت:\n/allow [آیدی]\n/deny [آیدی]\n/list"
    await c.bot.send_message(u.effective_chat.id, msg)

async def allow_user(u: Update, c: C):
    if str(u.effective_chat.id) != MY_ID:
        await c.bot.send_message(u.effective_chat.id, "⛔ این دستور فقط برای مدیر است.")
        return
    
    if not c.args:
        await c.bot.send_message(u.effective_chat.id, "لطفاً آیدی عددی را وارد کنید. مثال:\n/allow 12345678")
        return
    
    new_id = c.args[0]
    allowed = await get_allowed_users()
    
    if new_id in allowed:
        await c.bot.send_message(u.effective_chat.id, "این شخص از قبل دسترسی دارد.")
        return
        
    allowed.append(new_id)
    success = await set_github_var("ALLOWED_USERS", ",".join(allowed))
    
    if success:
        await c.bot.send_message(u.effective_chat.id, f"✅ دسترسی به آیدی {new_id} داده شد.")
    else:
        await c.bot.send_message(u.effective_chat.id, "❌ خطا در ذخیره در گیت‌هاب.")

async def deny_user(u: Update, c: C):
    if str(u.effective_chat.id) != MY_ID:
        await c.bot.send_message(u.effective_chat.id, "⛔ این دستور فقط برای مدیر است.")
        return
    
    if not c.args:
        await c.bot.send_message(u.effective_chat.id, "لطفاً آیدی عددی را وارد کنید. مثال:\n/deny 12345678")
        return
    
    del_id = c.args[0]
    allowed = await get_allowed_users()
    
    if del_id not in allowed:
        await c.bot.send_message(u.effective_chat.id, "این شخص در لیست نیست.")
        return
        
    allowed.remove(del_id)
    success = await set_github_var("ALLOWED_USERS", ",".join(allowed))
    
    if success:
        await c.bot.send_message(u.effective_chat.id, f"⛔ دسترسی آیدی {del_id} قطع شد.")
    else:
        await c.bot.send_message(u.effective_chat.id, "❌ خطا در ذخیره در گیت‌هاب.")

async def list_users(u: Update, c: C):
    if str(u.effective_chat.id) != MY_ID: return
    allowed = await get_allowed_users()
    txt = "👥 لیست افراد مجاز:\n"
    for i, a in enumerate(allowed):
        txt += f"{i+1}. `{a}`\n"
    await c.bot.send_message(u.effective_chat.id, txt, parse_mode='Markdown')

async def on(u: Update, c: C):
    if not await is_authorized(u, c): return
    r = await set_github_var("AUTO_UPDATE", "true")
    await c.bot.send_message(u.effective_chat.id, "✅ ربات روشن شد" if r else "❌ خطا")

async def off(u: Update, c: C):
    if not await is_authorized(u, c): return
    r = await set_github_var("AUTO_UPDATE", "false")
    await c.bot.send_message(u.effective_chat.id, "🛑 ربات خاموش شد" if r else "❌ خطا")

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
            await p.get_by_text("جدول مسابقات", exact=False).first.click(timeout=5000, force=True)
            await p.wait_for_timeout(3000)
        except: pass

        try:
            await p.get_by_text("نمایش دورها", exact=False).first.click(timeout=5000, force=True)
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
            if any(k in name for k in ["مقام", "اسب", "سن", "عملکرد", "وزن", "مالک", "جایزه", "اسبدوانی", "پخش زنده"]): continue
            
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

    except Exception as e:
        print(f"Error in round {r_num}: {e}")
        
    if not horses:
        try:
            screenshot_path = f"debug_{r_num}.png"
            await p.screenshot(path=screenshot_path, full_page=False)
            with open(screenshot_path, "rb") as image_file:
                await c.bot.send_photo(chat_id=chat_id, photo=image_file, caption=f"⚠️ خطا در خواندن جدول دور {r_num}.")
            os.remove(screenshot_path)
        except: pass
        return None
    
    good_jockeys = ["رحمانی", "قهرمانی", "نوروزی", "وحدانی", "مرادی"]
    for num, h in horses.items():
        market_score = 100 / (h['w_odd'] + 1) if h['w_odd'] > 0 else 0
        jockey_bonus = 20 if any(gj in h['j'] for gj in good_jockeys) else 0
        h['final_sc'] = (h['ior'] * 0.35) + (h['perf_sc'] * 0.30) + (market_score * 0.20) + (jockey_bonus * 0.15)

    sorted_horses = sorted(horses.values(), key=lambda y: y['final_sc'], reverse=True)
    
    w = sorted_horses[0]
    p_horse = sorted_horses[1] if len(sorted_horses) > 1 else w
    s_horse = sorted_horses[2] if len(sorted_horses) > 2 else p_horse
    
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
    if not await is_authorized(u, c): return
    
    chat_id = u.effective_chat.id
    if not A or not U:
        await c.bot.send_message(chat_id, "⚠️ خطا: توکن‌های ورود یافت نشد.")
        return
    
    cst = await get_github_var("AUTO_UPDATE", "true")
    if cst == "false":
        await c.bot.send_message(chat_id, "🛑 ربات خاموش است. /on را بزنید.")
        return
        
    tg = int(c.args[0]) if c.args and c.args[0].isdigit() else None
    await c.bot.send_message(chat_id, "⏳ در حال تحلیل...")
    
    browser = None
    try:
        async with ap() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
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
            pg.set_default_timeout(60000)
            
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000, wait_until="domcontentloaded")
            await pg.wait_for_timeout(10000) 

            body_text = await pg.locator('body').inner_text()
            if len(body_text.strip()) < 100:
                await c.bot.send_message(chat_id, "⚠️ صفحه سایت سفید است (IP مسدود شده).")
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
                    
            if not any(res_all):
                await c.bot.send_message(chat_id, "⚠️ هیچ دوری خوانده نشد.")
            else:
                await send_long_message(c, chat_id, rp)
            
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطای غیرمنتظره: {str(e)}")
    finally:
        if browser: await browser.close()

async def main():
    app = AB().token(T).build()
    app.add_handler(CH('start', st))
    app.add_handler(CH('on', on))
    app.add_handler(CH('off', off))
    app.add_handler(CH('check_odds', co))
    app.add_handler(CH('allow', allow_user))
    app.add_handler(CH('deny', deny_user))
    app.add_handler(CH('list', list_users))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.sleep(300)
    await app.updater.stop()
    await app.stop()
    await app.shutdown()

if True:
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
