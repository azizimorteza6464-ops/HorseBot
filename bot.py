import os
import re
import sys
import json
import time
import asyncio
import itertools
from datetime import datetime, timedelta

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.ext import (
    ApplicationBuilder as AB, ContextTypes as C,
    CommandHandler as CH, CallbackQueryHandler,
    MessageHandler, filters
)
from playwright.async_api import async_playwright as ap


# ======================== تنظیمات ========================

T = os.environ.get("BOT_TOKEN")
A = os.environ.get("USER_TOKEN")
D = os.environ.get("USER_DEVICE_ID")
U = os.environ.get("USER_UDI")
G = os.environ.get("GH_PAT")
R = os.environ.get("GITHUB_REPOSITORY")

MY_ID = "184149177"
CARD_NUMBER = "6219-8618-0229-0089"
CARD_HOLDER = "مرتضی عزیزی"
SUB_PRICE = "250,000"
REF_REWARD_DAYS = 3
TRIAL_DAYS = 1

SITE_URL = "https://mosharekat.asbdavani.app/"
CACHE_TTL = 45
COOLDOWN = 60
_last_call = {}
AR_CACHE = {}
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "17400"))

RANK_POINTS = {"1": 10, "2": 6, "3": 3, "4": 1, "5": 0.5}
JOCKEY_TIERS = {
    "S": ["رحمانی", "قهرمانی", "نوروزی"],
    "A": ["وحدانی", "مرادی", "صوفیان", "خواجوی"],
    "B": ["آقاجانی", "حیدری", "نصیری", "مسعودی"],
    "C": ["احمدی", "رضایی"],
}
JOCKEY_POINTS = {"S": 10, "A": 7, "B": 4, "C": 2}


# ======================== ابزارهای عمومی ========================

def clean_text(value):
    value = str(value or "").replace("\u200c", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def safe_float(value):
    if value is None:
        return None
    s = str(value).replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", s)
    try:
        return float(m.group()) if m else None
    except Exception:
        return None


def safe_int(value):
    n = safe_float(value)
    return int(n) if n is not None else None


def _split_safe(text, max_len=3900):
    if len(text) <= max_len:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            if current:
                chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)
    return chunks


async def send_long_message(c, chat_id, text, max_len=3900):
    for part in _split_safe(text, max_len):
        try:
            await c.bot.send_message(chat_id, part, parse_mode="HTML")
        except Exception:
            plain = re.sub(r"<[^>]*>", "", part)
            await c.bot.send_message(chat_id, plain)
        await asyncio.sleep(0.15)


# ======================== GitHub Variables ========================

