import os, re, httpx, sys, asyncio, json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder as AB, ContextTypes as C, CommandHandler as CH, CallbackQueryHandler
from playwright.async_api import async_playwright as ap

T = os.environ.get("BOT_TOKEN")
A = os.environ.get("USER_TOKEN")
D = os.environ.get("USER_DEVICE_ID")
U = os.environ.get("USER_UDI")
G = os.environ.get("GH_PAT")
R = os.environ.get("GITHUB_REPOSITORY")
MY_ID = "184149177" 

async def get_github_var(var_name, default_val=""):
    if not R or not G: return default_val
    try:
        url = f"https://api.github.com/repos/{R}/actions/variables/{var_name}"
        headers = {"Authorization": f"Bearer {G}", "Accept": "application/vnd.github+json"}
        async with httpx.AsyncClient() as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 200: return res.json().get("value", default_val)
        return default_val
    except: return default_val

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
    except: return False

async def get_allowed_users():
    users_str = await get_github_var("ALLOWED_USERS", MY_ID)
    return [u.strip() for u in users_str.split(",") if u.strip()]

async def is_authorized(u: Update, c: C) -> bool:
    if not u.effective_chat: return False
    chat_id = str(u.effective_chat.id)
    if chat_id == MY_ID: return True
    allowed = await get_allowed_users()
    if chat_id not in allowed:
        await c.bot.send_message(u.effective_chat.id, "⛔ شما اجازه استفاده ندارید.")
        return False
    return True

async def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=37.25&longitude=55.16&current_weather=true"
        async with httpx.AsyncClient() as client:
            res = await client.get(url)
            data = res.json()
            temp = data['current_weather']['temperature']
            wind = data['current_weather']['windspeed']
            return f"🌡 دما: {temp}°C | 💨 باد: {wind} km/h"
    except: return "⚠️ اطلاعات هواشناسی در دسترس نیست."

def calculate_kelly(bankroll, true_prob, decimal_odds):
    b = decimal_odds - 1
    p = true_prob / 100
    q = 1 - p
    if b <= 0: return 0
    f = (b * p - q) / b
    if f <= 0: return 0
    return round(bankroll * f, -3)

def get_confidence_level(score1, score2):
    diff = score1 - score2
    if diff > 20: return "🔥 بالا"
    elif diff > 10: return "⚖️ متوسط"
    else: return "⚠️ پایین"

def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("🟢 روشن", callback_data='on'), InlineKeyboardButton("🔴 خاموش", callback_data='off')],
        [InlineKeyboardButton("🏁 دور ۱", callback_data='r1'), InlineKeyboardButton("🏁 دور ۲", callback_data='r2'), InlineKeyboardButton("🏁 دور ۳", callback_data='r3')],
        [InlineKeyboardButton("🏁 دور ۴", callback_data='r4'), InlineKeyboardButton("🏁 دور ۵", callback_data='r5'), InlineKeyboardButton("🏁 دور ۶", callback_data='r6')],
        [InlineKeyboardButton("🏁 دور ۷", callback_data='r7'), InlineKeyboardButton("📊 تحلیل کل", callback_data='rall')],
        [InlineKeyboardButton("💎 شکار ارزش", callback_data='value'), InlineKeyboardButton("📜 سابقه تحلیل‌ها", callback_data='history')],
        [InlineKeyboardButton("🎯 ۶ پره", callback_data='ml_6'), InlineKeyboardButton("🎯 ۵ پره", callback_data='ml_5')],
        [InlineKeyboardButton("🎯 ۳ پره اول", callback_data='ml_3a'), InlineKeyboardButton("🎯 ۳ پره دوم", callback_data='ml_3b')],
        [InlineKeyboardButton("🎲 اسب شگفتی", callback_data='sur_h'), InlineKeyboardButton("🔥 دوقلوی شگفتی", callback_data='sur_d')],
        [InlineKeyboardButton("🌤 وضعیت هوا", callback_data='weather'), InlineKeyboardButton("🏙 تغییر مسیر/شهر", callback_data='change_city')],
        [InlineKeyboardButton("👑 پنل مدیریت", callback_data='admin_help')]
    ]
    return InlineKeyboardMarkup(keyboard)

async def send_long_message(c, chat_id, text):
    if len(text) > 4000:
        for i in range(0, len(text), 4000): await c.bot.send_message(chat_id, text[i:i+4000], parse_mode='HTML')
    else: await c.bot.send_message(chat_id, text, parse_mode='HTML')

