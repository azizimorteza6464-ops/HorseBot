import os, re, httpx, sys, asyncio, json
from datetime import datetime
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
# GitHub Variables
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
                await client.post(f"https://api.github.com/repos/{R}/actions/variables",
                                  headers=headers, json={"name": var_name, "value": val})
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

# ============================================================
# Weather
# ============================================================
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

# ============================================================
# Advanced Probability Model
# ============================================================
JOCKEY_TIERS = {
    'S': ['رحمانی', 'قهرمانی', 'نوروزی'],
    'A': ['وحدانی', 'مرادی', 'صوفیان', 'خواجوی'],
    'B': ['آقاجانی', 'حیدری', 'نصیری', 'مسعودی'],
    'C': ['احمدی', 'رضایی'],
}
JOCKEY_RATINGS = {'S': 0.95, 'A': 0.75, 'B': 0.55, 'C': 0.40, 'D': 0.28}

def jockey_rating(name):
    for tier, names in JOCKEY_TIERS.items():
        if any(n in name for n in names):
            return JOCKEY_RATINGS[tier]
    return JOCKEY_RATINGS['D']

RANK_POINTS = {'1': 10, '2': 6, '3': 3, '4': 1, '5': 0.5}
def decayed_form(perf_str, decay=0.85):
    """Decay نمایی: مسابقه آخر (سمت چپ) بیشترین وزن."""
    if not perf_str: return 0
    ranks = perf_str.split('-')
    score = 0
    for i, r in enumerate(ranks):
        r = r.strip()
        if r in ['0', '-', 'D', 'W', '']:
            continue
        try:
            rank = int(r)
            score += RANK_POINTS.get(str(rank), 0) * (decay ** i)
        except: continue
    return score

def power_probs(scores, power=1.8):
    """Bradley-Terry/Power: تبدیل امتیاز به احتمال واقعی."""
    min_s = min(scores)
    if min_s <= 0:
        scores = [s - min_s + 0.01 for s in scores]
    powered = [s ** power for s in scores]
    total = sum(powered) or 1
    return [p / total for p in powered]

def normalize_market(odds_list):
    """
    حذف Overround از ضریب بازار.
    بازار شرط‌بندی معمولاً 15-25% overround دارد.
    خروجی: احتمالات بازار که جمعشان 1 می‌شود.
    """
    implied = [1.0 / o if o > 0 else 0 for o in odds_list]
    total = sum(implied) or 1
    return [i / total for i in implied]

def place_probability(probs, i, num_places=3):
    """
    Harville: احتمال اینکه اسب i در سه رتبه اول تمام کند.
    probs: list احتمالات برد (جمع = 1)
    """
    n = len(probs)
    p_i = probs[i]
    if p_i <= 0: return 0
    
    p1 = p_i  # اول
    p2 = sum(probs[j] * p_i / (1 - probs[j]) for j in range(n) if j != i and probs[j] < 1)
    p3 = 0
    if num_places >= 3:
        for j in range(n):
            if j == i or probs[j] >= 1: continue
            for k in range(n):
                if k == i or k == j or probs[j] + probs[k] >= 1: continue
                p3 += probs[j] * (probs[k] / (1 - probs[j])) * (p_i / (1 - probs[j] - probs[k]))
    return p1 + p2 + (p3 if num_places >= 3 else 0)

def quinella_prob(probs, i, j):
    """احتمال اینکه {i,j} به هر ترتیبی اول و دوم شوند."""
    p_i, p_j = probs[i], probs[j]
    if p_i + p_j >= 1 or p_i <= 0 or p_j <= 0: return 0
    return p_i * p_j / (1 - p_i) + p_j * p_i / (1 - p_j)

def exacta_prob(probs, i, j):
    """احتمال i اول و j دوم (به ترتیب)."""
    if probs[i] >= 1: return 0
    return probs[i] * probs[j] / (1 - probs[i])

def correlation_adjust(parlay_prob, n_legs, rho=0.05):
    """همبستگی مثبت مسابقات هم‌روز → احتمال joint بالاتر → ضریب منصفانه پایین‌تر."""
    if n_legs <= 1: return parlay_prob
    return min(parlay_prob * (1 + rho * (n_legs - 1)), 0.95)

