import os, re, httpx, sys, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder as AB, ContextTypes as C, CommandHandler as CH, CallbackQueryHandler
from playwright.async_api import async_playwright as ap

# --- تنظیمات اولیه ---
T = os.environ.get("BOT_TOKEN")
A = os.environ.get("USER_TOKEN")
D = os.environ.get("USER_DEVICE_ID")
U = os.environ.get("USER_UDI")
G = os.environ.get("GH_PAT")
R = os.environ.get("GITHUB_REPOSITORY")

MY_ID = "184149177" 

# --- توابع ارتباط با گیت‌هاب ---
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

# --- مدیریت دسترسی ---
async def get_allowed_users():
    users_str = await get_github_var("ALLOWED_USERS", MY_ID)
    return [u.strip() for u in users_str.split(",") if u.strip()]

async def is_authorized(u: Update, c: C) -> bool:
    if not u.effective_chat: return False
    chat_id = str(u.effective_chat.id)
    if chat_id == MY_ID: return True
    allowed = await get_allowed_users()
    if chat_id not in allowed:
        await c.bot.send_message(u.effective_chat.id, "⛔ شما اجازه استفاده از این ربات را ندارید.")
        return False
    return True

# --- طراحی دکمه‌ها ---
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("🟢 روشن کردن ربات", callback_data='on'), InlineKeyboardButton("🔴 خاموش کردن ربات", callback_data='off')],
        [InlineKeyboardButton("🏁 دور ۱", callback_data='r1'), InlineKeyboardButton("🏁 دور ۲", callback_data='r2'), InlineKeyboardButton("🏁 دور ۳", callback_data='r3')],
        [InlineKeyboardButton("🏁 دور ۴", callback_data='r4'), InlineKeyboardButton("🏁 دور ۵", callback_data='r5'), InlineKeyboardButton("🏁 دور ۶", callback_data='r6')],
        [InlineKeyboardButton("🏁 دور ۷", callback_data='r7'), InlineKeyboardButton("📊 تحلیل کل (۱ تا ۶)", callback_data='rall')],
        [InlineKeyboardButton("👑 پنل مدیریت", callback_data='admin_help')]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- محاسبه توزیع سرمایه (Dutching) ---
def calculate_stakes(total_amount, selections):
    # selections: list of dicts with 'odd' and 'name'
    implied_probs = [1 / s['odd'] for s in selections if s['odd'] > 0]
    if not implied_probs: return []
    sum_implied = sum(implied_probs)
    stakes = []
    for i, s in enumerate(selections):
        if s['odd'] <= 0: continue
        stake = total_amount * ((1/s['odd']) / sum_implied)
        # گرد کردن به نزدیکترین 100 تومان
        stake = round(stake, -2)
        stakes.append({'name': s['name'], 'stake': stake})
    return stakes

# --- دستورات ربات ---
async def cmd_start(u: Update, c: C): 
    if not await is_authorized(u, c): return
    msg = "👋 <b>سلام مدیر عزیز!</b>\n\nبه ربات تحلیل مسابقات خوش آمدید.\nاز دکمه‌های زیر استفاده کنید یا دستور <code>/bet [مبلغ] [دور]</code> را بزنید."
    await c.bot.send_message(u.effective_chat.id, msg, reply_markup=get_main_menu(), parse_mode='HTML')

async def cmd_on(u: Update, c: C):
    if not await is_authorized(u, c): return
    success = await set_github_var("AUTO_UPDATE", "true")
    await c.bot.send_message(u.effective_chat.id, "✅ ربات روشن شد." if success else "❌ خطا")

async def cmd_off(u: Update, c: C):
    if not await is_authorized(u, c): return
    success = await set_github_var("AUTO_UPDATE", "false")
    await c.bot.send_message(u.effective_chat.id, "🛑 ربات خاموش شد." if success else "❌ خطا")

async def cmd_check_odds(u: Update, c: C):
    if not await is_authorized(u, c): return
    tg = int(c.args[0]) if c.args and c.args[0].isdigit() else None
    if tg: await process_odds(u.effective_chat.id, c, specific_round=tg)
    else: await process_odds(u.effective_chat.id, c, all_rounds=True)

async def cmd_bet(u: Update, c: C):
    if not await is_authorized(u, c): return
    if len(c.args) < 2 or not c.args[0].isdigit() or not c.args[1].isdigit():
        await c.bot.send_message(u.effective_chat.id, "⚠️ فرمت صحیح:\n<code>/bet [مبلغ] [شماره دور]</code>\nمثال: <code>/bet 100000 1</code>", parse_mode='HTML')
        return
    amount = int(c.args[0])
    r_num = int(c.args[1])
    await process_odds(u.effective_chat.id, c, specific_round=r_num, bet_amount=amount)

