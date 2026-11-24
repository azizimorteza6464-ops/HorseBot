import os, re, httpx, sys, asyncio, json, time
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.ext import (
    ApplicationBuilder as AB, ContextTypes as C,
    CommandHandler as CH, CallbackQueryHandler,
    MessageHandler, filters
)
from playwright.async_api import async_playwright as ap

from config import *
from logic import *

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

async def get_allowed_users_dict():
    users_str = await get_github_var("ALLOWED_USERS", "{}")
    try: return json.loads(users_str)
    except:
        users_dict = {}
        if users_str and ("," in users_str or users_str.isdigit()):
            default_exp = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            for u in users_str.split(","):
                if u.strip(): users_dict[u.strip()] = default_exp
        return users_dict

async def is_premium(chat_id):
    if str(chat_id) == MY_ID: return True
    users = await get_allowed_users_dict()
    if str(chat_id) in users:
        try:
            expire_date = datetime.strptime(users[str(chat_id)], "%Y-%m-%d %H:%M:%S")
            if datetime.now() <= expire_date: return True
        except: pass
    return False

async def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=37.25&longitude=55.16&current_weather=true"
        async with httpx.AsyncClient() as client:
            res = await client.get(url)
            data = res.json()
            return f"🌡 دما: {data['current_weather']['temperature']}°C | 💨 باد: {data['current_weather']['windspeed']} km/h"
    except: return "⚠️ اطلاعات هوا در دسترس نیست."

def get_main_menu(chat_id):
    keyboard = []
    if str(chat_id) == MY_ID:
        keyboard.append([InlineKeyboardButton("🟢 روشن", callback_data='on'), InlineKeyboardButton("🔴 خاموش", callback_data='off')])
    keyboard.extend([
        [InlineKeyboardButton("🏁 دور ۱", callback_data='r1'), InlineKeyboardButton("🏁 دور ۲", callback_data='r2'), InlineKeyboardButton("🏁 دور ۳", callback_data='r3')],
        [InlineKeyboardButton("🏁 دور ۴", callback_data='r4'), InlineKeyboardButton("🏁 دور ۵", callback_data='r5'), InlineKeyboardButton("🏁 دور ۶", callback_data='r6')],
        [InlineKeyboardButton("🏁 دور ۷", callback_data='r7'), InlineKeyboardButton("📊 تحلیل کل", callback_data='rall')],
        [InlineKeyboardButton("🟢 دوقلوی امن", callback_data='q_safe'), InlineKeyboardButton("💎 دوقلوی ارزش", callback_data='q_value'), InlineKeyboardButton("🍀 دوقلوی شانسی", callback_data='q_lucky')],
        [InlineKeyboardButton("💎 شکار ارزش", callback_data='value'), InlineKeyboardButton("📜 سابقه تحلیل‌ها", callback_data='history')],
        [InlineKeyboardButton("🎯 ۶ پره", callback_data='ml_6'), InlineKeyboardButton("🎯 ۵ پره", callback_data='ml_5')],
        [InlineKeyboardButton("🎯 ۳ پره اول", callback_data='ml_3a'), InlineKeyboardButton("🎯 ۳ پره دوم", callback_data='ml_3b')],
        [InlineKeyboardButton("🎲 اسب شگفتی", callback_data='sur_h'), InlineKeyboardButton("🔥 دوقلوی شگفتی", callback_data='sur_d')],
        [InlineKeyboardButton("💰 تعیین مبلغ شرط", callback_data='bet_set')],
        [InlineKeyboardButton("💳 خرید اشتراک / تمدید", callback_data='buy_sub'), InlineKeyboardButton("👤 وضعیت اشتراک من", callback_data='my_status')],
        [InlineKeyboardButton("🎁 دعوت از دوستان (هدیه)", callback_data='invite')],
        [InlineKeyboardButton("🌤 وضعیت هوا", callback_data='weather'), InlineKeyboardButton("🏙 تغییر مسیر/شهر", callback_data='change_city')]
    ])
    if str(chat_id) == MY_ID:
        keyboard.append([InlineKeyboardButton("👑 پنل مدیریت", callback_data='admin_help')])
    return InlineKeyboardMarkup(keyboard)