# ============================================================
# Kelly with Bayesian Shrinkage
# ============================================================
def calculate_kelly(bankroll, model_prob, decimal_odds, fraction=0.25,
                    min_bet=1000, max_pct=0.05, confidence=0.55):
    """
    Kelly واقعی با Shrinkage.
    - confidence=0.55: 55% مدل + 45% بازار (بازار شرط‌بندی خیلی کارآمد است)
    - fraction=0.25: ¼ Kelly (استاندارد صنعت)
    - max_pct: سقف 5٪ از سرمایه
    """
    if decimal_odds <= 1 or bankroll <= 0: return 0
    
    # بازار با حذف overround (تقریبی)
    market_prob = 100 / decimal_odds
    
    # Shrinkage: مدل را به سمت بازار می‌بریم
    p_true = model_prob * confidence + market_prob * (1 - confidence)
    
    b = decimal_odds - 1
    p = p_true / 100
    q = 1 - p
    if p <= 0 or p >= 1: return 0
    f = (b * p - q) / b
    if f <= 0: return 0
    
    raw = min(bankroll * f * fraction, bankroll * max_pct)
    if raw < min_bet: return 0
    return int(raw // 1000) * 1000

# Fraction واقعی هر نوع شرط (ریسک متفاوت)
FRACTIONS = {'win': 0.25, 'place': 0.20, 'quinella': 0.15, 'exacta': 0.10,
             'trifecta': 0.05, 'parlay': 0.08}

# ============================================================
# Confidence (بهبودیافته)
# ============================================================
def get_confidence_level(res_dict):
    """اطمینان بر اساس توافق مدل با بازار + فاصله احتمالات."""
    w, p = res_dict['w'], res_dict['p']
    model_gap = w['model_prob'] - p['model_prob']
    market_gap = w['market_prob'] - p['market_prob']
    
    agree = 0
    if w['edge'] > 0: agree += 1
    if abs(w['model_prob'] - w['market_prob']) < 10: agree += 1
    if market_gap > 5: agree += 1
    
    if model_gap > 12 and agree >= 2: return "🔥 بالا"
    elif model_gap > 6: return "⚖️ متوسط"
    else: return "⚠️ پایین"

def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 روشن", callback_data='on'), InlineKeyboardButton("🔴 خاموش", callback_data='off')],
        [InlineKeyboardButton("🏁 دور ۱", callback_data='r1'), InlineKeyboardButton("🏁 دور ۲", callback_data='r2'), InlineKeyboardButton("🏁 دور ۳", callback_data='r3')],
        [InlineKeyboardButton("🏁 دور ۴", callback_data='r4'), InlineKeyboardButton("🏁 دور ۵", callback_data='r5'), InlineKeyboardButton("🏁 دور ۶", callback_data='r6')],
        [InlineKeyboardButton("🏁 دور ۷", callback_data='r7'), InlineKeyboardButton("📊 تحلیل کل", callback_data='rall')],
        [InlineKeyboardButton("💎 شکار ارزش", callback_data='value'), InlineKeyboardButton("📜 سابقه تحلیل‌ها", callback_data='history')],
        [InlineKeyboardButton("🎯 ۶ پره", callback_data='ml_6'), InlineKeyboardButton("🎯 ۵ پره", callback_data='ml_5')],
        [InlineKeyboardButton("🎯 ۳ پره اول", callback_data='ml_3a'), InlineKeyboardButton("🎯 ۳ پره دوم", callback_data='ml_3b')],
        [InlineKeyboardButton("🎲 اسب شگفتی", callback_data='sur_h'), InlineKeyboardButton("🔥 دوقلوی شگفتی", callback_data='sur_d')],
        [InlineKeyboardButton("💰 تعیین مبلغ شرط", callback_data='bet_set')],
        [InlineKeyboardButton("🌤 وضعیت هوا", callback_data='weather'), InlineKeyboardButton("🏙 تغییر مسیر/شهر", callback_data='change_city')],
        [InlineKeyboardButton("👑 پنل مدیریت", callback_data='admin_help')]
    ])

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
                    else:
                        current = current + '\n' + line if current else line
            else: current = block
        else:
            current = current + '\n\n' + block if current else block
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
    entry = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "round": r_num,
        "win": f"{res_dict['w']['num']}-{res_dict['w']['n']}",
        "quinella": res_dict['q'], "confidence": res_dict['conf']
    }
    history.insert(0, entry)
    history = history[:5]
    await set_github_var("BOT_HISTORY", json.dumps(history, ensure_ascii=False))

