import os
import re
import sys
import json
import time
import html
import asyncio
import itertools
from datetime import datetime, timedelta, date

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from playwright.async_api import async_playwright

# ============================================================
# Configuration
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GH_PAT = os.getenv("GH_PAT", "")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "")
USER_TOKEN = os.getenv("USER_TOKEN", "")
USER_DEVICE_ID = os.getenv("USER_DEVICE_ID", "")
USER_UDI = os.getenv("USER_UDI", "")
ADMIN_ID = "184149177"

CARD_NUMBER = os.getenv("CARD_NUMBER", "6219-8618-0229-0089")
CARD_HOLDER = os.getenv("CARD_HOLDER", "مرتضی عزیزی")
SUB_PRICE = "250,000"
REF_REWARD_DAYS = 3

SITE_URL = "https://mosharekat.asbdavani.app/"
CACHE_TTL = 300
COOLDOWN = 45
AR_CACHE = {}
LAST_CALL = {}
BACKGROUND_TASKS = set()
TELEGRAM_BOT = None

JOCKEY_TIERS = {
    "S": ["رحمانی", "قهرمانی", "نوروزی"],
    "A": ["وحدانی", "مرادی", "صوفیان", "خواجوی"],
    "B": ["آقاجانی", "حیدری", "نصیری", "مسعودی"],
    "C": ["احمدی", "رضایی"],
}
JOCKEY_RATINGS = {"S": .95, "A": .75, "B": .55, "C": .40, "D": .28}
RANK_POINTS = {"1": 10, "2": 6, "3": 3, "4": 1, "5": .5}
FRACTIONS = {"win": .25, "place": .20, "quinella": .15, "parlay": .08}