async def save_to_history(res_dict, r_num):
    history_str = await get_github_var("BOT_HISTORY", "[]")
    try: history = json.loads(history_str)
    except: history = []
    entry = {"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "round": r_num, "win": f"{res_dict['w']['num']}-{res_dict['w']['n']}", "quinella": res_dict['q'], "confidence": res_dict['conf']}
    history.insert(0, entry)
    history = history[:5]
    await set_github_var("BOT_HISTORY", json.dumps(history, ensure_ascii=False))

async def prepare_page(pg):
    # انتخاب هوشمند جدیدترین مسابقه (شهر)
    try:
        await pg.wait_for_timeout(2000)
        dropdown = pg.get_by_text("انتخاب فصل مسابقات", exact=False).first
        if await dropdown.is_visible():
            await dropdown.click(timeout=5000, force=True)
            await pg.wait_for_timeout(2000)
            first_option = pg.locator("ul li, .v-list-item, .dropdown-item, [role='option']").first
            if await first_option.is_visible():
                await first_option.click()
                await pg.wait_for_timeout(3000)
            else:
                await pg.keyboard.press("Enter")
                await pg.wait_for_timeout(3000)
    except Exception as e:
        print(f"Track selection error: {e}")

async def ar(p, r_num, c, chat_id):
    horses = {}
    try:
        # 1. رفتن به تب جدول مسابقات
        try: await p.get_by_text("جدول مسابقات", exact=False).first.click(timeout=5000, force=True)
        except: pass
        
        # 2. کلیک روی نمایش دورها
        try: await p.get_by_text("نمایش دورها", exact=False).first.click(timeout=5000, force=True)
        except: pass

        # 3. کلیک روی دور مورد نظر
        clicked = False
        for text in [f"R {r_num}", f"R{r_num}", str(r_num)]:
            try: 
                await p.get_by_text(text, exact=True).first.click(timeout=3000, force=True)
                clicked = True
                break
            except: pass
        if not clicked: return "NO_DATA"
        
        await p.wait_for_timeout(4000) 
        table_text = await p.locator('body').inner_text()
        if "Kg" not in table_text: return "NO_DATA"
        
        # استخراج اطلاعات پایه
        pattern = r'([^\n]{3,20})\n?[^\n]*?\n?(\d{1,2})\s+(\d{2,3}Kg)\n?(\d{1,3})\n?([\d\-]+)\n?([^\n]+)'
        for m in re.finditer(pattern, table_text):
            name = m.group(1).strip()
            if any(k in name for k in ["مقام", "اسب", "سن", "عملکرد", "وزن", "مالک"]): continue
            num = int(m.group(2)); ior = int(m.group(4)); perf_str = m.group(5); jockey = m.group(6).strip()
            perf_score = sum({'1': 25, '2': 15, '3': 5}.get(rank.strip(), 0) for rank in perf_str.split('-'))
            horses[num] = {"n": name, "num": num, "ior": ior, "j": jockey, "perf": perf_str, "perf_sc": perf_score, "w_odd": 50.0}
            
        # 4. گرفتن ضرایب واقعی از تب دورهای (بخش اضافه شده)
        try:
            await p.get_by_text("دورهای", exact=False).first.click(timeout=3000, force=True)
            await p.wait_for_timeout(2000)
            try: await p.get_by_text(f"R {r_num}", exact=True).first.click(timeout=3000, force=True)
            except: await p.get_by_text(str(r_num), exact=True).first.click(timeout=3000, force=True)
            await p.wait_for_timeout(3000)
            
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
        return "ERROR"
    
    if not horses: return "NO_DATA"
    
    # محاسبه امتیاز نهایی (با لیست گسترده چابکسواران)
    good_jockeys = ["رحمانی", "قهرمانی", "نوروزی", "وحدانی", "مرادی", "صوفیان", "خواجوی", "آقاجانی", "حیدری", "نصیری", "مسعودی", "احمدی", "رضایی"]
    for num, h in horses.items():
        market_score = 100 / (h['w_odd'] + 1) if h['w_odd'] > 0 else 0
        jockey_bonus = 20 if any(gj in h['j'] for gj in good_jockeys) else 0
        h['final_sc'] = (h['ior'] * 0.4) + (h['perf_sc'] * 0.3) + (market_score * 0.2) + (jockey_bonus * 0.1)
        h['prob'] = round(100 / h['w_odd'], 1) if h['w_odd'] > 0 else 0
        
    sorted_horses = sorted(horses.values(), key=lambda y: y['final_sc'], reverse=True)
    w = sorted_horses[0]
    p_horse = sorted_horses[1] if len(sorted_horses) > 1 else w
    s_horse = sorted_horses[2] if len(sorted_horses) > 2 else p_horse
    conf = get_confidence_level(w['final_sc'], p_horse['final_sc'])
    
    res_dict = {"w": w, "p": p_horse, "s": s_horse, "q": f"{w['num']}-{w['n']} + {p_horse['num']}-{p_horse['n']}", "conf": conf}
    await save_to_history(res_dict, r_num)
    return res_dict

async def process_odds(chat_id, c, specific_round=None, all_rounds=False, bet_amount=None):
    if await get_github_var("AUTO_UPDATE", "true") == "false": return await c.bot.send_message(chat_id, "🛑 خاموش است.")
    await c.bot.send_message(chat_id, "⏳ <b>در حال اتصال و انتخاب جدیدترین مسابقه...</b>", parse_mode='HTML')
    weather = await get_weather()
    browser = None
    try:
        async with ap() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
            context = await browser.new_context(viewport={'width': 1440, 'height': 900})
            await context.add_cookies([{'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},{'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},{'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}])
            pg = await context.new_page()
            pg.set_default_timeout(60000)
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000, wait_until="domcontentloaded")
            await pg.wait_for_timeout(5000) 
            
            # مدیریت خطای ورود (اضافه شده)
            if "ورود" in await pg.locator('body').inner_text():
                await c.bot.send_message(chat_id, "⚠️ <b>خطای ورود:</b> کوکی‌ها منقضی شده‌اند. لطفاً USER_TOKEN را در گیت‌هاب آپدیت کنید.", parse_mode='HTML')
                return
            
            await prepare_page(pg)
            
            rp = f"🏇 <b>تحلیل جامع</b> 🏇\n{weather}\n➖➖➖➖➖➖➖➖\n"
            rounds = range(1, 7) if all_rounds else ([specific_round] if specific_round else [])
            found_any = False
            for r in rounds:
                res = await ar(pg, r, c, chat_id)
                if isinstance(res, dict):
                    found_any = True
                    rp += f"\n🏁 <b>دور R{r}</b>\n"
                    rp += f"🥇 <b>پیش‌بر:</b> {res['w']['num']}-{res['w']['n']} (ضریب {res['w']['w_odd']})\n"
                    rp += f"🥈 <b>میان‌بر:</b> {res['p']['num']}-{res['p']['n']}\n"
                    rp += f"👥 <b>دوقلو:</b> {res['q']}\n"
                    rp += f"🎯 <b>اطمینان:</b> {res['conf']}\n"
                    if bet_amount and specific_round and r == specific_round:
                        rp += "\n💰 <b>محاسبه دقیق Kelly:</b>\n"
                        kelly_w = calculate_kelly(bet_amount, res['w']['prob'], res['w']['w_odd'])
                        rp += f"🔸 روی <b>{res['w']['n']}</b>: <code>{kelly_w:,}</code> تومان\n"
                    rp += "➖➖➖➖➖➖➖➖\n"
            if not found_any:
                rp += "\n⚠️ <b>جدول مسابقات هنوز آپلود نشده است یا خطا در خواندن داده‌ها.</b>"
            await send_long_message(c, chat_id, rp)
    except Exception as e: 
        await c.bot.send_message(chat_id, f"❌ <b>خطای غیرمنتظره:</b>\n<code>{str(e)}</code>", parse_mode='HTML')
    finally:
        if browser: await browser.close()

async def process_multileg(chat_id, c, leg_type):
    await c.bot.send_message(chat_id, "⏳ <b>در حال بررسی...</b>", parse_mode='HTML')
    browser = None
    try:
        async with ap() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
            context = await browser.new_context()
            await context.add_cookies([{'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},{'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},{'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}])
            pg = await context.new_page()
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000)
            await pg.wait_for_timeout(5000) 
            
            if "ورود" in await pg.locator('body').inner_text():
                await c.bot.send_message(chat_id, "⚠️ <b>خطای ورود:</b> کوکی‌ها منقضی شده‌اند.", parse_mode='HTML')
                return

            await prepare_page(pg)
            
            res_7 = await ar(pg, 7, c, chat_id)
            is_7 = isinstance(res_7, dict) 
            if leg_type == 'ml_6': tr = [2,3,4,5,6,7] if is_7 else [1,2,3,4,5,6]; title = "۶ پره"
            elif leg_type == 'ml_5': tr = [3,4,5,6,7] if is_7 else [2,3,4,5,6]; title = "۵ پره"
            elif leg_type == 'ml_3a': tr = [1,2,3]; title = "۳ پره اول"
            elif leg_type == 'ml_3b': tr = [5,6,7] if is_7 else [4,5,6]; title = "۳ پره دوم"
            else: return
            
            picks = []
            for r in tr:
                if r == 7 and is_7: res = res_7 
                else: res = await ar(pg, r, c, chat_id)
                if isinstance(res, dict): picks.append(f"{res['w']['num']}-{res['w']['n']}")
                else: picks.append("نامشخص")
                
            msg = f"🎯 <b>ترکیب {title}</b> 🎯\n\n🏇 <b>انتخاب‌ها:</b>\n<code>{' + '.join(picks)}</code>"
            await c.bot.send_message(chat_id, msg, parse_mode='HTML')
    except Exception as e: 
        await c.bot.send_message(chat_id, f"❌ خطا: {str(e)}")
    finally:
        if browser: await browser.close()

async def process_surprises(chat_id, c, mode):
    await c.bot.send_message(chat_id, "⏳ <b>در حال پیدا کردن شگفتی...</b>", parse_mode='HTML')
    browser = None
    try:
        async with ap() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
            context = await browser.new_context()
            await context.add_cookies([{'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},{'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},{'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}])
            pg = await context.new_page()
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000)
            await pg.wait_for_timeout(5000) 
            
            if "ورود" in await pg.locator('body').inner_text():
                await c.bot.send_message(chat_id, "⚠️ <b>خطای ورود:</b> کوکی‌ها منقضی شده‌اند.", parse_mode='HTML')
                return

            await prepare_page(pg)
            
            res_7 = await ar(pg, 7, c, chat_id)
            max_r = 7 if isinstance(res_7, dict) else 6
            msg = f"{'🎲 اسب شگفتی' if mode == 'sur_h' else '🔥 دوقلوی شگفتی'}\n➖➖➖➖➖➖\n\n"
            for r in range(1, max_r + 1):
                res = res_7 if r == 7 and isinstance(res_7, dict) else await ar(pg, r, c, chat_id)
                if isinstance(res, dict):
                    msg += f"🏁 <b>دور {r}:</b> " + (f"{res['su']['num']}-{res['su']['n']}\n" if mode == 'sur_h' else f"{res['qs']}\n")
            await c.bot.send_message(chat_id, msg, parse_mode='HTML')
    except Exception as e: 
        await c.bot.send_message(chat_id, f"❌ خطا: {str(e)}")
    finally:
        if browser: await browser.close()

async def cmd_history(chat_id, c):
    history_str = await get_github_var("BOT_HISTORY", "[]")
    try: history = json.loads(history_str)
    except: history = []
    if not history: return await c.bot.send_message(chat_id, "📜 تاریخچه‌ای ثبت نشده است.")
    txt = "📜 <b>۵ تحلیل آخر:</b>\n\n"
    for item in history: txt += f"📅 {item['date']} | دور {item['round']}\n🥇 {item['win']}\n👥 {item['quinella']}\n🎯 {item['confidence']}\n➖➖➖➖\n"
    await c.bot.send_message(chat_id, txt, parse_mode='HTML')

async def process_value_bets(chat_id, c):
    await c.bot.send_message(chat_id, "⏳ <b>در حال شکار ارزش (Value)...</b>", parse_mode='HTML')
    await process_odds(chat_id, c, all_rounds=True)

async def button_callback(u: Update, c: C):
    query = u.callback_query
    await query.answer() 
    chat_id = query.message.chat_id
    data = query.data
    
    if not await is_authorized(u, c): return

    if data == 'on': await set_github_var("AUTO_UPDATE", "true"); await query.message.reply_text("✅ روشن شد.")
    elif data == 'off': await set_github_var("AUTO_UPDATE", "false"); await query.message.reply_text("🛑 خاموش شد.")
    elif data == 'history': await cmd_history(chat_id, c)
    elif data == 'weather': await query.message.reply_text(await get_weather(), parse_mode='HTML')
    elif data == 'value': await process_value_bets(chat_id, c)
    elif data == 'change_city': 
        await query.message.reply_text("🏙 <b>انتخاب مسیر/شهر:</b>\nدر حال حاضر ربات جدیدترین مسابقه را انتخاب می‌کند. قابلیت دستی در آپدیت بعدی.", parse_mode='HTML')
    elif data.startswith('r'):
        if data == 'rall': await process_odds(chat_id, c, all_rounds=True)
        else: await process_odds(chat_id, c, specific_round=int(data[1:]))
    elif data.startswith('ml_'): await process_multileg(chat_id, c, data)
    elif data in ['sur_h', 'sur_d']: await process_surprises(chat_id, c, data)

async def cmd_start(u: Update, c: C): 
    if not await is_authorized(u, c): return
    await c.bot.send_message(u.effective_chat.id, "👋 <b>سلام مدیر!</b>\nمنوی جدید با قابلیت انتخاب هوشمند مسابقات، ضرایب زنده و مدیریت خطا اضافه شد.", reply_markup=get_main_menu(), parse_mode='HTML')

async def main():
    app = AB().token(T).build()
    app.add_handler(CH('start', cmd_start))
    app.add_handler(CallbackQueryHandler(button_callback)) 
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await app.updater.stop()
    await app.stop()
    await app.shutdown()

if __name__ == "__main__":
    try: asyncio.run(main())
    except (KeyboardInterrupt, SystemExit): pass
