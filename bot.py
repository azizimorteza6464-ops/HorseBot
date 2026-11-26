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
CACHE_TTL = 300

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

# ============================================================
# Weather & Math Helpers
# ============================================================
async def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=37.25&longitude=55.16&current_weather=true"
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(url)
            if res.status_code == 200:
                data = res.json()
                cw = data.get('current_weather', {})
                return f"🌡 دما: {cw.get('temperature', '---')}°C | 💨 باد: {cw.get('windspeed', '---')} km/h"
    except Exception as e: 
        print(f"Weather error: {e}")
    return "⚠️ اطلاعات هوا در دسترس نیست."

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

# ============================================================
# ارسال پیام طولانی
# ============================================================
async def send_long_message(c, chat_id, text, max_len=4000):
    if not text: return
    parts = []
    while len(text) > max_len:
        split_idx = text.rfind('\n', 0, max_len)
        if split_idx == -1:
            split_idx = text.rfind(' ', 0, max_len)
        if split_idx == -1:
            split_idx = max_len
        parts.append(text[:split_idx])
        text = text[split_idx:].lstrip('\n')
    parts.append(text)
    
    for chunk in parts:
        try: await c.bot.send_message(chat_id, chunk, parse_mode='HTML')
        except Exception:
            try: await c.bot.send_message(chat_id, re.sub(r'<[^>]+>', '', chunk))
            except Exception as e: print(f"Send error: {e}")
        await asyncio.sleep(0.3)

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
        await pg.wait_for_timeout(3000)
        try: await pg.keyboard.press("Escape")
        except: pass
    except Exception as e:
        print(f"Prepare page error: {e}")

async def click_round(pg, r_num):
    try:
        locator = pg.locator(f"text=/^(R|دور)\\s*{r_num}$/i").first
        if await locator.is_visible():
            await locator.click()
            await pg.wait_for_timeout(3000)
            return True
            
        clicked = await pg.evaluate(f"""
            () => {{
                let elements = Array.from(document.querySelectorAll('button, div, span, a'));
                for (let el of elements) {{
                    let text = el.innerText ? el.innerText.trim() : '';
                    if ((text === 'دور {r_num}' || text === 'R {r_num}' || text === '{r_num}' || text === 'R{r_num}') 
                        && !el.closest('table') && !el.closest('.v-data-table') && !el.closest('tr')) {{
                        let rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {{
                            el.click();
                            return true;
                        }}
                    }}
                }}
                return false;
            }}
        """)
        if clicked:
            await pg.wait_for_timeout(3000) 
    except Exception as e:
        print(f"Click round error: {e}")

async def _dump_rendered_html(pg, c, chat_id, r_num):
    try:
        html = await pg.content()
        fname = f"Table_HTML_R{r_num}.txt"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(html)
        with open(fname, "rb") as f:
            await c.bot.send_document(
                chat_id, document=f, filename=fname,
                caption=(f"🔍 <b>دیباگ HTML دور {r_num}</b>\n📏 حجم: {len(html):,} کاراکتر"),
                parse_mode='HTML'
            )
    except Exception as e:
        print(f"Dump HTML error: {e}")