# ============================================================
# Admin Panel
# ============================================================
async def cmd_admin_panel(chat_id, c):
    auto_update = await get_github_var("AUTO_UPDATE", "true")
    status = "🟢 روشن" if auto_update == "true" else "🔴 خاموش"
    users = await get_allowed_users()
    users_str = "\n".join([f"• <code>{u}</code>" for u in users]) if users else "هیچ‌کس"
    bet = c.user_data.get('bet_amount', 0)
    bet_str = f"<code>{bet:,}</code> تومان" if bet else "تعیین نشده"
    txt = "👑 <b>پنل مدیریت</b>\n➖➖➖➖➖➖➖➖\n"
    txt += f"⚙️ <b>وضعیت:</b> {status}\n💰 <b>سرمایه:</b> {bet_str}\n"
    txt += f"👥 <b>کاربران ({len(users)}):</b>\n{users_str}\n\n"
    txt += "🔐 <b>تغییر توکن‌ها:</b>\n<code>Settings &gt; Secrets and variables</code>"
    await c.bot.send_message(chat_id, txt, parse_mode='HTML')

# ============================================================
# Page Prep
# ============================================================
async def prepare_page(pg):
    try:
        await pg.wait_for_timeout(2000)
        dropdown = pg.get_by_text("انتخاب فصل مسابقات", exact=False).first
        if await dropdown.is_visible():
            await dropdown.click(timeout=5000, force=True)
            await pg.wait_for_timeout(2000)
            first_option = pg.locator("ul li, .v-list-item, .dropdown-item, [role='option']").first
            try:
                await first_option.wait_for(state="visible", timeout=3000)
                await first_option.click()
                await pg.wait_for_timeout(3000)
            except:
                await pg.keyboard.press("Enter")
                await pg.wait_for_timeout(3000)
    except Exception as e: print(f"Track selection: {e}")

