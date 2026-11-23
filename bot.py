import os
import re
import sys
import json
import html
import time
import asyncio
from datetime import datetime, timedelta, date

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from playwright.async_api import async_playwright

# ============================================================
# تنظیمات و متغیرهای محیطی
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
USER_TOKEN = os.getenv("USER_TOKEN", "").strip()
USER_DEVICE_ID = os.getenv("USER_DEVICE_ID", "").strip()
USER_UDI = os.getenv("USER_UDI", "").strip()
GH_PAT = os.getenv("GH_PAT", "").strip()
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "").strip()
MY_ID = "184149177"

CARD_NUMBER = "6219-8618-0229-0089"
CARD_HOLDER = "مرتضی عزیزی"
SUB_PRICE = "250,000"
REF_REWARD_DAYS = 3
BOT_RUN_SECONDS = int(os.getenv("BOT_RUN_SECONDS", "17400"))

_last_call = {}
COOLDOWN = 60
AR_CACHE = {}
CACHE_TTL = 600

# ============================================================
# GitHub Actions Variables
# ============================================================
async def get_github_var(var_name, default_val=""):
    if not GITHUB_REPOSITORY or not GH_PAT:
        return default_val
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/variables/{var_name}"
    headers = {"Authorization": f"Bearer {GH_PAT}", "Accept": "application/vnd.github+json"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                return res.json().get("value", default_val)
    except Exception as exc:
        print(f"GitHub get variable error: {exc}")
    return default_val


async def set_github_var(var_name, val):
    if not GITHUB_REPOSITORY or not GH_PAT:
        print("GitHub repository or PAT is missing")
        return False
    base = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/variables"
    url = f"{base}/{var_name}"
    headers = {"Authorization": f"Bearer {GH_PAT}", "Accept": "application/vnd.github+json"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            check = await client.get(url, headers=headers)
            if check.status_code == 404:
                res = await client.post(base, headers=headers, json={"name": var_name, "value": str(val)})
            elif check.status_code == 200:
                res = await client.patch(url, headers=headers, json={"name": var_name, "value": str(val)})
            else:
                print(f"GitHub check variable failed: {check.status_code} {check.text[:300]}")
                return False
            if res.status_code in (201, 204):
                return True
            print(f"GitHub set variable failed: {res.status_code} {res.text[:300]}")
    except Exception as exc:
        print(f"GitHub set variable error: {exc}")
    return False


async def get_allowed_users_dict():
    users_str = await get_github_var("ALLOWED_USERS", "{}")
    try:
        data = json.loads(users_str)
        return data if isinstance(data, dict) else {}
    except Exception:
        users = {}
        if users_str and ("," in users_str or users_str.strip().isdigit()):
            default_exp = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            for uid in users_str.split(","):
                if uid.strip():
                    users[uid.strip()] = default_exp
        return users


async def is_premium(chat_id):
    if str(chat_id) == MY_ID:
        return True
    users = await get_allowed_users_dict()
    exp = users.get(str(chat_id))
    if not exp:
        return False
    try:
        return datetime.now() <= datetime.strptime(exp, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return False


async def add_vip_days(target_id, days):
    users = await get_allowed_users_dict()
    now = datetime.now()
    try:
        current_exp = datetime.strptime(users.get(str(target_id), ""), "%Y-%m-%d %H:%M:%S")
        if current_exp < now:
            current_exp = now
    except Exception:
        current_exp = now
    new_exp = (current_exp + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    users[str(target_id)] = new_exp
    await set_github_var("ALLOWED_USERS", json.dumps(users, ensure_ascii=False))
    return new_exp


async def get_referrals():
    try:
        data = json.loads(await get_github_var("REFERRALS", "{}"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

# ============================================================
# تحلیل و محاسبات
# ============================================================
JOCKEY_TIERS = {
    "S": ["رحمانی", "قهرمانی", "نوروزی"],
    "A": ["وحدانی", "مرادی", "صوفیان", "خواجوی"],
    "B": ["آقاجانی", "حیدری", "نصیری", "مسعودی"],
    "C": ["احمدی", "رضایی"],
}
JOCKEY_RATINGS = {"S": 0.95, "A": 0.75, "B": 0.55, "C": 0.40, "D": 0.28}
RANK_POINTS = {"1": 10, "2": 6, "3": 3, "4": 1, "5": 0.5}
FRACTIONS = {"win": 0.25, "place": 0.20, "quinella": 0.15, "exacta": 0.10, "trifecta": 0.05, "parlay": 0.08}


def jockey_rating(name):
    name = str(name or "")
    for tier, names in JOCKEY_TIERS.items():
        if any(n in name for n in names):
            return JOCKEY_RATINGS[tier]
    return JOCKEY_RATINGS["D"]


def decayed_form(perf_str, decay=0.85):
    if not perf_str:
        return 0
    total = 0
    for i, rank in enumerate(str(perf_str).split("-")):
        rank = rank.strip()
        if rank.isdigit():
            total += RANK_POINTS.get(rank, 0) * (decay ** i)
    return total


def power_probs(scores, power=1.8):
    if not scores:
        return []
    min_score = min(scores)
    adjusted = [s - min_score + 0.01 for s in scores] if min_score <= 0 else list(scores)
    powered = [s ** power for s in adjusted]
    total = sum(powered) or 1
    return [x / total for x in powered]


def normalize_market(odds_list):
    implied = [(1.0 / o) if o and o > 0 else 0 for o in odds_list]
    total = sum(implied) or 1
    return [x / total for x in implied]


def place_probability(probs, index, num_places=3):
    # تقريب احتمالية دخول الحصان ضمن أول 2 أو 3 مراكز من ترتيب احتمالي مبسط.
    n = len(probs)
    p_i = probs[index]
    if p_i <= 0:
        return 0.0
    result = p_i
    for j in range(n):
        if j == index or probs[j] >= 1:
            continue
        result += probs[j] * (p_i / max(1e-9, 1 - probs[j]))
    if num_places >= 3:
        for j in range(n):
            if j == index or probs[j] >= 1:
                continue
            for k in range(n):
                if k == index or k == j or probs[j] + probs[k] >= 1:
                    continue
                result += probs[j] * (probs[k] / max(1e-9, 1 - probs[j])) * (
                    p_i / max(1e-9, 1 - probs[j] - probs[k])
                )
    return min(result, 1.0)


def quinella_prob(probs, i, j):
    pi, pj = probs[i], probs[j]
    if pi <= 0 or pj <= 0 or pi + pj >= 1:
        return 0.0
    return pi * pj / (1 - pi) + pj * pi / (1 - pj)


def calculate_kelly(bankroll, model_prob, decimal_odds, fraction=0.25, min_bet=1000, max_pct=0.05, confidence=0.55):
    if decimal_odds <= 1 or bankroll <= 0:
        return 0
    market_prob = 100 / decimal_odds
    true_prob = model_prob * confidence + market_prob * (1 - confidence)
    p = true_prob / 100
    if p <= 0 or p >= 1:
        return 0
    b = decimal_odds - 1
    f = (b * p - (1 - p)) / b
    if f <= 0:
        return 0
    raw = min(bankroll * f * fraction, bankroll * max_pct)
    return int(raw // 1000) * 1000 if raw >= min_bet else 0


def get_confidence_level(res_dict):
    w, p = res_dict["w"], res_dict["p"]
    gap = w["model_prob"] - p["model_prob"]
    agree = (1 if w["edge"] > 0 else 0) + (1 if abs(w["model_prob"] - w["market_prob"]) < 10 else 0)
    if gap > 12 and agree >= 2:
        return "🔥 بالا"
    if gap > 6:
        return "⚖️ متوسط"
    return "⚠️ پایین"

# ============================================================
# منو و ابزار پیام
# ============================================================
def get_main_menu(chat_id):
    keyboard = []
    if str(chat_id) == MY_ID:
        keyboard.append([
            InlineKeyboardButton("🟢 روشن", callback_data="on"),
            InlineKeyboardButton("🔴 خاموش", callback_data="off"),
        ])
    keyboard.extend([
        [InlineKeyboardButton("🏁 دور ۱", callback_data="r1"), InlineKeyboardButton("🏁 دور ۲", callback_data="r2"), InlineKeyboardButton("🏁 دور ۳", callback_data="r3")],
        [InlineKeyboardButton("🏁 دور ۴", callback_data="r4"), InlineKeyboardButton("🏁 دور ۵", callback_data="r5"), InlineKeyboardButton("🏁 دور ۶", callback_data="r6")],
        [InlineKeyboardButton("🏁 دور ۷", callback_data="r7"), InlineKeyboardButton("📊 تحلیل کل", callback_data="rall")],
        [InlineKeyboardButton("🟢 دوقلوی امن", callback_data="q_safe"), InlineKeyboardButton("💎 دوقلوی ارزش", callback_data="q_value"), InlineKeyboardButton("🍀 دوقلوی شانسی", callback_data="q_lucky")],
        [InlineKeyboardButton("💎 شکار ارزش", callback_data="value"), InlineKeyboardButton("📜 سابقه تحلیل‌ها", callback_data="history")],
        [InlineKeyboardButton("🎯 ۶ پره", callback_data="ml_6"), InlineKeyboardButton("🎯 ۵ پره", callback_data="ml_5")],
        [InlineKeyboardButton("🎯 ۳ پره اول", callback_data="ml_3a"), InlineKeyboardButton("🎯 ۳ پره دوم", callback_data="ml_3b")],
        [InlineKeyboardButton("🎲 اسب شگفتی", callback_data="sur_h"), InlineKeyboardButton("🔥 دوقلوی شگفتی", callback_data="sur_d")],
        [InlineKeyboardButton("💰 تعیین مبلغ شرط", callback_data="bet_set")],
        [InlineKeyboardButton("💳 خرید اشتراک / تمدید", callback_data="buy_sub"), InlineKeyboardButton("👤 وضعیت اشتراک من", callback_data="my_status")],
        [InlineKeyboardButton("🎁 دعوت از دوستان (هدیه)", callback_data="invite")],
        [InlineKeyboardButton("🌤 وضعیت هوا", callback_data="weather"), InlineKeyboardButton("🏙 تغییر مسیر/شهر", callback_data="change_city")],
    ])
    if str(chat_id) == MY_ID:
        keyboard.append([InlineKeyboardButton("👑 پنل مدیریت", callback_data="admin_help")])
    return InlineKeyboardMarkup(keyboard)


def split_safe(message, max_len=4000):
    if len(message) <= max_len:
        return [message]
    chunks, current = [], ""
    for block in message.split("\n\n"):
        if len(current) + len(block) + 2 > max_len:
            if current:
                chunks.append(current)
            current = block
        else:
            current = current + "\n\n" + block if current else block
    if current:
        chunks.append(current)
    return chunks


async def send_long_message(context, chat_id, message, max_len=4000):
    for chunk in split_safe(message, max_len):
        try:
            await context.bot.send_message(chat_id, chunk, parse_mode="HTML")
        except Exception:
            clean = re.sub(r"<[^>]+>", "", chunk)
            await context.bot.send_message(chat_id, clean)
        await asyncio.sleep(0.2)


async def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=37.25&longitude=55.16&current_weather=true"
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url)
            response.raise_for_status()
            weather = response.json()["current_weather"]
            return f"🌡 دما: {weather['temperature']}°C | 💨 باد: {weather['windspeed']} km/h"
    except Exception:
        return "⚠️ اطلاعات هوا در دسترس نیست."


async def save_to_history(res_dict, round_num):
    try:
        history = json.loads(await get_github_var("BOT_HISTORY", "[]"))
        if not isinstance(history, list):
            history = []
    except Exception:
        history = []
    history.insert(0, {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "round": round_num,
        "win": f"{res_dict['w']['num']}-{res_dict['w']['n']}",
        "quinella": res_dict["q"],
        "confidence": res_dict["conf"],
    })
    await set_github_var("BOT_HISTORY", json.dumps(history[:5], ensure_ascii=False))


async def cmd_history(chat_id, context):
    try:
        history = json.loads(await get_github_var("BOT_HISTORY", "[]"))
    except Exception:
        history = []
    if not history:
        await context.bot.send_message(chat_id, "📜 تاریخچه‌ای ثبت نشده است.")
        return
    txt = "📜 <b>۵ تحلیل آخر:</b>\n\n"
    for item in history:
        txt += f"📅 {item.get('date', '-')} | دور {item.get('round', '-')}\n🥇 {item.get('win', '-')}\n👥 {item.get('quinella', '-')}\n🎯 {item.get('confidence', '-')}\n➖➖➖➖\n"
    await context.bot.send_message(chat_id, txt, parse_mode="HTML")

# ============================================================
# پنل مدیریت
# ============================================================
async def cmd_admin_panel(chat_id, context):
    auto_update = await get_github_var("AUTO_UPDATE", "true")
    users = await get_allowed_users_dict()
    bet = context.user_data.get("bet_amount", 0)
    status = "🟢 روشن" if auto_update == "true" else "🔴 خاموش"
    txt = (
        "👑 <b>پنل مدیریت</b>\n➖➖➖➖➖➖➖➖\n"
        f"⚙️ <b>وضعیت:</b> {status}\n"
        f"💰 <b>سرمایه:</b> {f'<code>{bet:,}</code> تومان' if bet else 'تعیین نشده'}\n\n"
        "👇 <b>لیست کاربران VIP:</b>"
    )
    keyboard = [[InlineKeyboardButton("🔄 بروزرسانی لیست", callback_data="admin_refresh")]]
    now = datetime.now()
    if not users:
        keyboard.append([InlineKeyboardButton("لیست خالی است", callback_data="ignore")])
    else:
        for uid, exp_str in users.items():
            try:
                chat = await context.bot.get_chat(int(uid))
                name = " ".join(x for x in [chat.first_name, chat.last_name] if x) or "بدون نام"
            except Exception:
                name = "نامشخص"
            try:
                exp = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
                left = (exp - now).days
                state = f"({left} روز مانده)" if left >= 0 else "(منقضی شده)"
            except Exception:
                state = "(نامشخص)"
            keyboard.append([InlineKeyboardButton(f"👤 {name} {state}", callback_data="ignore")])
            keyboard.append([
                InlineKeyboardButton("➕ تمدید ۳۰ روز", callback_data=f"adm_ren_{uid}_30"),
                InlineKeyboardButton("🚫 کنسل", callback_data=f"adm_can_{uid}"),
                InlineKeyboardButton("❌ حذف", callback_data=f"adm_del_{uid}"),
            ])
    await context.bot.send_message(chat_id, txt, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# ============================================================
# Playwright و استخراج جدول مسابقه
# ============================================================
async def prepare_page(page):
    try:
        await page.wait_for_timeout(2000)
        dropdown = page.get_by_text("انتخاب فصل مسابقات", exact=False).first
        if await dropdown.is_visible():
            await dropdown.click(timeout=5000, force=True)
            await page.wait_for_timeout(1500)
            option = page.locator("ul li, .v-list-item, .dropdown-item, [role='option']").first
            try:
                await option.wait_for(state="visible", timeout=3000)
                await option.click()
            except Exception:
                await page.keyboard.press("Enter")
            await page.wait_for_timeout(2500)
    except Exception:
        pass


async def ar(page, round_num, context, chat_id, save_history=True):
    """خواندن جدول دور با چند روش؛ نبودن عبارت Kg به‌تنهایی خطای آپلود نیست."""
    cache_key = f"{date.today().isoformat()}_{round_num}"
    cached = AR_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]

    def norm_digits(value):
        trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        return str(value).translate(trans)

    def clean_lines(value):
        value = norm_digits(value).replace("\u200c", " ").replace("\xa0", " ")
        return [re.sub(r"\s+", " ", x).strip() for x in value.splitlines() if x.strip()]

    horses = {}
    try:
        # رفتن به جدول مسابقات؛ در صورت نبودن دکمه‌ها نیز ادامه می‌دهیم.
        for label in ["جدول مسابقات", "نمایش دورها", "دورهای"]:
            try:
                await page.get_by_text(label, exact=False).first.click(timeout=2500, force=True)
                await page.wait_for_timeout(700)
            except Exception:
                pass

        clicked = False
        for label in [f"R {round_num}", f"R{round_num}", f"دور {round_num}", str(round_num)]:
            try:
                await page.get_by_text(label, exact=True).first.click(timeout=2500, force=True)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            print(f"[RACE DEBUG] R{round_num}: round selector not found")
            return "NO_DATA"

        await page.wait_for_timeout(4500)
        # ابتدا متن جدول‌های HTML و سپس متن کل صفحه را جمع می‌کنیم.
        table_parts = []
        try:
            rows = await page.locator("table tr").all_inner_texts()
            if rows:
                table_parts.extend(rows)
        except Exception:
            pass
        try:
            table_text = await page.locator("body").inner_text(timeout=10000)
        except Exception:
            table_text = ""
        lines = clean_lines("\n".join(table_parts) + "\n" + table_text)
        joined = "\n".join(lines)

        # پشتیبانی از قالب‌های Kg / kg / کیلوگرم و ارقام فارسی.
        weight_re = re.compile(r"(?<!\d)(\d{2,3})\s*(?:kg|KG|Kg|کیلوگرم|کیلو)(?!\w)", re.I)
        # روش اول: سطرهای جدولی؛ هر ردیف معمولاً شماره، نام، وزن، IOR، عملکرد و سوارکار دارد.
        try:
            row_texts = await page.locator("table tr").all_inner_texts()
        except Exception:
            row_texts = []
        for row in row_texts:
            row = norm_digits(row).replace("\u200c", " ")
            row_lines = [re.sub(r"\s+", " ", z).strip() for z in re.split(r"\s{2,}|\n", row) if z.strip()]
            if not row_lines:
                row_lines = row.split()
            m_weight = weight_re.search(row)
            if not m_weight:
                continue
            before = row[:m_weight.start()].strip()
            after = row[m_weight.end():].strip()
            nums = re.findall(r"\d+", before)
            if not nums:
                continue
            num = int(nums[-1])
            if not 1 <= num <= 30:
                continue
            name = re.sub(r"\b\d+\b", " ", before).strip(" -|:")
            tail_nums = re.findall(r"\d+", after)
            if not name or not tail_nums:
                continue
            ior = int(tail_nums[0])
            perf_match = re.search(r"(?:\d+\s*[-–/]\s*)+\d+", after)
            perf = perf_match.group(0).replace("–", "-").replace("/", "-") if perf_match else ""
            jockey = after.split()[-1] if after.split() else "نامشخص"
            horses[num] = {"n": name, "num": num, "ior": ior, "j": jockey, "perf": perf, "w_odd": 50.0}

        # روش دوم: متن صفحه، وقتی سایت جدول HTML استاندارد ندارد.
        if len(horses) < 2:
            for idx, line in enumerate(lines):
                mw = weight_re.search(line)
                if not mw:
                    continue
                before = line[:mw.start()].strip()
                after = line[mw.end():].strip()
                # شماره اسب ممکن است در همان خط یا خط قبل باشد.
                num_match = re.search(r"(?:^|\s)(\d{1,2})\s*$", before)
                if not num_match and idx > 0:
                    num_match = re.search(r"(?:^|\s)(\d{1,2})\s*$", lines[idx-1])
                    name = before or (lines[idx-2] if idx > 1 else "")
                else:
                    name = before[:num_match.start()].strip() if num_match else ""
                if not num_match:
                    continue
                num = int(num_match.group(1))
                if not 1 <= num <= 30:
                    continue
                tail_nums = re.findall(r"\d+", after)
                if not tail_nums:
                    continue
                ior = int(tail_nums[0])
                perf_match = re.search(r"(?:\d+\s*[-–/]\s*)+\d+", after)
                perf = perf_match.group(0).replace("–", "-").replace("/", "-") if perf_match else ""
                jockey = after.split()[-1] if after.split() else "نامشخص"
                name = re.sub(r"\s+", " ", name).strip(" -|:")
                if name and not any(x in name for x in ["مقام", "اسب", "سن", "عملکرد", "وزن", "مالک", "جدول"]):
                    horses[num] = {"n": name, "num": num, "ior": ior, "j": jockey, "perf": perf, "w_odd": 50.0}

        if len(horses) < 2:
            print(f"[RACE DEBUG] R{round_num}: parsed {len(horses)} horses; page text follows:\n{joined[:7000]}")
            # گزارش تشخیصی برای مدیر ربات؛ توکن و کوکی در پیام قرار نمی‌گیرد.
            try:
                excerpt = joined[:3000] or "متن صفحه خالی است"
                await context.bot.send_message(MY_ID, f"⚠️ خطای خواندن جدول دور {round_num}\nاسب استخراج‌شده: {len(horses)}\nمتن قابل مشاهده سایت:\n<pre>{html.escape(excerpt)}</pre>", parse_mode="HTML")
            except Exception:
                pass
            return "NOT_UPLOADED"

        # تلاش برای خواندن ضرایب؛ اگر پیدا نشد، مقدار پیش‌فرض 50 باقی می‌ماند.
        try:
            odds_text = joined
            odds_pattern = re.compile(r"([^\n]{2,80})\s*\n\s*(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)")
            for match in odds_pattern.finditer(odds_text):
                name = match.group(1).strip()
                odd = float(match.group(2))
                for horse in horses.values():
                    if name in horse["n"] or horse["n"] in name:
                        horse["w_odd"] = odd
                        break
        except Exception as exc:
            print(f"[ODDS DEBUG] R{round_num}: {exc}")

    except Exception as exc:
        print(f"Scraping error R{round_num}: {type(exc).__name__}: {exc}")
        return "ERROR"

    horse_list = list(horses.values())
    if len(horse_list) < 2:
        return "NO_DATA"

    iors = [h["ior"] for h in horse_list]
    ior_min, ior_max = min(iors), max(iors)
    ior_range = max(ior_max - ior_min, 1)
    forms = [decayed_form(h["perf"]) for h in horse_list]
    form_max = max(forms) if forms and max(forms) > 0 else 1
    market_probs = normalize_market([h["w_odd"] for h in horse_list])

    for idx, horse in enumerate(horse_list):
        ior_norm = (horse["ior"] - ior_min) / ior_range
        form_norm = decayed_form(horse["perf"]) / form_max
        market_norm = market_probs[idx]
        horse["combined"] = 0.40 * market_norm + 0.25 * ior_norm + 0.20 * form_norm + 0.10 * jockey_rating(horse["j"]) + 0.025
        horse["market_prob"] = market_probs[idx] * 100

    model_raw = power_probs([h["combined"] for h in horse_list], power=1.8)
    alpha = 0.35
    for idx, horse in enumerate(horse_list):
        blended = alpha * model_raw[idx] + (1 - alpha) * market_probs[idx]
        horse["model_prob"] = blended * 100
        horse["edge"] = horse["model_prob"] - horse["market_prob"]

    total = sum(h["model_prob"] for h in horse_list) or 1
    for horse in horse_list:
        horse["model_prob"] = horse["model_prob"] / total * 100

    sorted_horses = sorted(horse_list, key=lambda h: h["model_prob"], reverse=True)
    probs = [h["model_prob"] / 100 for h in sorted_horses]
    for idx, horse in enumerate(sorted_horses):
        horse["place_prob"] = place_probability(probs, idx, 3) * 100
        horse["place_fair_odd"] = round(100 / horse["place_prob"], 2) if horse["place_prob"] > 0 else 0

    win = sorted_horses[0]
    second = sorted_horses[1]
    third = sorted_horses[2] if len(sorted_horses) > 2 else second
    q_prob = quinella_prob(probs, 0, 1)
    q_fair = round(1 / q_prob, 2) if q_prob > 0 else 0
    result = {"w": win, "p": second, "s": third, "horses": sorted_horses,
              "q": f"{win['num']}-{win['n']} + {second['num']}-{second['n']}", "q_fair": q_fair}
    result["conf"] = get_confidence_level(result)
    long_shots = [h for h in sorted_horses if h["w_odd"] >= 12]
    top_long = sorted(long_shots, key=lambda h: h["model_prob"], reverse=True) if long_shots else sorted_horses[-2:]
    result["su"] = top_long[0] if top_long else sorted_horses[-1]
    result["qs"] = (f"{top_long[0]['num']}-{top_long[0]['n']} + {top_long[1]['num']}-{top_long[1]['n']}" if len(top_long) >= 2 else "N/A")
    if save_history:
        await save_to_history(result, round_num)
    AR_CACHE[cache_key] = (time.time(), result)
    return result


async def open_race_page():
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
    context = await browser.new_context(viewport={"width": 1440, "height": 900})
    cookies = []
    for name, value in [("auth_token", USER_TOKEN), ("device_id", USER_DEVICE_ID), ("_udi", USER_UDI)]:
        if value:
            cookies.append({"name": name, "value": value, "domain": ".asbdavani.app", "path": "/"})
    if cookies:
        await context.add_cookies(cookies)
    page = await context.new_page()
    await page.goto("https://mosharekat.asbdavani.app/", timeout=90000, wait_until="domcontentloaded")
    await page.wait_for_timeout(4500)
    if "ورود" in await page.locator("body").inner_text():
        await browser.close()
        await playwright.stop()
        raise RuntimeError("کوکی‌های سایت منقضی یا ناقص هستند؛ USER_TOKEN / USER_DEVICE_ID / USER_UDI را بررسی کن.")
    await prepare_page(page)
    return playwright, browser, page

# ============================================================
# تحلیل دورها
# ============================================================
async def process_odds(chat_id, context, specific_round=None, all_rounds=False, bet_amount=None, is_reminder=False):
    if await get_github_var("AUTO_UPDATE", "true") == "false" and not is_reminder:
        await context.bot.send_message(chat_id, "🛑 تحلیل‌ها موقتاً خاموش هستند.")
        return
    if not is_reminder:
        await context.bot.send_message(chat_id, "⏳ <b>در حال تحلیل مسابقه...</b>", parse_mode="HTML")
    weather = await get_weather()
    playwright = browser = None
    try:
        playwright, browser, page = await open_race_page()
        msg = "🏇 <b>تحلیل جامع مسابقات</b>\n" + weather + "\n"
        if bet_amount:
            msg += f"💰 سرمایه: <code>{bet_amount:,}</code> تومان\n"
        msg += "➖➖➖➖➖➖➖➖\n"
        rounds = range(1, 8) if all_rounds else ([specific_round] if specific_round else [])
        found = False
        for r in rounds:
            result = await ar(page, r, context, chat_id, save_history=(specific_round is not None))
            if not isinstance(result, dict):
                continue
            found = True
            win = result["w"]
            msg += f"\n🏁 <b>دور R{r}</b>\n"
            msg += f"🥇 <b>پیش‌بر:</b> {win['num']}-{win['n']} (ضریب {win['w_odd']})\n"
            msg += f"📊 مدل: {win['model_prob']:.1f}% | بازار: {win['market_prob']:.1f}% | اختلاف: {win['edge']:+.1f}%\n"
            msg += f"🥈 <b>میان‌بر:</b> {result['p']['num']}-{result['p']['n']}\n"
            msg += f"👥 <b>دوقلو امن:</b> {result['q']} | منصفانه: <code>{result['q_fair']}</code>\n"
            msg += f"🎯 <b>اطمینان:</b> {result['conf']}\n"
            if bet_amount and specific_round and r == specific_round:
                k = calculate_kelly(bet_amount, win["model_prob"], win["w_odd"], fraction=FRACTIONS["win"])
                if k:
                    msg += f"💰 مبلغ محاسباتی برد: <code>{k:,}</code> تومان\n"
            if specific_round:
                msg += "\n📈 <b>سه اسب برتر مدل:</b>\n"
                for horse in result["horses"][:3]:
                    msg += f"{horse['num']}-{horse['n']}: برد {horse['model_prob']:.1f}% | جایگاه {horse['place_prob']:.1f}%\n"
            msg += "➖➖➖➖➖➖➖➖\n"
        if not found:
            msg += "\n⚠️ جدول مسابقات هنوز بارگذاری نشده یا قابل خواندن نیست."
            await send_long_message(context, chat_id, msg)
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⏰ یادآوری خودکار", callback_data=f"rem_set_{specific_round or 1}")]])
            await context.bot.send_message(chat_id, "می‌خواهید چه زمانی دوباره بررسی کنم؟", reply_markup=keyboard)
        else:
            await send_long_message(context, chat_id, msg)
    except Exception as exc:
        await context.bot.send_message(chat_id, f"❌ خطا در تحلیل:\n<code>{str(exc)}</code>", parse_mode="HTML")
    finally:
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()

# ============================================================
# دوقلوها
# ============================================================
async def process_quinellas(chat_id, context, variant_type):
    await context.bot.send_message(chat_id, "⏳ <b>در حال محاسبه دوقلوهای هوشمند...</b>", parse_mode="HTML")
    bet = context.user_data.get("bet_amount")
    titles = {"q_safe": "🟢 دوقلوی امن", "q_value": "💎 دوقلوی ارزش", "q_lucky": "🍀 دوقلوی شانسی"}
    playwright = browser = None
    try:
        playwright, browser, page = await open_race_page()
        msg = titles[variant_type] + "\n➖➖➖➖➖➖➖➖\n"
        if bet:
            msg += f"💰 سرمایه ثبت‌شده: <code>{bet:,}</code> تومان\n"
        found = False
        for r in range(1, 8):
            result = await ar(page, r, context, chat_id, save_history=False)
            if not isinstance(result, dict):
                continue
            horses = result["horses"]
            probs = [h["model_prob"] / 100 for h in horses]
            candidates = []
            for i in range(len(horses)):
                for j in range(i + 1, len(horses)):
                    h1, h2 = horses[i], horses[j]
                    if variant_type == "q_safe" and not (i == 0 and j == 1):
                        continue
                    if variant_type == "q_value":
                        if not (((i < 3) or (j < 3)) and (h1["w_odd"] >= 5 or h2["w_odd"] >= 5)):
                            continue
                    if variant_type == "q_lucky" and not (h1["w_odd"] >= 5 and h2["w_odd"] >= 5):
                        continue
                    probability = quinella_prob(probs, i, j)
                    if probability > 0:
                        candidates.append({"h1": h1, "h2": h2, "prob": probability * 100, "fair": round(1 / probability, 2)})
            if not candidates:
                continue
            if variant_type == "q_safe":
                candidates.sort(key=lambda x: x["prob"], reverse=True)
            else:
                candidates.sort(key=lambda x: x["fair"], reverse=True)
            best = candidates[0]
            found = True
            msg += f"\n🏁 <b>دور R{r}</b>\n👥 <b>{best['h1']['num']}-{best['h1']['n']} + {best['h2']['num']}-{best['h2']['n']}</b>\n"
            msg += f"📊 احتمال محاسباتی: <b>{best['prob']:.1f}%</b> | ضریب منصفانه: <code>{best['fair']}</code>\n"
            if bet and variant_type != "q_lucky":
                k = calculate_kelly(bet, best["prob"], best["fair"], fraction=0.10 if variant_type == "q_value" else 0.15, confidence=0.45 if variant_type == "q_value" else 0.60)
                if k:
                    msg += f"💰 مبلغ محاسباتی: <code>{k:,}</code> تومان\n"
            elif variant_type == "q_lucky":
                msg += "🎰 گزینه پرریسک؛ فقط با مبلغی که از دست دادنش قابل‌تحمل است.\n"
            msg += "➖➖➖➖➖➖➖➖\n"
        if not found:
            msg += "\n⚠️ برای این نوع دوقلو گزینه مناسبی پیدا نشد یا جدول‌ها هنوز منتشر نشده‌اند."
        else:
            msg += "\n💡 ضریب منصفانه تخمین مدل است و تضمین سود یا برد نیست."
        await send_long_message(context, chat_id, msg)
    except Exception as exc:
        await context.bot.send_message(chat_id, f"❌ خطا: <code>{str(exc)}</code>", parse_mode="HTML")
    finally:
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()

# ============================================================
# چندپره، شگفتی و شکار ارزش
# ============================================================
async def process_multileg(chat_id, context, leg_type, bet_amount=None):
    await context.bot.send_message(chat_id, "⏳ <b>در حال بررسی چندپره...</b>", parse_mode="HTML")
    playwright = browser = None
    try:
        playwright, browser, page = await open_race_page()
        res7 = await ar(page, 7, context, chat_id, save_history=False)
        has7 = isinstance(res7, dict)
        if leg_type == "ml_6":
            rounds, title = ([2, 3, 4, 5, 6, 7] if has7 else [1, 2, 3, 4, 5, 6]), "۶ پره"
        elif leg_type == "ml_5":
            rounds, title = ([3, 4, 5, 6, 7] if has7 else [2, 3, 4, 5, 6]), "۵ پره"
        elif leg_type == "ml_3a":
            rounds, title = [1, 2, 3], "۳ پره اول"
        else:
            rounds, title = ([5, 6, 7] if has7 else [4, 5, 6]), "۳ پره دوم"
        picks, details = [], []
        for r in rounds:
            result = res7 if r == 7 and has7 else await ar(page, r, context, chat_id, save_history=False)
            if isinstance(result, dict):
                picks.append(f"{result['w']['num']}-{result['w']['n']}")
                details.append(result["w"])
            else:
                picks.append(f"دور {r}: نامشخص")
        msg = f"🎯 <b>ترکیب {title}</b>\n\n🏇 <b>انتخاب‌ها:</b>\n<code>{' + '.join(picks)}</code>\n"
        if len(details) == len(rounds):
            prob = 1.0
            for horse in details:
                prob *= horse["model_prob"] / 100
            prob = min(prob * (1 + 0.06 * (len(details) - 1)), 0.95)
            fair = round(1 / prob, 2) if prob > 0 else 0
            approx = round(sum(h["w_odd"] for h in details), 2)
            msg += f"\n📊 ضریب منصفانه تخمینی: <code>{fair}</code>\n📊 مجموع ضرایب تک‌اسب‌ها: <code>{approx}</code>\n"
            msg += "⚠️ مجموع ضرایب تک‌اسب‌ها، ضریب واقعی چندپره نیست. ضریب نهایی را باید از سایت مسابقه دید.\n"
        else:
            msg += "\n⚠️ بخشی از جدول‌ها در دسترس نبود؛ ترکیب کامل نیست."
        await context.bot.send_message(chat_id, msg, parse_mode="HTML")
    except Exception as exc:
        await context.bot.send_message(chat_id, f"❌ خطا: {str(exc)}")
    finally:
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()


async def process_surprises(chat_id, context, mode):
    await context.bot.send_message(chat_id, "⏳ <b>در حال پیدا کردن گزینه‌های شگفتی...</b>", parse_mode="HTML")
    playwright = browser = None
    try:
        playwright, browser, page = await open_race_page()
        res7 = await ar(page, 7, context, chat_id, save_history=False)
        max_round = 7 if isinstance(res7, dict) else 6
        msg = ("🎲 <b>اسب شگفتی</b>\n" if mode == "sur_h" else "🔥 <b>دوقلوی شگفتی</b>\n")
        for r in range(1, max_round + 1):
            result = res7 if r == 7 and isinstance(res7, dict) else await ar(page, r, context, chat_id, save_history=False)
            if not isinstance(result, dict):
                continue
            if mode == "sur_h":
                horse = result["su"]
                msg += f"🏁 دور {r}: {horse['num']}-{horse['n']} (ضریب {horse['w_odd']})\n"
            else:
                msg += f"🏁 دور {r}: {result['qs']}\n"
        await context.bot.send_message(chat_id, msg, parse_mode="HTML")
    except Exception as exc:
        await context.bot.send_message(chat_id, f"❌ خطا: {str(exc)}")
    finally:
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()


async def process_value_bets(chat_id, context, bet_amount=None):
    if await get_github_var("AUTO_UPDATE", "true") == "false":
        await context.bot.send_message(chat_id, "🛑 تحلیل‌ها موقتاً خاموش هستند.")
        return
    await context.bot.send_message(chat_id, "⏳ <b>در حال شکار ارزش...</b>", parse_mode="HTML")
    playwright = browser = None
    try:
        playwright, browser, page = await open_race_page()
        msg = "💎 <b>شکار ارزش (تک‌اسب)</b>\n"
        if bet_amount:
            msg += f"💰 سرمایه: <code>{bet_amount:,}</code> تومان\n"
        found = False
        for r in range(1, 8):
            result = await ar(page, r, context, chat_id, save_history=False)
            if not isinstance(result, dict):
                continue
            values = [h for h in result["horses"] if h["edge"] >= 5 and h["w_odd"] > 3]
            if values:
                found = True
                msg += f"\n🏁 <b>دور R{r}</b>\n"
                for horse in values[:3]:
                    line = f"💎 {horse['num']}-{horse['n']} | ضریب {horse['w_odd']} | مدل {horse['model_prob']:.1f}% در برابر بازار {horse['market_prob']:.1f}%"
                    if bet_amount:
                        amount = calculate_kelly(bet_amount, horse["model_prob"], horse["w_odd"], fraction=FRACTIONS["win"])
                        if amount:
                            line += f" → <code>{amount:,}</code> تومان"
                    msg += line + "\n"
        if not found:
            msg += "\n⚠️ با معیارهای فعلی گزینه ارزشمند پیدا نشد."
        await send_long_message(context, chat_id, msg)
    except Exception as exc:
        await context.bot.send_message(chat_id, f"❌ خطا: <code>{str(exc)}</code>", parse_mode="HTML")
    finally:
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()

# ============================================================
# یادآوری و کارهای پس‌زمینه
# ============================================================
async def clean_cache():
    while True:
        await asyncio.sleep(3600)
        now = time.time()
        for key in list(AR_CACHE):
            if now - AR_CACHE[key][0] > CACHE_TTL:
                del AR_CACHE[key]


async def check_expirations(bot):
    await asyncio.sleep(3600)
    while True:
        try:
            users = await get_allowed_users_dict()
            now = datetime.now()
            for uid, exp_str in users.items():
                try:
                    exp = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
                    if (exp - now).days == 2:
                        await bot.send_message(int(uid), "⚠️ <b>هشدار انقضای اشتراک</b>\nاشتراک شما حدود ۲ روز دیگر تمام می‌شود. برای تمدید از دکمه خرید اشتراک استفاده کنید.", parse_mode="HTML")
                except Exception:
                    pass
        except Exception as exc:
            print(f"Expiration checker error: {exc}")
        await asyncio.sleep(86400)


async def run_reminder(chat_id, context, round_num, delay_minutes):
    await asyncio.sleep(delay_minutes * 60)
    await context.bot.send_message(chat_id, f"⏰ یادآوری: بررسی دور {round_num} آغاز شد.")
    await process_odds(chat_id, context, specific_round=round_num, bet_amount=context.user_data.get("bet_amount"), is_reminder=True)

# ============================================================
# دستورات تلگرام
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)

    if context.args and context.args[0].startswith("ref_"):
        referrer = context.args[0][4:]
        if referrer != chat_id:
            referrals = await get_referrals()
            referrals.setdefault(referrer, [])
            users = await get_allowed_users_dict()
            if chat_id not in referrals[referrer] and chat_id not in users:
                referrals[referrer].append(chat_id)
                await set_github_var("REFERRALS", json.dumps(referrals, ensure_ascii=False))
                new_exp = await add_vip_days(referrer, REF_REWARD_DAYS)
                try:
                    await context.bot.send_message(int(referrer), f"🎉 یک نفر با لینک شما وارد شد!\n🎁 {REF_REWARD_DAYS} روز هدیه ثبت شد.\n📅 انقضای جدید: <code>{new_exp}</code>", parse_mode="HTML")
                except Exception:
                    pass

    users = await get_allowed_users_dict()
    is_new = chat_id not in users and chat_id != MY_ID
    if is_new:
        expiry = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        users[chat_id] = expiry
        await set_github_var("ALLOWED_USERS", json.dumps(users, ensure_ascii=False))
        welcome = (
            "👋 <b>سلام و خوش آمدید!</b>\n\n🎁 یک روز اشتراک آزمایشی برای شما فعال شد.\n"
            "برای ادامه استفاده پس از پایان مهلت، اشتراک تهیه کنید."
        )
    else:
        welcome = (
            "👋 <b>سلام دوباره!</b>\n"
            "✅ نسخه ۴ با امکانات تحلیل دورها، دوقلوهای هوشمند، چندپره، گزینه‌های شگفتی و شکار ارزش آماده است.\n"
            "⚠️ پیش‌بینی‌ها احتمالی‌اند و تضمین برد یا سود نیستند."
        )
    await context.bot.send_message(update.effective_chat.id, welcome, reply_markup=get_main_menu(chat_id), parse_mode="HTML")


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or str(update.effective_chat.id) != MY_ID:
        return
    try:
        if len(context.args) < 2:
            raise ValueError
        target, days = context.args[0], int(context.args[1])
        if days < 1:
            raise ValueError
        users = await get_allowed_users_dict()
        expiry = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        users[target] = expiry
        ok = await set_github_var("ALLOWED_USERS", json.dumps(users, ensure_ascii=False))
        if not ok:
            await update.message.reply_text("❌ ذخیره در GitHub ناموفق بود؛ دسترسی PAT و متغیرها را بررسی کن.")
            return
        await update.message.reply_text(f"✅ کاربر <code>{target}</code> برای {days} روز فعال شد.\nانقضا: <code>{expiry}</code>", parse_mode="HTML")
        try:
            await context.bot.send_message(int(target), f"🎉 اشتراک شما به مدت {days} روز فعال شد.\nانقضا: {expiry}")
        except Exception:
            pass
    except Exception:
        await update.message.reply_text("❌ قالب صحیح: /approve 123456789 30")


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or str(update.effective_chat.id) != MY_ID:
        return
    try:
        if not context.args:
            raise ValueError
        target = context.args[0].strip()
        users = await get_allowed_users_dict()
        if target not in users:
            await update.message.reply_text("این کاربر در فهرست اشتراک نیست.")
            return
        del users[target]
        ok = await set_github_var("ALLOWED_USERS", json.dumps(users, ensure_ascii=False))
        await update.message.reply_text("✅ کاربر حذف شد." if ok else "❌ ذخیره تغییرات در GitHub ناموفق بود.")
        try:
            await context.bot.send_message(int(target), "⚠️ اشتراک شما لغو شده است.")
        except Exception:
            pass
    except Exception:
        await update.message.reply_text("❌ قالب صحیح: /remove 123456789")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or str(update.effective_chat.id) != MY_ID:
        return
    if not context.args:
        await update.message.reply_text("قالب صحیح: /broadcast متن پیام")
        return
    msg = " ".join(context.args)
    users = await get_allowed_users_dict()
    success = fail = 0
    status = await update.message.reply_text(f"⏳ ارسال به {len(users)} کاربر...")
    for uid in users:
        try:
            await context.bot.send_message(int(uid), msg, parse_mode="HTML")
            success += 1
            await asyncio.sleep(0.1)
        except Exception:
            fail += 1
    await status.edit_text(f"✅ ارسال تمام شد.\nموفق: {success}\nناموفق: {fail}")

# ============================================================
# دریافت ورودی‌ها، رسید و دکمه‌ها
# ============================================================
async def handle_inputs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text
    if context.user_data.get("awaiting_bet"):
        context.user_data["awaiting_bet"] = False
        try:
            amount = int(re.sub(r"[^\d]", "", text))
            if amount < 1000:
                raise ValueError
            context.user_data["bet_amount"] = amount
            await update.message.reply_text(f"✅ سرمایه ثبت شد: <code>{amount:,}</code> تومان", parse_mode="HTML")
        except Exception:
            await update.message.reply_text("❌ مبلغ معتبر (حداقل ۱۰۰۰ تومان) بفرست.")
        return
    if context.user_data.get("awaiting_rem"):
        context.user_data["awaiting_rem"] = False
        try:
            minutes = int(re.sub(r"[^\d]", "", text))
            if minutes < 1 or minutes > 10080:
                raise ValueError
            round_num = context.user_data.get("rem_round", 1)
            asyncio.create_task(run_reminder(update.effective_chat.id, context, round_num, minutes))
            await update.message.reply_text(f"✅ یادآوری دور {round_num} برای {minutes} دقیقه دیگر تنظیم شد.")
        except Exception:
            await update.message.reply_text("❌ عدد معتبر بین ۱ تا ۱۰۰۸۰ دقیقه بفرست.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if str(chat_id) == MY_ID:
        return
    user_name = update.effective_user.first_name if update.effective_user else "کاربر"
    try:
        await context.bot.forward_message(chat_id=int(MY_ID), from_chat_id=chat_id, message_id=update.message.message_id)
        admin_msg = (
            "🔔 <b>رسید جدید!</b>\n\n"
            f"👤 نام: {user_name}\nآیدی عددی: <code>{chat_id}</code>\n\n"
            f"برای فعال‌سازی: <code>/approve {chat_id} 30</code>"
        )
        await context.bot.send_message(int(MY_ID), admin_msg, parse_mode="HTML")
        await update.message.reply_text("✅ رسید دریافت شد. پس از بررسی، اشتراک فعال می‌شود.")
    except Exception as exc:
        await update.message.reply_text(f"❌ ارسال رسید انجام نشد: {exc}")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data or ""

    premium_buttons = {"r1", "r2", "r3", "r4", "r5", "r6", "r7", "rall", "q_safe", "q_value", "q_lucky", "value", "ml_6", "ml_5", "ml_3a", "ml_3b", "sur_h", "sur_d"}
    if data in premium_buttons and not await is_premium(chat_id):
        await query.message.reply_text("⛔ این بخش ویژه مشترکین است. برای خرید اشتراک از دکمه خرید اشتراک استفاده کنید.")
        return
    if data in {"on", "off", "admin_help", "admin_refresh"} and str(chat_id) != MY_ID:
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    now = time.time()
    key = f"{chat_id}:{data}"
    if now - _last_call.get(key, 0) < COOLDOWN and data != "bet_set" and not data.startswith(("rem_", "adm_")):
        await query.answer(f"⏳ {int(COOLDOWN - (now - _last_call.get(key, 0)))} ثانیه صبر کنید.", show_alert=True)
        return
    _last_call[key] = now

    if data == "buy_sub":
        txt = ("💳 <b>خرید اشتراک ویژه</b>\n\n"
               f"مبلغ: <b>{SUB_PRICE} تومان</b>\n\n"
               f"💳 <code>{CARD_NUMBER}</code>\n👤 به نام: {CARD_HOLDER}\n\n"
               "پس از واریز، تصویر رسید را همین‌جا ارسال کنید.")
        await query.message.reply_text(txt, parse_mode="HTML")
    elif data == "my_status":
        users = await get_allowed_users_dict()
        if await is_premium(chat_id):
            expiry = users.get(str(chat_id), "دسترسی مدیر")
            await query.message.reply_text(f"✅ اشتراک فعال است.\nتاریخ انقضا: <code>{expiry}</code>", parse_mode="HTML")
        else:
            await query.message.reply_text("❌ اشتراک شما فعال نیست.")
    elif data == "invite":
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref_{chat_id}"
        refs = await get_referrals()
        count = len(refs.get(str(chat_id), []))
        await query.message.reply_text(
            f"🎁 به ازای هر دعوت موفق {REF_REWARD_DAYS} روز هدیه می‌گیرید.\n\n🔗 لینک شما:\n<code>{link}</code>\n\n👤 دعوت‌شده‌ها: {count} نفر",
            parse_mode="HTML"
        )
    elif data.startswith("adm_"):
        parts = data.split("_")
        if len(parts) < 3:
            return
        action, uid = parts[1], parts[2]
        users = await get_allowed_users_dict()
        if action == "ren":
            days = int(parts[3]) if len(parts) > 3 else 30
            expiry = await add_vip_days(uid, days)
            await query.message.reply_text(f"✅ اشتراک {uid} تمدید شد تا {expiry}")
        elif action == "can":
            users[uid] = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
            ok = await set_github_var("ALLOWED_USERS", json.dumps(users, ensure_ascii=False))
            await query.message.reply_text("🚫 اشتراک لغو شد." if ok else "❌ ذخیره ناموفق بود.")
        elif action == "del":
            users.pop(uid, None)
            ok = await set_github_var("ALLOWED_USERS", json.dumps(users, ensure_ascii=False))
            await query.message.reply_text("❌ کاربر حذف شد." if ok else "❌ ذخیره ناموفق بود.")
        await cmd_admin_panel(chat_id, context)
    elif data == "admin_refresh":
        await query.message.delete()
        await cmd_admin_panel(chat_id, context)
    elif data == "on":
        ok = await set_github_var("AUTO_UPDATE", "true")
        await query.message.reply_text("✅ روشن شد." if ok else "❌ تغییر وضعیت در GitHub ناموفق بود.")
    elif data == "off":
        ok = await set_github_var("AUTO_UPDATE", "false")
        await query.message.reply_text("🛑 خاموش شد." if ok else "❌ تغییر وضعیت در GitHub ناموفق بود.")
    elif data == "history":
        await cmd_history(chat_id, context)
    elif data == "weather":
        await query.message.reply_text(await get_weather())
    elif data == "value":
        await process_value_bets(chat_id, context, context.user_data.get("bet_amount"))
    elif data == "bet_set":
        context.user_data["awaiting_bet"] = True
        current = context.user_data.get("bet_amount", 0)
        await query.message.reply_text(f"💰 سرمایه فعلی: {current:,} تومان\nمبلغ جدید را بفرست:", reply_markup=ForceReply(selective=True))
    elif data == "change_city":
        await query.message.reply_text("🏙 در این نسخه شهر مسابقه به‌صورت خودکار انتخاب می‌شود.")
    elif data == "admin_help":
        await cmd_admin_panel(chat_id, context)
    elif data.startswith("rem_set_"):
        try:
            round_num = int(data.split("_")[-1])
            context.user_data["awaiting_rem"] = True
            context.user_data["rem_round"] = round_num
            await query.message.reply_text("⏰ چند دقیقه دیگر یادآوری کنم؟", reply_markup=ForceReply(selective=True))
        except Exception:
            await query.message.reply_text("❌ شماره دور نامعتبر است.")
    elif data in {"q_safe", "q_value", "q_lucky"}:
        await process_quinellas(chat_id, context, data)
    elif data.startswith("r"):
        bet = context.user_data.get("bet_amount")
        if data == "rall":
            await process_odds(chat_id, context, all_rounds=True, bet_amount=bet)
        else:
            await process_odds(chat_id, context, specific_round=int(data[1:]), bet_amount=bet)
    elif data.startswith("ml_"):
        await process_multileg(chat_id, context, data, context.user_data.get("bet_amount"))
    elif data in {"sur_h", "sur_d"}:
        await process_surprises(chat_id, context, data)
    elif data == "ignore":
        return


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"Unhandled Telegram error: {context.error}")

# ============================================================
# اجرای ربات
# ============================================================
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("متغیر BOT_TOKEN در تنظیمات GitHub تعریف نشده است.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_inputs))
    app.add_error_handler(error_handler)

    await app.initialize()
    try:
        # ابتدا دریافت آپدیت‌ها و سپس پردازش آن‌ها شروع می‌شود.
        await app.updater.start_polling(drop_pending_updates=True, bootstrap_retries=-1)
        await app.start()
        app.create_task(check_expirations(app.bot))
        app.create_task(clean_cache())
        print("Bot started successfully")
        await asyncio.sleep(BOT_RUN_SECONDS)
    finally:
        if app.updater and app.updater.running:
            await app.updater.stop()
        if app.running:
            await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"CRITICAL ERROR: {exc}")
        sys.exit(1)
