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

# ============================================================
# Anti-Spam & Cache
# ============================================================
_last_call = {}
COOLDOWN = 60
AR_CACHE = {}
CACHE_TTL = 0 # غیرفعال کردن کش برای دیباگ

# ============================================================
# GitHub Variables & Auth
# ============================================================
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
    try:
        return json.loads(users_str)
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

# ============================================================
# Weather & Math Helpers
# ============================================================
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

def normalize_market(odds_list):
    implied = [1.0 / o if o > 0 else 0 for o in odds_list]
    total = sum(implied) or 1
    return [i / total for i in implied]

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

def calculate_kelly(bankroll, model_prob, decimal_odds, fraction=0.25, min_bet=1000, max_pct=0.05, confidence=0.55):
    if decimal_odds <= 1 or bankroll <= 0: return 0
    market_prob = 100 / decimal_odds
    p_true = model_prob * confidence + market_prob * (1 - confidence)
    b = decimal_odds - 1; p = p_true / 100; q = 1 - p
    if p <= 0 or p >= 1: return 0
    f = (b * p - q) / b
    if f <= 0: return 0
    raw = min(bankroll * f * fraction, bankroll * max_pct)
    return int(raw // 1000) * 1000 if raw >= min_bet else 0

FRACTIONS = {'win': 0.25, 'place': 0.20, 'quinella': 0.15, 'exacta': 0.10, 'trifecta': 0.05, 'parlay': 0.08}

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
            except: user_status = "(نامشخص)"
            keyboard.append([InlineKeyboardButton(f"👤 {display_name} {user_status}", callback_data='ignore')])
            keyboard.append([
                InlineKeyboardButton("➕ تمدید ۳۰ روز", callback_data=f"adm_ren_{uid}_30"),
                InlineKeyboardButton("🚫 کنسل", callback_data=f"adm_can_{uid}"),
                InlineKeyboardButton("❌ حذف", callback_data=f"adm_del_{uid}")
            ])
    await c.bot.send_message(chat_id, txt, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def prepare_page(pg):
    try:
        await pg.wait_for_timeout(2000)
        dropdown = pg.get_by_text("انتخاب فصل مسابقات", exact=False).first
        if await dropdown.is_visible():
            await dropdown.click(timeout=5000, force=True)
            await pg.wait_for_timeout(1500)
            try:
                await pg.get_by_text("یزد", exact=False).last.click(timeout=3000)
            except:
                await pg.locator("ul li, .v-list-item, .dropdown-item, [role='option']").first.click()
            await pg.wait_for_timeout(3000)
    except: pass

# ============================================================
# ✅ Core Analysis (REWRITTEN FOR ROBUST TABLE EXTRACTION)
# ============================================================
async def ar(p, r_num, c, chat_id, save_history=True):
    if r_num in AR_CACHE:
        ts, cached_res = AR_CACHE[r_num]
        if time.time() - ts < CACHE_TTL: return cached_res

    horses = {}
    try:
        # 1. کلیک روی تب دور
        clicked = False
        for text in [f"R {r_num}", f"R{r_num}", str(r_num), f"دور {r_num}"]:
            try:
                loc = p.get_by_text(text, exact=True).first
                if await loc.is_visible():
                    await loc.click(timeout=2000, force=True)
                    clicked = True
                    break
            except: pass
        
        # 2. زمان بسیار بیشتر برای لود کامل جدول و ضرایب (خیلی مهم)
        await p.wait_for_timeout(7000)
        
        # 3. استخراج فوق‌هوشمند (تارگت کردن جدول + خواندن Input ها)
        js_extract = """() => {
            function getText(el) {
                let text = el.innerText ? el.innerText.trim() : '';
                // خواندن مقادیر داخل فیلدهای ورودی (ضرایب معمولاً اینجا هستند)
                if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                    text = el.value || '';
                }
                const inputs = el.querySelectorAll('input');
                if (inputs.length > 0) {
                    let inVals = Array.from(inputs).map(i => i.value).filter(v => v).join(' ');
                    if (inVals) text = text ? text + ' ' + inVals : inVals;
                }
                return text;
            }

            // تلاش اول: پیدا کردن جدول واقعی
            let tables = document.querySelectorAll('table');
            if (tables.length > 0) {
                let res = "";
                tables.forEach(t => {
                    t.querySelectorAll('tr').forEach(tr => {
                        let cells = Array.from(tr.querySelectorAll('td, th')).map(getText).filter(c => c);
                        if (cells.length > 0) res += cells.join(' | ') + '\\n';
                    });
                });
                if (res.trim()) return res;
            }

            // تلاش دوم: اگر جدول استاندارد نبود، سعی کن کارت‌های حاوی اطلاعات اسب را بخوانی
            let horseCards = document.querySelectorAll('.card, .row, .list-item, .horse-row');
            let res2 = "";
            horseCards.forEach(card => {
                let txt = getText(card);
                if (txt.length > 5 && txt.length < 200) res2 += txt + '\\n';
            });
            if (res2.trim()) return res2;

            // فال‌بک: اگر هیچ‌کدام نشد، متن کل صفحه را بده
            return document.body.innerText;
        }"""
        
        table_text = await p.evaluate(js_extract)
        
        # 4. پارس کردن متن (با فیلتر کردن هوشمند)
        for line in table_text.split('\n'):
            # نادیده گرفتن منوها و هدرهای سایت
            if any(kw in line for kw in ['جدول مسابقات', 'مالی', 'پخش زنده', 'گزارش', 'اسبدوانی', 'دوره‌ای', 'نظر سنجی', 'حراجی']):
                continue
                
            if '|' in line:
                parts = [x.strip() for x in line.split('|') if x.strip()]
                if len(parts) < 3: continue # یک ردیف داده معتبر حداقل ۳ ستون دارد
                
                # فیلتر حیاتی: اگر خط هیچ عدد یا ضریبی ندارد، احتمالا صفحه ماتریسی (ردیف اسم‌ها) است و نباید خوانده شود
                has_num = any(re.match(r'^\d{1,2}$', p_val) for p_val in parts)
                has_float = any(re.match(r'^\d+(\.\d+)?$', p_val) for p_val in parts)
                
                if not has_num or not has_float:
                    continue # رد کردن خطوط منو، هدر، و اسم‌های بدون ضریب
                
                num = 0
                name = ""
                ior = 0
                
                # پیدا کردن شماره اسب
                for p_val in parts:
                    if re.match(r'^\d{1,2}$', p_val) and num == 0:
                        num = int(p_val)
                        break
                
                # پیدا کردن نام اسب (نادیده گرفتن هدرها)
                for p_val in parts:
                    if len(p_val) > 2 and not re.match(r'^[\d\.]+$', p_val):
                        if p_val not in ['نام اسب', 'اسب', 'شماره', 'جیگه', 'کاپیتان', 'H', 'R 1', 'R 2', 'R 3', 'R 4', 'R 5', 'R 6', 'R 7', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7']:
                            name = p_val
                            break
                            
                # پیدا کردن ضریب
                for p_val in reversed(parts):
                    if re.match(r'^\d+(\.\d+)?$', p_val):
                        val = float(p_val)
                        if 1.0 <= val <= 999.0:
                            ior = val
                            break
                            
                if name:
                    if num == 0: num = len(horses) + 1 # اگر شماره نبود، خودکار بده
                    horses[num] = {"n": name, "num": num, "ior": ior or 10.0, "j": "ناشناس", "perf": "0", "w_odd": ior or 10.0}
        
        # اگر هیچ اسبی پیدا نشد، متن و فایل HTML را برای دیباگ بفرست
        if not horses:
            debug_text = table_text[:3500]
            await c.bot.send_message(chat_id, f"🚨 <b>خطا در خواندن جدول دور {r_num}</b>\nمتن استخراج شده:\n\n<pre>{debug_text}</pre>", parse_mode='HTML')
            
            # ذخیره و ارسال HTML برای دیباگ دقیق
            html_content = await p.content()
            with open("debug_table.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            with open("debug_table.html", "rb") as f:
                await c.bot.send_document(chat_id, document=f, caption="🔍 فایل HTML دیباگ (این را برای پشتیبانی بفرستید تا کد را دقیق تنظیم کنیم).")
            
            return "NOT_UPLOADED"

    except Exception as e: 
        print(f"Error in ar: {e}")
        await c.bot.send_message(chat_id, f"🚨 <b>خطای داخلی در تحلیل دور {r_num}</b>\n<code>{str(e)}</code>", parse_mode='HTML')
        return "ERROR"

    if not horses or len(horses) < 2: return "NO_DATA"

    # --- ادامه محاسبات ریاضی ---
    horse_list = list(horses.values())
    n = len(horse_list)
    iors = [h['ior'] for h in horse_list]
    ior_min, ior_max = min(iors), max(iors)
    ior_range = max(ior_max - ior_min, 1)
    forms = [decayed_form(h['perf']) for h in horse_list]
    form_max = max(forms) if forms and max(forms) > 0 else 1
    market_probs = normalize_market([h['w_odd'] for h in horse_list])

    for idx, h in enumerate(horse_list):
        ior_norm = (h['ior'] - ior_min) / ior_range
        form_norm = decayed_form(h['perf']) / form_max
        jockey_norm = jockey_rating(h['j'])
        market_norm = market_probs[idx]
        h['combined'] = (0.40 * market_norm + 0.25 * ior_norm + 0.20 * form_norm + 0.10 * jockey_norm + 0.05 * 0.5)
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
    long_shots = [h for h in sorted_horses if h['w_odd'] >= 12]
    top_ls = sorted(long_shots, key=lambda x: x['model_prob'], reverse=True) if long_shots else sorted_horses[-2:]
    res_dict['su'] = top_ls[0] if top_ls else sorted_horses[-1]
    res_dict['qs'] = f"{top_ls[0]['num']}-{top_ls[0]['n']} + {top_ls[1]['num']}-{top_ls[1]['n']}" if len(top_ls) >= 2 else "N/A"

    if save_history: await save_to_history(res_dict, r_num)
    AR_CACHE[r_num] = (time.time(), res_dict)
    return res_dict

# ============================================================
# Process Odds (With Debug Screenshot)
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
            
            await pg.screenshot(path="debug_start.png")
            with open("debug_start.png", "rb") as f:
                await c.bot.send_photo(chat_id, photo=f, caption="📸 عکس دیباگ: بعد از ورود به سایت")

            await prepare_page(pg)

            await pg.screenshot(path="debug_prepared.png")
            with open("debug_prepared.png", "rb") as f:
                await c.bot.send_photo(chat_id, photo=f, caption="📸 عکس دیباگ: بعد از انتخاب شهر")

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
                except Exception as e