async def get_github_var(var_name, default_val=""):
    if not R or not G:
        return default_val
    try:
        url = f"https://api.github.com/repos/{R}/actions/variables/{var_name}"
        headers = {
            "Authorization": f"Bearer {G}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                return res.json().get("value", default_val)
    except Exception as e:
        print("get_github_var:", e)
    return default_val


async def set_github_var(var_name, val):
    if not R or not G:
        return False
    url = f"https://api.github.com/repos/{R}/actions/variables/{var_name}"
    headers = {
        "Authorization": f"Bearer {G}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            old = await client.get(url, headers=headers)
            if old.status_code == 404:
                res = await client.post(
                    f"https://api.github.com/repos/{R}/actions/variables",
                    headers=headers,
                    json={"name": var_name, "value": str(val)},
                )
                return res.status_code in (201, 204)
            if old.status_code != 200:
                print("GitHub read error:", old.status_code, old.text[:300])
                return False
            res = await client.patch(
                url, headers=headers,
                json={"name": var_name, "value": str(val)},
            )
            return res.status_code in (200, 204)
    except Exception as e:
        print("set_github_var:", e)
        return False


async def get_allowed_users_dict():
    raw = await get_github_var("ALLOWED_USERS", "{}")
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        result = {}
        if raw and ("," in raw or raw.strip().isdigit()):
            exp = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            for uid in raw.split(","):
                if uid.strip():
                    result[uid.strip()] = exp
        return result


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
        result = json.loads(await get_github_var("REFERRALS", "{}"))
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


# ======================== هوا و مدل آماری ========================

async def get_weather():
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=37.25&longitude=55.16&current_weather=true"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(url)
            res.raise_for_status()
            weather = res.json()["current_weather"]
        return (
            f"🌡 دما: {weather['temperature']}°C"
            f" | 💨 باد: {weather['windspeed']} km/h"
        )
    except Exception:
        return "⚠️ اطلاعات هوا در دسترس نیست."


def decayed_form(perf_str, decay=0.85):
    if not perf_str:
        return 0.0
    total = 0.0
    for i, item in enumerate(str(perf_str).split("-")):
        rank = item.strip()
        if rank.isdigit():
            total += RANK_POINTS.get(rank, 0) * (decay ** i)
    return total


def jockey_score(name):
    name = clean_text(name)
    for tier, names in JOCKEY_TIERS.items():
        if any(x in name for x in names):
            return JOCKEY_POINTS[tier]
    return 0


def power_probs(scores, power=1.8):
    if not scores:
        return []
    values = [max(0.0001, float(x)) for x in scores]
    powered = [x ** power for x in values]
    total = sum(powered)
    return [x / total for x in powered] if total else [1 / len(values)] * len(values)


def quinella_prob(probs, i, j):
    pi, pj = probs[i], probs[j]
    if pi <= 0 or pj <= 0 or pi >= 1 or pj >= 1:
        return 0.0
    a = pi * pj / max(1e-9, 1 - pi)
    b = pj * pi / max(1e-9, 1 - pj)
    return min(1.0, a + b)


def place_probability(probs, index, places=3):
    # تقریب Plackett-Luce برای احتمال قرار گرفتن در سه رتبه اول
    if not probs or index >= len(probs):
        return 0.0
    p = probs[index]
    if p <= 0:
        return 0.0
    n = len(probs)
    result = p
    if places >= 2:
        for j in range(n):
            if j == index:
                continue
            pj = probs[j]
            if pj < 1:
                result += pj * p / max(1e-9, 1 - pj)
    if places >= 3:
        for j in range(n):
            if j == index:
                continue
            pj = probs[j]
            for k in range(n):
                if k in (index, j):
                    continue
                pk = probs[k]
                den = 1 - pj - pk
                if den > 1e-9:
                    result += pj * pk / max(1e-9, 1 - pj) * p / den
    return min(1.0, result)


def get_confidence_level(horses):
    if not horses:
        return "⚠️ پایین"
    top = horses[0]
    second = horses[1] if len(horses) > 1 else top
    gap = top["model_prob"] - second["model_prob"]
    data_count = sum(
        1 for h in horses
        if h.get("perf") and h["perf"] != "ناموجود"
    )
    if gap >= 12 and data_count >= max(2, len(horses) // 2):
        return "🔥 بالا"
    if gap >= 5:
        return "⚖️ متوسط"
    return "⚠️ پایین"


# ======================== مرورگر و استخراج جدول ========================

async def prepare_page(pg):
    await pg.wait_for_timeout(2500)
    try:
        await pg.keyboard.press("Escape")
    except Exception:
        pass


async def make_browser_context(p):
    browser = await p.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    )
    context = await browser.new_context(
        viewport={"width": 1440, "height": 1000},
        locale="fa-IR",
    )
    cookies = []
    for name, value in (
        ("auth_token", A), ("device_id", D), ("_udi", U)
    ):
        if value:
            cookies.append({
                "name": name, "value": value,
                "domain": ".asbdavani.app", "path": "/",
            })
    if cookies:
        await context.add_cookies(cookies)
    return browser, context


async def extract_race_columns(pg):
    """
    استخراج ستون‌های R1، R2 و ... از جدول اصلی.
    شماره اولیه بر اساس ترتیب دکمه‌هاست؛ اگر سایت شماره جداگانه
    روی دکمه یا ویژگی data داشته باشد، همان شماره استفاده می‌شود.
    """
    result = await pg.evaluate("""
    () => {
        const root = document.querySelector(
            'main div.flex.items-start.user-select-none'
        );
        if (!root) return {error: 'race container not found', rounds: []};

        const output = [];
        const children = Array.from(root.children);

        for (const col of children) {
            const headerEl = col.querySelector(':scope > div:first-child');
            const header = (headerEl?.innerText || '').trim();
            const m = header.match(/^R\\s*(\\d+)$/i);
            if (!m) continue;

            const round = Number(m[1]);
            const btns = Array.from(col.querySelectorAll(':scope > button'));
            const horses = [];

            btns.forEach((btn, idx) => {
                const name = (btn.innerText || '').trim()
                    .replace(/\\s+/g, ' ');
                if (!name) return;

                const attrs = [
                    btn.getAttribute('data-number'),
                    btn.getAttribute('data-horse-number'),
                    btn.getAttribute('aria-label'),
                    btn.getAttribute('title')
                ].filter(Boolean).join(' ');
                const nm = attrs.match(/(?:اسب|horse|شماره)?\\s*#?\\s*(\\d{1,2})\\b/i);

                horses.push({
                    num: nm ? Number(nm[1]) : idx + 1,
                    name: name,
                    row: idx
                });
            });

            if (horses.length) output.push({round, horses});
        }

        output.sort((a,b) => a.round-b.round);
        return {rounds: output};
    }
    """)
    if result.get("error"):
        raise ValueError(result["error"])
    return result.get("rounds", [])


async def extract_selected_horse_details(pg, round_no):
    """
    اطلاعات نمای انتخابی سایت را به صورت محافظه‌کارانه استخراج می‌کند.
    فقط فیلدهایی که واقعاً در DOM دیده شوند ثبت می‌شوند.
    """
    return await pg.evaluate("""(roundNo) => {
        const root = document.querySelector(
            'main div.flex.items-start.user-select-none'
        );
        if (!root) return [];
        const cols = Array.from(root.children);
        const col = cols.find(el => {
            const h = el.querySelector(':scope > div:first-child');
            return (h?.innerText || '').trim().toUpperCase() === 'R' + roundNo;
        });
        if (!col) return [];

        return Array.from(col.querySelectorAll(':scope > button')).map((btn, i) => {
            const text = (btn.innerText || '').trim();
            const all = (btn.outerHTML || '') + ' ' + text;
            const weightMatch = all.match(/(\\d+(?:\\.\\d+)?)\\s*kg/i);
            const iorMatch = all.match(/(?:IOR|ضریب|امتیاز)\\s*[:：]?\\s*(\\d+(?:\\.\\d+)?)/i);
            const perfMatch = all.match(/\\b([1-5](?:\\s*-\\s*[1-5]){1,8})\\b/);
            return {
                row: i,
                weight: weightMatch ? Number(weightMatch[1]) : null,
                ior: iorMatch ? Number(iorMatch[1]) : null,
                perf: perfMatch ? perfMatch[1].replace(/\\s/g, '') : null
            };
        });
    }""", round_no)


async def get_all_races(pg):
    await prepare_page(pg)
    rounds = await extract_race_columns(pg)
    if not rounds:
        body = clean_text(await pg.locator("body").inner_text())
        if "ورود" in body or "login" in body.lower():
            raise PermissionError("کوکی ورود معتبر نیست.")
        raise ValueError("جدول دورهای مسابقه در صفحه پیدا نشد.")

    all_data = {}
    for race in rounds:
        detail_rows = await extract_selected_horse_details(pg, race["round"])
        detail_by_row = {x["row"]: x for x in detail_rows}
        horses = []
        for horse in race["horses"]:
            d = detail_by_row.get(horse["row"], {})
            horses.append({
                "num": horse["num"],
                "n": clean_text(horse["name"]),
                "weight": d.get("weight"),
                "ior": d.get("ior"),
                "perf": d.get("perf") or "ناموجود",
                "j": "نامشخص",
            })
        all_data[race["round"]] = horses
    return all_data


# ======================== تحلیل هر دور ========================

def analyze_horses(horses):
    if len(horses) < 2:
        raise ValueError("برای تحلیل حداقل دو اسب لازم است.")

    # نرمال‌سازی داده‌ها؛ داده ناموجود امتیاز جعلی نمی‌گیرد.
    forms = [decayed_form(h["perf"]) for h in horses]
    max_form = max(forms) if max(forms) > 0 else 1.0

    raw_scores = []
    for i, h in enumerate(horses):
        form = forms[i] / max_form
        jockey = jockey_score(h.get("j", ""))
        jockey_norm = jockey / 10.0
        # وزن و IOR فقط در صورت وجود واقعی نگهداری می‌شوند.
        score = 0.72 * form + 0.18 * jockey_norm + 0.10
        raw_scores.append(max(0.03, score))

    model = power_probs(raw_scores, power=1.8)

    # بازار تنها زمانی وارد ترکیب می‌شود که IOR واقعی برای همه اسب‌ها باشد.
    iors = [safe_float(h.get("ior")) for h in horses]
    has_full_market = all(x is not None and x > 0 for x in iors)
    if has_full_market:
        total_ior = sum(iors)
        market = [x / total_ior for x in iors]
        alpha = 0.55
        final = [
            alpha * model[i] + (1 - alpha) * market[i]
            for i in range(len(horses))
        ]
    else:
        market = [None] * len(horses)
        final = model[:]

    total = sum(final) or 1
    final = [x / total for x in final]

    for i, h in enumerate(horses):
        h["market_prob"] = market[i] * 100 if market[i] is not None else None
        h["model_prob"] = final[i] * 100
        h["edge"] = (
            h["model_prob"] - h["market_prob"]
            if h["market_prob"] is not None else None
        )
        h["place_prob"] = place_probability(final, i, 3) * 100
        h["place_fair_odd"] = (
            round(100 / h["place_prob"], 2) if h["place_prob"] > 0 else None
        )

    ranked = sorted(horses, key=lambda x: x["model_prob"], reverse=True)
    probs = [h["model_prob"] / 100 for h in ranked]

    q_candidates = []
    for i, j in itertools.combinations(range(len(ranked)), 2):
        qp = quinella_prob(probs, i, j)
        q_candidates.append({
            "h1": ranked[i], "h2": ranked[j],
            "prob": qp * 100,
            "fair_odd": round(1 / qp, 2) if qp > 0 else None,
        })
    q_candidates.sort(key=lambda x: x["prob"], reverse=True)

    longshots = [
        h for h in ranked[2:]
        if h["model_prob"] >= 5
    ]
    if not longshots:
        longshots = ranked[2:] or ranked[-1:]

    return {
        "horses": ranked,
        "w": ranked[0],
        "p": ranked[1],
        "s": ranked[2] if len(ranked) > 2 else ranked[1],
        "q": q_candidates[0] if q_candidates else None,
        "q_candidates": q_candidates,
        "su": longshots[0],
        "qs": (
            f"{longshots[0]['num']}-{longshots[0]['n']} + "
            f"{longshots[1]['num']}-{longshots[1]['n']}"
            if len(longshots) > 1 else "N/A"
        ),
        "conf": get_confidence_level(ranked),
        "market_available": has_full_market,
    }


async def save_to_history(res_dict, round_no):
    raw = await get_github_var("BOT_HISTORY", "[]")
    try:
        history = json.loads(raw)
    except Exception:
        history = []
    q = res_dict.get("q")
    entry = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "round": round_no,
        "win": f"{res_dict['w']['num']}-{res_dict['w']['n']}",
        "quinella": (
            f"{q['h1']['num']}-{q['h1']['n']} + "
            f"{q['h2']['num']}-{q['h2']['n']}"
            if q else "ناموجود"
        ),
        "confidence": res_dict["conf"],
    }
    history.insert(0, entry)
    await set_github_var(
        "BOT_HISTORY",
        json.dumps(history[:5], ensure_ascii=False),
    )


async def analyze_round(pg, all_races, round_no, save_history=False):
    if round_no not in all_races:
        return None
    horses = [dict(h) for h in all_races[round_no]]
    result = analyze_horses(horses)
    if save_history:
        await save_to_history(result, round_no)
    return result


# ======================== منو و پنل مدیریت ========================

def get_main_menu(chat_id):
    keyboard = []
    if str(chat_id) == MY_ID:
        keyboard.append([
            InlineKeyboardButton("🟢 روشن", callback_data="on"),
            InlineKeyboardButton("🔴 خاموش", callback_data="off"),
        ])

    keyboard.extend([
        [
            InlineKeyboardButton("🏁 دور ۱", callback_data="r1"),
            InlineKeyboardButton("🏁 دور ۲", callback_data="r2"),
            InlineKeyboardButton("🏁 دور ۳", callback_data="r3"),
        ],
        [
            InlineKeyboardButton("🏁 دور ۴", callback_data="r4"),
            InlineKeyboardButton("🏁 دور ۵", callback_data="r5"),
            InlineKeyboardButton("🏁 دور ۶", callback_data="r6"),
        ],
        [
            InlineKeyboardButton("🏁 دور ۷", callback_data="r7"),
            InlineKeyboardButton("📊 تحلیل کل", callback_data="rall"),
        ],
        [
            InlineKeyboardButton("🟢 دوقلوی امن", callback_data="q_safe"),
            InlineKeyboardButton("💎 دوقلوی ارزش", callback_data="q_value"),
            InlineKeyboardButton("🍀 دوقلوی شانسی", callback_data="q_lucky"),
        ],
        [
            InlineKeyboardButton("💎 شکار ارزش", callback_data="value"),
            InlineKeyboardButton("📜 سابقه تحلیل‌ها", callback_data="history"),
        ],
        [
            InlineKeyboardButton("🎯 ۶ پره", callback_data="ml_6"),
            InlineKeyboardButton("🎯 ۵ پره", callback_data="ml_5"),
        ],
        [
            InlineKeyboardButton("🎯 ۳ پره اول", callback_data="ml_3a"),
            InlineKeyboardButton("🎯 ۳ پره دوم", callback_data="ml_3b"),
        ],
        [
            InlineKeyboardButton("🎲 اسب شگفتی", callback_data="sur_h"),
            InlineKeyboardButton("🔥 دوقلوی شگفتی", callback_data="sur_d"),
        ],
        [InlineKeyboardButton("💰 تعیین مبلغ شرط", callback_data="bet_set")],
        [
            InlineKeyboardButton("💳 خرید اشتراک / تمدید", callback_data="buy_sub"),
            InlineKeyboardButton("👤 وضعیت اشتراک من", callback_data="my_status"),
        ],
        [InlineKeyboardButton("🎁 دعوت از دوستان", callback_data="invite")],
        [
            InlineKeyboardButton("🌤 وضعیت هوا", callback_data="weather"),
            InlineKeyboardButton("🏙 تغییر مسیر/شهر", callback_data="change_city"),
        ],
    ])
    if str(chat_id) == MY_ID:
        keyboard.append([
            InlineKeyboardButton("👑 پنل مدیریت", callback_data="admin_help")
        ])
    return InlineKeyboardMarkup(keyboard)


async def cmd_admin_panel(chat_id, c):
    enabled = await get_github_var("AUTO_UPDATE", "true")
    users = await get_allowed_users_dict()
    bet = c.user_data.get("bet_amount", 0)
    status = "🟢 روشن" if enabled.lower() == "true" else "🔴 خاموش"
    msg = (
        "👑 <b>پنل مدیریت</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"⚙️ وضعیت: {status}\n"
        f"💰 سرمایه ثبت‌شده: {bet:,} تومان\n\n"
        "👥 <b>کاربران VIP:</b>"
    )
    kb = [[InlineKeyboardButton("🔄 بروزرسانی", callback_data="admin_refresh")]]
    now = datetime.now()
    if not users:
        kb.append([InlineKeyboardButton("لیست خالی است", callback_data="ignore")])
    for uid, exp in users.items():
        try:
            dt = datetime.strptime(exp, "%Y-%m-%d %H:%M:%S")
            left = (dt - now).days
            status_txt = f"{left} روز مانده" if left >= 0 else "منقضی"
        except Exception:
            status_txt = "نامشخص"
        name = str(uid)
        try:
            info = await c.bot.get_chat(int(uid))
            name = info.first_name or name
        except Exception:
            pass
        kb.append([InlineKeyboardButton(
            f"👤 {name} ({status_txt})", callback_data="ignore"
        )])
        kb.append([
            InlineKeyboardButton("➕ تمدید ۳۰ روز", callback_data=f"adm_ren_{uid}_30"),
            InlineKeyboardButton("🚫 لغو", callback_data=f"adm_can_{uid}"),
            InlineKeyboardButton("❌ حذف", callback_data=f"adm_del_{uid}"),
        ])
    await c.bot.send_message(
        chat_id, msg, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb),
    )