# ============================================================
# Round Analysis (CORE - بازنویسی‌شده)
# ============================================================
async def ar(p, r_num, c, chat_id, save_history=True):
    horses = {}
    try:
        try: await p.get_by_text("جدول مسابقات", exact=False).first.click(timeout=5000, force=True)
        except: pass
        try: await p.get_by_text("نمایش دورها", exact=False).first.click(timeout=5000, force=True)
        except: pass

        clicked = False
        for text in [f"R {r_num}", f"R{r_num}", str(r_num)]:
            try:
                await p.get_by_text(text, exact=True).first.click(timeout=3000, force=True)
                clicked = True; break
            except: pass
        if not clicked: return "NO_DATA"

        await p.wait_for_timeout(4000)
        table_text = await p.locator('body').inner_text()
        if "Kg" not in table_text: return "NO_DATA"

        pattern = r'([^\n]{3,20})\n?[^\n]*?\n?(\d{1,2})\s+(\d{2,3}Kg)\n?(\d{1,3})\n?([\d\-]+)\n?([^\n]+)'
        for m in re.finditer(pattern, table_text):
            name = m.group(1).strip()
            if any(k in name for k in ["مقام", "اسب", "سن", "عملکرد", "وزن", "مالک"]): continue
            num = int(m.group(2)); ior = int(m.group(4))
            perf_str = m.group(5); jockey = m.group(6).strip()
            horses[num] = {"n": name, "num": num, "ior": ior, "j": jockey,
                           "perf": perf_str, "w_odd": 50.0}

        try:
            await p.get_by_text("دورهای", exact=False).first.click(timeout=3000, force=True)
            await p.wait_for_timeout(2000)
            try: await p.get_by_text(f"R {r_num}", exact=True).first.click(timeout=3000, force=True)
            except: await p.get_by_text(str(r_num), exact=True).first.click(timeout=3000, force=True)
            await p.wait_for_timeout(3000)
            x_odds = await p.locator('body').inner_text()
            odds_pattern = r'([^\n]+)\n(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)'
            for m in re.finditer(odds_pattern, x_odds):
                name = m.group(1).strip(); w_odd = float(m.group(2))
                for num, h in horses.items():
                    if name in h['n'] or h['n'] in name:
                        horses[num]['w_odd'] = w_odd; break
        except: pass
    except Exception: return "ERROR"

    if not horses or len(horses) < 2: return "NO_DATA"

    horse_list = list(horses.values())
    n = len(horse_list)

    # ====== 1. نرمال‌سازی فاکتورها ======
    iors = [h['ior'] for h in horse_list]
    ior_min, ior_max = min(iors), max(iors)
    ior_range = max(ior_max - ior_min, 1)

    forms = [decayed_form(h['perf']) for h in horse_list]
    form_max = max(forms) if forms and max(forms) > 0 else 1

    # ====== 2. احتمال بازار با حذف Overround ======
    market_probs = normalize_market([h['w_odd'] for h in horse_list])

    # ====== 3. امتیاز ترکیبی با وزن‌های توجیه‌شده ======
    for idx, h in enumerate(horse_list):
        ior_norm = (h['ior'] - ior_min) / ior_range
        form_norm = decayed_form(h['perf']) / form_max
        jockey_norm = jockey_rating(h['j'])
        market_norm = market_probs[idx]
        
        # وزن‌ها: بازار ۴۰٪ (کارآمدترین)، IOR ۲۵٪، فرم ۲۰٪، سوارکار ۱۰٪، شرایط ۵٪
        h['combined'] = (
            0.40 * market_norm +
            0.25 * ior_norm +
            0.20 * form_norm +
            0.10 * jockey_norm +
            0.05 * 0.5  # placeholder شرایط مسیر
        )
        h['market_prob'] = market_probs[idx] * 100

    # ====== 4. تبدیل به احتمال با Power Method ======
    model_raw = power_probs([h['combined'] for h in horse_list], power=1.8)

    # ====== 5. Blending نهایی (مدل + بازار) ======
    ALPHA = 0.35  # وزن مدل (بازار 65%)
    for idx, h in enumerate(horse_list):
        blended = ALPHA * model_raw[idx] + (1 - ALPHA) * market_probs[idx]
        h['model_prob'] = blended * 100
        h['edge'] = h['model_prob'] - h['market_prob']
        h['final_sc'] = h['model_prob']

    # نرمال‌سازی نهایی
    total_p = sum(h['model_prob'] for h in horse_list) or 1
    for h in horse_list: h['model_prob'] = (h['model_prob'] / total_p) * 100

    sorted_horses = sorted(horse_list, key=lambda y: y['model_prob'], reverse=True)
    probs_norm = [h['model_prob'] / 100 for h in sorted_horses]

    # ====== 6. Harville: احتمال Place برای هر اسب ======
    for idx, h in enumerate(sorted_horses):
        h['place_prob'] = place_probability(probs_norm, idx, num_places=3) * 100
        h['place_fair_odd'] = round(100 / h['place_prob'], 2) if h['place_prob'] > 0 else 0

    w = sorted_horses[0]
    p_horse = sorted_horses[1] if n > 1 else w
    s_horse = sorted_horses[2] if n > 2 else p_horse

    # ====== 7. دوقلو دقیق ======
    q_prob = quinella_prob(probs_norm, 0, 1)
    q_fair = round(1 / q_prob, 2) if q_prob > 0 else 0

    # ====== 8. Exacta (اول-دوم ترتیبی) ======
    ex_prob = exacta_prob(probs_norm, 0, 1)
    ex_fair = round(1 / ex_prob, 2) if ex_prob > 0 else 0

    # ====== 9. Trifecta (سه تای اول ترتیبی) ======
    trif_prob = 0
    if n >= 3 and probs_norm[0] < 1 and probs_norm[0] + probs_norm[1] < 1:
        trif_prob = probs_norm[0] * (probs_norm[1] / (1 - probs_norm[0])) * (probs_norm[2] / (1 - probs_norm[0] - probs_norm[1]))
    trif_fair = round(1 / trif_prob, 1) if trif_prob > 0 else 0

    res_dict = {
        "w": w, "p": p_horse, "s": s_horse, "horses": sorted_horses,
        "q": f"{w['num']}-{w['n']} + {p_horse['num']}-{p_horse['n']}",
        "q_fair": q_fair, "ex_fair": ex_fair, "trif_fair": trif_fair,
    }
    res_dict['conf'] = get_confidence_level(res_dict)

    # شگفتی: Long-shot با بهترین مدل‌پروب نسبی
    long_shots = [h for h in sorted_horses if h['w_odd'] >= 12]
    if long_shots:
        top_ls = sorted(long_shots, key=lambda x: x['model_prob'], reverse=True)
    else:
        top_ls = sorted_horses[-2:]
    res_dict['su'] = top_ls[0] if top_ls else sorted_horses[-1]
    if len(top_ls) >= 2:
        res_dict['qs'] = f"{top_ls[0]['num']}-{top_ls[0]['n']} + {top_ls[1]['num']}-{top_ls[1]['n']}"
    else:
        res_dict['qs'] = "N/A"

    if save_history: await save_to_history(res_dict, r_num)
    return res_dict