# ============================================================
# General helpers
# ============================================================
def clean_text(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def norm_digits(value):
    return str(value).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def spawn_task(coro):
    task = asyncio.create_task(coro)
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    return task


def split_safe(text, limit=3900):
    chunks, current = [], ""
    for line in text.splitlines(True):
        if len(current) + len(line) > limit and current:
            chunks.append(current)
            current = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current += line
    if current:
        chunks.append(current)
    return chunks or [""]


async def send_long(bot, chat_id, text):
    for part in split_safe(text):
        try:
            await bot.send_message(chat_id, part, parse_mode="HTML")
        except Exception:
            await bot.send_message(chat_id, re.sub(r"<[^>]*>", "", part))
        await asyncio.sleep(.15)


# ============================================================
# GitHub repository variables and subscriptions
# ============================================================
def gh_headers():
    return {"Authorization": f"Bearer {GH_PAT}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


async def get_github_var(name, default=""):
    if not GH_PAT or not GITHUB_REPOSITORY:
        return default
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/variables/{name}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(url, headers=gh_headers())
            if res.status_code == 200:
                return res.json().get("value", default)
    except Exception as exc:
        print("GitHub read error:", exc)
    return default


async def set_github_var(name, value):
    if not GH_PAT or not GITHUB_REPOSITORY:
        print("GitHub variables are not configured.")
        return False
    base = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/variables"
    url = f"{base}/{name}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            res = await client.get(url, headers=gh_headers())
            if res.status_code == 404:
                res = await client.post(base, headers=gh_headers(),
                                        json={"name": name, "value": str(value)})
            elif res.status_code == 200:
                res = await client.patch(url, headers=gh_headers(),
                                         json={"name": name, "value": str(value)})
            else:
                print("GitHub lookup failed:", res.status_code, res.text[:300])
                return False
            if res.status_code not in (201, 204):
                print("GitHub write failed:", res.status_code, res.text[:300])
                return False
            return True
    except Exception as exc:
        print("GitHub write error:", exc)
        return False


async def get_allowed_users():
    raw = await get_github_var("ALLOWED_USERS", "{}")
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def save_allowed_users(users):
    return await set_github_var("ALLOWED_USERS", json.dumps(users, ensure_ascii=False))


async def is_premium(chat_id):
    uid = str(chat_id)
    if uid == ADMIN_ID:
        return True
    users = await get_allowed_users()
    try:
        exp = datetime.strptime(users.get(uid, ""), "%Y-%m-%d %H:%M:%S")
        return datetime.now() <= exp
    except Exception:
        return False


async def add_vip_days(user_id, days):
    users = await get_allowed_users()
    now = datetime.now()
    try:
        current = datetime.strptime(users.get(str(user_id), ""), "%Y-%m-%d %H:%M:%S")
        if current < now:
            current = now
    except Exception:
        current = now
    expiry = current + timedelta(days=days)
    users[str(user_id)] = expiry.strftime("%Y-%m-%d %H:%M:%S")
    if not await save_allowed_users(users):
        return None
    return users[str(user_id)]


# ============================================================
# Math / analysis
# ============================================================
def jockey_rating(name):
    for tier, names in JOCKEY_TIERS.items():
        if any(n in (name or "") for n in names):
            return JOCKEY_RATINGS[tier]
    return JOCKEY_RATINGS["D"]


def decayed_form(perf, decay=.85):
    if not perf:
        return 0.0
    total = 0.0
    for i, rank in enumerate(re.split(r"[-،,\s]+", str(perf))):
        rank = norm_digits(rank).strip()
        if rank.isdigit():
            total += RANK_POINTS.get(rank, 0) * decay ** i
    return total


def normalize_market(odds):
    valid = [1 / x if x and x > 1 else 0 for x in odds]
    total = sum(valid)
    return [x / total for x in valid] if total else [0.0] * len(odds)


def power_probs(scores, power=1.8):
    positive = [max(0.001, float(x)) for x in scores]
    vals = [x ** power for x in positive]
    total = sum(vals)
    return [x / total for x in vals] if total else [1 / len(vals)] * len(vals)


def place_probability(probs, index, num_places=3):
    # Approximation under a Plackett-Luce ordering model.
    p = probs[index]
    if p <= 0:
        return 0.0
    result = p
    for j, pj in enumerate(probs):
        if j == index or pj >= 1:
            continue
        result += pj * p / max(1e-9, 1 - pj)
    if num_places >= 3:
        for j, pj in enumerate(probs):
            if j == index:
                continue
            for k, pk in enumerate(probs):
                if k in (index, j) or pj + pk >= 1:
                    continue
                result += pj * pk / max(1e-9, 1-pj) * p / max(1e-9, 1-pj-pk)
    return min(1.0, result)


def quinella_prob(probs, i, j):
    pi, pj = probs[i], probs[j]
    if pi <= 0 or pj <= 0:
        return 0.0
    return pi * pj / max(1e-9, 1-pi) + pj * pi / max(1e-9, 1-pj)


def calculate_kelly(bankroll, model_prob, decimal_odds, fraction=.25,
                    min_bet=1000, max_pct=.05, confidence=.55):
    if not decimal_odds or decimal_odds <= 1 or bankroll <= 0:
        return 0
    market_prob = 100 / decimal_odds
    p_true = (model_prob * confidence + market_prob * (1-confidence)) / 100
    b = decimal_odds - 1
    q = 1-p_true
    f = (b*p_true-q)/b
    if f <= 0:
        return 0
    amount = min(bankroll*f*fraction, bankroll*max_pct)
    return int(amount//1000)*1000 if amount >= min_bet else 0


def confidence_label(horses):
    if len(horses) < 2:
        return "⚠️ پایین"
    gap = horses[0]["model_prob"] - horses[1]["model_prob"]
    if gap >= 12:
        return "🔥 بالا"
    if gap >= 6:
        return "⚖️ متوسط"
    return "⚠️ پایین"


# ============================================================
# Weather
# ============================================================
async def get_weather():
    try:
        url = ("https://api.open-meteo.com/v1/forecast?"
               "latitude=31.03&longitude=61.50&current=temperature_2m,wind_speed_10m")
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(url)
            res.raise_for_status()
            cur = res.json()["current"]
            return f"🌡 دما: {cur['temperature_2m']}°C | 💨 باد: {cur['wind_speed_10m']} km/h"
    except Exception:
        return "⚠️ اطلاعات هوا در دسترس نیست. (مختصات پیش‌فرض ایران جنوبی است)"


# ============================================================
# Racing website scraping
# ============================================================
async def prepare_page(page):
    await page.goto(SITE_URL, wait_until="domcontentloaded", timeout=90000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(2500)

    # Select season only when a visible, explicit season selector is present.
    candidates = page.locator("select")
    for i in range(await candidates.count()):
        el = candidates.nth(i)
        try:
            if await el.is_visible():
                opts = await el.locator("option").all_text_contents()
                if any("فصل" in x or "سال" in x or "۱۴۰۵" in x or "1405" in x for x in opts):
                    selected = await el.locator("option:checked").inner_text()
                    if not selected.strip():
                        await el.select_option(index=1)
                    break
        except Exception:
            continue


async def click_round(page, round_no):
    # Prefer exact visible controls; avoid broad substring matching on the whole page.
    labels = [f"R {round_no}", f"R{round_no}", f"دور {round_no}"]
    for label in labels:
        loc = page.get_by_text(label, exact=True)
        try:
            count = await loc.count()
            for i in range(min(count, 5)):
                item = loc.nth(i)
                if await item.is_visible():
                    await item.click(timeout=4000)
                    await page.wait_for_timeout(1800)
                    return True
        except Exception:
            pass
    # Tab/button fallback with exact normalized text.
    for selector in ("button", "[role=tab]", "[role=button]", "a"):
        loc = page.locator(selector)
        try:
            for i in range(await loc.count()):
                item = loc.nth(i)
                if not await item.is_visible():
                    continue
                txt = clean_text(await item.inner_text())
                if txt in labels:
                    await item.click(timeout=4000)
                    await page.wait_for_timeout(1800)
                    return True
        except Exception:
            continue
    return False


async def extract_rows(page):
    """Read structured table rows only. Never fall back to the whole page body."""
    rows = []
    tables = page.locator("table")
    try:
        for ti in range(await tables.count()):
            table = tables.nth(ti)
            if not await table.is_visible():
                continue
            trs = table.locator("tr")
            for ri in range(await trs.count()):
                tr = trs.nth(ri)
                cells = tr.locator("th,td")
                count = await cells.count()
                if count < 3:
                    continue
                vals = [clean_text(await cells.nth(ci).inner_text()) for ci in range(count)]
                if any(vals):
                    rows.append(vals)
    except Exception as exc:
        print("table row extraction:", exc)

    # Vuetify / div-grid fallback: inspect row containers, not the full document.
    if not rows:
        for selector in (".v-data-table__tr", ".v-data-table tr",
                         "[role=row]", ".table-row", ".ant-table-row"):
            try:
                loc = page.locator(selector)
                if await loc.count() == 0:
                    continue
                for ri in range(await loc.count()):
                    row = loc.nth(ri)
                    if not await row.is_visible():
                        continue
                    cells = row.locator("th,td,[role=cell],[role=gridcell],.v-data-table__td")
                    vals = [clean_text(await cells.nth(ci).inner_text())
                            for ci in range(await cells.count())]
                    if len(vals) >= 3 and any(vals):
                        rows.append(vals)
                if rows:
                    break
            except Exception:
                continue
    return rows


def parse_horse_rows(rows):
    """Conservative parser: only accepts rows with a horse number and identifiable fields."""
    horses = {}
    for row in rows:
        cells = [clean_text(x) for x in row]
        joined = " | ".join(cells)
        if not joined or any(x in joined for x in ("نام اسب", "شماره", "نام مالک", "نام سوارکار")):
            continue

        number = None
        for cell in cells[:4]:
            m = re.fullmatch(r"\D*(\d{1,2})\D*", norm_digits(cell))
            if m:
                candidate = int(m.group(1))
                if 1 <= candidate <= 30:
                    number = candidate
                    break
        if number is None:
            continue

        # Find likely horse name: non-numeric cell, not just a weight/odds/form value.
        name = ""
        for cell in cells:
            if (len(cell) >= 2 and not re.fullmatch(r"[\d.,:/\-]+", norm_digits(cell))
                    and not re.search(r"\bkg\b|کیلو|ضریب|وزن|مقام", cell, re.I)
                    and not re.fullmatch(r"R\s*\d+", cell, re.I)):
                name = cell
                break
        if not name:
            continue

        weight = None
        ior = None
        perf = ""
        jockey = ""
        odds = None
        for cell in cells:
            normalized = norm_digits(cell)
            wm = re.search(r"(\d{2,3}(?:\.\d+)?)\s*(?:kg|کیلو)", normalized, re.I)
            if wm:
                weight = float(wm.group(1))
            if re.fullmatch(r"\d{1,3}", normalized) and ior is None:
                val = int(normalized)
                if 1 <= val <= 150:
                    ior = val
            if re.fullmatch(r"(?:\d[-\s,]*){1,10}", normalized) and "-" in normalized:
                perf = normalized
            om = re.fullmatch(r"(\d{1,3}(?:\.\d{1,2})?)", normalized)
            if om and float(om.group(1)) > 1:
                # Odds are not assigned from ambiguous generic numeric cells.
                pass

        # Heuristic jockey: last plausible Persian/Latin text cell other than horse name.
        text_cells = [x for x in cells if x != name and len(x) >= 3
                      and not re.search(r"کیلو|ضریب|وزن|مالک|مربی|سن|عملکرد", x)]
        if text_cells:
            jockey = text_cells[-1]

        horses[number] = {
            "num": number, "n": name, "weight": weight,
            "ior": ior if ior is not None else 50,
            "j": jockey, "perf": perf, "w_odd": None
        }
    return horses


async def send_debug_to_admin(page, context, round_no, reason):
    if not ADMIN_ID or TELEGRAM_BOT is None:
        return
    try:
        html_path = f"/tmp/race_debug_R{round_no}.html"
        png_path = f"/tmp/race_debug_R{round_no}.png"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(await page.content())
        await page.screenshot(path=png_path, full_page=True)
        await TELEGRAM_BOT.send_message(
            int(ADMIN_ID),
            f"🔎 اشکال استخراج جدول دور R{round_no}\nعلت: {html.escape(reason)}\n"
            f"آدرس: {html.escape(page.url)}"
        )
        with open(html_path, "rb") as f:
            await TELEGRAM_BOT.send_document(int(ADMIN_ID), f, filename=f"race_debug_R{round_no}.html")
        with open(png_path, "rb") as f:
            await TELEGRAM_BOT.send_photo(int(ADMIN_ID), f, caption=f"نمای صفحه R{round_no}")
    except Exception as exc:
        print("debug send error:", exc)


def analyze_horses(horses):
    horse_list = list(horses.values())
    if len(horse_list) < 2:
        return None

    # Do not invent market odds. If odds are absent, model is based on form,
    # rating and jockey only; market probability is explicitly unavailable.
    valid_odds = [h["w_odd"] for h in horse_list]
    has_market = all(x is not None and x > 1 for x in valid_odds)
    market_probs = normalize_market(valid_odds) if has_market else [0.0] * len(horse_list)

    forms = [decayed_form(h["perf"]) for h in horse_list]
    max_form = max(forms) if max(forms) > 0 else 1
    iors = [h["ior"] for h in horse_list]
    lo, hi = min(iors), max(iors)
    span = max(hi-lo, 1)

    scores = []
    for i, h in enumerate(horse_list):
        ior_norm = (h["ior"]-lo)/span
        form_norm = forms[i]/max_form
        j_norm = jockey_rating(h["j"])
        market_component = market_probs[i] if has_market else 0
        score = (.40*market_component + .30*ior_norm + .20*form_norm
                 + .10*j_norm) if has_market else (.55*ior_norm + .30*form_norm + .15*j_norm)
        h["market_prob"] = market_probs[i]*100 if has_market else None
        scores.append(max(score, .01))

    raw = power_probs(scores)
    for i, h in enumerate(horse_list):
        h["model_prob"] = raw[i]*100
        h["edge"] = (h["model_prob"]-h["market_prob"]) if has_market else None

    ranked = sorted(horse_list, key=lambda x: x["model_prob"], reverse=True)
    probs = [h["model_prob"]/100 for h in ranked]
    for i, h in enumerate(ranked):
        h["place_prob"] = place_probability(probs, i)*100
        h["place_fair_odd"] = round(100/h["place_prob"], 2) if h["place_prob"] else None

    first, second = ranked[0], ranked[1]
    qprob = quinella_prob(probs, 0, 1)
    longshots = [h for h in ranked if h["w_odd"] and h["w_odd"] >= 8]
    surprise = longshots[0] if longshots else ranked[-1]
    surprise_pair = longshots[:2] if len(longshots) >= 2 else ranked[-2:]
    return {
        "w": first, "p": second, "s": ranked[2] if len(ranked)>2 else second,
        "horses": ranked,
        "q": f"{first['num']}-{first['n']} + {second['num']}-{second['n']}",
        "q_fair": round(1/qprob, 2) if qprob else None,
        "q_prob": qprob*100,
        "conf": confidence_label(ranked),
        "su": surprise,
        "qs": " + ".join(f"{h['num']}-{h['n']}" for h in surprise_pair),
        "has_market": has_market
    }


async def scrape_round(page, context, round_no):
    # Cache key includes season/date/url, rather than only the round number.
    cache_key = f"{date.today().isoformat()}|{page.url}|R{round_no}"
    cached = AR_CACHE.get(cache_key)
    if cached and time.time()-cached[0] < CACHE_TTL:
        return cached[1]

    if not await click_round(page, round_no):
        await send_debug_to_admin(page, context, round_no, "دکمه/زبانه دور پیدا نشد")
        return "NO_DATA"

    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    await page.wait_for_timeout(1800)

    rows = await extract_rows(page)
    horses = parse_horse_rows(rows)
    if len(horses) < 2:
        await send_debug_to_admin(page, context, round_no,
                                  f"ردیف‌های جدول قابل تشخیص نیستند؛ تعداد اسب استخراج‌شده: {len(horses)}")
        return "NOT_UPLOADED"

    result = analyze_horses(horses)
    if not result:
        return "NO_DATA"
    AR_CACHE[cache_key] = (time.time(), result)
    return result


# ============================================================
# Telegram UI
# ============================================================
def main_menu(chat_id):
    kb = []
    if str(chat_id) == ADMIN_ID:
        kb.append([InlineKeyboardButton("🟢 روشن", callback_data="on"),
                   InlineKeyboardButton("🔴 خاموش", callback_data="off")])
    kb += [
        [InlineKeyboardButton("🏁 دور ۱", callback_data="r1"),
         InlineKeyboardButton("🏁 دور ۲", callback_data="r2"),
         InlineKeyboardButton("🏁 دور ۳", callback_data="r3")],
        [InlineKeyboardButton("🏁 دور ۴", callback_data="r4"),
         InlineKeyboardButton("🏁 دور ۵", callback_data="r5"),
         InlineKeyboardButton("🏁 دور ۶", callback_data="r6")],
        [InlineKeyboardButton("🏁 دور ۷", callback_data="r7"),
         InlineKeyboardButton("📊 تحلیل کل", callback_data="rall")],
        [InlineKeyboardButton("🟢 دوقلوی امن", callback_data="q_safe"),
         InlineKeyboardButton("💎 دوقلوی ارزش", callback_data="q_value"),
         InlineKeyboardButton("🍀 دوقلوی شانسی", callback_data="q_lucky")],
        [InlineKeyboardButton("💎 شکار ارزش", callback_data="value"),
         InlineKeyboardButton("📜 سابقه تحلیل‌ها", callback_data="history")],
        [InlineKeyboardButton("🎯 ۶ پره", callback_data="ml_6"),
         InlineKeyboardButton("🎯 ۵ پره", callback_data="ml_5")],
        [InlineKeyboardButton("🎯 ۳ پره اول", callback_data="ml_3a"),
         InlineKeyboardButton("🎯 ۳ پره دوم", callback_data="ml_3b")],
        [InlineKeyboardButton("🎲 اسب شگفتی", callback_data="sur_h"),
         InlineKeyboardButton("🔥 دوقلوی شگفتی", callback_data="sur_d")],
        [InlineKeyboardButton("💰 تعیین مبلغ شرط", callback_data="bet_set")],
        [InlineKeyboardButton("💳 خرید اشتراک / تمدید", callback_data="buy_sub"),
         InlineKeyboardButton("👤 وضعیت اشتراک من", callback_data="my_status")],
        [InlineKeyboardButton("🎁 دعوت از دوستان", callback_data="invite")],
        [InlineKeyboardButton("🌤 وضعیت هوا", callback_data="weather")],
    ]
    if str(chat_id) == ADMIN_ID:
        kb.append([InlineKeyboardButton("👑 پنل مدیریت", callback_data="admin_help")])
    return InlineKeyboardMarkup(kb)


async def history_save(result, round_no):
    raw = await get_github_var("BOT_HISTORY", "[]")
    try:
        history = json.loads(raw)
    except Exception:
        history = []
    history.insert(0, {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "round": round_no,
        "win": f"{result['w']['num']}-{result['w']['n']}",
        "quinella": result["q"],
        "confidence": result["conf"],
    })
    await set_github_var("BOT_HISTORY", json.dumps(history[:5], ensure_ascii=False))


async def open_racing_context():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
    )
    context = await browser.new_context(
        viewport={"width": 1440, "height": 1000},
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
    )
    cookies = []
    for name, value in (("auth_token", USER_TOKEN), ("device_id", USER_DEVICE_ID),
                        ("_udi", USER_UDI)):
        if value:
            cookies.append({"name": name, "value": value,
                            "domain": ".asbdavani.app", "path": "/"})
    if cookies:
        await context.add_cookies(cookies)
    page = await context.new_page()
    await prepare_page(page)
    body = await page.locator("body").inner_text()
    if "ورود" in body[:1000] and not any(x in body for x in ("جدول مسابقات", "نمایش دورها")):
        await browser.close()
        await pw.stop()
        raise RuntimeError("نشست سایت معتبر نیست یا کوکی‌ها منقضی شده‌اند.")
    return pw, browser, context, page


async def process_rounds(chat_id, context, selected=None, all_rounds=False,
                         bet_amount=None, reminder=False):
    if await get_github_var("AUTO_UPDATE", "true") == "false" and not reminder:
        await context.bot.send_message(chat_id, "🛑 تحلیل موقتاً توسط مدیریت خاموش شده است.")
        return
    if not reminder:
        await context.bot.send_message(chat_id, "⏳ در حال دریافت جدول و تحلیل...")
    weather = await get_weather()
    pw = browser = None
    try:
        pw, browser, web_context, page = await open_racing_context()
        rounds = range(1, 8) if all_rounds else [selected]
        output = f"🏇 <b>تحلیل مسابقات</b>\n{weather}\n"
        if bet_amount:
            output += f"💰 سرمایه ثبت‌شده: <code>{bet_amount:,}</code> تومان\n"
        output += "━━━━━━━━━━━━━━\n"
        found = 0
        for r in rounds:
            result = await scrape_round(page, web_context, r)
            if not isinstance(result, dict):
                output += f"\n🏁 <b>دور R{r}</b>\n⚠️ جدول این دور قابل استخراج نیست؛ پیش‌بینی ارائه نشد.\n"
                continue
            found += 1
            if not all_rounds:
                await history_save(result, r)
            w, p = result["w"], result["p"]
            output += (
                f"\n🏁 <b>دور R{r}</b>\n"
                f"🥇 پیش‌بر: <b>{w['num']}-{html.escape(w['n'])}</b>\n"
                f"📊 احتمال مدل: {w['model_prob']:.1f}%\n"
                f"🥈 میان‌بر: <b>{p['num']}-{html.escape(p['n'])}</b>\n"
                f"🥉 گزینه سوم: <b>{result['s']['num']}-{html.escape(result['s']['n'])}</b>\n"
                f"👥 دوقلو: <b>{html.escape(result['q'])}</b>\n"
                f"📈 احتمال تخمینی دوقلو: {result['q_prob']:.1f}% | ضریب منصفانه مدل: "
                f"{result['q_fair'] if result['q_fair'] else 'نامشخص'}\n"
                f"🎯 اطمینان: {result['conf']}\n"
                f"🎲 شگفتی: <b>{result['su']['num']}-{html.escape(result['su']['n'])}</b>\n"
            )
            if result["has_market"]:
                output += "📌 ضرایب بازار در دسترس بود.\n"
            else:
                output += "⚠️ ضرایب بازار استخراج نشد؛ تحلیل فقط بر اساس امتیاز، فرم و سوارکار است.\n"
            output += "━━━━━━━━━━━━━━\n"
        if not found:
            output += ("\n⚠️ هیچ جدول معتبری استخراج نشد. فایل HTML و تصویر صفحه برای مدیر ارسال شد "
                       "تا ساختار واقعی جدول بررسی شود.")
            await context.bot.send_message(
                chat_id, output, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏰ یادآوری خودکار", callback_data=f"rem_set_{selected or 1}")
                ]])
            )
        else:
            await send_long(context.bot, chat_id, output)
    except Exception as exc:
        await context.bot.send_message(chat_id, f"❌ خطا در دریافت مسابقات:\n<code>{html.escape(str(exc))}</code>",
                                       parse_mode="HTML")
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()