# ======================== اجرای تحلیل‌ها ========================

async def fetch_races_for_chat(chat_id, c):
    if not all([A, D, U]):
        await c.bot.send_message(chat_id, "⚠️ اطلاعات ورود سایت در تنظیمات ربات کامل نیست.")
        return None
    browser = None
    try:
        async with ap() as p:
            browser, context = await make_browser_context(p)
            pg = await context.new_page()
            await pg.goto(SITE_URL, timeout=90000, wait_until="domcontentloaded")
            await pg.wait_for_timeout(5000)
            body = clean_text(await pg.locator("body").inner_text())
            if "ورود" in body[:500] or "login" in body[:500].lower():
                await c.bot.send_message(chat_id, "⚠️ نشست ورود سایت منقضی شده است.")
                return None
            races = await get_all_races(pg)
            return races
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ دریافت جدول ناموفق بود:\n<code>{str(e)[:800]}</code>", parse_mode="HTML")
        return None
    finally:
        if browser:
            await browser.close()


def format_round_result(round_no, res, detailed=False):
    w, p, s = res["w"], res["p"], res["s"]
    msg = (
        f"\n🏁 <b>دور R{round_no}</b>\n"
        f"🥇 <b>پیش‌بر:</b> {w['num']}-{w['n']} "
        f"({w['model_prob']:.1f}٪)\n"
        f"🥈 <b>میان‌بر:</b> {p['num']}-{p['n']} "
        f"({p['model_prob']:.1f}٪)\n"
        f"🥉 <b>پی‌بر:</b> {s['num']}-{s['n']} "
        f"({s['model_prob']:.1f}٪)\n"
        f"👥 <b>دوقلوی اصلی:</b> "
    )
    if res["q"]:
        q = res["q"]
        msg += (
            f"{q['h1']['num']}-{q['h1']['n']} + "
            f"{q['h2']['num']}-{q['h2']['n']} "
            f"({q['prob']:.1f}٪)\n"
        )
    else:
        msg += "ناموجود\n"
    msg += f"🎯 اطمینان مدل: {res['conf']}\n"
    if not res["market_available"]:
        msg += "ℹ️ ضرایب/IOR کامل در صفحه قابل مشاهده نبود؛ احتمال‌ها فقط بر پایه داده‌های موجود مدل هستند.\n"
    if detailed:
        msg += "\n📊 <b>رتبه‌بندی اسب‌ها:</b>\n"
        for i, h in enumerate(res["horses"], 1):
            market = (
                f"{h['market_prob']:.1f}%"
                if h["market_prob"] is not None else "ناموجود"
            )
            msg += (
                f"{i}. {h['num']}-{h['n']} | مدل {h['model_prob']:.1f}%"
                f" | بازار {market}\n"
            )
    return msg + "━━━━━━━━━━━━━━\n"


