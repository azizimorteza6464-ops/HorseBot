import os
import re
import sys
import json
import time
import asyncio
import itertools
import html
import zipfile
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
CARD_NUMBER = os.environ.get("CARD_NUMBER", "")
CARD_HOLDER = os.environ.get("CARD_HOLDER", "")
SUB_PRICE = os.environ.get("SUB_PRICE", "250,000")
REF_REWARD_DAYS = 3
TRIAL_DAYS = 1

SITE_URL = "https://mosharekat.asbdavani.app/"
API_BASE = "https://api-mosharekat.asbdavani.app/api/v1"
CACHE_TTL = 45
COOLDOWN = 60
_last_call = {}
AR_CACHE = {}
SITE_WEATHER_CACHE = None
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "0"))

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


def normalize_digits(value):
    """Convert Persian/Arabic digits to ASCII without changing other text."""
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return str(value).translate(table)


def normalize_horse_name(value):
    """Normalize Persian/Arabic horse names for reliable cross-page matching."""
    value = normalize_digits(clean_text(value)).lower()
    value = value.replace("ي", "ی").replace("ك", "ک").replace("ة", "ه")
    value = re.sub(r"[^0-9a-z؀-ۿ]+", "", value)
    return value


def safe_float(value):
    if value is None:
        return None
    s = normalize_digits(value).replace("٫", ".").replace("٬", "")
    s = s.replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    try:
        return float(m.group()) if m else None
    except (TypeError, ValueError):
        return None


def safe_int(value):
    n = safe_float(value)
    return int(n) if n is not None else None


def _split_safe(text, max_len=3900):
    if len(text) <= max_len:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(line) > max_len:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(line), max_len):
                chunks.append(line[start:start + max_len])
            continue
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
            plain = html.unescape(re.sub(r"<[^>]*>", "", part))
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
    """Return weather captured from the racing site; use Open-Meteo as fallback."""
    global SITE_WEATHER_CACHE
    if SITE_WEATHER_CACHE:
        return SITE_WEATHER_CACHE
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=37.25&longitude=55.16"
            "&current=temperature_2m,wind_speed_10m"
            "&timezone=auto"
        )
        async with httpx.AsyncClient(timeout=18) as client:
            res = await client.get(url)
            res.raise_for_status()
            current = res.json().get("current", {})
        temp = current.get("temperature_2m")
        wind = current.get("wind_speed_10m")
        if temp is not None and wind is not None:
            return f"🌡 دما: {temp}°C | 💨 باد: {wind} km/h"
    except Exception as e:
        print("weather fallback failed:", repr(e))
    return "🌤 اطلاعات هوا از سایت/سرویس هواشناسی دریافت نشد."


def extract_site_weather(body_text):
    """Extract visible temperature/wind from the site's own weather strip."""
    text = clean_text(body_text)
    temp = re.search(r"(?:دما|temperature|temp)\s*[:：]?\s*(-?\d+(?:[.,]\d+)?)\s*°?\s*C", text, re.I)
    wind = re.search(r"(?:باد|wind)\s*[:：]?\s*(\d+(?:[.,]\d+)?)\s*(?:km\s*/\s*h|kmh|کیلومتر\s*/?\s*ساعت)", text, re.I)
    if temp and wind:
        t = temp.group(1).replace(",", ".")
        w = wind.group(1).replace(",", ".")
        return f"🌡 دما: {t}°C | 💨 باد: {w} km/h"
    return None


def decayed_form(perf_str, decay=0.85):
    """Score recent finishing positions; accept Persian digits and common separators."""
    if not perf_str:
        return 0.0
    text = normalize_digits(str(perf_str))
    ranks = re.findall(r"(?<!\d)([1-5])(?!\d)", text)
    return sum(RANK_POINTS.get(rank, 0) * (decay ** i)
               for i, rank in enumerate(ranks))


def career_form_score(horse):
    """Use official career podium counts when recent-form text is absent."""
    starts = safe_float(horse.get("starts"))
    wins = safe_float(horse.get("p1"))
    seconds = safe_float(horse.get("p2"))
    thirds = safe_float(horse.get("p3"))
    if starts is None or starts <= 0:
        return 0.0
    wins = max(0.0, wins or 0.0)
    seconds = max(0.0, seconds or 0.0)
    thirds = max(0.0, thirds or 0.0)
    return min(1.0, (wins + 0.60 * seconds + 0.35 * thirds) / starts)


def has_horse_form_data(horse):
    """True only when at least one numeric performance feature is usable."""
    if decayed_form(horse.get("perf")) > 0:
        return True
    if career_form_score(horse) > 0:
        return True
    ior = safe_float(horse.get("ior"))
    return ior is not None and ior > 0


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
    if not probs or index < 0 or index >= len(probs) or len(probs) < 1:
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
    data_count = sum(1 for h in horses if has_horse_form_data(h))
    if data_count == 0:
        return "⚠️ پایین"
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