async def process_quinella(chat_id, context, kind):
    await context.bot.send_message(chat_id, "⏳ در حال بررسی دوقلوها...")
    pw = browser = None
    try:
        pw, browser, web_context, page = await open_racing_context()
        lines = [f"🏇 <b>دوقلوها: {kind}</b>\n"]
        for r in range(1, 8):
            res = await scrape_round(page, web_context, r)
            if not isinstance(res, dict):
                continue
            horses = res["horses"]
            probs = [h["model_prob"]/100 for h in horses]
            pairs = []
            for i, j in itertools.combinations(range(len(horses)), 2):
                a, b = horses[i], horses[j]
                if kind == "امن" and (i, j) != (0, 1):
                    continue
                if kind == "ارزش" and not (i < 3 or j < 3):
                    continue
                if kind == "شانسی" and i < 3 and j < 3:
                    continue
                probability = quinella_prob(probs, i, j)
                if probability:
                    pairs.append((probability, a, b))
            if pairs:
                # Safe: highest model probability. Other types: choose best probability among eligible
                # pairs; do not pretend fair odds are real market odds.
                probability, a, b = max(pairs, key=lambda x: x[0])
                lines.append(
                    f"\n🏁 <b>دور R{r}</b>\n{a['num']}-{html.escape(a['n'])} + "
                    f"{b['num']}-{html.escape(b['n'])}\n"
                    f"احتمال تخمینی: {probability*100:.1f}% | "
                    f"ضریب منصفانه مدل: {1/probability:.2f}\n"
                )
        await send_long(context.bot, chat_id, "\n".join(lines) if len(lines)>1 else "⚠️ داده کافی برای دوقلوها پیدا نشد.")
    except Exception as exc:
        await context.bot.send_message(chat_id, f"❌ {html.escape(str(exc))}")
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()