async def process_odds(
    chat_id, c, specific_round=None, all_rounds=False,
    bet_amount=None, is_reminder=False
):
    if (
        await get_github_var("AUTO_UPDATE", "true")
    ).lower() == "false" and not is_reminder:
        return await c.bot.send_message(chat_id, "🛑 ربات توسط مدیریت خاموش شده است.")
    if not is_reminder:
        await c.bot.send_message(chat_id, "⏳ در حال دریافت و تحلیل جدول مسابقات...")
    weather = await get_weather()
    races = await fetch_races_for_chat(chat_id, c)
    if not races:
        return
    available = sorted(races.keys())
    if specific_round is not None:
        selected = [specific_round] if specific_round in races else []
    elif all_rounds:
        selected = available
    else:
        selected = []

    if not selected:
        await c.bot.send_message(
            chat_id,
            "⚠️ دور درخواستی در جدول فعلی وجود ندارد.\n"
            f"دورهای موجود: {', '.join('R'+str(x) for x in available)}",
        )
        return

    msg = "🏇 <b>تحلیل مسابقات</b>\n" + weather + "\n"
    if bet_amount:
        msg += f"💰 سرمایه ثبت‌شده: <code>{bet_amount:,}</code> تومان\n"
    msg += "━━━━━━━━━━━━━━\n"

    for r in selected:
        try:
            res = analyze_horses([dict(x) for x in races[r]])
            msg += format_round_result(r, res, detailed=(specific_round is not None))
            if specific_round is not None:
                await save_to_history(res, r)
        except Exception as e:
            msg += f"\n🏁 دور R{r}: ⚠️ تحلیل نشد ({clean_text(e)})\n"
    msg += "\n⚠️ احتمال‌های مدل تضمین نتیجه نیستند؛ بدون ضرایب واقعی، ارزش شرط قابل تأیید نیست."
    await send_long_message(c, chat_id, msg)