async def cmd_allow(u: Update, c: C):
    if str(u.effective_chat.id) != MY_ID: return
    if not c.args:
        await c.bot.send_message(u.effective_chat.id, "لطفاً آیدی را وارد کنید. مثال:\n<code>/allow 12345678</code>", parse_mode='HTML')
        return
    new_id = c.args[0]
    allowed = await get_allowed_users()
    if new_id in allowed:
        await c.bot.send_message(u.effective_chat.id, "این شخص از قبل دسترسی دارد.")
        return
    allowed.append(new_id)
    await set_github_var("ALLOWED_USERS", ",".join(allowed))
    await c.bot.send_message(u.effective_chat.id, f"✅ دسترسی به <code>{new_id}</code> داده شد.", parse_mode='HTML')

async def cmd_deny(u: Update, c: C):
    if str(u.effective_chat.id) != MY_ID: return
    if not c.args: return
    del_id = c.args[0]
    allowed = await get_allowed_users()
    if del_id in allowed:
        allowed.remove(del_id)
        await set_github_var("ALLOWED_USERS", ",".join(allowed))
        await c.bot.send_message(u.effective_chat.id, f"⛔ دسترسی <code>{del_id}</code> قطع شد.", parse_mode='HTML')

async def cmd_list(u: Update, c: C):
    if str(u.effective_chat.id) != MY_ID: return
    allowed = await get_allowed_users()
    txt = "👥 <b>لیست افراد مجاز:</b>\n\n"
    for a in allowed: txt += f"🔸 <code>{a}</code>\n"
    await c.bot.send_message(u.effective_chat.id, txt, parse_mode='HTML')

# --- اسکرپ و تحلیل ---
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
        try: await p.get_by_text("جدول مسابقات", exact=False).first.click(timeout=5000, force=True)
        except: pass
        try: await p.get_by_text("نمایش دورها", exact=False).first.click(timeout=5000, force=True)
        except: pass
        try: await p.get_by_text(f"R {r_num}", exact=True).first.click(timeout=5000, force=True)
        except:
            try: await p.get_by_text(str(r_num), exact=True).first.click(timeout=5000, force=True)
            except: return "NO_ROUND" 
        
        await p.wait_for_timeout(6000)
        table_text = await p.locator('body').inner_text()
        if "Kg" not in table_text: return "NO_DATA"

        pattern = r'([^\n]+)\n[^\n]*\n(\d{1,2})\s+(\d{2,3}Kg)\n(\d{1,3})\n([\d\-]+)\n([^\n]+)'
        for m in re.finditer(pattern, table_text):
            name = m.group(1).strip()
            if any(k in name for k in ["مقام", "اسب", "سن", "عملکرد", "وزن", "مالک", "جایزه"]): continue
            num = int(m.group(2))
            ior = int(m.group(4))
            perf_str = m.group(5)
            jockey = m.group(6).strip()
            perf_score = sum({'1': 25, '2': 15, '3': 5}.get(rank.strip(), 0) for rank in perf_str.split('-'))
            horses[num] = {"n": name, "num": num, "ior": ior, "j": jockey, "perf": perf_str, "perf_sc": perf_score, "w_odd": 50.0}
    except: return "ERROR"
        
    if not horses: return "NO_DATA"
    
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