async def fetch_racecard_performance(pg):
    """Scrape publicly rendered racecard performance: form, weight, IOR, coach and recent-race interval."""
    url = "https://asbdavani.app/racecards"
    try:
        await pg.goto(url, timeout=90000, wait_until="domcontentloaded")
        await pg.wait_for_timeout(3500)
        rows = await pg.evaluate("""() => {
          const clean = s => (s || '').replace(/\\u200c/g,' ').replace(/\\s+/g,' ').trim();
          const num = s => {
            s = String(s || '').replace(/[۰-۹]/g, d => '۰۱۲۳۴۵۶۷۸۹'.indexOf(d));
            const m = s.match(/\\d+(?:[.,]\\d+)?/); return m ? Number(m[0].replace(',','.')) : null;
          };
          return Array.from(document.querySelectorAll('.race-card-item')).map(card => {
            const a = card.querySelector('a.race-card-horse-name');
            if (!a) return null;
            const href = a.getAttribute('href') || '';
            const idm = href.match(/\\/performance\\/horses\\/([^/?#]+)/);
            const imgAlt = card.querySelector('.race-card-horse img')?.getAttribute('alt') || '';
            const nm = imgAlt.match(/([0-9۰-۹]+)\\s*$/);
            const number = nm ? num(nm[1]) : null;
            const rating = card.querySelector('.race-card-rating');
            const spans = rating ? Array.from(rating.querySelectorAll(':scope > span')) : [];
            const weightEl = spans.find(x => /kg/i.test(x.innerText || ''));
            const weight = weightEl ? num(weightEl.innerText) : null;
            const iorEl = rating?.querySelector('.ior');
            const ior = iorEl ? num(iorEl.innerText) : null;
            const perf = card.querySelector('.race-card-performance');
            const perfSpans = perf ? Array.from(perf.querySelectorAll(':scope > span')).map(x => clean(x.innerText)) : [];
            const ranks = (perfSpans[1] || '').replace(/[۰-۹]/g, d => '۰۱۲۳۴۵۶۷۸۹'.indexOf(d)).replace(/[^0-9-]/g,'');
            const stat = (perfSpans[2] || '').replace(/[۰-۹]/g, d => '۰۱۲۳۴۵۶۷۸۹'.indexOf(d));
            const statm = stat.match(/(\\d+)\\s*[-–]\\s*(\\d+)\\s*[-–]\\s*(\\d+)\\s*\\/\\s*(\\d+)/);
            const jockeyBox = card.querySelector('.race-card-jockey');
            const coachEl = card.querySelector('a.race-card-coach');
            const owner = jockeyBox ? clean(Array.from(jockeyBox.children).find(x => !x.matches('a'))?.innerText) : '';
            const riderEl = card.querySelector('.race-card-rider, .race-card-jockey a.race-card-jockey, [class*="race-card-jockey"] [class*="rider"]');
            const rider = riderEl ? clean(riderEl.innerText) : '';
            const coach = coachEl ? clean(coachEl.innerText) : '';
            const days = num(perfSpans[0]);
            return {
              horse_id: idm ? idm[1] : null,
              name: clean(a.innerText), number, weight, ior,
              perf: ranks || null, days_since_last: days,
              p1: statm ? Number(statm[1]) : null,
              p2: statm ? Number(statm[2]) : null,
              p3: statm ? Number(statm[3]) : null,
              starts: statm ? Number(statm[4]) : null,
              owner, coach, jockey: rider || null
            };
          }).filter(Boolean);
        }""")
        return rows or []
    except Exception as e:
        print("racecard performance scrape failed:", repr(e))
        return []


# ======================== تحلیل هر دور ========================

def _normalized_inverse_odds(horses, field="pishbar_odd"):
    """Normalize a pool only when every active runner has a valid decimal odd."""
    values = [safe_float(h.get(field)) for h in horses]
    if not values or any(x is None or x <= 1.0 for x in values):
        return [None] * len(horses)
    inverse = [1.0 / x for x in values]
    total = sum(inverse)
    if total <= 0:
        return [None] * len(horses)
    return [x / total for x in inverse]


def _combined_market_probabilities(horses):
    """Derive win probabilities from the win (pishbar) pool only.

    Place/show pools represent different events and must never be mixed into
    win probabilities. Incomplete win pools are ignored for whole-field ranking.
    """
    return _normalized_inverse_odds(horses, "pishbar_odd")


def _normalized_percentages(horses, field="survey_percent"):
    """Use survey shares only when all active runners are represented."""
    vals = [safe_float(h.get(field)) for h in horses]
    if not vals or any(x is None or x < 0 or x > 100 for x in vals):
        return [None] * len(horses)
    total = sum(vals)
    if total <= 0:
        return [None] * len(horses)
    return [x / total for x in vals]


def _has_form_signal(horses):
    return any(has_horse_form_data(h) for h in horses)