async def process_quinellas(chat_id, c, variant_type):
    titles = {
        "q_safe": "🟢 دوقلوی امن",
        "q_value": "💎 دوقلوی ارزش",
        "q_lucky": "🍀 دوقلوی شانسی",
    }
    await c.bot.send_message(chat_id, "⏳ در حال بررسی دوقلوها...")
    races = await fetch_races_for_chat(chat_id, c)
    if not races:
        return
    msg = f"<b>{titles[variant_type]}</b>\n━━━━━━━━━━━━━━\n"
    for r in sorted(races):
        res = analyze_horses([dict(x) for x in races[r]])
        candidates = res["q_candidates"]
        if not candidates:
            continue
        if variant_type == "q_safe":
            chosen = candidates[0]
        elif variant_type == "q_value":
            # بدون ضریب واقعی بازار، ارزش شرط قابل محاسبه نیست.
            # بهترین ترکیب رتبه‌های 2 تا 4 فقط به عنوان گزینه پرریسک معرفی می‌شود.
            subset = [
                x for x in candidates
                if x["h1"]["model_prob"] < 35 or x["h2"]["model_prob"] < 35
            ]
            chosen = max(subset, key=lambda x: x["prob"]) if subset else candidates[0]
        else:
            subset = [
                x for x in candidates
                if x["h1"]["model_prob"] < 20 and x["h2"]["model_prob"] < 20
            ]
            chosen = max(subset, key=lambda x: x["prob"]) if subset else candidates[-1]
        msg += (
            f"\n🏁 <b>دور R{r}</b>\n"
            f"👥 {chosen['h1']['num']}-{chosen['h1']['n']} + "
            f"{chosen['h2']['num']}-{chosen['h2']['n']}\n"
            f"📊 احتمال تخمینی: {chosen['prob']:.1f}%\n"
        )
        if variant_type == "q_value":
            msg += "ℹ️ ارزش واقعی فقط با ضریب پرداختی بازار قابل محاسبه است.\n"
        msg += "━━━━━━━━━━━━━━\n"
    await send_long_message(c, chat_id, msg)


async def process_multileg(chat_id, c, leg_type, bet_amount=None):
    await c.bot.send_message(chat_id, "⏳ در حال ساخت ترکیب پره‌ها...")
    races = await fetch_races_for_chat(chat_id, c)
    if not races:
        return
    available = sorted(races)
    if leg_type == "ml_6":
        selected = available[-6:]
        title = "۶ پره"
    elif leg_type == "ml_5":
        selected = available[-5:]
        title = "۵ پره"
    elif leg_type == "ml_3a":
        selected = available[:3]
        title = "۳ پره اول"
    elif leg_type == "ml_3b":
        selected = available[-3:]
        title = "۳ پره دوم"
    else:
        return
    picks = []
    details = []
    for r in selected:
        res = analyze_horses([dict(x) for x in races[r]])
        picks.append(f"R{r}: {res['w']['num']}-{res['w']['n']}")
        details.append(
            f"R{r}: {res['w']['num']}-{res['w']['n']} "
            f"({res['w']['model_prob']:.1f}%)"
        )
    msg = (
        f"🎯 <b>ترکیب {title}</b>\n\n"
        + "\n".join(details)
        + "\n\n<code>"
        + " | ".join(picks)
        + "</code>\n\n"
        "⚠️ ترکیب چندپره ریسک بالایی دارد و هیچ تضمینی برای برد ندارد."
    )
    await send_long_message(c, chat_id, msg)