# ============================================================
# Core Analysis (استخراج DOM بر اساس Tailwind)
# ============================================================
async def ar(p, r_num, c, chat_id, save_history=True):
    if r_num in AR_CACHE:
        ts, cached_res = AR_CACHE[r_num]
        if time.time() - ts < CACHE_TTL: return cached_res

    await click_round(p, r_num)
    horses = {}
    full_text = ""
    try:
        await p.wait_for_timeout(4000)
        full_text = await p.evaluate("() => document.body.innerText")
        
        rows_data = await p.evaluate("""
            () => {
                let rows = [];
                let buttons = Array.from(document.querySelectorAll('button'));
                
                for (let btn of buttons) {
                    let text = btn.innerText ? btn.innerText.trim() : '';
                    if (text.length < 2 || text.includes('دور') || text.includes('نمایش') || text.includes('R ')) continue;
                    
                    let divs = btn.querySelectorAll('div');
                    let texts = [];
                    divs.forEach(d => {
                        let t = d.innerText ? d.innerText.trim() : '';
                        if (t.length > 0 && !texts.includes(t)) texts.push(t);
                    });
                    
                    if (texts.length >= 3) {
                        rows.push(texts);
                    }
                }
                return rows;
            }
        """)

        if rows_data:
            for cells in rows_data:
                trans = str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789')
                cells = [c.translate(trans).strip() for c in cells if c.strip()]
                
                if len(cells) >= 3:
                    rank, name, jockey, weight, ior, perf, w_odd = 0, "", "", 0.0, 0, "", 50.0
                    
                    for val in cells:
                        if val.isdigit() and rank == 0 and int(val) < 20:
                            rank = int(val)
                        elif len(val) > 2 and not val.replace('.','',1).isdigit() and not name:
                            name = val
                        elif len(val) > 2 and not val.replace('.','',1).isdigit() and not jockey:
                            jockey = val
                        elif ('kg' in val or val.replace('.','',1).isdigit()) and weight == 0.0:
                            try: weight = float(val.replace('kg','').replace('کیلو','').strip())
                            except: pass
                        elif val.isdigit() and ior == 0 and 10 < int(val) < 200:
                            ior = int(val)
                        elif re.match(r'^\d+\-\d+', val) and not perf:
                            perf = val
                        elif re.match(r'^\d+(\.\d+)?$', val) and w_odd == 50.0:
                            w_odd = float(val)

                    if rank == 0 and name:
                        rank = len(horses) + 1

                    if rank > 0 and name:
                        horses[rank] = {"n": name, "num": rank, "ior": ior, "j": jockey, "perf": perf, "w_odd": w_odd, "weight": weight}

    except Exception as e: 
        print(f"Error in ar: {e}")
        await _dump_rendered_html(p, c, chat_id, r_num)
        await c.bot.send_message(chat_id, f"🚨 <b>خطای سیستمی در خواندن جدول دور {r_num}:</b>\n<code>{str(e)}</code>", parse_mode='HTML')
        return "ERROR"

    if not horses:
        snippet = full_text.replace('\n', ' | ')[:1500]
        await c.bot.send_message(chat_id, f"⚠️ <b>ربات جدول را پیدا نکرد. پیش‌نمایش متن صفحه:</b>\n<code>{snippet}</code>", parse_mode='HTML')
        await _dump_rendered_html(p, c, chat_id, r_num)
        return "NOT_UPLOADED"

    if len(horses) < 2: return "NO_DATA"

    horse_list = list(horses.values())
    n = len(horse_list)
    
    forms = [decayed_form(h['perf']) for h in horse_list]
    form_max = max(forms) if forms and max(forms) > 0 else 1
    
    implied_probs = [1.0 / h['w_odd'] if h['w_odd'] > 1 else 0 for h in horse_list]
    total_implied = sum(implied_probs) or 1
    market_probs = [p / total_implied for p in implied_probs]

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
# توابع پردازش ربات
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
                    rp += f"   📊 مدل: {w['model_prob']:.1f}% | بازار: {w['market_prob']:.1f}% (ضریب: {w['w_odd']})\n"
                    rp += f"🥈 <b>میان‌بر:</b> {res['p']['num']}-{res['p']['n']}\n"
                    rp += f"👥 <b>دوقلو (امن):</b> {res['q']}\n"
                    rp += f"🎯 <b>اطمینان:</b> {res['conf']}\n"
                    
                    if bet_amount and specific_round and r == specific_round:
                        rp += f"\n💰 <b>مدیریت سرمایه (کِلی)</b>\n"
                        bet_amt = calculate_kelly(bet_amount, w['model_prob'], w['w_odd'])
                        if bet_amt > 0:
                            rp += f"  پیشنهاد شرط روی {w['num']}-{w['n']}: <code>{bet_amt:,}</code> تومان\n"
                        else:
                            rp += f"  ⚠️ ارزش شرط‌بندی وجود ندارد (ریسک بالا).\n"
                    
                    if specific_round:
                        rp += "\n📈 <b>سه اسب برتر:</b>\n"
                        for h in res['horses'][:3]:
                            rp += f"  {h['num']}-{h['n']}: احتمال {h['model_prob']:.1f}% (ضریب: {h['w_odd']})\n"
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
                            if not ((i < 3) or (j < 3)): continue
                        if variant_type == 'q_lucky':
                            if not (i >= 3 and j >= 3): continue
                        q_p = quinella_prob(probs, i, j)
                        if q_p <= 0: continue
                        candidates.append({"h1": h1, "h2": h2, "prob": q_p * 100, "fair_odd": round(1 / q_p, 2)})
                if candidates:
                    if variant_type == 'q_safe': candidates.sort(key=lambda x: x['prob'], reverse=True)
                    else: candidates.sort(key=lambda x: x['fair_odd'], reverse=True)
                    best = candidates[0]
                    found_any = True
                    rp += f"\n🏁 <b>دور R{r}</b>\n"
                    rp += f"👥 <b>{best['h1']['num']}-{best['h1']['n']} + {best['h2']['num']}-{best['h2']['n']}</b>\n"
                    rp += f"   📊 احتمال برد: <b>{best['prob']:.1f}%</b>\n➖➖➖➖➖➖➖➖\n"
            if not found_any: rp += "\n⚠️ برای این نوع دوقلو، گزینه مناسبی پیدا نشد.\n"
            await send_long_message(c, chat_id, rp)
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطا:\n<code>{str(e)}</code>", parse_mode='HTML')
    finally:
        if browser: await browser.close()

async def process_multile
