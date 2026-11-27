import os, re, httpx, sys, asyncio, json, time
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.ext import (
    ApplicationBuilder as AB, ContextTypes as C,
    CommandHandler as CH, CallbackQueryHandler,
    MessageHandler, filters
)
from playwright.async_api import async_playwright as ap

T = os.environ.get("BOT_TOKEN")
A = os.environ.get("USER_TOKEN")
D = os.environ.get("USER_DEVICE_ID")
U = os.environ.get("USER_UDI")
G = os.environ.get("GH_PAT")
R = os.environ.get("GITHUB_REPOSITORY")
MY_ID = "184149177"

# ============================================================
# ⚠️ تنظیمات اشتراک ⚠️
# ============================================================
CARD_NUMBER = "6219-8618-0229-0089"
CARD_HOLDER = "مرتضی عزیزی"
SUB_PRICE = "250,000"                 
REF_REWARD_DAYS = 3                   
# ============================================================

_last_call = {}
COOLDOWN = 60
AR_CACHE = {}
CACHE_TTL = 0 

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

async def is_authorized(u: Update, c: C) -> bool: return True 

async def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=37.25&longitude=55.16&current_weather=true"
        async with httpx.AsyncClient() as client:
            res = await client.get(url)
            data = res.json()
            return f"🌡 دما: {data['current_weather']['temperature']}°C | 💨 باد: {data['current_weather']['windspeed']} km/h"
    except: return "⚠️ اطلاعات هوا در دسترس نیست."

JOCKEY_TIERS = {'S': ['رحمانی', 'قهرمانی', 'نوروزی'], 'A': ['وحدانی', 'مرادی', 'صوفیان', 'خواجوی'],
                'B': ['آقاجانی', 'حیدری', 'نصیری', 'مسعودی'], 'C': ['احمدی', 'رضایی']}
JOCKEY_RATINGS = {'S': 0.95, 'A': 0.75, 'B': 0.55, 'C': 0.40, 'D': 0.28}

def jockey_rating(name):
    for tier, names in JOCKEY_TIERS.items():
        if any(n in name for n in names): return JOCKEY_RATINGS[tier]
    return JOCKEY_RATINGS['D']

RANK_POINTS = {'1': 10, '2': 6, '3': 3, '4': 1, '5': 0.5}
def decayed_form(perf_str, decay=0.85):
    if not perf_str: return 0
    return sum(RANK_POINTS.get(str(int(r.strip())), 0) * (decay ** i) 
               for i, r in enumerate(perf_str.split('-')) if r.strip().isdigit())

def power_probs(scores, power=1.8):
    min_s = min(scores)
    if min_s <= 0: scores = [s - min_s + 0.01 for s in scores]
    powered = [s ** power for s in scores]
    total = sum(powered) or 1
    return [p / total for p in powered]

def place_probability(probs, i, num_places=3):
    n = len(probs); p_i = probs[i]
    if p_i <= 0: return 0
    p2 = sum(probs[j] * p_i / (1 - probs[j]) for j in range(n) if j != i and probs[j] < 1)
    p3 = 0
    if num_places >= 3:
        for j in range(n):
            if j == i or probs[j] >= 1: continue
            for k in range(n):
                if k == i or k == j or probs[j] + probs[k] >= 1: continue
                p3 += probs[j] * (probs[k] / (1 - probs[j])) * (p_i / (1 - probs[j] - probs[k]))
    return p_i + p2 + p3

def quinella_prob(probs, i, j):
    p_i, p_j = probs[i], probs[j]
    if p_i + p_j >= 1 or p_i <= 0 or p_j <= 0: return 0
    return p_i * p_j / (1 - p_i) + p_j * p_i / (1 - p_j)