def _split_safe(text, max_len=4000):
    if len(text) <= max_len: return [text]
    chunks, current = [], ""
    for block in text.split('\n\n'):
        if len(current) + len(block) + 2 > max_len:
            if current: chunks.append(current)
            if len(block) > max_len:
                current = ""
                for line in block.split('\n'):
                    if len(current) + len(line) + 1 > max_len:
                        if current: chunks.append(current)
                        current = line
                    else: current = current + '\n' + line if current else line
            else: current = block
        else: current = current + '\n\n' + block if current else block
    if current: chunks.append(current)
    return chunks

async def send_long_message(c, chat_id, text, max_len=4000):
    for chunk in _split_safe(text, max_len):
        try: await c.bot.send_message(chat_id, chunk, parse_mode='HTML')
        except Exception:
            try: await c.bot.send_message(chat_id, re.sub(r'<[^>]+>', '', chunk))
            except Exception as e: print(f"Send error: {e}")
        await asyncio.sleep(0.2)

async def save_to_history(res_dict, r_num):
    history_str = await get_github_var("BOT_HISTORY", "[]")
    try: history = json.loads(history_str)
    except: history = []
    entry = {"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "round": r_num, "win": f"{res_dict['w']['num']}-{res_dict['w']['n']}", "quinella": res_dict['q'], "confidence": res_dict['conf']}
    history.insert(0, entry)
    await set_github_var("BOT_HISTORY", json.dumps(history[:5], ensure_ascii=False))

async def cmd_admin_panel(chat_id, c):
    auto_update = await get_github_var("AUTO_UPDATE", "true")
    status = "🟢 روشن" if auto_update == "true" else "🔴 خاموش"
    users_dict = await get_allowed_users_dict()
    bet = c.user_data.get('bet_amount', 0)
    txt = f"👑 <b>پنل مدیریت</b>\n➖➖➖➖➖➖➖➖\n⚙️ <b>وضعیت:</b> {status}\n💰 <b>سرمایه:</b> {f'<code>{bet:,}</code> تومان' if bet else 'تعیین نشده'}\n\n👇 <b>لیست کاربران VIP:</b>"
    keyboard = [[InlineKeyboardButton("🔄 بروزرسانی لیست", callback_data='admin_refresh')]]
    now = datetime.now()
    if not users_dict: keyboard.append([InlineKeyboardButton("لیست خالی است", callback_data='ignore')])
    else:
        for uid, exp_str in users_dict.items():
            display_name = "ناشناس"
            try:
                chat_info = await c.bot.get_chat(int(uid))
                display_name = chat_info.first_name or "بدون نام"
            except: display_name = "نامشخص"
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
                days_left = (exp_date - now).days
                user_status = f"({days_left} روز مانده)" if days_left >= 0 else "(منقضی شده)"
            except: user_status = "(نامشخص)"
            keyboard.append([InlineKeyboardButton(f"👤 {display_name} {user_status}", callback_data='ignore')])
            keyboard.append([
                InlineKeyboardButton("➕ تمدید ۳۰ روز", callback_data=f"adm_ren_{uid}_30"),
                InlineKeyboardButton("🚫 کنسل", callback_data=f"adm_can_{uid}"),
                InlineKeyboardButton("❌ حذف", callback_data=f"adm_del_{uid}")
            ])
    await c.bot.send_message(chat_id, txt, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def process_odds(chat_id, c, specific_round=None, all_rounds=False, bet_amount=None, is_reminder=False):
    if await get_github_var("AUTO_UPDATE", "true") == "false" and not is_reminder:
        return await c.bot.send_message(chat_id, "🛑 خاموش است.")
    if not is_reminder: await c.bot.send_message(chat_id, "⏳ <b>در حال تحلیل...</b>", parse_mode='HTML')
    
    weather = await get_weather()
    browser = None
    try:
        async with ap() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            context = await browser.new_context(viewport={'width': 1440, 'height': 900})
            await context.add_cookies([{'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},{'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},{'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}])
            pg = await context.new_page()
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000, wait_until="domcontentloaded")
            await pg.wait_for_timeout(5000)
            
            if "ورود" in await pg.locator('body').inner_text():
                await c.bot.send_message(chat_id, "⚠️ کوکی‌ها منقضی شده‌اند.", parse_mode='HTML')
                return
            
            await prepare_page(pg)

            rp = f"🏇 <b>تحلیل جامع</b>\n{weather}\n" + (f"💰 سرمایه: <code>{bet_amount:,}</code> ت\n" if bet_amount else "") + "➖➖➖➖➖➖➖➖\n"
            rounds = range(1, 8) if all_rounds else ([specific_round] if specific_round else [])
            save_h = specific_round is not None
            found_any = False
            
            for r in rounds:
                try:
                    res = await ar(pg, r, c, chat_id, save_history=save_h)
                    if isinstance(res, dict):
                        found_any = True
                        w = res['w']
                        rp += f"\n🏁 <b>دور R{r}</b>\n"
                        rp += f"🥇 <b>پیش‌بر:</b> {w['num']}-{w['n']} (ضریب {w['w_odd']})\n"
                        rp += f"   📊 مدل: {w['model_prob']:.1f}% | بازار: {w['market_prob']:.1f}% | Edge: {w['edge']:+.1f}%\n"
                        rp += f"🥈 <b>میان‌بر:</b> {res['p']['num']}-{res['p']['n']}\n"
                        rp += f"👥 <b>دوقلو (امن):</b> {res['q']} → منصفانه <code>{res['q_fair']}</code>\n"
                        rp += f"🎯 <b>اطمینان:</b> {res['conf']}\n"
                        
                        if bet_amount and specific_round and r == specific_round:
                            rp += f"\n💰 <b>مدیریت سرمایه</b>\n"
                            k_win = calculate_kelly(bet_amount, w['model_prob'], w['w_odd'], fraction=FRACTIONS['win'])
                            if k_win > 0: rp += f"🥇 برد {w['n']}: <code>{k_win:,}</code> ت\n"
                            w_place_odd = round((w['w_odd'] - 1) / 4 + 1, 2)
                            k_place = calculate_kelly(bet_amount, w['place_prob'], w_place_odd, fraction=FRACTIONS['place'], confidence=0.60)
                            if k_place > 0: rp += f"🥈 جایگاه {w['n']}: <code>{k_place:,}</code> ت\n"
                            k_q = calculate_kelly(bet_amount, 100/res['q_fair'], res['q_fair'], fraction=FRACTIONS['quinella'], confidence=0.45)
                            if k_q > 0: rp += f"👥 دوقلو امن: <code>{k_q:,}</code> ت\n"
                        
                        if specific_round:
                            rp += "\n📈 <b>سه اسب برتر (Place):</b>\n"
                            for h in res['horses'][:3]:
                                rp += f"  {h['num']}-{h['n']}: برد {h['model_prob']:.1f}% | جایگاه {h['place_prob']:.1f}% (منصفانه {h['place_fair_odd']})\n"
                        rp += "➖➖➖➖➖➖➖➖\n"
                    else:
                        rp += f"\n⚠️ <b>دور R{r}:</b> {res}\n"
                except Exception as e:
                    rp += f"\n🚨 <b>خطا در دور R{r}:</b> {str(e)}\n"
                    
            if not found_any:
                rp += "\n⚠️ <b>جدول مسابقات هنوز آپلود نشده است یا ساختار سایت تغییر کرده است.</b>"
                await send_long_message(c, chat_id, rp)
                markup = InlineKeyboardMarkup([[InlineKeyboardButton("⏰ یادآوری خودکار", callback_data=f"rem_set_{specific_round or 1}")]])
                await c.bot.send_message(chat_id, "می‌خواهید ربات چه زمانی دوباره چک کند؟", reply_markup=markup)
            else: await send_long_message(c, chat_id, rp)
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطا:\n<code>{str(e)}</code>", parse_mode='HTML')
    finally:
        if browser: await browser.close()

async def process_quinellas(chat_id, c, variant_type):
    await c.bot.send_message(chat_id, "⏳ <b>در حال محاسبه دوقلوهای هوشمند...</b>", parse_mode='HTML')
    browser = None
    bet = c.user_data.get('bet_amount')
    titles = {'q_safe': "🟢 دوقلوی امن (Safe)", 'q_value': "💎 دوقلوی ارزش (Value)", 'q_lucky': "🍀 دوقلوی شانسی (Lucky)"}
    title = titles[variant_type]
    try:
        async with ap() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            context = await browser.new_context()
            await context.add_cookies([{'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},{'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},{'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}])
            pg = await context.new_page()
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000)
            await pg.wait_for_timeout(4000)
            if "ورود" in await pg.locator('body').inner_text():
                await c.bot.send_message(chat_id, "⚠️ کوکی‌ها منقضی شده‌اند.", parse_mode='HTML')
                return
            await prepare_page(pg)
            rp = f"{title}\n"
            if bet: rp += f"💰 سرمایه: <code>{bet:,}</code> ت (Kelly)\n"
            rp += "➖➖➖➖➖➖➖➖\n"
            found_any = False
            for r in range(1, 8):
                res = await ar(pg, r, c, chat_id, save_history=False)
                if not isinstance(res, dict): continue
                horses = res['horses']
                n = len(horses)
                probs = [h['model_prob']/100 for h in horses]
                candidates = []
                for i in range(n):
                    for j in range(i+1, n):
                        h1, h2 = horses[i], horses[j]
                        if variant_type == 'q_safe' and not (i == 0 and j == 1): continue
                        if variant_type == 'q_value':
                            is_top = (i < 3) or (j < 3)
                            is_long = (h1['w_odd'] >= 5) or (h2['w_odd'] >= 5)
                            if not (is_top and is_long): continue
                        if variant_type == 'q_lucky':
                            if not (h1['w_odd'] >= 5 and h2['w_odd'] >= 5): continue
                        q_p = quinella_prob(probs, i, j)
                        if q_p <= 0: continue
                        fair_odd = round(1 / q_p, 2)
                        candidates.append({"h1": h1, "h2": h2, "prob": q_p * 100, "fair_odd": fair_odd})
                if candidates:
                    if variant_type == 'q_safe': candidates.sort(key=lambda x: x['prob'], reverse=True)
                    else: candidates.sort(key=lambda x: x['fair_odd'], reverse=True)
                    best = candidates[0]
                    found_any = True
                    rp += f"\n🏁 <b>دور R{r}</b>\n"
                    rp += f"👥 <b>{best['h1']['num']}-{best['h1']['n']} + {best['h2']['num']}-{best['h2']['n']}</b>\n"
                    rp += f"   📊 احتمال برد: <b>{best['prob']:.1f}%</b>\n"
                    rp += f"   📊 ضریب منصفانه: <code>{best['fair_odd']}</code>\n"
                    if bet:
                        if variant_type == 'q_safe':
                            k = calculate_kelly(bet, best['prob'], best['fair_odd'], fraction=0.15, confidence=0.60)
                            if k > 0: rp += f"   💰 شرط پیشنهادی: <code>{k:,}</code> ت\n"
                        elif variant_type == 'q_value':
                            k = calculate_kelly(bet, best['prob'], best['fair_odd'], fraction=0.10, confidence=0.45)
                            if k > 0: rp += f"   💰 شرط پیشنهادی: <code>{k:,}</code> ت\n"
                            else: rp += f"   💡 ارزش دارد اگر سایت بالای {best['fair_odd']} بدهد.\n"
                        else:
                            rp += "   🎰 <b>بلیت بخت‌آزمایی</b> (ریسک بالا، مبلغ کم)\n"
                            k = calculate_kelly(bet, best['prob'], best['fair_odd'], fraction=0.05, confidence=0.30)
                            if k > 0: rp += f"   💰 شرط پیشنهادی: <code>{k:,}</code> ت\n"
                    rp += "➖➖➖➖➖➖➖➖\n"
            if not found_any: rp += "\n⚠️ برای این نوع دوقلو، گزینه مناسبی پیدا نشد.\n"
            else: rp += "\n💡 <i>ضریب منصفانه یعنی اگر سایت بیشتر از این عدد بده، شرط شما سودده است.</i>"
            await send_long_message(c, chat_id, rp)
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطا:\n<code>{str(e)}</code>", parse_mode='HTML')
    finally:
        if browser: await browser.close()