async def process_surprises(chat_id, c, mode, bet_amount=None):
    await c.bot.send_message(chat_id, "⏳ در حال بررسی گزینه‌های شگفتی...")
    races = await fetch_races_for_chat(chat_id, c)
    if not races:
        return
    title = "🎲 اسب شگفتی" if mode == "sur_h" else "🔥 دوقلوی شگفتی"
    msg = f"<b>{title}</b>\n━━━━━━━━━━━━━━\n"
    for r in sorted(races):
        res = analyze_horses([dict(x) for x in races[r]])
        if mode == "sur_h":
            h = res["su"]
            msg += (
                f"\n🏁 دور R{r}: {h['num']}-{h['n']} "
                f"({h['model_prob']:.1f}٪)\n"
            )
        else:
            msg += f"\n🏁 دور R{r}: {res['qs']}\n"
    msg += "\n⚠️ این گزینه‌ها پرریسک هستند."
    await send_long_message(c, chat_id, msg)


async def process_value_bets(chat_id, c, bet_amount=None):
    if (
        await get_github_var("AUTO_UPDATE", "true")
    ).lower() == "false":
        return await c.bot.send_message(chat_id, "🛑 ربات خاموش است.")
    await c.bot.send_message(chat_id, "⏳ در حال بررسی گزینه‌های ارزش...")
    races = await fetch_races_for_chat(chat_id, c)
    if not races:
        return
    msg = "💎 <b>شکار ارزش</b>\n━━━━━━━━━━━━━━\n"
    found = False
    for r in sorted(races):
        res = analyze_horses([dict(x) for x in races[r]])
        # Value betting needs actual offered odds. Do not fabricate a value edge.
        if not res["market_available"]:
            continue
        candidates = [
            h for h in res["horses"]
            if h["edge"] is not None and h["edge"] >= 3
        ]
        if candidates:
            found = True
            msg += f"\n🏁 <b>دور R{r}</b>\n"
            for h in candidates[:3]:
                msg += (
                    f"💎 {h['num']}-{h['n']} | مدل {h['model_prob']:.1f}%"
                    f" | اختلاف با بازار {h['edge']:+.1f}%\n"
                )
    if not found:
        msg += "\n⚠️ با داده‌های فعلی، گزینه ارزش قابل تأییدی پیدا نشد."
    await send_long_message(c, chat_id, msg)


# ======================== تاریخچه و یادآوری ========================

async def cmd_history(chat_id, c):
    try:
        history = json.loads(await get_github_var("BOT_HISTORY", "[]"))
    except Exception:
        history = []
    if not history:
        return await c.bot.send_message(chat_id, "📜 تاریخچه‌ای ثبت نشده است.")
    msg = "📜 <b>۵ تحلیل آخر</b>\n\n"
    for item in history[:5]:
        msg += (
            f"📅 {item.get('date','') } | دور {item.get('round','')}\n"
            f"🥇 {item.get('win','')}\n"
            f"👥 {item.get('quinella','')}\n"
            f"🎯 {item.get('confidence','')}\n"
            "━━━━━━━━━━━━━━\n"
        )
    await send_long_message(c, chat_id, msg)


async def check_expirations(bot):
    await asyncio.sleep(60)
    while True:
        try:
            users = await get_allowed_users_dict()
            now = datetime.now()
            for uid, exp in users.items():
                try:
                    dt = datetime.strptime(exp, "%Y-%m-%d %H:%M:%S")
                    if 0 <= (dt - now).total_seconds() <= 48 * 3600:
                        await bot.send_message(
                            int(uid),
                            "⚠️ اشتراک شما کمتر از دو روز دیگر منقضی می‌شود.",
                        )
                except Exception:
                    pass
        except Exception as e:
            print("expiration check:", e)
        await asyncio.sleep(3600)


async def run_reminder(chat_id, c, round_no, delay_minutes):
    await asyncio.sleep(delay_minutes * 60)
    await c.bot.send_message(chat_id, f"⏰ یادآوری تحلیل دور {round_no}")
    await process_odds(
        chat_id, c, specific_round=round_no,
        bet_amount=c.user_data.get("bet_amount"),
        is_reminder=True,
    )


# ======================== دستورات مدیریت ========================

async def cmd_approve(u: Update, c: C):
    if str(u.effective_chat.id) != MY_ID:
        return
    try:
        _, target, days = u.message.text.split()[:3]
        days = int(days)
        expiry = await add_vip_days(target, days)
        await u.message.reply_text(
            f"✅ کاربر <code>{target}</code> برای {days} روز فعال شد.\n"
            f"انقضا: <code>{expiry}</code>",
            parse_mode="HTML",
        )
        try:
            await c.bot.send_message(
                int(target),
                f"🎉 اشتراک شما به مدت {days} روز فعال شد.\nانقضا: {expiry}",
            )
        except Exception:
            pass
    except Exception:
        await u.message.reply_text(
            "فرمت صحیح:\n<code>/approve 123456789 30</code>",
            parse_mode="HTML",
        )


async def cmd_remove(u: Update, c: C):
    if str(u.effective_chat.id) != MY_ID:
        return
    try:
        target = u.message.text.split()[1]
        users = await get_allowed_users_dict()
        users.pop(target, None)
        ok = await set_github_var(
            "ALLOWED_USERS", json.dumps(users, ensure_ascii=False)
        )
        await u.message.reply_text("✅ حذف شد." if ok else "❌ ذخیره در GitHub ناموفق بود.")
    except Exception:
        await u.message.reply_text("فرمت صحیح: /remove 123456789")


async def cmd_broadcast(u: Update, c: C):
    if str(u.effective_chat.id) != MY_ID:
        return
    if not c.args:
        return await u.message.reply_text("متن پیام را بعد از /broadcast بنویسید.")
    users = await get_allowed_users_dict()
    ok = fail = 0
    status = await u.message.reply_text(f"⏳ ارسال به {len(users)} کاربر...")
    for uid in users:
        try:
            await c.bot.send_message(int(uid), " ".join(c.args), parse_mode="HTML")
            ok += 1
            await asyncio.sleep(0.08)
        except Exception:
            fail += 1
    await status.edit_text(f"✅ پایان ارسال\nموفق: {ok}\nناموفق: {fail}")


