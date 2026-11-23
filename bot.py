import os
import re
import sys
import json
import html
import time
import asyncio
import logging
from datetime import datetime, timedelta, date

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from playwright.async_api import async_playwright

# ============================================================
# تنظیمات
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
USER_TOKEN = os.getenv("USER_TOKEN", "").strip()
USER_DEVICE_ID = os.getenv("USER_DEVICE_ID", "").strip()
USER_UDI = os.getenv("USER_UDI", "").strip()
GH_PAT = os.getenv("GH_PAT", "").strip()
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "").strip()
MY_ID = os.getenv("ADMIN_ID", "").strip()
CARD_NUMBER = os.getenv("CARD_NUMBER", "شماره کارت را در متغیر CARD_NUMBER تنظیم کنید")
CARD_HOLDER = os.getenv("CARD_HOLDER", "نام صاحب کارت")
SUB_PRICE = os.getenv("SUB_PRICE", "250,000")
REF_REWARD_DAYS = 3
BOT_RUN_SECONDS = int(os.getenv("BOT_RUN_SECONDS", "17400"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("HorseBot")

_last_call = {}
COOLDOWN = 60
AR_CACHE = {}
CACHE_TTL = 180

# ============================================================
# ابزارهای عمومی و GitHub Variables
# ============================================================
def esc(value):
    return html.escape(str(value or ""), quote=False)

def digits(value):
    trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return str(value or "").translate(trans)

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
            if res.status_code != 404:
                log.warning("GitHub variable read failed: %s", res.status_code)
    except Exception:
        log.exception("GitHub variable read error")
    return default_val

async def set_github_var(var_name, val):
    if not GITHUB_REPOSITORY or not GH_PAT:
        log.error("GITHUB_REPOSITORY یا GH_PAT تنظیم نشده است")
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
                log.error("GitHub variable check failed: %s", check.status_code)
                return False
            return res.status_code in (201, 204)
    except Exception:
        log.exception("GitHub variable write error")
        return False

async def get_allowed_users_dict():
    raw = await get_github_var("ALLOWED_USERS", "{}")
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}

async def is_premium(chat_id):
    if MY_ID and str(chat_id) == MY_ID:
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
        current = datetime.strptime(users.get(str(target_id), ""), "%Y-%m-%d %H:%M:%S")
        if current < now:
            current = now
    except Exception:
        current = now
    expiry = (current + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    users[str(target_id)] = expiry
    await set_github_var("ALLOWED_USERS", json.dumps(users, ensure_ascii=False))
    return expiry

async def get_referrals():
    try:
        value = json.loads(await get_github_var("REFERRALS", "{}"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}

# ============================================================
# مدل تحلیلی
# ============================================================
JOCKEY_TIERS = {
    "S": ["رحمانی", "قهرمانی", "نوروزی"],
    "A": ["وحدانی", "مرادی", "صوفیان", "خواجوی"],
    "B": ["آقاجانی", "حیدری", "نصیری", "مسعودی"],
    "C": ["احمدی", "رضایی"],
}
JOCKEY_RATINGS = {"S": .95, "A": .75, "B": .55, "C": .40, "D": .28}
RANK_POINTS = {"1": 10, "2": 6, "3": 3, "4": 1, "5": .5}
FRACTIONS = {"win": .25, "place": .20, "quinella": .15, "exacta": .10, "trifecta": .05, "parlay": .08}

def jockey_rating(name):
    name = str(name or "")
    for tier, names in JOCKEY_TIERS.items():
        if any(n in name for n in names):
            return JOCKEY_RATINGS[tier]
    return JOCKEY_RATINGS["D"]

def decayed_form(perf, decay=.85):
    total = 0.0
    for i, rank in enumerate(str(perf or "").split("-")):
        rank = rank.strip()
        if rank.isdigit():
            total += RANK_POINTS.get(rank, 0) * decay ** i
    return total

def power_probs(scores, power=1.8):
    if not scores:
        return []
    low = min(scores)
    vals = [max(.001, s - low + .01) for s in scores] if low <= 0 else [max(.001, s) for s in scores]
    vals = [x ** power for x in vals]
    total = sum(vals) or 1
    return [x / total for x in vals]

def normalize_market(odds):
    # فقط ضرایب واقعی و معتبر وارد بازار می‌شوند.
    implied = [(1.0 / x) if isinstance(x, (int, float)) and x > 1 else 0 for x in odds]
    total = sum(implied)
    if total <= 0:
        return None
    return [x / total for x in implied]

def place_probability(probs, index, num_places=3):
    # تقریب ساده رتبه‌ای؛ خروجی بین صفر و یک
    p = probs[index]
    if p <= 0:
        return 0.0
    n = len(probs)
    result = p
    for j in range(n):
        if j != index and probs[j] < 1:
            result += probs[j] * p / max(1e-9, 1 - probs[j])
    if num_places >= 3:
        for j in range(n):
            if j == index or probs[j] >= 1:
                continue
            for k in range(n):
                if k in (index, j) or probs[j] + probs[k] >= 1:
                    continue
                result += probs[j] * probs[k] / max(1e-9, 1-probs[j]) * (
                    p / max(1e-9, 1-probs[j]-probs[k])
                )
    return min(1.0, result)

def quinella_prob(probs, i, j):
    pi, pj = probs[i], probs[j]
    if pi <= 0 or pj <= 0 or pi + pj >= 1:
        return 0.0
    return pi * pj / (1-pi) + pj * pi / (1-pj)

def calculate_kelly(bankroll, model_prob, decimal_odds, fraction=.25, min_bet=1000, max_pct=.05, confidence=.55):
    if not decimal_odds or decimal_odds <= 1 or bankroll <= 0:
        return 0
    market_prob = 1 / decimal_odds
    p_model = model_prob / 100 if model_prob > 1 else model_prob
    p = p_model * confidence + market_prob * (1-confidence)
    if not 0 < p < 1:
        return 0
    b = decimal_odds - 1
    f = (b*p - (1-p)) / b
    if f <= 0:
        return 0
    amount = min(bankroll*f*fraction, bankroll*max_pct)
    return int(amount // 1000)*1000 if amount >= min_bet else 0

def get_confidence_level(result):
    w, p = result["w"], result["p"]
    if w.get("market_prob") is None:
        return "⚠️ پایین (ضریب بازار موجود نیست)"
    gap = w["model_prob"] - p["model_prob"]
    agree = (w["edge"] > 0) + (abs(w["model_prob"]-w["market_prob"]) < 10)
    if gap > 12 and agree >= 2:
        return "🔥 بالا"
    if gap > 6:
        return "⚖️ متوسط"
    return "⚠️ پایین"

# ============================================================
# منو و ارسال پیام
# ============================================================
def get_main_menu(chat_id):
    keyboard = []
    if MY_ID and str(chat_id) == MY_ID:
        keyboard.append([InlineKeyboardButton("🟢 روشن", callback_data="on"),
                         InlineKeyboardButton("🔴 خاموش", callback_data="off")])
    keyboard += [
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
        [InlineKeyboardButton("🎁 دعوت از دوستان", callback_data="invite")],
        [InlineKeyboardButton("🌤 وضعیت هوا", callback_data="weather"), InlineKeyboardButton("🏙 تغییر مسیر/شهر", callback_data="change_city")],
    ]
    if MY_ID and str(chat_id) == MY_ID:
        keyboard.append([InlineKeyboardButton("👑 پنل مدیریت", callback_data="admin_help")])
    return InlineKeyboardMarkup(keyboard)

def split_safe(message, max_len=3900):
    chunks, current = [], ""
    for line in message.splitlines(True):
        if len(current) + len(line) > max_len and current:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks or [message]

async def send_long_message(context, chat_id, message):
    for chunk in split_safe(message):
        try:
            await context.bot.send_message(chat_id, chunk, parse_mode="HTML")
        except Exception:
            await context.bot.send_message(chat_id, re.sub(r"<[^>]*>", "", chunk))
        await asyncio.sleep(.15)

async def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=37.25&longitude=55.16&current_weather=true"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
            r.raise_for_status()
            w = r.json()["current_weather"]
            return f"🌡 دما: {w['temperature']}°C | 💨 باد: {w['windspeed']} km/h"
    except Exception:
        return "⚠️ اطلاعات هوا در دسترس نیست."

async def save_to_history(result, round_num):
    try:
        history = json.loads(await get_github_var("BOT_HISTORY", "[]"))
        if not isinstance(history, list):
            history = []
    except Exception:
        history = []
    history.insert(0, {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "round": round_num,
        "win": f"{result['w']['num']}-{result['w']['n']}",
        "quinella": result["q"],
        "confidence": result["conf"]
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
        txt += (f"📅 {esc(item.get('date','-'))} | دور {esc(item.get('round','-'))}\n"
                f"🥇 {esc(item.get('win','-'))}\n👥 {esc(item.get('quinella','-'))}\n"
                f"🎯 {esc(item.get('confidence','-'))}\n➖➖➖\n")
    await context.bot.send_message(chat_id, txt, parse_mode="HTML")

# ============================================================
# مدیریت
# ============================================================
async def cmd_admin_panel(chat_id, context):
    auto = await get_github_var("AUTO_UPDATE", "true")
    users = await get_allowed_users_dict()
    status = "🟢 روشن" if auto == "true" else "🔴 خاموش"
    text = f"👑 <b>پنل مدیریت</b>\n⚙️ وضعیت: {status}\n👥 تعداد کاربران: {len(users)}"
    kb = [[InlineKeyboardButton("🔄 بروزرسانی", callback_data="admin_refresh")]]
    for uid, exp in list(users.items())[:30]:
        try:
            dt = datetime.strptime(exp, "%Y-%m-%d %H:%M:%S")
            left = (dt-datetime.now()).days
            state = f"{left} روز مانده" if left >= 0 else "منقضی"
        except Exception:
            state = "نامشخص"
        kb.append([InlineKeyboardButton(f"کاربر {uid} - {state}", callback_data="ignore")])
        kb.append([
            InlineKeyboardButton("➕ تمدید ۳۰ روز", callback_data=f"adm_ren_{uid}_30"),
            InlineKeyboardButton("🚫 لغو", callback_data=f"adm_can_{uid}"),
            InlineKeyboardButton("❌ حذف", callback_data=f"adm_del_{uid}")
        ])
    await context.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))

# ============================================================
# Playwright: بازکردن سایت و تشخیص دور
# ============================================================
async def prepare_page(page):
    await page.wait_for_timeout(2500)
    # عمداً فصل را خودکار روی اولین گزینه نمی‌گذاریم؛ ممکن است فصل اشتباه باشد.
    body = (await page.locator("body").inner_text())[:5000]
    if "انتخاب فصل مسابقات" in body:
        log.info("صفحه انتخاب فصل نمایش داده شده؛ انتخاب خودکار فصل غیرفعال است.")

async def open_race_page():
    if not all([USER_TOKEN, USER_DEVICE_ID, USER_UDI]):
        raise RuntimeError("یکی از متغیرهای USER_TOKEN، USER_DEVICE_ID یا USER_UDI در GitHub Secrets خالی است.")
    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1100},
            locale="fa-IR"
        )
        cookies = [
            {"name": "auth_token", "value": USER_TOKEN, "domain": ".asbdavani.app", "path": "/"},
            {"name": "device_id", "value": USER_DEVICE_ID, "domain": ".asbdavani.app", "path": "/"},
            {"name": "_udi", "value": USER_UDI, "domain": ".asbdavani.app", "path": "/"}
        ]
        await context.add_cookies(cookies)
        page = await context.new_page()
        await page.goto("https://mosharekat.asbdavani.app/", timeout=90000, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)
        body = await page.locator("body").inner_text(timeout=15000)
        if "ورود" in body[:1500] and ("رمز" in body[:1500] or "نام کاربری" in body[:1500]):
            raise RuntimeError("ورود به سایت انجام نشد؛ کوکی‌های سایت منقضی یا ناقص هستند.")
        await prepare_page(page)
        return pw, browser, page
    except Exception:
        if browser:
            await browser.close()
        await pw.stop()
        raise

async def select_round(page, round_num):
    """انتخاب دور فقط از عناصر قابل کلیک با متن دقیق؛ از کلیک عدد خام خودداری می‌شود."""
    patterns = [
        re.compile(rf"^\s*R\s*{round_num}\s*$", re.I),
        re.compile(rf"^\s*دور\s*{round_num}\s*$"),
        re.compile(rf"^\s*راند\s*{round_num}\s*$")
    ]
    selectors = ["button", "[role=button]", "a", "[role=tab]", ".v-btn", ".v-tab"]
    for selector in selectors:
        loc = page.locator(selector)
        try:
            count = min(await loc.count(), 250)
        except Exception:
            continue
        for i in range(count):
            item = loc.nth(i)
            try:
                if not await item.is_visible():
                    continue
                label = re.sub(r"\s+", " ", digits(await item.inner_text())).strip()
                if any(p.match(label) for p in patterns):
                    await item.click(timeout=3500)
                    await page.wait_for_timeout(1600)
                    return True
            except Exception:
                continue
    # متن‌های دور با aria-label یا title
    for attr in ("aria-label", "title"):
        for p in patterns:
            try:
                item = page.locator(f'[{attr}="{p.pattern}"]')
                if await item.count():
                    await item.first.click(timeout=2500)
                    await page.wait_for_timeout(1500)
                    return True
            except Exception:
                pass
    return False

def parse_odds_from_text(text, horses):
    """ضریب تنها وقتی به‌صورت واضح کنار نام اسب دیده شود ثبت می‌شود."""
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        m = re.search(r"(?<![\d.])(\d{1,3}(?:[.,]\d{1,2})?)(?!\d)", line)
        if not m:
            continue
        try:
            odd = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        if odd <= 1 or odd > 1000:
            continue
        for horse in horses.values():
            if horse["n"] and (horse["n"] in line or line in horse["n"]):
                horse["w_odd"] = odd
                break

def parse_table_rows(rows):
    """استخراج محافظه‌کارانه از جدول HTML: شماره، نام و وزن باید قابل تشخیص باشند."""
    horses = {}
    weight_re = re.compile(r"(?<!\d)(\d{2,3})\s*(?:kg|کیلوگرم|کیلو)\b?", re.I)
    for row in rows:
        row = re.sub(r"\s+", " ", digits(row)).strip()
        if not row:
            continue
        wm = weight_re.search(row)
        if not wm:
            continue
        before = row[:wm.start()].strip(" |:-")
        after = row[wm.end():].strip()
        # شماره معمولاً اولین یا آخرین عدد قبل از نام/وزن است؛ فقط ردیف‌های واضح قبول می‌شوند.
        nums = re.findall(r"(?<!\d)\d{1,2}(?!\d)", before)
        if not nums:
            continue
        num = int(nums[0])
        if not 1 <= num <= 30:
            num = int(nums[-1])
        if not 1 <= num <= 30:
            continue
        name = re.sub(r"(?<!\d)\d{1,2}(?!\d)", " ", before, count=1).strip(" |:-")
        name = re.sub(r"\s+", " ", name)
        if len(name) < 2 or re.search(r"(جدول|شماره|نام اسب|وزن|سوارکار|عملکرد)", name):
            continue
        tail_nums = re.findall(r"\d+", after)
        ior = int(tail_nums[0]) if tail_nums else 0
        perf_match = re.search(r"(?:\d+\s*[-–/]\s*)+\d+", after)
        perf = perf_match.group(0).replace("–", "-").replace("/", "-") if perf_match else ""
        jockey = after.split()[-1] if after.split() else "نامشخص"
        horses[num] = {
            "n": name, "num": num, "weight": int(wm.group(1)),
            "ior": ior, "j": jockey, "perf": perf, "w_odd": None
        }
    return horses

async def ar(page, round_num, context, chat_id, save_history=True):
    cache_key = f"{date.today().isoformat()}_{round_num}"
    cached = AR_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]

    try:
        # ورود به بخش جدول‌ها فقط با عبارت‌های مشخص
        for label in ("جدول مسابقات", "نمایش دورها", "دورهای مسابقه"):
            try:
                loc = page.get_by_text(label, exact=False).first
                if await loc.is_visible():
                    await loc.click(timeout=1800)
                    await page.wait_for_timeout(500)
            except Exception:
                pass

        if not await select_round(page, round_num):
            log.warning("R%s selector not found", round_num)
            return "NO_DATA"

        await page.wait_for_timeout(2500)
        table_rows = []
        try:
            table_rows = await page.locator("table tr").all_inner_texts()
        except Exception:
            pass

        horses = parse_table_rows(table_rows)
        body = await page.locator("body").inner_text(timeout=12000)
        body = digits(body).replace("\u200c", " ")
        if len(horses) < 2:
            # fallback فقط از خطوطی که شماره + نام + وزن صریح دارند.
            lines = [re.sub(r"\s+", " ", x).strip() for x in body.splitlines() if x.strip()]
            for idx, line in enumerate(lines):
                wm = re.search(r"(?<!\d)(\d{2,3})\s*(?:kg|کیلوگرم|کیلو)\b?", line, re.I)
                if not wm:
                    continue
                left = line[:wm.start()].strip(" |:-")
                mnum = re.search(r"(?<!\d)(\d{1,2})(?!\d)", left)
                if not mnum:
                    continue
                num = int(mnum.group(1))
                name = (left[:mnum.start()] + " " + left[mnum.end():]).strip(" |:-")
                if len(name) < 2 and idx:
                    name = lines[idx-1]
                if not 1 <= num <= 30 or len(name) < 2:
                    continue
                horses[num] = {
                    "n": name, "num": num, "weight": int(wm.group(1)),
                    "ior": 0, "j": "نامشخص", "perf": "", "w_odd": None
                }

        if len(horses) < 2:
            excerpt = esc(body[:2500] or "متن صفحه خالی است")
            log.warning("R%s parsed only %s horses. Page excerpt: %s", round_num, len(horses), body[:1000])
            try:
                await context.bot.send_message(
                    MY_ID or chat_id,
                    f"⚠️ <b>خواندن جدول دور {round_num} ناموفق بود</b>\n"
                    f"اسب‌های قابل تشخیص: {len(horses)}\n"
                    f"<pre>{excerpt}</pre>",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            return "NOT_UPLOADED"

        parse_odds_from_text(body, horses)
    except Exception:
        log.exception("Scraping failed on round %s", round_num)
        return "ERROR"

    horse_list = list(horses.values())
    # محاسبه مدل بدون ضریب بازار جعلی
    iors = [h["ior"] for h in horse_list]
    imin, imax = min(iors), max(iors)
    irange = max(imax-imin, 1)
    forms = [decayed_form(h["perf"]) for h in horse_list]
    fmax = max(forms) if max(forms, default=0) > 0 else 1
    odds = [h["w_odd"] for h in horse_list]
    market_probs = normalize_market(odds)

    for idx, h in enumerate(horse_list):
        ior_norm = (h["ior"]-imin)/irange if h["ior"] else 0.5
        form_norm = decayed_form(h["perf"])/fmax
        h["combined"] = .48*ior_norm + .30*form_norm + .17*jockey_rating(h["j"]) + .05
        if market_probs:
            h["market_prob"] = market_probs[idx]*100
        else:
            h["market_prob"] = None

    model_raw = power_probs([h["combined"] for h in horse_list])
    for idx, h in enumerate(horse_list):
        # اگر بازار موجود نیست، فقط مدل عملکردی استفاده می‌شود.
        blended = (.35*model_raw[idx] + .65*market_probs[idx]) if market_probs else model_raw[idx]
        h["model_prob"] = blended*100
        h["edge"] = h["model_prob"]-h["market_prob"] if h["market_prob"] is not None else None

    total = sum(h["model_prob"] for h in horse_list) or 1
    for h in horse_list:
        h["model_prob"] = h["model_prob"]/total*100

    sorted_horses = sorted(horse_list, key=lambda x: x["model_prob"], reverse=True)
    probs = [h["model_prob"]/100 for h in sorted_horses]
    for idx, h in enumerate(sorted_horses):
        h["place_prob"] = place_probability(probs, idx, 3)*100
        h["place_fair_odd"] = round(100/h["place_prob"], 2) if h["place_prob"] else None

    win = sorted_horses[0]
    second = sorted_horses[1]
    third = sorted_horses[2] if len(sorted_horses) > 2 else second
    qp = quinella_prob(probs, 0, 1)
    qfair = round(1/qp, 2) if qp else None
    result = {
        "w": win, "p": second, "s": third, "horses": sorted_horses,
        "q": f"{win['num']}-{win['n']} + {second['num']}-{second['n']}",
        "q_fair": qfair
    }
    result["conf"] = get_confidence_level(result)
    long_shots = [h for h in sorted_horses if h["w_odd"] and h["w_odd"] >= 12]
    result["su"] = long_shots[0] if long_shots else sorted_horses[-1]
    result["qs"] = (
        f"{long_shots[0]['num']}-{long_shots[0]['n']} + {long_shots[1]['num']}-{long_shots[1]['n']}"
        if len(long_shots) >= 2 else "گزینه شگفتی دوم مشخص نیست"
    )
    if save_history:
        await save_to_history(result, round_num)
    AR_CACHE[cache_key] = (time.time(), result)
    return result

# ============================================================
# تحلیل دورها
# ============================================================
async def process_odds(chat_id, context, specific_round=None, all_rounds=False, bet_amount=None, is_reminder=False):
    if await get_github_var("AUTO_UPDATE", "true") == "false" and not is_reminder:
        await context.bot.send_message(chat_id, "🛑 تحلیل‌ها موقتاً خاموش هستند.")
        return
    if not is_reminder:
        await context.bot.send_message(chat_id, "⏳ <b>در حال تحلیل مسابقه...</b>", parse_mode="HTML")
    pw = browser = None
    try:
        pw, browser, page = await open_race_page()
        weather = await get_weather()
        msg = "🏇 <b>تحلیل مسابقات</b>\n" + weather + "\n"
        if bet_amount:
            msg += f"💰 سرمایه: <code>{bet_amount:,}</code> تومان\n"
        found = False
        rounds = range(1, 8) if all_rounds else ([specific_round] if specific_round else [])
        for r in rounds:
            result = await ar(page, r, context, chat_id, save_history=(specific_round is not None))
            if not isinstance(result, dict):
                continue
            found = True
            w, p = result["w"], result["p"]
            odds_text = f"ضریب {w['w_odd']}" if w["w_odd"] else "ضریب در دسترس نیست"
            market_text = f"{w['market_prob']:.1f}%" if w["market_prob"] is not None else "ناموجود"
            edge_text = f"{w['edge']:+.1f}%" if w["edge"] is not None else "محاسبه‌نشده"
            msg += (
                f"\n🏁 <b>دور R{r}</b>\n"
                f"🥇 <b>پیش‌بر:</b> {w['num']}-{esc(w['n'])} ({odds_text})\n"
                f"📊 مدل: {w['model_prob']:.1f}% | بازار: {market_text} | اختلاف: {edge_text}\n"
                f"🥈 <b>میان‌بر:</b> {p['num']}-{esc(p['n'])}\n"
                f"👥 <b>دوقلو امن:</b> {esc(result['q'])} | ضریب منصفانه مدل: {result['q_fair'] or 'ناموجود'}\n"
                f"🎯 <b>اطمینان:</b> {result['conf']}\n"
            )
            if bet_amount and specific_round and r == specific_round:
                if w["w_odd"]:
                    k = calculate_kelly(bet_amount, w["model_prob"], w["w_odd"], fraction=FRACTIONS["win"])
                    if k:
                        msg += f"💰 مبلغ محاسباتی: <code>{k:,}</code> تومان\n"
                else:
                    msg += "💰 مبلغ شرط محاسبه نشد؛ ضریب واقعی موجود نیست.\n"
            if specific_round:
                msg += "\n📈 <b>سه اسب برتر مدل:</b>\n"
                for h in result["horses"][:3]:
                    msg += f"{h['num']}-{esc(h['n'])}: برد {h['model_prob']:.1f}% | جایگاه {h['place_prob']:.1f}%\n"
            msg += "➖➖➖➖➖➖\n"
        if not found:
            msg += "\n⚠️ جدول مسابقات هنوز منتشر نشده یا ساختار صفحه قابل تشخیص نیست."
            await send_long_message(context, chat_id, msg)
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏰ یادآوری خودکار", callback_data=f"rem_set_{specific_round or 1}")]])
            await context.bot.send_message(chat_id, "می‌خواهید چه زمانی دوباره بررسی کنم؟", reply_markup=kb)
        else:
            await send_long_message(context, chat_id, msg)
    except Exception as exc:
        log.exception("process_odds error")
        await context.bot.send_message(chat_id, f"❌ خطا در تحلیل:\n<code>{esc(exc)}</code>", parse_mode="HTML")
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()

# ============================================================
# دوقلوها، چندپره و شگفتی
# ============================================================
async def process_quinellas(chat_id, context, variant_type):
    await context.bot.send_message(chat_id, "⏳ <b>در حال محاسبه دوقلوها...</b>", parse_mode="HTML")
    pw = browser = None
    titles = {"q_safe": "🟢 دوقلوی امن", "q_value": "💎 دوقلوی ارزش", "q_lucky": "🍀 دوقلوی شانسی"}
    try:
        pw, browser, page = await open_race_page()
        msg = titles[variant_type] + "\n"
        bet = context.user_data.get("bet_amount")
        found = False
        for r in range(1, 8):
            res = await ar(page, r, context, chat_id, save_history=False)
            if not isinstance(res, dict):
                continue
            horses = res["horses"]
            probs = [h["model_prob"]/100 for h in horses]
            candidates = []
            for i in range(len(horses)):
                for j in range(i+1, len(horses)):
                    a, b = horses[i], horses[j]
                    if variant_type == "q_safe" and (i, j) != (0, 1):
                        continue
                    if variant_type == "q_value" and not (i < 3 or j < 3):
                        continue
                    if variant_type == "q_lucky" and not (
                        (a["w_odd"] and a["w_odd"] >= 5) and (b["w_odd"] and b["w_odd"] >= 5)
                    ):
                        continue
                    prob = quinella_prob(probs, i, j)
                    if prob:
                        candidates.append({"a": a, "b": b, "prob": prob*100, "fair": 1/prob})
            if not candidates:
                continue
            candidates.sort(key=lambda x: x["prob"], reverse=True) if variant_type == "q_safe" else candidates.sort(key=lambda x: x["fair"], reverse=True)
            best = candidates[0]
            found = True
            msg += (
                f"\n🏁 <b>دور {r}</b>\n"
                f"👥 {best['a']['num']}-{esc(best['a']['n'])} + {best['b']['num']}-{esc(best['b']['n'])}\n"
                f"📊 احتمال مدل: {best['prob']:.1f}% | ضریب منصفانه تخمینی: {best['fair']:.2f}\n"
            )
            if bet and variant_type != "q_lucky":
                # ضریب منصفانه مدل، ضریب واقعی شرط‌بندی نیست؛ Kelly روی آن اجرا نمی‌شود.
                msg += "💡 برای محاسبه مبلغ شرط، ضریب واقعی بازار لازم است.\n"
            if variant_type == "q_lucky":
                msg += "🎰 گزینه پرریسک؛ احتمال از دست رفتن مبلغ بالاست.\n"
        if not found:
            msg += "\n⚠️ گزینه مناسبی پیدا نشد یا جدول منتشر نشده است."
        msg += "\n\n⚠️ ضریب منصفانه صرفاً تخمین مدل است و تضمین برد نیست."
        await send_long_message(context, chat_id, msg)
    except Exception as exc:
        log.exception("quinella error")
        await context.bot.send_message(chat_id, f"❌ خطا: {esc(exc)}", parse_mode="HTML")
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()

async def process_multileg(chat_id, context, leg_type, bet_amount=None):
    await context.bot.send_message(chat_id, "⏳ <b>در حال بررسی چندپره...</b>", parse_mode="HTML")
    pw = browser = None
    try:
        pw, browser, page = await open_race_page()
        # نبودن دور ۷ به‌تنهایی به معنی مسابقه ۶ دوره‌ای نیست؛ همه دورها جداگانه بررسی می‌شوند.
        results = {}
        for r in range(1, 8):
            results[r] = await ar(page, r, context, chat_id, save_history=False)
        available = [r for r, x in results.items() if isinstance(x, dict)]
        has7 = 7 in available
        if leg_type == "ml_6":
            rounds, title = ([2,3,4,5,6,7] if has7 else [1,2,3,4,5,6]), "۶ پره"
        elif leg_type == "ml_5":
            rounds, title = ([3,4,5,6,7] if has7 else [2,3,4,5,6]), "۵ پره"
        elif leg_type == "ml_3a":
            rounds, title = [1,2,3], "۳ پره اول"
        else:
            rounds, title = ([5,6,7] if has7 else [4,5,6]), "۳ پره دوم"
        picks, details = [], []
        for r in rounds:
            res = results.get(r)
            if isinstance(res, dict):
                h = res["w"]
                picks.append(f"{r}: {h['num']}-{h['n']}")
                details.append(h)
            else:
                picks.append(f"{r}: جدول در دسترس نیست")
        msg = f"🎯 <b>{title}</b>\n" + "\n".join(picks)
        if len(details) == len(rounds):
            prob = 1.0
            for h in details:
                prob *= h["model_prob"]/100
            msg += f"\n📊 احتمال تقریبی ترکیب: {prob*100:.3f}%"
            msg += f"\n📊 ضریب منصفانه تخمینی: {1/prob:.2f}" if prob > 0 else ""
        else:
            msg += "\n⚠️ ترکیب کامل نیست؛ برخی دورها قابل خواندن نبودند."
        msg += "\n⚠️ این محاسبه تخمینی است و ضریب واقعی چندپره محسوب نمی‌شود."
        await send_long_message(context, chat_id, msg)
    except Exception as exc:
        log.exception("multileg error")
        await context.bot.send_message(chat_id, f"❌ خطا: {esc(exc)}", parse_mode="HTML")
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()

async def process_surprises(chat_id, context, mode):
    await context.bot.send_message(chat_id, "⏳ <b>در حال بررسی گزینه‌های شگفتی...</b>", parse_mode="HTML")
    pw = browser = None
    try:
        pw, browser, page = await open_race_page()
        msg = "🎲 <b>اسب شگفتی</b>\n" if mode == "sur_h" else "🔥 <b>دوقلوی شگفتی</b>\n"
        found = False
        for r in range(1, 8):
            res = await ar(page, r, context, chat_id, save_history=False)
            if not isinstance(res, dict):
                continue
            found = True
            if mode == "sur_h":
                h = res["su"]
                odds = f"ضریب {h['w_odd']}" if h["w_odd"] else "ضریب ناموجود"
                msg += f"\n🏁 دور {r}: {h['num']}-{esc(h['n'])} | {odds}\n"
            else:
                msg += f"\n🏁 دور {r}: {esc(res['qs'])}\n"
        if not found:
            msg += "\n⚠️ جدول قابل خواندن نیست."
        await send_long_message(context, chat_id, msg)
    except Exception as exc:
        log.exception("surprise error")
        await context.bot.send_message(chat_id, f"❌ خطا: {esc(exc)}", parse_mode="HTML")
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()

async def process_value_bets(chat_id, context, bet_amount=None):
    if await get_github_var("AUTO_UPDATE", "true") == "false":
        await context.bot.send_message(chat_id, "🛑 تحلیل‌ها موقتاً خاموش هستند.")
        return
    await context.bot.send_message(chat_id, "⏳ <b>در حال بررسی شکار ارزش...</b>", parse_mode="HTML")
    pw = browser = None
    try:
        pw, browser, page = await open_race_page()
        msg = "💎 <b>شکار ارزش</b>\n"
        found = False
        for r in range(1, 8):
            res = await ar(page, r, context, chat_id, save_history=False)
            if not isinstance(res, dict):
                continue
            values = [h for h in res["horses"] if h["edge"] is not None and h["edge"] >= 5 and h["w_odd"] and h["w_odd"] > 3]
            if values:
                found = True
                msg += f"\n🏁 <b>دور {r}</b>\n"
                for h in values[:3]:
                    msg += f"💎 {h['num']}-{esc(h['n'])} | ضریب {h['w_odd']} | مدل {h['model_prob']:.1f}% | بازار {h['market_prob']:.1f}%\n"
                    if bet_amount:
                        amount = calculate_kelly(bet_amount, h["model_prob"], h["w_odd"], fraction=FRACTIONS["win"])
                        if amount:
                            msg += f"💰 مبلغ محاسباتی: <code>{amount:,}</code> تومان\n"
        if not found:
            msg += "\n⚠️ ضریب واقعی و اختلاف مثبت کافی برای گزینه ارزشمند پیدا نشد."
        await send_long_message(context, chat_id, msg)
    except Exception as exc:
        log.exception("value error")
        await context.bot.send_message(chat_id, f"❌ خطا: {esc(exc)}", parse_mode="HTML")
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()

# ============================================================
# کارهای پس‌زمینه
# ============================================================
async def clean_cache():
    while True:
        await asyncio.sleep(1800)
        now = time.time()
        for key in list(AR_CACHE):
            if now-AR_CACHE[key][0] > CACHE_TTL:
                AR_CACHE.pop(key, None)

async def check_expirations(bot):
    await asyncio.sleep(3600)
    while True:
        try:
            users = await get_allowed_users_dict()
            now = datetime.now()
            for uid, exp_str in users.items():
                try:
                    exp = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
                    if 0 <= (exp-now).days <= 2:
                        await bot.send_message(int(uid), "⚠️ اشتراک شما حدود دو روز دیگر تمام می‌شود. برای تمدید از منوی خرید اشتراک استفاده کنید.")
                except Exception:
                    pass
        except Exception:
            log.exception("expiration checker error")
        await asyncio.sleep(86400)

async def run_reminder(chat_id, context, round_num, delay_minutes):
    await asyncio.sleep(delay_minutes*60)
    await context.bot.send_message(chat_id, f"⏰ یادآوری: بررسی دور {round_num} آغاز شد.")
    await process_odds(chat_id, context, specific_round=round_num,
                       bet_amount=context.user_data.get("bet_amount"), is_reminder=True)

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
            refs = await get_referrals()
            refs.setdefault(referrer, [])
            users = await get_allowed_users_dict()
            if chat_id not in refs[referrer] and chat_id not in users:
                refs[referrer].append(chat_id)
                await set_github_var("REFERRALS", json.dumps(refs, ensure_ascii=False))
                expiry = await add_vip_days(referrer, REF_REWARD_DAYS)
                try:
                    await context.bot.send_message(int(referrer), f"🎉 دعوت موفق بود؛ {REF_REWARD_DAYS} روز هدیه ثبت شد.\nانقضا: {expiry}")
                except Exception:
                    pass
    users = await get_allowed_users_dict()
    is_new = chat_id not in users and (not MY_ID or chat_id != MY_ID)
    if is_new:
        expiry = (datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        users[chat_id] = expiry
        await set_github_var("ALLOWED_USERS", json.dumps(users, ensure_ascii=False))
        welcome = "👋 خوش آمدید!\n🎁 یک روز اشتراک آزمایشی برای شما فعال شد."
    else:
        welcome = ("👋 سلام دوباره!\n✅ ربات تحلیل مسابقات آماده است.\n"
                   "⚠️ پیش‌بینی‌ها احتمالی‌اند و تضمین برد یا سود نیستند.")
    await context.bot.send_message(update.effective_chat.id, welcome,
                                   reply_markup=get_main_menu(chat_id), parse_mode="HTML")

async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not MY_ID or str(update.effective_chat.id) != MY_ID:
        return
    try:
        target, days = context.args[0], int(context.args[1])
        if days < 1:
            raise ValueError
        users = await get_allowed_users_dict()
        expiry = (datetime.now()+timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        users[target] = expiry
        ok = await set_github_var("ALLOWED_USERS", json.dumps(users, ensure_ascii=False))
        await update.message.reply_text(
            f"✅ فعال شد تا {expiry}" if ok else "❌ ذخیره در GitHub ناموفق بود."
        )
        if ok:
            try:
                await context.bot.send_message(int(target), f"🎉 اشتراک شما {days} روز فعال شد. انقضا: {expiry}")
            except Exception:
                pass
    except Exception:
        await update.message.reply_text("قالب صحیح: /approve 123456789 30")

async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not MY_ID or str(update.effective_chat.id) != MY_ID:
        return
    try:
        target = context.args[0].strip()
        users = await get_allowed_users_dict()
        users.pop(target, None)
        ok = await set_github_var("ALLOWED_USERS", json.dumps(users, ensure_ascii=False))
        await update.message.reply_text("✅ حذف شد." if ok else "❌ ذخیره ناموفق بود.")
    except Exception:
        await update.message.reply_text("قالب صحیح: /remove 123456789")

async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not MY_ID or str(update.effective_chat.id) != MY_ID:
        return
    if not context.args:
        await update.message.reply_text("قالب صحیح: /broadcast متن پیام")
        return
    users = await get_allowed_users_dict()
    success = fail = 0
    status = await update.message.reply_text(f"⏳ ارسال به {len(users)} کاربر...")
    for uid in users:
        try:
            await context.bot.send_message(int(uid), " ".join(context.args), parse_mode="HTML")
            success += 1
            await asyncio.sleep(.12)
        except Exception:
            fail += 1
    await status.edit_text(f"✅ پایان ارسال\nموفق: {success}\nناموفق: {fail}")

# ============================================================
# دریافت متن و رسید
# ============================================================
async def handle_inputs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text
    if context.user_data.get("awaiting_bet"):
        context.user_data["awaiting_bet"] = False
        try:
            amount = int(re.sub(r"\D", "", digits(text)))
            if amount < 1000:
                raise ValueError
            context.user_data["bet_amount"] = amount
            await update.message.reply_text(f"✅ سرمایه ثبت شد: {amount:,} تومان")
        except Exception:
            await update.message.reply_text("❌ مبلغ معتبر (حداقل ۱۰۰۰ تومان) بفرستید.")
    elif context.user_data.get("awaiting_rem"):
        context.user_data["awaiting_rem"] = False
        try:
            minutes = int(re.sub(r"\D", "", digits(text)))
            if not 1 <= minutes <= 10080:
                raise ValueError
            r = context.user_data.get("rem_round", 1)
            asyncio.create_task(run_reminder(update.effective_chat.id, context, r, minutes))
            await update.message.reply_text(f"✅ یادآوری دور {r} برای {minutes} دقیقه دیگر تنظیم شد.")
        except Exception:
            await update.message.reply_text("❌ عددی بین ۱ تا ۱۰۰۸۰ دقیقه بفرستید.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if MY_ID and str(chat_id) == MY_ID:
        return
    try:
        await context.bot.forward_message(chat_id=int(MY_ID), from_chat_id=chat_id,
                                          message_id=update.message.message_id)
        await context.bot.send_message(
            int(MY_ID),
            f"🔔 رسید جدید\nکاربر: {esc(update.effective_user.first_name if update.effective_user else 'کاربر')}\n"
            f"آیدی: <code>{chat_id}</code>\nبرای فعال‌سازی: <code>/approve {chat_id} 30</code>",
            parse_mode="HTML"
        )
        await update.message.reply_text("✅ رسید دریافت شد. پس از بررسی اشتراک فعال می‌شود.")
    except Exception as exc:
        await update.message.reply_text(f"❌ ارسال رسید ناموفق بود: {esc(exc)}")

# ============================================================
# دکمه‌ها
# ============================================================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data or ""
    premium = {"r1","r2","r3","r4","r5","r6","r7","rall",
               "q_safe","q_value","q_lucky","value","ml_6","ml_5","ml_3a","ml_3b","sur_h","sur_d"}
    if data in premium and not await is_premium(chat_id):
        await query.message.reply_text("⛔ این بخش ویژه مشترکین است. از دکمه خرید اشتراک استفاده کنید.")
        return
    if data in {"on","off","admin_help","admin_refresh"} and (not MY_ID or str(chat_id) != MY_ID):
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    now = time.time()
    key = f"{chat_id}:{data}"
    if now-_last_call.get(key, 0) < COOLDOWN and data not in {"bet_set"} and not data.startswith(("rem_","adm_")):
        await query.answer(f"⏳ {int(COOLDOWN-(now-_last_call[key]))} ثانیه صبر کنید.", show_alert=True)
        return
    _last_call[key] = now

    if data == "buy_sub":
        await query.message.reply_text(
            f"💳 خرید اشتراک ویژه\nمبلغ: {SUB_PRICE} تومان\n"
            f"شماره کارت: {CARD_NUMBER}\nبه نام: {CARD_HOLDER}\n\nپس از واریز تصویر رسید را ارسال کنید."
        )
    elif data == "my_status":
        users = await get_allowed_users_dict()
        if await is_premium(chat_id):
            await query.message.reply_text(f"✅ اشتراک فعال است.\nانقضا: {users.get(str(chat_id), 'دسترسی مدیر')}")
        else:
            await query.message.reply_text("❌ اشتراک فعال نیست.")
    elif data == "invite":
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref_{chat_id}"
        refs = await get_referrals()
        await query.message.reply_text(
            f"🎁 هر دعوت موفق {REF_REWARD_DAYS} روز هدیه دارد.\n{link}\n"
            f"دعوت‌شده‌ها: {len(refs.get(str(chat_id), []))}"
        )
    elif data.startswith("adm_"):
        parts = data.split("_")
        if len(parts) >= 3:
            action, uid = parts[1], parts[2]
            users = await get_allowed_users_dict()
            if action == "ren":
                exp = await add_vip_days(uid, int(parts[3]) if len(parts)>3 else 30)
                await query.message.reply_text(f"✅ تمدید شد تا {exp}")
            elif action == "can":
                users[uid] = (datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
                ok = await set_github_var("ALLOWED_USERS", json.dumps(users, ensure_ascii=False))
                await query.message.reply_text("🚫 لغو شد." if ok else "❌ ذخیره ناموفق")
            elif action == "del":
                users.pop(uid, None)
                ok = await set_github_var("ALLOWED_USERS", json.dumps(users, ensure_ascii=False))
                await query.message.reply_text("❌ حذف شد." if ok else "❌ ذخیره ناموفق")
            await cmd_admin_panel(chat_id, context)
    elif data == "admin_refresh":
        await query.message.delete()
        await cmd_admin_panel(chat_id, context)
    elif data in {"on","off"}:
        ok = await set_github_var("AUTO_UPDATE", "true" if data == "on" else "false")
        await query.message.reply_text(("✅ روشن شد." if data=="on" else "🛑 خاموش شد.") if ok else "❌ ذخیره در GitHub ناموفق بود.")
    elif data == "history":
        await cmd_history(chat_id, context)
    elif data == "weather":
        await query.message.reply_text(await get_weather())
    elif data == "value":
        await process_value_bets(chat_id, context, context.user_data.get("bet_amount"))
    elif data == "bet_set":
        context.user_data["awaiting_bet"] = True
        await query.message.reply_text("💰 مبلغ سرمایه را به تومان بفرستید:", reply_markup=ForceReply(selective=True))
    elif data == "change_city":
        await query.message.reply_text("🏙 در این نسخه مختصات هوا ثابت است و شهر مسابقه از سایت خوانده می‌شود.")
    elif data == "admin_help":
        await cmd_admin_panel(chat_id, context)
    elif data.startswith("rem_set_"):
        try:
            r = int(data.split("_")[-1])
            context.user_data["awaiting_rem"] = True
            context.user_data["rem_round"] = r
            await query.message.reply_text("⏰ چند دقیقه دیگر یادآوری کنم؟", reply_markup=ForceReply(selective=True))
        except Exception:
            await query.message.reply_text("❌ شماره دور نامعتبر است.")
    elif data in {"q_safe","q_value","q_lucky"}:
        await process_quinellas(chat_id, context, data)
    elif data == "rall":
        await process_odds(chat_id, context, all_rounds=True, bet_amount=context.user_data.get("bet_amount"))
    elif re.fullmatch(r"r[1-7]", data):
        await process_odds(chat_id, context, specific_round=int(data[1:]), bet_amount=context.user_data.get("bet_amount"))
    elif data.startswith("ml_"):
        await process_multileg(chat_id, context, data, context.user_data.get("bet_amount"))
    elif data in {"sur_h","sur_d"}:
        await process_surprises(chat_id, context, data)
    elif data == "ignore":
        return

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled Telegram error", exc_info=context.error)

# ============================================================
# اجرای برنامه
# ============================================================
async def post_init(app):
    app.create_task(check_expirations(app.bot))
    app.create_task(clean_cache())

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("متغیر BOT_TOKEN در GitHub Secrets تعریف نشده است.")
    if not MY_ID:
        log.warning("ADMIN_ID تنظیم نشده؛ امکانات مدیریتی و ارسال گزارش مدیر غیرفعال می‌شوند.")
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
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
        await app.start()
        if app.updater:
            await app.updater.start_polling(drop_pending_updates=True, bootstrap_retries=-1)
        log.info("HorseBot started")
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
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("CRITICAL ERROR")
        sys.exit(1)