async def process_multileg(chat_id, context, kind):
    await context.bot.send_message(chat_id, "⏳ در حال بررسی پره‌ها...")
    pw = browser = None
    try:
        pw, browser, web_context, page = await open_racing_context()
        rounds = {"ml_6": [2,3,4,5,6,7], "ml_5": [3,4,5,6,7],
                  "ml_3a": [1,2,3], "ml_3b": [5,6,7]}[kind]
        picks, probs = [], []
        for r in rounds:
            res = await scrape_round(page, web_context, r)
            if not isinstance(res, dict):
                picks.append(f"R{r}: نامشخص")
                probs.append(None)
            else:
                h = res["w"]
                picks.append(f"R{r}: {h['num']}-{h['n']}")
                probs.append(h["model_prob"]/100)
        valid = [p for p in probs if p is not None]
        msg = "🎯 <b>ترکیب پره‌ها</b>\n\n" + "\n".join(html.escape(x) for x in picks)
        if len(valid) == len(rounds):
            combined = 1.0
            for p in valid:
                combined *= p
            msg += f"\n\nاحتمال تقریبی ترکیب: {combined*100:.4f}%"
            msg += f"\nضریب منصفانه مدل: {1/combined:.2f}" if combined else ""
        else:
            msg += "\n\n⚠️ چون بعضی دورها داده معتبر ندارند، احتمال ترکیبی محاسبه نشد."
        await context.bot.send_message(chat_id, msg, parse_mode="HTML")
    except Exception as exc:
        await context.bot.send_message(chat_id, f"❌ {html.escape(str(exc))}")
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()