async def cmd_debug(u: Update, c: C):
    if str(u.effective_chat.id) != MY_ID:
        return await u.message.reply_text("⛔ فقط ادمین.")
    browser = None
    try:
        await u.message.reply_text("⏳ در حال دریافت جدول...")
        async with ap() as p:
            browser, context = await make_browser_context(p)
            pg = await context.new_page()
            await pg.goto(SITE_URL, timeout=90000, wait_until="domcontentloaded")
            await pg.wait_for_timeout(5000)
            html = await pg.content()
            fname = "Table_HTML_current.txt"
            with open(fname, "w", encoding="utf-8") as f:
                f.write(html)
            with open(fname, "rb") as f:
                await c.bot.send_document(
                    u.effective_chat.id, document=f,
                    filename=fname, caption="HTML جدول فعلی مسابقات",
                )
            try:
                races = await get_all_races(pg)
                summary = "\n".join(
                    f"R{r}: {len(hs)} اسب - " + "، ".join(x["n"] for x in hs)
                    for r, hs in races.items()
                )
                await send_long_message(c, u.effective_chat.id, summary)
            except Exception as e:
                await u.message.reply_text(f"خطای استخراج: {e}")
    except Exception as e:
        await u.message.reply_text(f"❌ {e}")
    finally:
        if browser:
            await browser.close()


# ======================== ورودی‌ها و پرداخت ========================

async def handle_photo(u: Update, c: C):
    chat_id = u.effective_chat.id
    if str(chat_id) == MY_ID:
        return
    name = u.effective_chat.first_name or "کاربر"
    await c.bot.forward_message(
        chat_id=int(MY_ID),
        from_chat_id=chat_id,
        message_id=u.message.message_id,
    )
    await c.bot.send_message(
        int(MY_ID),
        f"🔔 رسید جدید\nنام: {name}\nآیدی: <code>{chat_id}</code>\n"
        f"تأیید: <code>/approve {chat_id} 30</code>",
        parse_mode="HTML",
    )
    await u.message.reply_text("✅ رسید دریافت شد. پس از بررسی اشتراک فعال می‌شود.")


async def handle_inputs(u: Update, c: C):
    if not u.message or not u.message.text:
        return
    if c.user_data.get("awaiting_bet"):
        c.user_data["awaiting_bet"] = False
        try:
            amount = int(re.sub(r"[^\d]", "", u.message.text))
            if amount < 1000:
                raise ValueError
            c.user_data["bet_amount"] = amount
            await u.message.reply_text(
                f"✅ سرمایه ثبت شد: <code>{amount:,}</code> تومان",
                parse_mode="HTML",
            )
        except Exception:
            await u.message.reply_text("❌ مبلغ معتبر و حداقل ۱۰۰۰ تومان وارد کنید.")
        return
    if c.user_data.get("awaiting_rem"):
        c.user_data["awaiting_rem"] = False
        try:
            mins = int(re.sub(r"[^\d]", "", u.message.text))
            if mins < 1 or mins > 10080:
                raise ValueError
            round_no = c.user_data.get("rem_round", 1)
            asyncio.create_task(run_reminder(
                u.effective_chat.id, c, round_no, mins
            ))
            await u.message.reply_text(f"✅ یادآوری برای {mins} دقیقه دیگر تنظیم شد.")
        except Exception:
            await u.message.reply_text("❌ عددی بین ۱ تا ۱۰۰۸۰ دقیقه وارد کنید.")


# ======================== شروع و دعوت ========================

async def cmd_start(u: Update, c: C):
    chat_id = str(u.effective_chat.id)
    if c.args and c.args[0].startswith("ref_"):
        referrer = c.args[0][4:]
        if referrer != chat_id:
            refs = await get_referrals()
            users = await get_allowed_users_dict()
            invited = refs.setdefault(referrer, [])
            if chat_id not in invited and chat_id not in users:
                invited.append(chat_id)
                await set_github_var("REFERRALS", json.dumps(refs))
                expiry = await add_vip_days(referrer, REF_REWARD_DAYS)
                try:
                    await c.bot.send_message(
                        int(referrer),
                        f"🎁 دعوت موفق! {REF_REWARD_DAYS} روز هدیه اضافه شد.\n{expiry}",
                    )
                except Exception:
                    pass

    users = await get_allowed_users_dict()
    if chat_id != MY_ID and chat_id not in users:
        expiry = (
            datetime.now() + timedelta(days=TRIAL_DAYS)
        ).strftime("%Y-%m-%d %H:%M:%S")
        users[chat_id] = expiry
        await set_github_var("ALLOWED_USERS", json.dumps(users))
        welcome = (
            "👋 خوش آمدید!\n"
            "🎁 یک روز اشتراک آزمایشی برای شما فعال شد."
        )
    else:
        welcome = "👋 خوش آمدید. ربات آماده است."
    await c.bot.send_message(
        u.effective_chat.id,
        welcome,
        reply_markup=get_main_menu(chat_id),
        parse_mode="HTML",
    )


# ======================== دکمه‌ها ========================