def get_confidence_level(res_dict):
    w, p = res_dict['w'], res_dict['p']
    model_gap = w['model_prob'] - p['model_prob']
    agree = (1 if w['edge'] > 0 else 0) + (1 if abs(w['model_prob'] - w['market_prob']) < 10 else 0)
    if model_gap > 12 and agree >= 2: return "🔥 بالا"
    elif model_gap > 6: return "⚖️ متوسط"
    else: return "⚠️ پایین"

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
            except: 
                user_status = "(نامشخص)"
            
            keyboard.append([InlineKeyboardButton(f"👤 {display_name} {user_status}", callback_data='ignore')])
            keyboard.append([
                InlineKeyboardButton("➕ تمدید ۳۰ روز", callback_data=f"adm_ren_{uid}_30"),
                InlineKeyboardButton("🚫 کنسل", callback_data=f"adm_can_{uid}"),
                InlineKeyboardButton("❌ حذف", callback_data=f"adm_del_{uid}")
            ])
    await c.bot.send_message(chat_id, txt, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def prepare_page(pg):
    try:
        await pg.wait_for_timeout(4000)
        try: await pg.keyboard.press("Escape")
        except: pass
    except Exception as e:
        print(f"Prepare page error: {e}")

async def _dump_rendered_html(pg, c, chat_id, r_num):
    try:
        html = await pg.content()
        fname = f"Table_HTML_R{r_num}.txt"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(html)
        with open(fname, "rb") as f:
            await c.bot.send_document(
                chat_id,
                document=f,
                filename=fname,
                caption=(f"🔍 <b>دیباگ HTML دور {r_num}</b>\n"
                         f"📏 حجم: {len(html):,} کاراکتر\n"
                         f"👇 در فایل سرچ کنید: <code>سانتال</code> یا <code>58kg</code>"),
                parse_mode='HTML'
            )
    except Exception as e:
        print(f"Dump HTML error: {e}")

# ============================================================
# 🆕 Core Analysis (TEXT-BASED PARSING - NO TABLE TAG)
# ============================================================
async def ar(p, r_num, c, chat_id, save_history=True):
    if r_num in AR_CACHE:
        ts, cached_res = AR_CACHE[r_num]
        if time.time() - ts < CACHE_TTL: return cached_res

    horses = {}
    try:
        await p.wait_for_timeout(3000)
        
        # 1. پیدا کردن تمام کارت‌های اسب (divهایی که شامل شماره و وزن هستند)
        # این روش به جای تگ table، به دنبال ساختارهای گرافیکی می‌گردد
        horse_cards = await p.evaluate("""
            () => {
                // پیدا کردن تمام divهایی که شبیه به یک ردیف اسب هستند
                let elements = Array.from(document.querySelectorAll('div, tr, li'));
                let cards = elements.filter(el => {
                    let txt = el.innerText || '';
                    // الگوی تقریبی: شامل kg و خط تیره (برای عملکرد) یا کلمه اسب
                    return (txt.includes('kg') || txt.includes('اسب')) && 
                           (txt.match(/\\d+-\\d+/) || txt.length < 500); // جلوگیری از انتخاب کل صفحه
                });
                
                // حذف عناصر تکراری (وقتی والد و فرزند هر دو انتخاب می‌شوند)
                let uniqueCards = [];
                for (let card of cards) {
                    if (!uniqueCards.some(c => c.contains(card) && c !== card)) {
                        uniqueCards.push(card);
                    }
                }
                
                return uniqueCards.map(el => {
                    // استخراج متن با حفظ ساختار خطوط
                    let lines = Array.from(el.childNodes)
                        .filter(n => n.nodeType === 3 || (n.nodeType === 1 && !['STYLE','SCRIPT','SVG'].includes(n.tagName)))
                        .map(n => (n.innerText || n.textContent || '').trim())
                        .filter(t => t.length > 0);
                    
                    // اگر خطوط خیلی کم بود، از innerText استفاده کن
                    if (lines.length < 3) {
                        lines = (el.innerText || '').split('\\n').map(l => l.trim()).filter(l => l.length > 0);
                    }
                    return lines;
                }).filter(lines => lines.length >= 4); // حداقل ۴ خط: شماره، نام، وزن، عملکرد
            }
        """)

        if not horse_cards:
            raise ValueError(f"هیچ ساختار اسبی در صفحه پیدا نشد.")

        # 2. پردازش کارت‌ها و تبدیل به دیکشنری اسب‌ها
        for lines in horse_cards:
            num = None; name = None; weight = None; ior = None; perf_str = "0"; jockey = "نامشخص"
            
            for i, line in enumerate(lines):
                # شماره اسب: اولین عدد تک‌رقمی یا دورقمی که در ابتدای خطوط است
                if not num and re.fullmatch(r'\d{1,2}', line):
                    num = int(line)
                # نام اسب: متنی که شامل حروف است و طولش بیشتر از ۲ است
                elif not name and re.search(r'[آ-یa-zA-Z]{2,}', line) and 'اسب' not in line:
                    name = line
                # وزن: شامل kg
                elif 'kg' in line.lower() and not weight:
                    weight = float(re.sub(r'[^\d.]', '', line.lower()))
                # IOR: عدد سه رقمی (بین ۱۰ تا ۹۹۹)
                elif re.fullmatch(r'\d{2,3}', line) and not ior:
                    ior = int(line)
                # عملکرد: شامل خط تیره (مثل 1-2-3)
                elif re.search(r'\d+-\d+', line) and perf_str == "0":
                    perf_str = re.sub(r'[^\d\-]', '', line)
                # چابکسوار: در انتهای لیست معمولا اسم می‌آید
                elif re.search(r'[آ-ی]{3,}', line) and jockey == "نامشخص":
                    jockey = line

            if num and name and num not in horses:
                # اگر IOR پیدا نشد، یک مقدار پیش‌فرض بر اساس وزن در نظر بگیر
                if not ior:
                    ior = int(100 - (weight if weight else 55)) + 10
                horses[num] = {
                    "n": name, "num": num, "ior": max(10, ior), "j": jockey,
                    "perf": perf_str if perf_str else "0", "w_odd": 50.0, "weight": weight
                }

        if len(horses) < 2:
            # اگر تعداد اسب‌های پیدا شده خیلی کم بود، خطا بده
            raise ValueError(f"تعداد اسب‌های معتبر یافت شده ({len(horses)}) کمتر از حد نیاز است.")

    except Exception as e:
        print(f"Error in ar: {e}")
        await _dump_rendered_html(p, c, chat_id, r_num)
        await c.bot.send_message(chat_id, f"🚨 <b>خطا در پارس دور {r_num}:</b>\n<code>{str(e)}</code>", parse_mode='HTML')
        return "ERROR"

    # --- شروع محاسبات آماری ---
    horse_list = list(horses.values())
    n = len(horse_list)
    
    forms = [decayed_form(h['perf']) for h in horse_list]
    form_max = max(forms) if forms and max(forms) > 0 else 1
    
    total_ior = sum(h['ior'] for h in horse_list)
    market_probs = [h['ior'] / total_ior if total_ior > 0 else 1/n for h in horse_list]

    for idx, h in enumerate(horse_list):
        form_norm = decayed_form(h['perf']) / form_max
        h['combined'] = (0.60 * form_norm + 0.40 * market_probs[idx])
        h['market_prob'] = market_probs[idx] * 100

    model_raw = power_probs([h['combined'] for h in horse_list], power=1.8)
    ALPHA = 0.35
    for idx, h in enumerate(horse_list):
        blended = ALPHA * model_raw[idx] + (1 - ALPHA) * market_probs[idx]
        h['model_prob'] = blended * 100
        h['edge'] = h['model_prob'] - h['market_prob']
        h['final_sc'] = h['model_prob']

    total_p = sum(h['model_prob'] for h in horse_list) or 1
    for h in horse_list: h['model_prob'] = (h['model_prob'] / total_p) * 100

    sorted_horses = sorted(horse_list, key=lambda y: y['model_prob'], reverse=True)
    probs_norm = [h['model_prob'] / 100 for h in sorted_horses]

    for idx, h in enumerate(sorted_horses):
        h['place_prob'] = place_probability(probs_norm, idx, num_places=3) * 100
        h['place_fair_odd'] = round(100 / h['place_prob'], 2) if h['place_prob'] > 0 else 0

    w = sorted_horses[0]
    p_horse = sorted_horses[1] if n > 1 else w
    s_horse = sorted_horses[2] if n > 2 else p_horse

    q_prob = quinella_prob(probs_norm, 0, 1)
    q_fair = round(1 / q_prob, 2) if q_prob > 0 else 0

    res_dict = {"w": w, "p": p_horse, "s": s_horse, "horses": sorted_horses, "q": f"{w['num']}-{w['n']} + {p_horse['num']}-{p_horse['n']}", "q_fair": q_fair}
    res_dict['conf'] = get_confidence_level(res_dict)
    
    long_shots = [h for h in sorted_horses if h['market_prob'] <= 10]
    top_ls = sorted(long_shots, key=lambda x: x['model_prob'], reverse=True) if long_shots else sorted_horses[-2:]
    res_dict['su'] = top_ls[0] if top_ls else sorted_horses[-1]
    res_dict['qs'] = f"{top_ls[0]['num']}-{top_ls[0]['n']} + {top_ls[1]['num']}-{top_ls[1]['n']}" if len(top_ls) >= 2 else "N/A"

    if save_history: await save_to_history(res_dict, r_num)
    AR_CACHE[r_num] = (time.time(), res_dict)
    return res_dict

# ============================================================
# بقیه توابع (بدون تغییر)
# ============================================================
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
                res = await ar(pg, r, c, chat_id, save_history=save_h)
                if isinstance(res, dict):
                    found_any = True
                    w = res['w']
                    rp += f"\n🏁 <b>دور R{r}</b>\n"
                    rp += f"🥇 <b>پیش‌بر:</b> {w['num']}-{w['n']}\n"
                    rp += f"   📊 مدل: {w['model_prob']:.1f}% | بازار: {w['market_prob']:.1f}%\n"
                    rp += f"🥈 <b>میان‌بر:</b> {res['p']['num']}-{res['p']['n']}\n"
                    rp += f"👥 <b>دوقلو (امن):</b> {res['q']}\n"
                    rp += f"🎯 <b>اطمینان:</b> {res['conf']}\n"
                    
                    if bet_amount and specific_round and r == specific_round:
                        rp += f"\n💰 <b>مدیریت سرمایه</b>\n(به دلیل نبود ضرایب در سایت، شرط‌بندی پیشنهاد نمی‌شود.)\n"
                    
                    if specific_round:
                        rp += "\n📈 <b>سه اسب برتر:</b>\n"
                        for h in res['horses'][:3]:
                            rp += f"  {h['num']}-{h['n']}: احتمال {h['model_prob']:.1f}% (IOR: {h['ior']})\n"
                    rp += "➖➖➖➖➖➖➖➖\n"
                    
            if not found_any:
                rp += "\n⚠️ <b>جدول مسابقات هنوز آپلود نشده است یا ربات نتوانست آن را بخواند.</b>"
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
    titles = {'q_safe': "🟢 دوقلوی امن (Safe)", 'q_value': "💎 دوقلوی ارزش (Value)", 'q_lucky': "🍀 دوقلوی شانسی (Lucky)"}
    title = titles[variant_type]
    try:
        async with ap() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            context = await browser.new_context()
            await context.add_cookies([{'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},{'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},{'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}])

            pg = await context.new_page()
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000)
            await pg.wait_for_timeout(5000)
            if "ورود" in await pg.locator('body').inner_text():
                await c.bot.send_message(chat_id, "⚠️ کوکی‌ها منقضی شده‌اند.", parse_mode='HTML')
                return
            await prepare_page(pg)
            rp = f"{title}\n➖➖➖➖➖➖➖➖\n"
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
                            if not is_top: continue
                        if variant_type == 'q_lucky':
                            if not (i >= 3 and j >= 3): continue
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
                    rp += "➖➖➖➖➖➖➖➖\n"
            if not found_any: rp += "\n⚠️ برای این نوع دوقلو، گزینه مناسبی پیدا نشد.\n"
            await send_long_message(c, chat_id, rp)
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطا:\n<code>{str(e)}</code>", parse_mode='HTML')
    finally:
        if browser: await browser.close()

async def process_multileg(chat_id, c, leg_type, bet_amount=None):
    await c.bot.send_message(chat_id, "⏳ <b>در حال بررسی...</b>", parse_mode='HTML')
    browser = None
    try:
        async with ap() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            context = await browser.new_context()
            await context.add_cookies([{'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},{'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},{'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}])
            pg = await context.new_page()
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000)
            await pg.wait_for_timeout(5000)
            if "ورود" in await pg.locator('body').inner_text():
                await c.bot.send_message(chat_id, "⚠️ کوکی‌ها منقضی شده‌اند.", parse_mode='HTML')
                return
            await prepare_page(pg)
            res_7 = await ar(pg, 7, c, chat_id, save_history=False)
            is_7 = isinstance(res_7, dict)
            if leg_type == 'ml_6': tr = [2,3,4,5,6,7] if is_7 else [1,2,3,4,5,6]; title = "۶ پره"
            elif leg_type == 'ml_5': tr = [3,4,5,6,7] if is_7 else [2,3,4,5,6]; title = "۵ پره"
            elif leg_type == 'ml_3a': tr = [1,2,3]; title = "۳ پره اول"
            elif leg_type == 'ml_3b': tr = [5,6,7] if is_7 else [4,5,6]; title = "۳ پره دوم"
            else: return
            picks, details = [], []
            for r in tr:
                res = res_7 if r == 7 and is_7 else await ar(pg, r, c, chat_id, save_history=False)
                if isinstance(res, dict):
                    picks.append(f"{res['w']['num']}-{res['w']['n']}")
                    details.append(res['w'])
                else: picks.append("نامشخص")
            msg = f"🎯 <b>ترکیب {title}</b>\n\n🏇 <b>انتخاب‌ها:</b>\n<code>{' + '.join(picks)}</code>\n"
            await c.bot.send_message(chat_id, msg, parse_mode='HTML')
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطا: {str(e)}")
    finally:
        if browser: await browser.close()

async def process_surprises(chat_id, c, mode, bet_amount=None):
    await c.bot.send_message(chat_id, "⏳ <b>در حال پیدا کردن شگفتی...</b>", parse_mode='HTML')
    browser = None
    try:
        async with ap() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            context = await browser.new_context()
            await context.add_cookies([{'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},{'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},{'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}])

            pg = await context.new_page()
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000)
            await pg.wait_for_timeout(5000)
            if "ورود" in await pg.locator('body').inner_text():
                await c.bot.send_message(chat_id, "⚠️ کوکی‌ها منقضی شده‌اند.", parse_mode='HTML')
                return
            await prepare_page(pg)
            res_7 = await ar(pg, 7, c, chat_id, save_history=False)
            max_r = 7 if isinstance(res_7, dict) else 6
            title = "🎲 اسب شگفتی" if mode == 'sur_h' else "🔥 دوقلوی شگفتی"
            msg = f"{title}\n➖➖➖➖➖➖\n\n"
            for r in range(1, max_r + 1):
                res = res_7 if r == 7 and isinstance(res_7, dict) else await ar(pg, r, c, chat_id, save_history=False)
                if isinstance(res, dict):
                    if mode == 'sur_h':
                        sh = res['su']
                        msg += f"🏁 <b>دور {r}:</b> {sh['num']}-{sh['n']}\n"
                    else:
                        msg += f"🏁 <b>دور {r}:</b> {res['qs']}\n"
            await c.bot.send_message(chat_id, msg, parse_mode='HTML')
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطا: {str(e)}")
    finally:
        if browser: await browser.close()

async def process_value_bets(chat_id, c, bet_amount=None):
    if await get_github_var("AUTO_UPDATE", "true") == "false":
        return await c.bot.send_message(chat_id, "🛑 خاموش است.")
    await c.bot.send_message(chat_id, "⏳ <b>در حال شکار ارزش...</b>", parse_mode='HTML')
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
            rp = f"💎 <b>شکار ارزش (تک‌اسب)</b>\n➖➖➖➖➖➖➖➖\n"
            found_any = False
            for r in range(1, 8):
                res = await ar(pg, r, c, chat_id, save_history=False)
                if isinstance(res, dict):
                    values = [h for h in res['horses'] if h['model_prob'] > 20]
                    if values:
                        found_any = True
                        rp += f"\n🏁 <b>دور R{r}:</b>\n"
                        for v in values[:3]:
                            line = f"💎 {v['num']}-{v['n']} | مدل {v['model_prob']:.1f}% (IOR: {v['ior']})"
                            rp += line + "\n"
                        rp += "➖➖➖➖➖➖➖➖\n"
            if not found_any: rp += "\n⚠️ <b>ارزشی پیدا نشد.</b>"
            await send_long_message(c, chat_id, rp)
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطا: {str(e)}")
    finally:
        if browser: await browser.close()

async def cmd_history(chat_id, c):
    history_str = await get_github_var("BOT_HISTORY", "[]")
    try: history = json.loads(history_str)
    except: history = []
    if not history: return await c.bot.send_message(chat_id, "📜 تاریخچه‌ای نیست.")
    txt = "📜 <b>۵ تحلیل آخر:</b>\n\n"
    for item in history:
        txt += f"📅 {item['date']} | دور {item['round']}\n🥇 {item['win']}\n👥 {item['quinella']}\n🎯 {item['confidence']}\n➖➖➖➖\n"
    await c.bot.send_message(chat_id, txt, parse_mode='HTML')

async def get_referrals():
    ref_str = await get_github_var("REFERRALS", "{}")
    try: return json.loads(ref_str)
    except: return {}

async def add_vip_days(target_id, days):
    users = await get_allowed_users_dict()
    now = datetime.now()
    if str(target_id) in users:
        try:
            current_exp = datetime.strptime(users[str(target_id)], "%Y-%m-%d %H:%M:%S")
            if current_exp < now: current_exp = now
        except: current_exp = now
    else: current_exp = now
    new_exp = (current_exp + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    users[str(target_id)] = new_exp
    await set_github_var("ALLOWED_USERS", json.dumps(users))
    return new_exp

async def check_expirations(bot):
    await asyncio.sleep(3600)
    while True:
        try:
            users = await get_allowed_users_dict()
            now = datetime.now()
            for uid, exp_str in users.items():
                try:
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
                    days_left = (exp_date - now).days
                    if days_left == 2:
                        try:
                            await bot.send_message(int(uid), "⚠️ <b>هشدار انقضای اشتراک</b>\n\nاشتراک شما ۲ روز دیگر به پایان می‌رسد.\nبرای جلوگیری از قطع دسترسی به تحلیل‌ها، از دکمه «💳 خرید اشتراک» استفاده کنید.", parse_mode='HTML')
                        except: pass
                except: pass
        except: pass
        await asyncio.sleep(86400)

async def run_reminder(chat_id, c, r_num, delay_minutes):
    await asyncio.sleep(delay_minutes * 60)
    await c.bot.send_message(chat_id, f"⏰ <b>یادآوری!</b> در حال تحلیل خودکار دور {r_num}...", parse_mode='HTML')
    await process_odds(chat_id, c, specific_round=r_num, bet_amount=c.user_data.get('bet_amount'), is_reminder=True)

async def button_callback(u: Update, c: C):
    query = u.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data
    premium_buttons = ['r1', 'r2', 'r3', 'r4', 'r5', 'r6', 'r7', 'rall', 'q_safe', 'q_value', 'q_lucky', 'value', 'ml_6', 'ml_5', 'ml_3a', 'ml_3b', 'sur_h', 'sur_d']
    if data in premium_buttons:
        if not await is_premium(chat_id):
            await query.message.reply_text("⛔ <b>دسترسی محدود!</b>\n\nاین بخش ویژه مشترکین است.\nبرای تهیه اشتراک روی دکمه «💳 خرید اشتراک» بزنید.", parse_mode='HTML')
            return
    if data in ['on', 'off'] and str(chat_id) != MY_ID:
        await query.answer("⛔ شما اجازه انجام این کار را ندارید.", show_alert=True)
        return
    now = time.time()
    key = f"{chat_id}:{data}"
    if now - _last_call.get(key, 0) < COOLDOWN and data != 'bet_set' and not data.startswith('rem_') and not data.startswith('adm_'):
        remaining = int(COOLDOWN - (now - _last_call.get(key, 0)))
        return await query.answer(f"⏳ {remaining} ثانیه صبر کنید", show_alert=True)
    _last_call[key] = now

    if data == 'buy_sub':
        txt = f"💳 <b>خرید اشتراک ویژه</b>\n\nبرای دسترسی به تمام امکانات ربات، مبلغ <b>{SUB_PRICE} تومان</b> را به شماره کارت زیر واریز کنید:\n\n💳 <code>{CARD_NUMBER}</code>\n👤 به نام: {CARD_HOLDER}\n\n📸 <b>پس از واریز، عکس رسید پرداخت را همین‌جا ارسال کنید.</b>\nپس از تایید توسط مدیریت، اشتراک شما فعال می‌شود."
        await query.message.reply_text(txt, parse_mode='HTML')
        return
    elif data == 'my_status':
        if await is_premium(chat_id):
            users = await get_allowed_users_dict()
            exp_str = users.get(str(chat_id), "نامشخص")
            await query.message.reply_text(f"✅ <b>وضعیت شما:</b> فعال\nتاریخ انقضا: <code>{exp_str}</code>", parse_mode='HTML')
        else:
            await query.message.reply_text("❌ <b>وضعیت شما:</b> غیرفعال\nبرای خرید اشتراک روی دکمه «💳 خرید اشتراک» بزنید.", parse_mode='HTML')
        return
    elif data == 'invite':
        bot_info = await c.bot.get_me()
        link = f"https://t.me/{bot_info.username}?start=ref_{chat_id}"
        referrals = await get_referrals()
        invited = len(referrals.get(str(chat_id), []))
        txt = f"🎁 <b>دعوت از دوستان</b>\n\nبا دعوت از دوستان خود، به ازای هر نفر <b>{REF_REWARD_DAYS} روز</b> اشتراک رایگان دریافت کنید!\n\n🔗 <b>لینک اختصاصی شما:</b>\n<code>{link}</code>\n\n👤 <b>تعداد دعوت شدگان:</b> {invited} نفر\n🎁 (معادل {invited * REF_REWARD_DAYS} روز اشتراک هدیه گرفته‌اید)"
        await query.message.reply_text(txt, parse_mode='HTML')
        return
    elif data.startswith('adm_'):
        if str(chat_id) != MY_ID: return
        parts = data.split('_'); action = parts[1]; uid = parts[2]; users = await get_allowed_users_dict()
        if action == 'ren':
            days = int(parts[3]); expire = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            users[uid] = expire; await set_github_var("ALLOWED_USERS", json.dumps(users))
            await query.message.reply_text(f"✅ اشتراک <code>{uid}</code> برای {days} روز تمدید شد.", parse_mode='HTML')
            try: await c.bot.send_message(uid, f"🎉 اشتراک شما توسط مدیریت برای {days} روز دیگر تمدید شد.", parse_mode='HTML')
            except: pass
            await cmd_admin_panel(chat_id, c)
        elif action == 'can':
            expire = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
            users[uid] = expire; await set_github_var("ALLOWED_USERS", json.dumps(users))
            await query.message.reply_text(f"🚫 اشتراک <code>{uid}</code> کنسل شد.", parse_mode='HTML')
            try: await c.bot.send_message(uid, "⚠️ اشتراک شما توسط مدیریت لغو شد.", parse_mode='HTML')
            except: pass
            await cmd_admin_panel(chat_id, c)
        elif action == 'del':
            if uid in users: del users[uid]
            await set_github_var("ALLOWED_USERS", json.dumps(users))
            await query.message.reply_text(f"❌ کاربر <code>{uid}</code> کاملاً حذف شد.", parse_mode='HTML')
            try: await c.bot.send_message(uid, "⚠️ دسترسی شما به ربات کاملاً قطع شد.", parse_mode='HTML')
            except: pass
            await cmd_admin_panel(chat_id, c)
    elif data == 'admin_refresh':
        if str(chat_id) != MY_ID: return
        await query.message.delete()
        await cmd_admin_panel(chat_id, c)
    elif data == 'on': await set_github_var("AUTO_UPDATE", "true"); await query.message.reply_text("✅ روشن شد.")
    elif data == 'off': await set_github_var("AUTO_UPDATE", "false"); await query.message.reply_text("🛑 خاموش شد.")
    elif data == 'history': await cmd_history(chat_id, c)
    elif data == 'weather': await query.message.reply_text(await get_weather(), parse_mode='HTML')
    elif data == 'value': await process_value_bets(chat_id, c, bet_amount=c.user_data.get('bet_amount'))
    elif data == 'bet_set':
        c.user_data['awaiting_bet'] = True
        cur = c.user_data.get('bet_amount', 0)
        await query.message.reply_text(f"💰 <b>سرمایه فعلی:</b> <code>{cur:,}</code> ت\nعدد جدید را بفرست:", parse_mode='HTML', reply_markup=ForceReply(selective=True))
    elif data == 'change_city': await query.message.reply_text("🏙 مسابقه خودکار انتخاب می‌شود.", parse_mode='HTML')
    elif data == 'admin_help': 
        if str(chat_id) == MY_ID: await cmd_admin_panel(chat_id, c)
        else: await query.answer("⛔ شما دسترسی ندارید.", show_alert=True)
    elif data.startswith('rem_set_'):
        r_num = int(data.split('_')[2])
        c.user_data['awaiting_rem'] = True; c.user_data['rem_round'] = r_num
        await query.message.reply_text("⏰ چند دقیقه دیگر یادآوری کنم؟ (عدد بفرست)", parse_mode='HTML', reply_markup=ForceReply(selective=True))
    elif data in ['q_safe', 'q_value', 'q_lucky']: await process_quinellas(chat_id, c, data)
    elif data.startswith('r'):
        bet = c.user_data.get('bet_amount')
        if data == 'rall': await process_odds(chat_id, c, all_rounds=True)
        else: await process_odds(chat_id, c, specific_round=int(data[1:]), bet_amount=bet)
    elif data.startswith('ml_'): await process_multileg(chat_id, c, data, bet_amount=c.user_data.get('bet_amount'))
    elif data in ['sur_h', 'sur_d']: await process_surprises(chat_id, c, data, bet_amount=c.user_data.get('bet_amount'))

async def handle_inputs(u: Update, c: C):
    if c.user_data.get('awaiting_bet'):
        c.user_data['awaiting_bet'] = False
        try:
            amount = int(re.sub(r'[^\d]', '', u.message.text))
            if amount < 1000: raise ValueError
            c.user_data['bet_amount'] = amount
            await u.message.reply_text(f"✅ سرمایه: <code>{amount:,}</code> ت", parse_mode='HTML')
        except: await u.message.reply_text("❌ عدد معتبر (≥ ۱۰۰۰) بفرست.")
        return
    if c.user_data.get('awaiting_rem'):
        c.user_data['awaiting_rem'] = False
        try:
            mins = int(re.sub(r'[^\d]', '', u.message.text))
            if mins < 1: raise ValueError
            asyncio.create_task(run_reminder(u.effective_chat.id, c, c.user_data.get('rem_round', 1), mins))
            await u.message.reply_text(f"✅ یادآوری برای {mins} دقیقه دیگر تنظیم شد.", parse_mode='HTML')
        except: await u.message.reply_text("❌ عدد معتبر بفرست.")

async def handle_photo(u: Update, c: C):
    chat_id = u.effective_chat.id
    if str(chat_id) == MY_ID: return
    user_name = u.effective_chat.first_name or "کاربر"
    await c.bot.forward_message(chat_id=MY_ID, from_chat_id=chat_id, message_id=u.message.message_id)
    admin_msg = f"🔔 <b>رسید جدید!</b>\n\n👤 نام: {user_name}\nآیدی عددی: <code>{chat_id}</code>\n\nبرای تایید ۳۰ روزه، روی این متن کلیک کنید تا کپی شود و سپس ارسال کنید:\n<code>/approve {chat_id} 30</code>\n<i>(اگر می‌خواهید تعداد روز دیگری باشد، عدد ۳۰ را تغییر دهید)</i>"
    await c.bot.send_message(MY_ID, admin_msg, parse_mode='HTML')
    await u.message.reply_text("✅ رسید شما دریافت شد.\nپس از بررسی توسط پشتیبانی، اشتراک شما فعال خواهد شد.")

async def cmd_approve(u: Update, c: C):
    if str(u.effective_chat.id) != MY_ID: return
    try:
        parts = u.message.text.split()
        if len(parts) < 3: raise ValueError
        target_id = parts[1].strip(); days = int(parts[2])
        users = await get_allowed_users_dict()
        expire_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        users[target_id] = expire_date
        await set_github_var("ALLOWED_USERS", json.dumps(users))
        await u.message.reply_text(f"✅ کاربر <code>{target_id}</code> برای {days} روز تایید شد.\nتاریخ انقضا: <code>{expire_date}</code>", parse_mode='HTML')
        try: await c.bot.send_message(target_id, f"🎉 <b>تبریک!</b>\nاشتراک شما به مدت {days} روز فعال شد.\nتاریخ انقضا: {expire_date}", parse_mode='HTML')
        except: await u.message.reply_text("⚠️ نتوانستم به کاربر پیام بدم.")
    except: await u.message.reply_text("❌ فرمت صحیح:\n<code>/approve 123456789 30</code>", parse_mode='HTML')

async def cmd_remove(u: Update, c: C):
    if str(u.effective_chat.id) != MY_ID: return
    try:
        parts = u.message.text.split()
        if len(parts) < 2: raise ValueError
        target_id = parts[1].strip()
        users = await get_allowed_users_dict()
        if target_id in users:
            del users[target_id]
            await set_github_var("ALLOWED_USERS", json.dumps(users))
            await u.message.reply_text(f"🗑 کاربر <code>{target_id}</code> از لیست VIP حذف شد.", parse_mode='HTML')
            try: await c.bot.send_message(target_id, "⚠️ اشتراک شما لغو شده است.", parse_mode='HTML')
            except: pass
        else: await u.message.reply_text("این کاربر در لیست VIP نیست.")
    except: await u.message.reply_text("❌ فرمت صحیح:\n<code>/remove 123456789</code>", parse_mode='HTML')

async def cmd_broadcast(u: Update, c: C):
    if str(u.effective_chat.id) != MY_ID: return
    if not c.args:
        await u.message.reply_text("❌ متن پیام را وارد کنید:\n<code>/broadcast سلام به همه</code>", parse_mode='HTML')
        return
    msg = " ".join(c.args); users = await get_allowed_users_dict(); success = 0; fail = 0
    status_msg = await u.message.reply_text(f"⏳ در حال ارسال پیام به {len(users)} کاربر...")
    for uid in users.keys():
        try:
            await c.bot.send_message(int(uid), msg, parse_mode='HTML')
            success += 1; await asyncio.sleep(0.1) 
        except: fail += 1
    await status_msg.edit_text(f"✅ ارسال به پایان رسید.\n\nموفق: {success}\nناموفق: {fail}")

async def cmd_debug(u: Update, c: C):
    if str(u.effective_chat.id) != MY_ID:
        return await u.message.reply_text("⛔ فقط ادمین.")
    try:
        r_num = int(c.args[0]) if c.args else 1
    except:
        r_num = 1
    await u.message.reply_text(f"⏳ در حال گرفتن HTML دور {r_num}...")
    browser = None
    try:
        async with ap() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            context = await browser.new_context(viewport={'width': 1440, 'height': 900})
            await context.add_cookies([
                {'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}
            ])
            pg = await context.new_page()
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000, wait_until="domcontentloaded")
            await pg.wait_for_timeout(5000)
            await prepare_page(pg)
            await _dump_rendered_html(pg, c, u.effective_chat.id, r_num)
    except Exception as e:
        await u.message.reply_text(f"❌ خطا: {e}")
    finally:
        if browser: await browser.close()

async def cmd_start(u: Update, c: C):
    chat_id = str(u.effective_chat.id)
    if c.args and c.args[0].startswith('ref_'):
        referrer_id = c.args[0].replace('ref_', '')
        if referrer_id != chat_id:
            referrals = await get_referrals()
            if referrer_id not in referrals: referrals[referrer_id] = []
            users = await get_allowed_users_dict()
            if chat_id not in referrals[referrer_id] and chat_id not in users:
                referrals[referrer_id].append(chat_id)
                await set_github_var("REFERRALS", json.dumps(referrals))
                new_exp = await add_vip_days(referrer_id, REF_REWARD_DAYS)
                try: await c.bot.send_message(int(referrer_id), f"🎉 <b>یک نفر با لینک شما وارد ربات شد!</b>\n\n🎁 {REF_REWARD_DAYS} روز به اشتراک شما اضافه شد.\n📅 تاریخ انقضای جدید: <code>{new_exp}</code>", parse_mode='HTML')
                except: pass
    users = await get_allowed_users_dict()
    is_new_user = (chat_id not in users) and (chat_id != MY_ID)
    if is_new_user:
        expire_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        users[chat_id] = expire_date; await set_github_var("ALLOWED_USERS", json.dumps(users))
        welcome_msg = "👋 <b>سلام و خوش آمدید!</b>\n\n🎁 <b>به شما ۱ روز اشتراک رایگان هدیه دادیم!</b>\nاز همین حالا می‌توانید تمام دکمه‌ها را تست کنید.\n\n⏳ <i>اشتراک رایگان شما ۲۴ ساعت دیگر منقضی می‌شود.</i>"
    else:
        welcome_msg = "👋 <b>سلام دوباره!</b>\n✅ <b>نسخه 4.5 (پارس هوشمند متنی - بدون وابستگی به تگ Table)</b>"
    await c.bot.send_message(u.effective_chat.id, welcome_msg, reply_markup=get_main_menu(chat_id), parse_mode='HTML')

async def main():
    app = AB().token(T).build()
    app.add_handler(CH('start', cmd_start))
    app.add_handler(CH('approve', cmd_approve))
    app.add_handler(CH('remove', cmd_remove))
    app.add_handler(CH('broadcast', cmd_broadcast)) 
    app.add_handler(CH('debug', cmd_debug))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_inputs))
    asyncio.create_task(check_expirations(app.bot))
    await app.initialize(); await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.sleep(17400)
    await app.updater.stop(); await app.stop(); await app.shutdown()

if __name__ == "__main__":
    try: asyncio.run(main())
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        sys.exit(1)