async def process_surprise(chat_id, context, mode):
    await context.bot.send_message(chat_id, "⏳ در حال بررسی گزینه‌های شگفتی...")
    pw = browser = None
    try:
        pw, browser, web_context, page = await open_racing_context()
        lines = ["🎲 <b>گزینه‌های شگفتی</b>\n"]
        for r in range(1, 8):
            res = await scrape_round(page, web_context, r)
            if not isinstance(res, dict):
                continue
            if mode == "sur_h":
                h = res["su"]
                lines.append(f"R{r}: {h['num']}-{html.escape(h['n'])}")
            else:
                lines.append(f"R{r}: {html.escape(res['qs'])}")
        await send_long(context.bot, chat_id, "\n".join(lines) if len(lines)>1 else "⚠️ گزینه‌ای با داده معتبر پیدا نشد.")
    except Exception as exc:
        await context.bot.send_message(chat_id, f"❌ {html.escape(str(exc))}")
    finally:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()


async def cmd_history(chat_id, context):
    raw = await get_github_var("BOT_HISTORY", "[]")
    try:
        items = json.loads(raw)
    except Exception:
        items = []
    if not items:
        await context.bot.send_message(chat_id, "📜 هنوز سابقه‌ای ثبت نشده است.")
        return
    msg = "📜 <b>۵ تحلیل آخر</b>\n\n"
    for x in items[:5]:
        msg += (f"📅 {html.escape(x.get('date',''))} | دور {x.get('round')}\n"
                f"🥇 {html.escape(x.get('win',''))}\n"
                f"👥 {html.escape(x.get('quinella',''))}\n"
                f"🎯 {html.escape(x.get('confidence',''))}\n──────────\n")
    await context.bot.send_message(chat_id, msg, parse_mode="HTML")