async def button_callback(u: Update, c: C):
    query = u.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data or ""

    premium_buttons = {
        "r1", "r2", "r3", "r4", "r5", "r6", "r7", "rall",
        "q_safe", "q_value", "q_lucky", "value",
        "ml_6", "ml_5", "ml_3a", "ml_3b", "sur_h", "sur_d",
    }
    if data in premium_buttons and not await is_premium(chat_id):
        return await query.message.reply_text(
            "⛔ دسترسی ویژه مشترکین است. برای تهیه اشتراک از دکمه خرید استفاده کنید."
        )

    if data in ("on", "off") and str(chat_id) != MY_ID:
        return await query.answer("⛔ دسترسی ندارید.", show_alert=True)

    now = time.time()
    key = f"{chat_id}:{data}"
    if (
        now - _last_call.get(key, 0) < COOLDOWN
        and data not in ("bet_set", "admin_refresh")
        and not data.startswith(("rem_", "adm_"))
    ):
        remain = int(COOLDOWN - (now - _last_call.get(key, 0)))
        return await query.answer(f"⏳ {remain} ثانیه صبر کنید.", show_alert=True)
    _last_call[key] = now

    if data == "buy_sub":
        await query.message.reply_text(
            f"💳 اشتراک ویژه\nمبلغ: {SUB_PRICE} تومان\n"
            f"شماره کارت: <code>{CARD_NUMBER}</code>\n"
            f"به نام: {CARD_HOLDER}\n\nپس از پرداخت، تصویر رسید را ارسال کنید.",
            parse_mode="HTML",
        )
    elif data == "my_status":
        users = await get_allowed_users_dict()
        if await is_premium(chat_id):
            await query.message.reply_text(
                f"✅ اشتراک فعال\nانقضا: <code>{users.get(str(chat_id), 'مدیر')}</code>",
                parse_mode="HTML",
            )
        else:
            await query.message.reply_text("❌ اشتراک شما فعال نیست.")
    elif data == "invite":
        info = await c.bot.get_me()
        link = f"https://t.me/{info.username}?start=ref_{chat_id}"
        refs = await get_referrals()
        count = len(refs.get(str(chat_id), []))
        await query.message.reply_text(
            f"🎁 لینک دعوت:\n<code>{link}</code>\n"
            f"تعداد دعوت‌شده: {count}\n"
            f"هدیه هر دعوت: {REF_REWARD_DAYS} روز",
            parse_mode="HTML",
        )
    elif data == "on":
        ok = await set_github_var("AUTO_UPDATE", "true")
        await query.message.reply_text("✅ روشن شد." if ok else "❌ ذخیره وضعیت ناموفق بود.")
    elif data == "off":
        ok = await set_github_var("AUTO_UPDATE", "false")
        await query.message.reply_text("🛑 خاموش شد." if ok else "❌ ذخیره وضعیت ناموفق بود.")
    elif data in ("admin_help", "admin_refresh"):
        if str(chat_id) == MY_ID:
            if data == "admin_refresh":
                try:
                    await query.message.delete()
                except Exception:
                    pass
            await cmd_admin_panel(chat_id, c)
    elif data.startswith("adm_"):
        if str(chat_id) != MY_ID:
            return
        parts = data.split("_")
        action, uid = parts[1], parts[2]
        users = await get_allowed_users_dict()
        if action == "ren":
            days = int(parts[3])
            expiry = await add_vip_days(uid, days)
            await query.message.reply_text(f"✅ تمدید شد: {expiry}")
        elif action == "can":
            users[uid] = (
                datetime.now() - timedelta(days=1)
            ).strftime("%Y-%m-%d %H:%M:%S")
            ok = await set_github_var("ALLOWED_USERS", json.dumps(users))
            await query.message.reply_text("🚫 لغو شد." if ok else "❌ خطا در ذخیره.")
        elif action == "del":
            users.pop(uid, None)
            ok = await set_github_var("ALLOWED_USERS", json.dumps(users))
            await query.message.reply_text("❌ کاربر حذف شد." if ok else "❌ خطا در ذخیره.")
        await cmd_admin_panel(chat_id, c)
    elif data == "history":
        await cmd_history(chat_id, c)
    elif data == "weather":
        await query.message.reply_text(await get_weather())
    elif data == "change_city":
        await query.message.reply_text("🏙 مکان فعلی مسابقات از سایت خوانده می‌شود.")
    elif data == "bet_set":
        c.user_data["awaiting_bet"] = True
        current = c.user_data.get("bet_amount", 0)
        await query.message.reply_text(
            f"سرمایه فعلی: {current:,} تومان\nمبلغ جدید را ارسال کنید.",
            reply_markup=ForceReply(selective=True),
        )
    elif data.startswith("rem_set_"):
        try:
            c.user_data["rem_round"] = int(data.rsplit("_", 1)[1])
        except Exception:
            c.user_data["rem_round"] = 1
        c.user_data["awaiting_rem"] = True
        await query.message.reply_text(
            "چند دقیقه دیگر یادآوری کنم؟",
            reply_markup=ForceReply(selective=True),
        )
    elif data in ("q_safe", "q_value", "q_lucky"):
        await process_quinellas(chat_id, c, data)
    elif data == "value":
        await process_value_bets(chat_id, c, c.user_data.get("bet_amount"))
    elif data.startswith("r"):
        if data == "rall":
            await process_odds(chat_id, c, all_rounds=True)
        else:
            try:
                await process_odds(
                    chat_id, c, specific_round=int(data[1:]),
                    bet_amount=c.user_data.get("bet_amount"),
                )
            except Exception:
                await query.message.reply_text("شماره دور نامعتبر است.")
    elif data.startswith("ml_"):
        await process_multileg(
            chat_id, c, data, c.user_data.get("bet_amount")
        )
    elif data in ("sur_h", "sur_d"):
        await process_surprises(chat_id, c, data, c.user_data.get("bet_amount"))


# ======================== اجرای اصلی ========================

async def main():
    if not T:
        raise RuntimeError("BOT_TOKEN تنظیم نشده است.")

    app = AB().token(T).build()
    app.add_handler(CH("start", cmd_start))
    app.add_handler(CH("approve", cmd_approve))
    app.add_handler(CH("remove", cmd_remove))
    app.add_handler(CH("broadcast", cmd_broadcast))
    app.add_handler(CH("debug", cmd_debug))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_inputs))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    expiry_task = asyncio.create_task(check_expirations(app.bot))
    try:
        await asyncio.sleep(RUN_SECONDS)
    finally:
        expiry_task.cancel()
        try:
            await expiry_task
        except asyncio.CancelledError:
            pass
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print("CRITICAL ERROR:", repr(exc))
        sys.exit(1)