# ============================================================
# Format round result
# ============================================================
def format_round_result(res, r, bet_amount=None, show_details=True):
    """خروجی HTML برای یک دور با تمام بازارها."""
    rp = f"\n🏁 <b>دور R{r}</b>\n"
    w = res['w']
    rp += f"🥇 <b>پیش‌بر:</b> {w['num']}-{w['n']} (ضریب {w['w_odd']})\n"
    rp += f"   📊 احتمال مدل: <b>{w['model_prob']:.1f}%</b> | بازار: {w['market_prob']:.1f}% | Edge: {w['edge']:+.1f}%\n"
    rp += f"🥈 <b>میان‌بر:</b> {res['p']['num']}-{res['p']['n']} (احتمال {res['p']['model_prob']:.1f}%)\n"
    rp += f"👥 <b>دوقلو:</b> {res['q']} → ضریب منصفانه <code>{res['q_fair']}</code>\n"
    rp += f"🎯 <b>اطمینان:</b> {res['conf']}\n"

    if show_details and bet_amount:
        rp += f"\n💰 <b>مدیریت سرمایه ({bet_amount:,} ت)</b>\n"
        
        # Win
        k_win = calculate_kelly(bet_amount, w['model_prob'], w['w_odd'],
                                fraction=FRACTIONS['win'])
        if k_win > 0:
            rp += f"🥇 برد {w['n']}: <code>{k_win:,}</code> ت\n"
        
        # Place (اول-سوم)
        w_place_odd = round((w['w_odd'] - 1) / 4 + 1, 2)  # تخمین ¼
        if w['place_prob'] > 0 and w_place_odd > 1:
            k_place = calculate_kelly(bet_amount, w['place_prob'], w_place_odd,
                                      fraction=FRACTIONS['place'], confidence=0.60)
            if k_place > 0:
                rp += f"🥈 جایگاه {w['n']} (ضریب تخمین {w_place_odd}): <code>{k_place:,}</code> ت\n"
        
        # دوقلو
        if res['q_fair'] > 0:
            k_q = calculate_kelly(bet_amount, 100/res['q_fair'], res['q_fair'],
                                  fraction=FRACTIONS['quinella'], confidence=0.45)
            if k_q > 0:
                rp += f"👥 دوقلو (منصفانه {res['q_fair']}): <code>{k_q:,}</code> ت\n"
        
        # Exacta
        if res['ex_fair'] > 0 and res['ex_fair'] < 500:
            k_ex = calculate_kelly(bet_amount, 100/res['ex_fair'], res['ex_fair'],
                                   fraction=FRACTIONS['exacta'], confidence=0.40)
            if k_ex > 0:
                rp += f"🎯 Exacta (منصفانه {res['ex_fair']}): <code>{k_ex:,}</code> ت\n"
        
        # بقیه اسب‌های دارای ارزش
        extras = [h for h in res['horses'][1:] if h['edge'] >= 5 and h['w_odd'] >= 3][:2]
        for h in extras:
            k = calculate_kelly(bet_amount, h['model_prob'], h['w_odd'], fraction=FRACTIONS['win'])
            if k > 0:
                rp += f"💎 {h['n']} (Edge {h['edge']:+.1f}%): <code>{k:,}</code> ت\n"
        
        if k_win == 0 and not extras:
            rp += "⚠️ شرط ارزشمند پیدا نشد.\n"
    
    # بهترین اسب‌ها با Place odds
    if show_details:
        rp += "\n📈 <b>سه اسب برتر (Place):</b>\n"
        for h in res['horses'][:3]:
            rp += f"  {h['num']}-{h['n']}: برد {h['model_prob']:.1f}% | جایگاه {h['place_prob']:.1f}% (ضریب منصفانه {h['place_fair_odd']})\n"

    rp += "➖➖➖➖➖➖➖➖\n"
    return rp

