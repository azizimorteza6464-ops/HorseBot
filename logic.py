import re, time
from playwright.async_api import async_playwright as ap

from config import A, D, U, AR_CACHE, CACHE_TTL

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

async def prepare_page(pg):
    try:
        await pg.wait_for_timeout(3000)
        dropdown = pg.get_by_text("انتخاب فصل مسابقات", exact=False).first
        if await dropdown.is_visible():
            await dropdown.click(timeout=5000, force=True)
            await pg.wait_for_timeout(1500)
            try:
                await pg.get_by_text("یزد", exact=False).last.click(timeout=3000)
            except:
                await pg.locator("ul li, .v-list-item, .dropdown-item, [role='option']").first.click()
            await pg.wait_for_timeout(3000)
    except Exception as e:
        print(f"Prepare page error: {e}")

async def ar(p, r_num, c, chat_id, save_history=True):
    if r_num in AR_CACHE:
        ts, cached_res = AR_CACHE[r_num]
        if time.time() - ts < CACHE_TTL: return cached_res

    horses = {}
    try:
        for text in [f"R {r_num}", f"R{r_num}", str(r_num), f"دور {r_num}"]:
            try:
                loc = p.get_by_text(text, exact=True).first
                if await loc.is_visible():
                    await loc.click(timeout=2000, force=True)
                    break
            except: pass
        
        await p.wait_for_timeout(8000)
        
        js_extract = """() => {
            function getCellText(el) {
                let text = el.innerText ? el.innerText.trim() : '';
                if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') { text = el.value || ''; }
                const inputs = el.querySelectorAll('input, textarea, select');
                if (inputs.length > 0) {
                    let vals = Array.from(inputs).map(i => i.value || i.innerText || '').filter(v => v && v.trim()).join(' ');
                    if (vals) text = text ? (text + ' ' + vals) : vals;
                }
                return text.replace(/\\s+/g, ' ').trim();
            }

            let tables = document.querySelectorAll('table');
            let result = [];
            tables.forEach(t => {
                t.querySelectorAll('tr').forEach(tr => {
                    let cells = Array.from(tr.querySelectorAll('td, th')).map(getCellText).filter(c => c);
                    if (cells.length >= 2) result.push(cells.join(' | '));
                });
            });
            if (result.length > 0) return result.join('\\n');

            let cards = document.querySelectorAll('[class*="horse"], [class*="runner"], [class*="card"], [class*="row"]');
            let res2 = [];
            cards.forEach(card => {
                let txt = getCellText(card);
                if (txt.length > 5 && txt.length < 300) res2.push(txt);
            });
            if (res2.length > 0) return res2.join('\\n');

            return document.body.innerText;
        }"""
        
        table_text = await p.evaluate(js_extract)
        
        for line in table_text.split('\n'):
            line = line.strip()
            if not line: continue
            if any(kw in line for kw in ['جدول مسابقات', 'مالی', 'پخش زنده', 'گزارش', 'اسبدوانی', 'دوره‌ای', 'نظر سنجی', 'حراجی', 'منو', 'ورود', 'ثبت نام']):
                continue
                
            if '|' in line:
                parts = [x.strip() for x in line.split('|') if x.strip()]
                if len(parts) < 3: continue
                
                has_num = any(re.match(r'^\d{1,2}$', pv) for pv in parts)
                has_float = any(re.match(r'^\d+(\.\d+)?$', pv) for pv in parts)
                if not has_num or not has_float: continue
                
                num, name, ior = 0, "", 0
                
                for pv in parts:
                    if re.match(r'^\d{1,2}$', pv) and num == 0:
                        num = int(pv)
                        break
                
                for pv in parts:
                    if len(pv) > 2 and not re.match(r'^[\d\.]+$', pv):
                        if pv not in ['نام اسب', 'اسب', 'شماره', 'H', 'R 1', 'R 2', 'R 3', 'R 4', 'R 5', 'R 6', 'R 7', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7']:
                            name = pv
                            break
                            
                for pv in reversed(parts):
                    if re.match(r'^\d+(\.\d+)?$', pv):
                        val = float(pv)
                        if 1.0 <= val <= 999.0:
                            ior = val
                            break
                            
                if name:
                    if num == 0: num = len(horses) + 1
                    horses[num] = {"n": name, "num": num, "ior": ior or 10.0, "j": "ناشناس", "perf": "0", "w_odd": ior or 10.0}
        
        if not horses:
            debug_text = table_text[:3000] if table_text else "متن خالی"
            try:
                await c.bot.send_message(chat_id, f"🚨 <b>خطا در خواندن جدول دور {r_num}</b>\n\n<b>متن استخراج شده:</b>\n<pre>{debug_text}</pre>", parse_mode='HTML')
                await p.screenshot(path=f"debug_r{r_num}.png")
                with open(f"debug_r{r_num}.png", "rb") as f:
                    await c.bot.send_photo(chat_id, photo=f, caption=f"📸 عکس صفحه - دور {r_num}")
                html_content = await p.content()
                with open(f"debug_r{r_num}.html", "w", encoding="utf-8") as f:
                    f.write(html_content)
                with open(f"debug_r{r_num}.html", "rb") as f:
                    await c.bot.send_document(chat_id, document=f, caption=f"📄 فایل HTML دیباگ دور {r_num}")
            except Exception as e: print(f"خطا در ارسال دیباگ: {e}")
            return "NOT_UPLOADED"

    except Exception as e: 
        print(f"Error in ar: {e}")
        try: await c.bot.send_message(chat_id, f"🚨 <b>خطای داخلی در تحلیل دور {r_num}</b>\n<code>{str(e)}</code>", parse_mode='HTML')
        except: pass
        return "ERROR"

    if not horses or len(horses) < 2: return "NO_DATA"

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

    if save_history: 
        pass
    AR_CACHE[r_num] = (time.time(), res_dict)
    return res_dict