async def process_odds(chat_id, c, specific_round=None, all_rounds=False, bet_amount=None):
    if not A or not U:
        await c.bot.send_message(chat_id, "⚠️ توکن‌های ورود یافت نشد.")
        return
    if await get_github_var("AUTO_UPDATE", "true") == "false":
        await c.bot.send_message(chat_id, "🛑 ربات خاموش است. /on را بزنید.")
        return
        
    await c.bot.send_message(chat_id, "⏳ <b>در حال تحلیل...</b>", parse_mode='HTML')
    
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
            await pg.wait_for_timeout(8000) 

            if len((await pg.locator('body').inner_text()).strip()) < 100:
                await c.bot.send_message(chat_id, "⚠️ صفحه سایت سفید است (IP مسدود شده).")
                return
                
            rp = "🏇 <b>تحلیل تخصصی مسابقات</b> 🏇\n➖➖➖➖➖➖➖➖➖➖\n"
            res_all = []
            rounds_to_check = range(1, 7) if all_rounds else ([specific_round] if specific_round else [])
            
            for r in rounds_to_check:
                res = await ar(pg, r, c, chat_id)
                
                if isinstance(res, dict):
                    res_all.append(res)
                    rp += f"\n🏁 <b>دور R{r}</b>\n"
                    rp += f"🥇 <b>پیش‌بر:</b> {res['w']['num']}-{res['w']['n']} <i>(ضریب {res['w']['w_odd']})</i>\n"
                    rp += f"🥈 <b>میان‌بر:</b> {res['p']['num']}-{res['p']['n']} <i>(ضریب {res['p']['w_odd']})</i>\n"
                    rp += f"🥉 <b>پی‌بر:</b> {res['s']['num']}-{res['s']['n']} <i>(ضریب {res['s']['w_odd']})</i>\n"
                    rp += f"👥 <b>دوقلو:</b> {res['q']}\n"
                    rp += f"🎲 <b>اسب شگفتی:</b> {res['su']['num']}-{res['su']['n']}\n"
                    rp += f"🔥 <b>دوقلوی شگفتی:</b> {res['qs']}\n"
                    
                    # --- بخش محاسبه شرط (اگر مبلغ وارد شده باشد) ---
                    if bet_amount and specific_round and r == specific_round:
                        rp += "\n💰 <b>توزیع سرمایه (Dutching):</b>\n"
                        # انتخاب اسب‌های پیشنهادی برای شرط
                        selections = [
                            {'name': f"{res['w']['num']}-{res['w']['n']}", 'odd': res['w']['w_odd']},
                            {'name': f"{res['p']['num']}-{res['p']['n']}", 'odd': res['p']['w_odd']}
                        ]
                        stakes = calculate_stakes(bet_amount, selections)
                        for st in stakes:
                            rp += f"🔸 روی <b>{st['name']}</b> ببند: <code>{st['stake']:,}</code> تومان\n"
                        rp += f"💵 <i>(کل مبلغ: {bet_amount:,} تومان)</i>\n"
                        
                    rp += "➖➖➖➖➖➖➖➖➖➖\n"
                else:
                    res_all.append(None)
                    
            if not any(res_all):
                if all_rounds:
                    await c.bot.send_message(chat_id, "⚠️ <b>اطلاعات مسابقه هنوز کامل نیست!</b> سایت ضرایب را منتشر نکرده است.", parse_mode='HTML')
                else:
                    await c.bot.send_message(chat_id, f"⚠️ <b>اطلاعات دور {specific_round} هنوز کامل نیست!</b>", parse_mode='HTML')
            else:
                await send_long_message(c, chat_id, rp)
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطای غیرمنتظره: {str(e)}")
    finally:
        if browser: await browser.close()

# --- هندلر دکمه‌ها ---
async def button_callback(u: Update, c: C):
    query = u.callback_query
    await query.answer() 
    chat_id = query.message.chat_id
    data = query.data
    
    if str(chat_id) != MY_ID and str(chat_id) not in await get_allowed_users():
        await query.message.reply_text("⛔ شما اجازه استفاده ندارید.")
        return

    if data == 'on':
        await set_github_var("AUTO_UPDATE", "true")
        await query.message.reply_text("✅ ربات روشن شد.")
    elif data == 'off':
        await set_github_var("AUTO_UPDATE", "false")
        await query.message.reply_text("🛑 ربات خاموش شد.")
    elif data == 'admin_help':
        if str(chat_id) == MY_ID:
            help_txt = "👑 <b>پنل مدیریت</b>\n\n🔹 <code>/allow [آیدی]</code>\n🔹 <code>/deny [آیدی]</code>\n🔹 <code>/list</code>\n\n💡 برای شرط‌بندی: <code>/bet [مبلغ] [دور]</code>"
            await query.message.reply_text(help_txt, parse_mode='HTML')
        else:
            await query.message.reply_text("⛔ این بخش فقط برای مدیر است.")
    elif data.startswith('r'):
        if data == 'rall': await process_odds(chat_id, c, all_rounds=True)
        else: await process_odds(chat_id, c, specific_round=int(data[1:]))

# --- راه‌اندازی ---
async def main():
    app = AB().token(T).build()
    app.add_handler(CH('start', cmd_start))
    app.add_handler(CH('on', cmd_on))
    app.add_handler(CH('off', cmd_off))
    app.add_handler(CH('check_odds', cmd_check_odds))
    app.add_handler(CH('bet', cmd_bet)) # دستور جدید شرط‌بندی
    app.add_handler(CH('allow', cmd_allow))
    app.add_handler(CH('deny', cmd_deny))
    app.add_handler(CH('list', cmd_list))
    app.add_handler(CallbackQueryHandler(button_callback)) 

    print("Bot started...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.sleep(300) # 5 دقیقه
    await app.updater.stop()
    await app.stop()
    await app.shutdown()

if True:
    try: asyncio.run(main())
    except (KeyboardInterrupt, SystemExit): pass