# ============================================================
# Odds Processing
# ============================================================
async def process_odds(chat_id, c, specific_round=None, all_rounds=False, bet_amount=None):
    if await get_github_var("AUTO_UPDATE", "true") == "false":
        return await c.bot.send_message(chat_id, "🛑 خاموش است.")
    await c.bot.send_message(chat_id, "⏳ <b>در حال تحلیل...</b>", parse_mode='HTML')
    weather = await get_weather()
    browser = None
    try:
        async with ap() as p:
            browser = await p.chromium.launch(headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            context = await browser.new_context(viewport={'width': 1440, 'height': 900})
            await context.add_cookies([
                {'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}
            ])
            pg = await context.new_page()
            pg.set_default_timeout(60000)
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000, wait_until="domcontentloaded")
            await pg.wait_for_timeout(5000)
            if "ورود" in await pg.locator('body').inner_text():
                await c.bot.send_message(chat_id,
                    "⚠️ <b>کوکی‌ها منقضی شده‌اند.</b>", parse_mode='HTML')
                return
            await prepare_page(pg)

            header = f"🏇 <b>تحلیل جامع</b>\n{weather}\n"
            if bet_amount:
                header += f"💰 سرمایه: <code>{bet_amount:,}</code> ت | Shrinkage Kelly\n"
            rp = header + "➖➖➖➖➖➖➖➖\n"

            rounds = range(1, 8) if all_rounds else ([specific_round] if specific_round else [])
            save_h = specific_round is not None
            found_any = False
            for r in rounds:
                res = await ar(pg, r, c, chat_id, save_history=save_h)
                if isinstance(res, dict):
                    found_any = True
                    rp += format_round_result(res, r, bet_amount,
                                              show_details=(specific_round is not None))
            if not found_any:
                rp += "\n⚠️ <b>جدول آپلود نشده یا خطا در خواندن داده‌ها.</b>"
            await send_long_message(c, chat_id, rp)
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطا:\n<code>{str(e)}</code>", parse_mode='HTML')
    finally:
        if browser: await browser.close()

# ============================================================
# Multileg با همبستگی
# ============================================================
async def process_multileg(chat_id, c, leg_type, bet_amount=None):
    await c.bot.send_message(chat_id, "⏳ <b>در حال بررسی...</b>", parse_mode='HTML')
    browser = None
    try:
        async with ap() as p:
            browser = await p.chromium.launch(headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            context = await browser.new_context()
            await context.add_cookies([
                {'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}
            ])
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

            if len(details) == len(tr) and all(h['model_prob'] > 0 for h in details):
                # احتمال مشترک مستقل
                prob_product = 1.0
                for h in details: prob_product *= h['model_prob'] / 100
                
                # تصحیح همبستگی
                prob_adj = correlation_adjust(prob_product, len(details), rho=0.06)
                fair_odd = round(1 / prob_adj, 1) if prob_adj > 0 else 0
                
                # ضریب واقعی سایت
                actual_odd = 1.0
                for h in details: actual_odd *= h['w_odd']
                actual_odd = round(actual_odd, 1)
                
                msg += f"\n📊 <b>احتمال مدل:</b> {prob_product*100:.3f}% (با همبستگی: {prob_adj*100:.3f}%)\n"
                msg += f"📊 <b>ضریب منصفانه (با تصحیح همبستگی):</b> <code>{fair_odd:,.1f}</code>\n"
                msg += f"📊 <b>ضریب سایت:</b> <code>{actual_odd:,.1f}</code>\n"
                
                if fair_odd > 0 and actual_odd > fair_odd:
                    edge_pct = (actual_odd / fair_odd - 1) * 100
                    msg += f"✅ <b>ارزشمند</b> (Edge: +{edge_pct:.1f}%)\n"
                    if bet_amount:
                        k = calculate_kelly(bet_amount, 100/fair_odd, actual_odd,
                                            fraction=FRACTIONS['parlay'],
                                            max_pct=0.02, confidence=0.40)
                        if k > 0:
                            msg += f"💰 پیشنهاد (8% Kelly، سقف ۲٪): <code>{k:,}</code> ت\n"
                        else:
                            msg += "💡 ریسک خیلی بالا → مبلغ ناچیز\n"
                else:
                    msg += f"⚠️ <b>بدون ارزش</b> (سایت کمتر از منصفانه می‌دهد)\n"
            await c.bot.send_message(chat_id, msg, parse_mode='HTML')
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطا: {str(e)}")
    finally:
        if browser: await browser.close()

# ============================================================
# Surprises
# ============================================================
async def process_surprises(chat_id, c, mode, bet_amount=None):
    await c.bot.send_message(chat_id, "⏳ <b>در حال پیدا کردن شگفتی...</b>", parse_mode='HTML')
    browser = None
    try:
        async with ap() as p:
            browser = await p.chromium.launch(headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            context = await browser.new_context()
            await context.add_cookies([
                {'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}
            ])
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
            msg = f"{title}\n➖➖➖➖➖➖\n"
            if bet_amount: msg += f"💰 سرمایه: <code>{bet_amount:,}</code> ت\n"
            msg += "\n"

            for r in range(1, max_r + 1):
                res = res_7 if r == 7 and isinstance(res_7, dict) else await ar(pg, r, c, chat_id, save_history=False)
                if isinstance(res, dict):
                    if mode == 'sur_h':
                        sh = res['su']
                        line = f"🏁 <b>دور {r}:</b> {sh['num']}-{sh['n']} (ضریب {sh['w_odd']}, احتمال {sh['model_prob']:.1f}%)"
                        if bet_amount and sh['edge'] > 0:
                            k = calculate_kelly(bet_amount, sh['model_prob'], sh['w_odd'],
                                                fraction=FRACTIONS['win'])
                            if k > 0: line += f" → <code>{k:,}</code> ت"
                        msg += line + "\n"
                    else:
                        msg += f"🏁 <b>دور {r}:</b> {res['qs']}\n"
            await c.bot.send_message(chat_id, msg, parse_mode='HTML')
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطا: {str(e)}")
    finally:
        if browser: await browser.close()

# ============================================================
# Value Bet
# ============================================================
async def process_value_bets(chat_id, c, bet_amount=None):
    if await get_github_var("AUTO_UPDATE", "true") == "false":
        return await c.bot.send_message(chat_id, "🛑 خاموش است.")
    await c.bot.send_message(chat_id, "⏳ <b>در حال شکار ارزش...</b>", parse_mode='HTML')
    weather = await get_weather()
    browser = None
    try:
        async with ap() as p:
            browser = await p.chromium.launch(headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            context = await browser.new_context(viewport={'width': 1440, 'height': 900})
            await context.add_cookies([
                {'name': 'auth_token', 'value': A, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': 'device_id', 'value': D, 'domain': '.asbdavani.app', 'path': '/'},
                {'name': '_udi', 'value': U, 'domain': '.asbdavani.app', 'path': '/'}
            ])
            pg = await context.new_page()
            pg.set_default_timeout(60000)
            await pg.goto("https://mosharekat.asbdavani.app/", timeout=90000, wait_until="domcontentloaded")
            await pg.wait_for_timeout(5000)
            if "ورود" in await pg.locator('body').inner_text():
                await c.bot.send_message(chat_id, "⚠️ کوکی‌ها منقضی شده‌اند.", parse_mode='HTML')
                return
            await prepare_page(pg)

            header = f"💎 <b>شکار ارزش</b>\n{weather}\n"
            if bet_amount: header += f"💰 سرمایه: <code>{bet_amount:,}</code> ت\n"
            rp = header + "➖➖➖➖➖➖➖➖\n"

            found_any = False
            for r in range(1, 8):
                res = await ar(pg, r, c, chat_id, save_history=False)
                if isinstance(res, dict):
                    # Value: Edge ≥ 5٪ + احتمال بازار معقول + ضریب > 3
                    values = [h for h in res['horses']
                              if h['edge'] >= 5 and h['w_odd'] > 3 and h['model_prob'] > 5]
                    if values:
                        found_any = True
                        rp += f"\n🏁 <b>دور R{r}:</b>\n"
                        for v in values[:3]:
                            line = f"💎 {v['num']}-{v['n']} | ضریب {v['w_odd']} | مدل {v['model_prob']:.1f}% vs بازار {v['market_prob']:.1f}% | Edge <b>{v['edge']:+.1f}%</b>"
                            if bet_amount:
                                k = calculate_kelly(bet_amount, v['model_prob'], v['w_odd'],
                                                    fraction=FRACTIONS['win'])
                                if k > 0: line += f" → <code>{k:,}</code> ت"
                            rp += line + "\n"
                        rp += "➖➖➖➖➖➖➖➖\n"
            if not found_any:
                rp += "\n⚠️ <b>هیچ اسبی با ارزش (Edge ≥ 5٪) پیدا نشد.</b>"
            await send_long_message(c, chat_id, rp)
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ خطا:\n<code>{str(e)}</code>", parse_mode='HTML')
    finally:
        if browser: await browser.close()

# ============================================================
# History & Router
# ============================================================
async def cmd_history(chat_id, c):
    history_str = await get_github_var("BOT_HISTORY", "[]")
    try: history = json.loads(history_str)
    except: history = []
    if not history: return await c.bot.send_message(chat_id, "📜 تاریخچه‌ای نیست.")
    txt = "📜 <b>۵ تحلیل آخر:</b>\n\n"
    for item in history:
        txt += f"📅 {item['date']} | دور {item['round']}\n🥇 {item['win']}\n👥 {item['quinella']}\n🎯 {item['confidence']}\n➖➖➖➖\n"
    await c.bot.send_message(chat_id, txt, parse_mode='HTML')

async def button_callback(u: Update, c: C):
    query = u.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data
    if not await is_authorized(u, c): return

    if data == 'on':
        await set_github_var("AUTO_UPDATE", "true"); await query.message.reply_text("✅ روشن شد.")
    elif data == 'off':
        await set_github_var("AUTO_UPDATE", "false"); await query.message.reply_text("🛑 خاموش شد.")
    elif data == 'history': await cmd_history(chat_id, c)
    elif data == 'weather': await query.message.reply_text(await get_weather(), parse_mode='HTML')
    elif data == 'value': await process_value_bets(chat_id, c, bet_amount=c.user_data.get('bet_amount'))
    elif data == 'bet_set':
        c.user_data['awaiting_bet'] = True
        cur = c.user_data.get('bet_amount', 0)
        await query.message.reply_text(
            f"💰 <b>سرمایه فعلی:</b> <code>{cur:,}</code> ت\nعدد جدید را بفرست:",
            parse_mode='HTML', reply_markup=ForceReply(selective=True))
    elif data == 'change_city':
        await query.message.reply_text("🏙 جدیدترین مسابقه خودکار انتخاب می‌شود.", parse_mode='HTML')
    elif data == 'admin_help': await cmd_admin_panel(chat_id, c)
    elif data.startswith('r'):
        bet = c.user_data.get('bet_amount')
        if data == 'rall': await process_odds(chat_id, c, all_rounds=True)
        else: await process_odds(chat_id, c, specific_round=int(data[1:]), bet_amount=bet)
    elif data.startswith('ml_'):
        await process_multileg(chat_id, c, data, bet_amount=c.user_data.get('bet_amount'))
    elif data in ['sur_h', 'sur_d']:
        await process_surprises(chat_id, c, data, bet_amount=c.user_data.get('bet_amount'))

async def handle_bet_input(u: Update, c: C):
    if not c.user_data.get('awaiting_bet'): return
    if not await is_authorized(u, c): return
    c.user_data['awaiting_bet'] = False
    try:
        amount = int(re.sub(r'[^\d]', '', u.message.text))
        if amount < 1000: raise ValueError
        c.user_data['bet_amount'] = amount
        await u.message.reply_text(f"✅ سرمایه: <code>{amount:,}</code> ت", parse_mode='HTML')
    except:
        await u.message.reply_text("❌ عدد معتبر (≥ ۱۰۰۰ تومان) بفرست.")

async def cmd_start(u: Update, c: C):
    if not await is_authorized(u, c): return
    await c.bot.send_message(
        u.effective_chat.id,
        "👋 <b>سلام مدیر!</b>\n"
        "نسخه جدید با تحلیل واقعی:\n"
        "✅ مدل احتمالاتی Power Method\n"
        "✅ حذف Overround بازار\n"
        "✅ Kelly با Shrinkage (55% مدل + 45% بازار)\n"
        "✅ Harville برای جایگاه/دوقلو/Exacta/Trifecta\n"
        "✅ تصحیح همبستگی پارلی\n"
        "✅ فرم با Decay نمایی\n"
        "✅ ۵ سطح سوارکار",
        reply_markup=get_main_menu(), parse_mode='HTML')

async def main():
    app = AB().token(T).build()
    app.add_handler(CH('start', cmd_start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_bet_input))
    await app.initialize(); await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.sleep(17400)
    await app.updater.stop(); await app.stop(); await app.shutdown()

if __name__ == "__main__":
    try: asyncio.run(main())
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        sys.exit(1)