# ============================================================
# Admin / subscriptions / referrals
# ============================================================
async def admin_panel(chat_id, context):
    users = await get_allowed_users()
    auto = await get_github_var("AUTO_UPDATE", "true")
    msg = f"👑 <b>پنل مدیریت</b>\nوضعیت تحلیل: {'روشن' if auto == 'true' else 'خاموش'}\nتعداد کاربران: {len(users)}"
    kb = [[InlineKeyboardButton("🔄 بروزرسانی", callback_data="admin_refresh")]]
    for uid, expiry in list(users.items())[:30]:
        kb.append([InlineKeyboardButton(f"کاربر {uid} | {expiry}", callback_data="ignore")])
        kb.append([
            InlineKeyboardButton("➕ تمدید ۳۰ روز", callback_data=f"adm_ren_{uid}_30"),
            InlineKeyboardButton("🚫 لغو", callback_data=f"adm_can_{uid}"),
            InlineKeyboardButton("❌ حذف", callback_data=f"adm_del_{uid}")
        ])
    await context.bot.send_message(chat_id, msg, parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(kb))


async def get_referrals():
    try:
        return json.loads(await get_github_var("REFERRALS", "{}"))
    except Exception:
        return {}


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_chat.id)
    if context.args and context.args[0].startswith("ref_"):
        referrer = context.args[0][4:]
        if referrer != uid:
            referrals = await get_referrals()
            invited = referrals.setdefault(referrer, [])
            if uid not in invited:
                invited.append(uid)
                if await set_github_var("REFERRALS", json.dumps(referrals)):
                    expiry = await add_vip_days(referrer, REF_REWARD_DAYS)
                    if expiry:
                        try:
                            await context.bot.send_message(int(referrer),
                                f"🎁 دعوت جدید ثبت شد؛ {REF_REWARD_DAYS} روز هدیه. انقضا: {expiry}")
                        except Exception:
                            pass

    users = await get_allowed_users()
    if uid not in users and uid != ADMIN_ID:
        expiry = (datetime.now()+timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        users[uid] = expiry
        await save_allowed_users(users)
        welcome = "👋 خوش آمدید!\n🎁 یک روز دسترسی آزمایشی برای شما فعال شد."
    else:
        welcome = "👋 خوش آمدید!\nاز منوی زیر برای تحلیل مسابقات استفاده کنید."
    await context.bot.send_message(update.effective_chat.id, welcome,
                                   reply_markup=main_menu(uid))


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != ADMIN_ID:
        return
    try:
        target, days = context.args[0], int(context.args[1])
        if days < 1 or days > 3650:
            raise ValueError
        expiry = await add_vip_days(target, days)
        if not expiry:
            raise RuntimeError("ذخیره اشتراک در GitHub ناموفق بود.")
        await update.message.reply_text(f"✅ اشتراک {target} تا {expiry} فعال شد.")
        try:
            await context.bot.send_message(int(target), f"🎉 اشتراک شما به مدت {days} روز فعال/تمدید شد.\nانقضا: {expiry}")
        except Exception:
            pass
    except Exception as exc:
        await update.message.reply_text(f"❌ دستور صحیح: /approve 123456789 30\n{html.escape(str(exc))}")


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != ADMIN_ID or not context.args:
        return
    users = await get_allowed_users()
    target = context.args[0]
    users.pop(target, None)
    if await save_allowed_users(users):
        await update.message.reply_text(f"🗑 دسترسی {target} حذف شد.")
    else:
        await update.message.reply_text("❌ ذخیره تغییرات ناموفق بود.")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("متن پیام را پس از دستور بنویسید.")
        return
    users = await get_allowed_users()
    success = fail = 0
    for uid in users:
        try:
            await context.bot.send_message(int(uid), " ".join(context.args))
            success += 1
            await asyncio.sleep(.1)
        except Exception:
            fail += 1
    await update.message.reply_text(f"پایان ارسال. موفق: {success} | ناموفق: {fail}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    if str(uid) == ADMIN_ID:
        return
    await context.bot.forward_message(ADMIN_ID, uid, update.message.message_id)
    user = update.effective_user
    await context.bot.send_message(
        ADMIN_ID,
        f"🔔 رسید جدید\nنام: {html.escape(user.full_name or 'کاربر')}\n"
        f"آیدی: <code>{uid}</code>\n/approve {uid} 30",
        parse_mode="HTML"
    )
    await update.message.reply_text("✅ رسید دریافت شد و برای بررسی ارسال شد.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    if context.user_data.get("awaiting_bet"):
        context.user_data["awaiting_bet"] = False
        try:
            amount = int(re.sub(r"\D", "", norm_digits(update.message.text)))
            if amount < 1000:
                raise ValueError
            context.user_data["bet_amount"] = amount
            await update.message.reply_text(f"✅ سرمایه ثبت شد: {amount:,} تومان")
        except Exception:
            await update.message.reply_text("❌ مبلغ معتبر (حداقل ۱۰۰۰) ارسال کنید.")
    elif context.user_data.get("awaiting_rem"):
        context.user_data["awaiting_rem"] = False
        try:
            minutes = int(re.sub(r"\D", "", norm_digits(update.message.text)))
            if not 1 <= minutes <= 10080:
                raise ValueError
            round_no = context.user_data.get("rem_round", 1)
            async def reminder_job():
                await asyncio.sleep(minutes*60)
                await process_rounds(update.effective_chat.id, context,
                                      selected=round_no, reminder=True,
                                      bet_amount=context.user_data.get("bet_amount"))
            spawn_task(reminder_job())
            await update.message.reply_text(f"✅ یادآوری برای {minutes} دقیقه دیگر تنظیم شد.")
        except Exception:
            await update.message.reply_text("❌ تعداد دقیقه معتبر نیست.")


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.message.chat_id
    data = query.data or ""

    premium = {"r1","r2","r3","r4","r5","r6","r7","rall","q_safe","q_value",
               "q_lucky","value","ml_6","ml_5","ml_3a","ml_3b","sur_h","sur_d"}
    if data in premium and not await is_premium(uid):
        await query.message.reply_text("⛔ این بخش ویژه مشترکین است. ابتدا اشتراک تهیه کنید.")
        return

    now = time.time()
    key = f"{uid}:{data}"
    if data not in ("bet_set",) and not data.startswith(("rem_", "adm_")):
        if now-LAST_CALL.get(key, 0) < COOLDOWN:
            return
        LAST_CALL[key] = now

    if data == "buy_sub":
        await query.message.reply_text(
            f"💳 مبلغ اشتراک: {SUB_PRICE} تومان\nشماره کارت: <code>{CARD_NUMBER}</code>\n"
            f"به نام: {html.escape(CARD_HOLDER)}\nپس از واریز، تصویر رسید را ارسال کنید.",
            parse_mode="HTML")
    elif data == "my_status":
        users = await get_allowed_users()
        if await is_premium(uid):
            await query.message.reply_text(f"✅ فعال\nانقضا: {users.get(str(uid), 'نامشخص')}")
        else:
            await query.message.reply_text("❌ اشتراک فعال ندارید.")
    elif data == "invite":
        me = await context.bot.get_me()
        refs = await get_referrals()
        count = len(refs.get(str(uid), []))
        link = f"https://t.me/{me.username}?start=ref_{uid}"
        await query.message.reply_text(f"🎁 لینک دعوت:\n<code>{link}</code>\nدعوت‌ها: {count}",
                                       parse_mode="HTML")
    elif data == "bet_set":
        context.user_data["awaiting_bet"] = True
        await query.message.reply_text("مبلغ سرمایه را به تومان ارسال کنید:",
                                       reply_markup=ForceReply(selective=True))
    elif data == "weather":
        await query.message.reply_text(await get_weather())
    elif data == "history":
        await cmd_history(uid, context)
    elif data == "admin_help":
        if str(uid) == ADMIN_ID:
            await admin_panel(uid, context)
    elif data == "admin_refresh":
        if str(uid) == ADMIN_ID:
            await admin_panel(uid, context)
    elif data in ("on", "off"):
        if str(uid) != ADMIN_ID:
            return
        ok = await set_github_var("AUTO_UPDATE", "true" if data == "on" else "false")
        await query.message.reply_text("✅ انجام شد." if ok else "❌ ذخیره تنظیمات ناموفق بود.")
    elif data.startswith("adm_"):
        if str(uid) != ADMIN_ID:
            return
        parts = data.split("_")
        action, target = parts[1], parts[2]
        users = await get_allowed_users()
        if action == "ren":
            days = int(parts[3])
            expiry = await add_vip_days(target, days)
            await query.message.reply_text(f"تمدید انجام شد: {expiry}" if expiry else "ذخیره ناموفق بود.")
        elif action == "can":
            users[target] = (datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
            ok = await save_allowed_users(users)
            await query.message.reply_text("اشتراک لغو شد." if ok else "ذخیره ناموفق بود.")
        elif action == "del":
            users.pop(target, None)
            ok = await save_allowed_users(users)
            await query.message.reply_text("کاربر حذف شد." if ok else "ذخیره ناموفق بود.")
    elif data.startswith("rem_set_"):
        context.user_data["awaiting_rem"] = True
        context.user_data["rem_round"] = int(data.rsplit("_", 1)[1])
        await query.message.reply_text("چند دقیقه دیگر دوباره بررسی کنم؟",
                                       reply_markup=ForceReply(selective=True))
    elif data in ("q_safe", "q_value", "q_lucky"):
        await process_quinella(uid, context,
                               {"q_safe":"امن", "q_value":"ارزش", "q_lucky":"شانسی"}[data])
    elif data.startswith("r") and data != "rall":
        await process_rounds(uid, context, selected=int(data[1:]),
                             bet_amount=context.user_data.get("bet_amount"))
    elif data == "rall":
        await process_rounds(uid, context, all_rounds=True,
                             bet_amount=context.user_data.get("bet_amount"))
    elif data.startswith("ml_"):
        await process_multileg(uid, context, data)
    elif data in ("sur_h", "sur_d"):
        await process_surprise(uid, context, data)
    elif data == "value":
        await process_rounds(uid, context, all_rounds=True)


async def expiration_worker(bot):
    already_warned = set()
    while True:
        try:
            users = await get_allowed_users()
            now = datetime.now()
            for uid, raw in users.items():
                try:
                    exp = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
                    remaining = exp-now
                    if timedelta(0) < remaining <= timedelta(days=2) and uid not in already_warned:
                        await bot.send_message(int(uid),
                            "⚠️ اشتراک شما کمتر از دو روز دیگر منقضی می‌شود.")
                        already_warned.add(uid)
                except Exception:
                    continue
        except Exception as exc:
            print("expiration worker:", exc)
        await asyncio.sleep(3600)


async def cache_worker():
    while True:
        now = time.time()
        for key in list(AR_CACHE):
            if now-AR_CACHE[key][0] > CACHE_TTL:
                AR_CACHE.pop(key, None)
        await asyncio.sleep(600)


async def post_init(app):
    global TELEGRAM_BOT
    TELEGRAM_BOT = app.bot
    spawn_task(expiration_worker(app.bot))
    spawn_task(cache_worker())


async def post_shutdown(app):
    for task in list(BACKGROUND_TASKS):
        task.cancel()
    if BACKGROUND_TASKS:
        await asyncio.gather(*BACKGROUND_TASKS, return_exceptions=True)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("متغیر محیطی BOT_TOKEN تنظیم نشده است.")
    app = (ApplicationBuilder().token(BOT_TOKEN)
           .post_init(post_init).post_shutdown(post_shutdown).build())
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("Bot started; polling continuously.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Bot stopped.")
    except Exception as exc:
        print("CRITICAL ERROR:", exc)
        sys.exit(1)