def analyze_horses(horses):
    horses = [dict(h) for h in (horses or [])]
    if len(horses) < 2:
        raise ValueError("برای تحلیل حداقل دو اسب لازم است.")

    # Remove scratched runners from calculations.
    horses = [h for h in horses if not h.get("scratched")]
    if len(horses) < 2:
        raise ValueError("کمتر از دو اسب فعال در این دور وجود دارد.")

    recent_forms = [decayed_form(h.get("perf")) for h in horses]
    career_forms = [career_form_score(h) for h in horses]
    form_values = [recent_forms[i] if recent_forms[i] > 0 else career_forms[i]
                   for i in range(len(horses))]
    max_form = max(form_values) if max(form_values, default=0) > 0 else 1.0
    ior_values = [safe_float(h.get("ior")) for h in horses]
    valid_ior = [x for x in ior_values if x is not None and x > 0]
    max_ior = max(valid_ior) if valid_ior else 1.0

    raw_scores = []
    for i, h in enumerate(horses):
        # IOR is used only when the source supplies a positive numeric rating.
        form_component = form_values[i] / max_form if form_values[i] > 0 else None
        ior_component = (ior_values[i] / max_ior
                         if ior_values[i] is not None and ior_values[i] > 0 else None)
        components = []
        if form_component is not None:
            components.append((0.75, form_component))
        if ior_component is not None:
            components.append((0.25, ior_component))
        score = (sum(w * value for w, value in components) / sum(w for w, _ in components)
                 if components else 0.10)
        raw_scores.append(max(0.03, score))

    model = power_probs(raw_scores, power=1.8)
    model_signal = _has_form_signal(horses)

    # Only complete pools can influence a whole-field probability distribution.
    # Partial odds/survey are retained in raw runner fields for diagnostics, not scored.
    market = _combined_market_probabilities(horses)
    survey = _normalized_percentages(horses, "survey_percent")
    has_full_market = bool(market) and all(x is not None for x in market)
    has_full_survey = bool(survey) and all(x is not None for x in survey)
    has_partial_market = any(
        safe_float(h.get("pishbar_odd")) is not None and safe_float(h.get("pishbar_odd")) > 1.0
        for h in horses
    ) and not has_full_market
    has_partial_survey = any(safe_float(h.get("survey_percent")) is not None for h in horses) and not has_full_survey

    if not has_full_market and not has_full_survey and not model_signal:
        raise ValueError("داده کامل و معتبر برای این دور موجود نیست؛ ضرایب/نظرسنجی ناقص به‌عنوان پیش‌بینی استفاده نشد.")

    if has_full_market and has_full_survey and model_signal:
        final = [0.72 * market[i] + 0.13 * survey[i] + 0.15 * model[i]
                 for i in range(len(horses))]
    elif has_full_market and has_full_survey:
        final = [0.85 * market[i] + 0.15 * survey[i] for i in range(len(horses))]
    elif has_full_market and model_signal:
        final = [0.82 * market[i] + 0.18 * model[i] for i in range(len(horses))]
    elif has_full_market:
        final = market[:]
    elif has_full_survey and model_signal:
        final = [0.70 * survey[i] + 0.30 * model[i] for i in range(len(horses))]
    elif has_full_survey:
        final = survey[:]
    else:
        # No complete market/survey: use genuine form/IOR only.
        final = model[:]

    total = sum(final) or 1.0
    final = [x / total for x in final]

    for i, h in enumerate(horses):
        h["market_prob"] = market[i] * 100 if market[i] is not None else None
        h["survey_prob"] = survey[i] * 100 if survey[i] is not None else None
        h["model_prob"] = final[i] * 100
        h["edge"] = h["model_prob"] - h["market_prob"] if h["market_prob"] is not None else None
        h["place_prob"] = place_probability(final, i, 3) * 100
        h["place_fair_odd"] = round(100 / h["place_prob"], 2) if h["place_prob"] > 0 else None

    ranked = sorted(horses, key=lambda x: x["model_prob"], reverse=True)
    probs = [h["model_prob"] / 100 for h in ranked]
    q_candidates = []
    for i, j in itertools.combinations(range(len(ranked)), 2):
        qp = quinella_prob(probs, i, j)
        q_candidates.append({
            "h1": ranked[i], "h2": ranked[j], "prob": qp * 100,
            "fair_odd": round(1 / qp, 2) if qp > 0 else None,
        })
    q_candidates.sort(key=lambda x: x["prob"], reverse=True)
    longshots = [h for h in ranked[2:] if h["model_prob"] >= 5]
    if not longshots:
        longshots = ranked[2:] or ranked[-1:]

    confidence = get_confidence_level(ranked)
    if (not has_full_market and not has_full_survey) or not model_signal or has_partial_market or has_partial_survey:
        confidence = "⚠️ پایین"

    return {
        "horses": ranked,
        "w": ranked[0], "p": ranked[1],
        "s": ranked[2] if len(ranked) > 2 else ranked[1],
        "q": q_candidates[0] if q_candidates else None,
        "q_candidates": q_candidates,
        "su": longshots[0],
        "qs": (f"{longshots[0]['num']}-{longshots[0]['n']} + {longshots[1]['num']}-{longshots[1]['n']}"
               if len(longshots) > 1 else "N/A"),
        "conf": confidence,
        "market_available": has_full_market,
        "market_partial": has_partial_market,
        "survey_available": has_full_survey,
        "survey_partial": has_partial_survey,
        "model_signal": model_signal,
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
            f"👤 {html.escape(str(name))} ({status_txt})", callback_data="ignore"
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

async def api_get_json(pg, path):
    """GET the site's documented-by-observation JSON API using the browser context cookies."""
    url = API_BASE + path
    response = await pg.context.request.get(url, timeout=35000)
    if not response.ok:
        raise RuntimeError(f"API {response.status}: {path}")
    try:
        payload = await response.json()
    except Exception as exc:
        raise RuntimeError(f"پاسخ JSON نامعتبر از {path}") from exc
    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError(f"API پاسخ success=false داد: {path}")
    return payload


def _api_data(payload):
    """Unwrap common API envelopes while preserving list responses."""
    value = payload
    for _ in range(5):
        if isinstance(value, dict):
            next_value = None
            for key in ("data", "result", "payload"):
                candidate = value.get(key)
                if isinstance(candidate, (dict, list)):
                    next_value = candidate
                    break
            if next_value is None:
                return value
            value = next_value
        else:
            return value
    return value


def _norm_api_key(value):
    value = normalize_digits(str(value or "")).lower()
    value = value.replace("ي", "ی").replace("ك", "ک")
    return re.sub(r"[^a-z0-9؀-ۿ]", "", value)


def _id_tokens(*values):
    tokens = set()
    for value in values:
        if value is None or isinstance(value, (dict, list, tuple)):
            continue
        raw = clean_text(value)
        if not raw:
            continue
        tokens.add(raw)
        tokens.add(_norm_api_key(raw))
        number = safe_int(raw)
        if number is not None:
            tokens.add(number)
            tokens.add(str(number))
    return {x for x in tokens if x not in (None, "")}


def _market_name(value):
    key = _norm_api_key(value)
    aliases = {
        "pishbar": ("pishbar", "prebar", "win", "winner", "first", "برنده", "پیشبر"),
        "miyanbar": ("miyanbar", "place", "second", "دوم", "میانبر"),
        "paybar": ("paybar", "show", "third", "سوم", "پیبر"),
    }
    for market, words in aliases.items():
        if any(_norm_api_key(word) in key for word in words):
            return market
    return None


def _odds_by_runner(odds_payload):
    """Parse market odds from nested-market, row-list, or runner-keyed API JSON."""
    data = _api_data(odds_payload)
    result = {}
    id_fields = (
        "runnerId", "runner_id", "horseId", "horse_id", "horseNumber",
        "horse_number", "horse_num", "number", "num", "external_id",
        "externalId", "id",
    )
    odd_fields = ("odd", "odds", "value", "price", "coefficient", "rate")

    def add(ids, market, odd):
        value = safe_float(odd)
        if not market or value is None or value <= 1.0:
            return
        for token in _id_tokens(*ids):
            result.setdefault(token, {})[market] = value

    def walk(node, path=(), parent_key=None):
        if isinstance(node, list):
            for child in node:
                walk(child, path, None)
            return
        if not isinstance(node, dict):
            market = next((_market_name(x) for x in reversed(path) if _market_name(x)), None)
            if market and parent_key is not None:
                add((parent_key,), market, node)
            return

        ids = [node.get(k) for k in id_fields if node.get(k) is not None]
        market = None
        for field in ("pool", "pool_name", "market", "market_name", "type", "type_name", "title"):
            market = _market_name(node.get(field))
            if market:
                break
        if not market:
            market = next((_market_name(x) for x in reversed(path) if _market_name(x)), None)
        odd = next((node.get(k) for k in odd_fields if node.get(k) is not None), None)
        if market and odd is not None and (ids or parent_key is not None):
            add(ids + ([parent_key] if parent_key is not None else []), market, odd)

        # Shape: {runner_number: {pishbar: 2.2, ...}}
        if parent_key is not None:
            for key, value in node.items():
                child_market = _market_name(key)
                if child_market and not isinstance(value, (dict, list)):
                    add(ids + [parent_key], child_market, value)

        for key, value in node.items():
            child_market = _market_name(key)
            if child_market and isinstance(value, dict):
                # Shape: {pishbar: {runner_id: odd}} or rows under a pool.
                for runner_key, runner_value in value.items():
                    if isinstance(runner_value, dict):
                        row_ids = [runner_value.get(k) for k in id_fields if runner_value.get(k) is not None]
                        row_odd = next((runner_value.get(k) for k in odd_fields if runner_value.get(k) is not None), None)
                        add(row_ids + [runner_key], child_market, row_odd)
                    else:
                        add((runner_key,), child_market, runner_value)
            elif isinstance(value, (dict, list)):
                walk(value, path + (str(key),), str(key))
            elif child_market:
                add(ids + [parent_key, key], child_market, value)

    walk(data)
    return result


def _survey_by_runner(survey_payload):
    """Read survey values from common statistics/participants API layouts."""
    data = _api_data(survey_payload)
    result = {}
    id_fields = (
        "horse_num", "horse_number", "horseNumber", "number", "num",
        "runner_number", "runnerNumber", "runner_id", "runnerId",
        "horse_id", "horseId", "external_id", "id",
    )
    value_fields = ("percent", "percentage", "survey_percent", "share", "value", "rate")

    def walk(node, parent_key=None):
        if isinstance(node, list):
            for row in node:
                walk(row)
            return
        if not isinstance(node, dict):
            if parent_key is not None:
                value = safe_float(node)
                if value is not None and 0 <= value <= 100:
                    for token in _id_tokens(parent_key):
                        result[token] = value
            return
        ids = [node.get(k) for k in id_fields if node.get(k) is not None]
        raw_value = next((node.get(k) for k in value_fields if node.get(k) is not None), None)
        value = safe_float(raw_value)
        if value is not None and 0 <= value <= 100:
            for token in _id_tokens(*(ids + ([parent_key] if parent_key is not None else []))):
                result[token] = value
        for key, child in node.items():
            if isinstance(child, (dict, list)):
                walk(child, key)
            elif parent_key is None and key not in id_fields:
                numeric = safe_float(child)
                if numeric is not None and 0 <= numeric <= 100:
                    for token in _id_tokens(key):
                        result[token] = numeric

    walk(data)
    return result


async def fetch_api_races(pg):
    """Fetch current racecard, actual pool odds and survey percentages from API."""
    days_payload = await api_get_json(pg, "/races/days?limit=50&offset=0&has_races=true")
    day_data = _api_data(days_payload)
    days = (day_data.get("race_days") or day_data.get("days") or day_data.get("items") or []) if isinstance(day_data, dict) else []
    if not days:
        days_payload = await api_get_json(pg, "/races/days?featured=true&limit=50")
        day_data = _api_data(days_payload)
        days = (day_data.get("race_days") or day_data.get("days") or day_data.get("items") or []) if isinstance(day_data, dict) else []
    if not days:
        raise RuntimeError("روز مسابقه از API دریافت نشد")

    # Prefer the currently featured day that actually has races, otherwise latest dated day.
    candidates = [d for d in days if d.get("races")]
    if not candidates:
        featured = [d for d in days if d.get("featured")]
        candidates = featured or days
    day = sorted(candidates, key=lambda d: (bool(d.get("featured")), str(d.get("date") or "")), reverse=True)[0]
    day_id = safe_int(day.get("id"))
    if day_id is None:
        raise RuntimeError("شناسه روز مسابقه نامعتبر است")

    race_payload = await api_get_json(pg, f"/races/racecard?day_id={day_id}")
    race_data = _api_data(race_payload)
    api_races = (race_data.get("races") or race_data.get("items") or
                 race_data.get("race_list") or []) if isinstance(race_data, dict) else []
    if isinstance(api_races, dict):
        api_races = list(api_races.values())
    if not isinstance(api_races, list) or not api_races:
        raise RuntimeError("جدول مسابقات در API خالی یا نامعتبر است")

    all_data = {}
    sem = asyncio.Semaphore(4)

    async def enrich(race):
        rid = safe_int(race.get("id"))
        rno = safe_int(race.get("number", race.get("race_number", race.get("raceNo"))))
        if rid is None or rno is None or rno < 1:
            return rno, []
        async with sem:
            odds_task = api_get_json(pg, f"/pools/race/{rid}/odds")
            survey_task = api_get_json(pg, f"/races/{rid}/survey-statistics")
            odds_payload, survey_payload = await asyncio.gather(odds_task, survey_task, return_exceptions=True)
        if isinstance(odds_payload, Exception):
            print(f"R{rno} odds API failed:", repr(odds_payload))
        if isinstance(survey_payload, Exception):
            print(f"R{rno} survey API failed:", repr(survey_payload))
        odds_map = _odds_by_runner(odds_payload) if not isinstance(odds_payload, Exception) else {}
        survey_map = _survey_by_runner(survey_payload) if not isinstance(survey_payload, Exception) else {}
        entries = []
        runners = (race.get("race_horses") or race.get("horses") or
                   race.get("runners") or race.get("participants") or [])
        if isinstance(runners, dict):
            runners = list(runners.values())
        for runner in runners:
            if not isinstance(runner, dict):
                continue
            num = safe_int(runner.get("number", runner.get("horse_number", runner.get("horse_num"))))
            if num is None:
                continue
            runner_id = runner.get("runner_id", runner.get("runnerId", runner.get("id")))
            horse_id = runner.get("horse_id", runner.get("horseId"))
            external_id = runner.get("external_id", runner.get("externalId"))
            identifiers = _id_tokens(runner_id, horse_id, external_id, num)
            horse_odds = {}
            for token in identifiers:
                horse_odds.update(odds_map.get(token, {}))
            survey_percent = next((survey_map[token] for token in identifiers if token in survey_map), None)
            horse_name = runner.get("horse_name") or runner.get("name")
            if not horse_name and isinstance(runner.get("horse"), dict):
                horse_name = runner["horse"].get("name")
            entries.append({
                "num": num,
                "n": clean_text(horse_name or f"اسب {num}"),
                "race_id": rid,
                "horse_id": safe_int(horse_id if horse_id is not None else runner_id),
                "runner_id": safe_int(runner_id),
                "external_id": clean_text(external_id or ""),
                "scratched": bool(runner.get("scratched") or runner.get("is_scratched")),
                "not_finished": bool(runner.get("not_finished")),
                "pishbar_odd": horse_odds.get("pishbar"),
                "miyanbar_odd": horse_odds.get("miyanbar"),
                "paybar_odd": horse_odds.get("paybar"),
                "survey_percent": survey_percent,
                "weight": safe_float(runner.get("weight")),
                "ior": safe_float(runner.get("ior")),
                "perf": runner.get("perf") or runner.get("form") or "ناموجود",
                "j": runner.get("jockey_name") or runner.get("jockey") or "نامشخص",
            })
        odds_runner_count = sum(1 for h in entries if any(h.get(k) is not None for k in ("pishbar_odd", "miyanbar_odd", "paybar_odd")))
        survey_runner_count = sum(1 for h in entries if h.get("survey_percent") is not None)
        print(f"R{rno}: runners={len(entries)}, odds_matched={odds_runner_count}, survey_matched={survey_runner_count}")
        return rno, entries

    results = await asyncio.gather(*(enrich(r) for r in api_races), return_exceptions=True)
    for item in results:
        if isinstance(item, Exception):
            print("race enrichment failed:", repr(item))
            continue
        rno, entries = item
        if rno is not None and entries:
            all_data[rno] = entries
    return all_data


async def fetch_races_for_chat(chat_id, c):
    global SITE_WEATHER_CACHE
    SITE_WEATHER_CACHE = None
    if not all([A, D, U]):
        await c.bot.send_message(chat_id, "⚠️ اطلاعات ورود سایت در تنظیمات ربات کامل نیست.")
        return None
    browser = None
    try:
        async with ap() as p:
            browser, context = await make_browser_context(p)
            pg = await context.new_page()
            await pg.goto(SITE_URL, timeout=90000, wait_until="domcontentloaded")
            await pg.wait_for_timeout(4500)
            body = clean_text(await pg.locator("body").inner_text())
            SITE_WEATHER_CACHE = extract_site_weather(body) or SITE_WEATHER_CACHE
            if "ورود" in body[:500] or "login" in body[:500].lower():
                await c.bot.send_message(chat_id, "⚠️ نشست ورود سایت منقضی شده است.")
                return None

            # API is authoritative for race IDs, horse numbers, live pool odds and survey.
            try:
                races = await fetch_api_races(pg)
            except Exception as api_error:
                print("API race fetch failed; trying visible racecard:", repr(api_error))
                races = await get_all_races(pg)

            # Enrich every runner with the official racecard performance page.
            # This page includes last-rank sequence, starts/podiums, weight, IOR and coach.
            try:
                performance_rows = await fetch_racecard_performance(pg)
                perf_by_id = {clean_text(x.get("horse_id")): x for x in performance_rows if x.get("horse_id")}
                perf_by_name = {normalize_horse_name(x.get("name")): x for x in performance_rows if x.get("name")}
                perf_by_id = {clean_text(x.get("horse_id")): x for x in performance_rows if x.get("horse_id")}
                perf_by_name = {normalize_horse_name(x.get("name")): x for x in performance_rows if x.get("name")}
                for horses in races.values():
                    for horse in horses:
                        perf = (perf_by_id.get(clean_text(horse.get("external_id")))
                                or perf_by_id.get(clean_text(horse.get("horse_id")))
                                or perf_by_name.get(normalize_horse_name(horse.get("n"))))
                        if not perf:
                            continue
                        for dest, srcfield in (("weight","weight"),("ior","ior"),("perf","perf"),
                                              ("days_since_last","days_since_last"),("p1","p1"),
                                              ("p2","p2"),("p3","p3"),("starts","starts"),
                                              ("owner","owner"),("coach","coach"),("j","jockey")):
                            val = perf.get(srcfield)
                            if val not in (None, "", "ناموجود", "نامشخص"):
                                horse[dest] = val
            except Exception as perf_error:
                print("racecard performance enrichment skipped:", repr(perf_error))

            # DOM-only fields from participation page are merged by race and horse number.
            try:
                await pg.goto(SITE_URL, timeout=90000, wait_until="domcontentloaded")
                await pg.wait_for_timeout(2200)
                dom_races = await get_all_races(pg)
                for rno, horses in races.items():
                    dom_by_num = {safe_int(h.get("num")): h for h in dom_races.get(rno, [])}
                    for horse in horses:
                        extra = dom_by_num.get(safe_int(horse.get("num")), {})
                        for field in ("weight", "ior", "perf", "j"):
                            value = extra.get(field)
                            current = horse.get(field)
                            if value not in (None, "", "ناموجود", "نامشخص") and current in (None, "", "ناموجود", "نامشخص"):
                                horse[field] = value
            except Exception as dom_error:
                print("DOM enrichment skipped:", repr(dom_error))
            return races
    except Exception as e:
        await c.bot.send_message(chat_id, f"❌ دریافت جدول ناموفق بود:\n<code>{html.escape(str(e)[:800])}</code>", parse_mode="HTML")
        return None
    finally:
        if browser:
            await browser.close()


def format_round_result(round_no, res, detailed=False):
    w, p, s = res["w"], res["p"], res["s"]
    msg = (
        f"\n🏁 <b>دور R{round_no}</b>\n"
        f"🥇 <b>پیش‌بر:</b> {w['num']}-{html.escape(str(w['n']))} "
        f"({w['model_prob']:.1f}٪)\n"
        f"🥈 <b>میان‌بر:</b> {p['num']}-{html.escape(str(p['n']))} "
        f"({p['model_prob']:.1f}٪)\n"
        f"🥉 <b>پی‌بر:</b> {s['num']}-{html.escape(str(s['n']))} "
        f"({s['model_prob']:.1f}٪)\n"
        f"👥 <b>دوقلوی اصلی:</b> "
    )
    if res["q"]:
        q = res["q"]
        msg += (
            f"{q['h1']['num']}-{html.escape(str(q['h1']['n']))} + "
            f"{q['h2']['num']}-{html.escape(str(q['h2']['n']))} "
            f"({q['prob']:.1f}٪)\n"
        )
    else:
        msg += "ناموجود\n"
    msg += f"🎯 اطمینان تحلیل: {res['conf']}\n"
    if not res.get("model_signal"):
        msg += "ℹ️ سابقه فرم/چابکسوار در این دریافت کامل نبود؛ احتمال‌ها عمدتاً ترکیب ضرایب و مشارکت هستند.\n"
    if not res["market_available"]:
        if res.get("market_partial"):
            msg += "ℹ️ ضرایب بازار فقط برای بخشی از اسب‌ها دریافت شد؛ رتبه‌بندی با داده ناقص انجام شده و اطمینان پایین است.\n"
        else:
            msg += "ℹ️ ضرایب بازار برای همه اسب‌ها کامل نبود؛ رتبه‌بندی فقط از داده‌های قابل‌دسترس ساخته شده است.\n"
    if res.get("survey_partial"):
        msg += "ℹ️ آمار مشارکت فقط برای بخشی از اسب‌ها موجود بود.\n"
    if detailed:
        msg += "\n📊 <b>رتبه‌بندی اسب‌ها:</b>\n"
        for rank, h in enumerate(res["horses"], 1):
            market = f"{h['market_prob']:.1f}%" if h["market_prob"] is not None else "ناموجود"
            odd = f"{h['pishbar_odd']:.2f}" if safe_float(h.get("pishbar_odd")) else "ناموجود"
            mid_odd = f"{h['miyanbar_odd']:.2f}" if safe_float(h.get("miyanbar_odd")) else "ناموجود"
            place_odd = f"{h['paybar_odd']:.2f}" if safe_float(h.get("paybar_odd")) else "ناموجود"
            survey = f"{h['survey_percent']:.1f}%" if safe_float(h.get("survey_percent")) is not None else "ناموجود"
            msg += (
                f"{rank}) شماره {h['num']} - {html.escape(str(h['n']))}"
                f" | مدل {h['model_prob']:.1f}% | پیش‌بر {odd}"
                f" | میان‌بر {mid_odd} | پی‌بر {place_odd}"
                f" | بازار {market} | مشارکت {survey}"
                f" | فرم {html.escape(str(h.get('perf') or 'ناموجود'))}"
                f" | وزن {html.escape(str(h.get('weight') or 'ناموجود'))}"
                f" | IOR {html.escape(str(h.get('ior') if h.get('ior') is not None else 'ناموجود'))}"
                f" | روز از آخرین مسابقه {html.escape(str(h.get('days_since_last') if h.get('days_since_last') is not None else 'ناموجود'))}"
                f" | مربی {html.escape(str(h.get('coach') or 'ناموجود'))}"
                f" | چابکسوار {html.escape(str(h.get('j') or 'نامشخص'))}\n"
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
    races = await fetch_races_for_chat(chat_id, c)
    if not races:
        return
    weather = await get_weather()
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
    msg += "\n⚠️ تحلیل آماری تضمین نتیجه نیست. ضرایب و مشارکت از API سایت دریافت می‌شوند؛ اگر داده‌ای موجود نباشد در رتبه‌بندی وارد نمی‌شود."
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
        try:
            res = analyze_horses([dict(x) for x in races[r]])
        except Exception as exc:
            msg += f"\n🏁 دور R{r}: ⚠️ تحلیل نشد ({html.escape(clean_text(exc))})\n━━━━━━━━━━━━━━\n"
            continue
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
            f"{chosen['h2']['num']}-{html.escape(str(chosen['h2']['n']))}\n"
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
        try:
            res = analyze_horses([dict(x) for x in races[r]])
        except Exception as exc:
            details.append(f"R{r}: تحلیل نشد ({html.escape(clean_text(exc))})")
            continue
        picks.append(f"R{r}: {res['w']['num']}-{html.escape(str(res['w']['n']))}")
        details.append(
            f"R{r}: {res['w']['num']}-{html.escape(str(res['w']['n']))} "
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
        try:
            res = analyze_horses([dict(x) for x in races[r]])
        except Exception as exc:
            msg += f"\n🏁 دور R{r}: ⚠️ تحلیل نشد ({html.escape(clean_text(exc))})\n"
            continue
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
        try:
            res = analyze_horses([dict(x) for x in races[r]])
        except Exception as exc:
            msg += f"\n🏁 دور R{r}: ⚠️ تحلیل نشد ({html.escape(clean_text(exc))})\n"
            continue
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
                    f"💎 {h['num']}-{html.escape(str(h['n']))} | مدل {h['model_prob']:.1f}%"
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
            f"🥇 {html.escape(str(item.get('win','')))}\n"
            f"👥 {html.escape(str(item.get('quinella','')))}\n"
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
            await c.bot.send_message(int(uid), html.escape(" ".join(c.args)))
            ok += 1
            await asyncio.sleep(0.08)
        except Exception:
            fail += 1
    await status.edit_text(f"✅ پایان ارسال\nموفق: {ok}\nناموفق: {fail}")


async def cmd_debug(u: Update, c: C):
    """Collect rendered site sections and fetch/XHR data for diagnostics."""
    if str(u.effective_chat.id) != MY_ID:
        return await u.message.reply_text("⛔ فقط ادمین.")
    browser = None
    try:
        await u.message.reply_text("⏳ در حال بررسی بخش‌های سایت و دریافت داده‌ها...")
        async with ap() as p:
            browser, context = await make_browser_context(p)
            pg = await context.new_page()
            dump_dir = "asbdavani_debug"
            os.makedirs(dump_dir, exist_ok=True)
            network_log = []
            tasks = []

            async def save_response(response):
                try:
                    req = response.request
                    if req.resource_type not in ("xhr", "fetch"):
                        return
                    headers = await response.all_headers()
                    ctype = headers.get("content-type", "")
                    entry = {"url": response.url, "method": req.method,
                             "status": response.status, "resource_type": req.resource_type,
                             "content_type": ctype}
                    network_log.append(entry)
                    if "json" in ctype.lower() or "text/" in ctype.lower():
                        body = await response.body()
                        if len(body) <= 2_000_000:
                            base = re.sub(r"[^a-zA-Z0-9._-]+", "_", response.url.split("?")[0].split("/")[-1])[:80] or "response"
                            fname = f"{len(network_log):03d}_{base}.txt"
                            with open(os.path.join(dump_dir, fname), "wb") as f:
                                f.write(body)
                            entry["saved_body"] = fname
                except Exception as ex:
                    network_log.append({"url": getattr(response, "url", ""), "error": str(ex)})

            pg.on("response", lambda response: tasks.append(asyncio.create_task(save_response(response))))
            pages = [
                ("home", SITE_URL), ("odds", SITE_URL.rstrip("/") + "/odds"),
                ("dorei", SITE_URL.rstrip("/") + "/dorei"),
                ("bets", SITE_URL.rstrip("/") + "/bets"),
                ("wallet", SITE_URL.rstrip("/") + "/wallet"),
                ("racecards", "https://asbdavani.app/racecards"),
                ("live", "https://asbdavani.app/live"),
            ]
            for label, url in pages:
                try:
                    await pg.goto(url, timeout=90000, wait_until="domcontentloaded")
                    await pg.wait_for_timeout(3500)
                    with open(os.path.join(dump_dir, label + ".html"), "w", encoding="utf-8") as f:
                        f.write(await pg.content())
                    try:
                        body_text = await pg.locator("body").inner_text(timeout=5000)
                        with open(os.path.join(dump_dir, label + ".txt"), "w", encoding="utf-8") as f:
                            f.write(body_text)
                    except Exception:
                        pass
                except Exception as ex:
                    with open(os.path.join(dump_dir, label + "_ERROR.txt"), "w", encoding="utf-8") as f:
                        f.write(url + "\n" + str(ex))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            with open(os.path.join(dump_dir, "network_requests.json"), "w", encoding="utf-8") as f:
                json.dump(network_log, f, ensure_ascii=False, indent=2)
            try:
                await pg.goto(SITE_URL, timeout=90000, wait_until="domcontentloaded")
                await pg.wait_for_timeout(3000)
                races = await get_all_races(pg)
                summary = "\n".join(f"R{r}: {len(hs)} اسب - " + "، ".join(x["n"] for x in hs) for r, hs in races.items())
                if summary:
                    await send_long_message(c, u.effective_chat.id, summary)
            except Exception as ex:
                await u.message.reply_text(f"⚠️ خلاصه جدول: {ex}")
            archive = "asbdavani_debug.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for name in os.listdir(dump_dir):
                    full = os.path.join(dump_dir, name)
                    if os.path.isfile(full):
                        zf.write(full, arcname=name)
            with open(archive, "rb") as f:
                await c.bot.send_document(u.effective_chat.id, document=f, filename=archive,
                    caption="گزارش صفحات سایت و پاسخ‌های fetch/XHR برای شناسایی API")
    except Exception as e:
        await u.message.reply_text(f"❌ خطای بررسی سایت: {e}")
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
        if not CARD_NUMBER or not CARD_HOLDER:
            await query.message.reply_text(
                "⚠️ اطلاعات پرداخت در تنظیمات محیطی ربات ثبت نشده است؛ با پشتیبانی تماس بگیرید."
            )
            return
        await query.message.reply_text(
            f"💳 اشتراک ویژه\nمبلغ: {html.escape(SUB_PRICE)} تومان\n"
            f"شماره کارت: <code>{html.escape(CARD_NUMBER)}</code>\n"
            f"به نام: {html.escape(CARD_HOLDER)}\n\nپس از پرداخت، تصویر رسید را ارسال کنید.",
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
    if not all([A, D, U]):
        print("WARNING: USER_TOKEN / USER_DEVICE_ID / USER_UDI ناقص است؛ استخراج جدول کار نخواهد کرد.")

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
        # Keep polling indefinitely. Set RUN_SECONDS to a positive value only for
        # deployments that intentionally require a timed process restart.
        if RUN_SECONDS > 0:
            await asyncio.sleep(RUN_SECONDS)
        else:
            await asyncio.Event().wait()
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
