import re


def _fa_to_en(value):
    """تبدیل اعداد فارسی و عربی به انگلیسی"""
    if value is None:
        return ""

    value = str(value)

    table = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )

    return value.translate(table)


def _normalize_text(value):
    """یکسان‌سازی متن فارسی برای تشخیص عنوان ستون‌ها"""
    value = _fa_to_en(value)

    value = value.replace("ي", "ی")
    value = value.replace("ك", "ک")
    value = value.replace("\u200c", "")
    value = value.replace("\u200f", "")
    value = value.replace("\ufeff", "")

    value = re.sub(r"\s+", "", value)
    value = value.replace("ـ", "")

    return value.strip().lower()


def _column_type(value):
    """تشخیص نوع ستون بر اساس عنوان واقعی آن"""
    text = _normalize_text(value)

    if "شماره" in text or text in ("no", "number", "bib"):
        return "number"

    if "ناماسب" in text or text in ("horse", "horsename"):
        return "name"

    if "پیشبر" in text or "پیشبَر" in text:
        return "pre"

    if "میانبر" in text or "میانبَر" in text:
        return "mid"

    if "پیبر" in text or "پیبَر" in text:
        return "pi"

    return None


def _parse_decimal(value):
    """استخراج عدد اعشاری از متن"""
    value = _fa_to_en(value)
    value = value.replace("٫", ".").replace(",", ".")

    match = re.search(r"(?<!\d)\d+(?:\.\d+)?", value)

    if not match:
        return None

    try:
        number = float(match.group(0))

        if number <= 0:
            return None

        return number

    except (TypeError, ValueError):
        return None


def _parse_horse_number(cell):
    """استخراج شماره اسب از متن یا مشخصات تصویر لباس اسب"""
    if isinstance(cell, dict):
        text = str(cell.get("text", ""))
        meta = str(cell.get("meta", ""))
    else:
        text = str(cell)
        meta = ""

    text = _fa_to_en(text)
    meta = _fa_to_en(meta)

    # ابتدا شماره‌ای که مستقیماً در سلول نوشته شده
    match = re.search(r"^\s*(\d{1,2})\s*$", text)

    if match:
        return int(match.group(1))

    # سپس شماره موجود در alt، title یا مسیر تصویر لباس
    match = re.search(
        r"(?:^|\D)(\d{1,2})(?:\D|$)",
        meta
    )

    if match:
        return int(match.group(1))

    # در نهایت بررسی متن سلول
    match = re.search(
        r"(?:^|\D)(\d{1,2})(?:\D|$)",
        text
    )

    if match:
        return int(match.group(1))

    return None


def _is_scratched(row):
    """تشخیص اسب حذف‌شده"""
    text_parts = []

    for cell in row:
        if isinstance(cell, dict):
            text_parts.append(str(cell.get("text", "")))
            text_parts.append(str(cell.get("meta", "")))

            if cell.get("strike"):
                return True
        else:
            text_parts.append(str(cell))

    text = " ".join(text_parts)
    normalized = _normalize_text(text)

    if any(mark in text for mark in ("×", "✕", "✖")):
        return True

    if any(word in normalized for word in (
        "حذف",
        "حذفشده",
        "انصراف",
        "scratch",
        "scratched",
        "withdrawn"
    )):
        return True

    return False


async def extract_rows(page):
    """
    استخراج سطرهای جدول از صفحه سایت.
    عنوان و اطلاعات سلول‌ها همراه با مشخصات تصاویر ذخیره می‌شوند.
    """

    result = await page.evaluate("""
    () => {
        const selectors = [
            'table tr',
            '[role="row"]',
            '.v-data-table__tr'
        ];

        const elements = [];

        for (const selector of selectors) {
            document.querySelectorAll(selector).forEach(el => {
                if (!elements.includes(el)) {
                    elements.push(el);
                }
            });
        }

        return elements.map(row => {
            let cells = Array.from(row.children).filter(el => {
                const tag = el.tagName.toLowerCase();
                const role = el.getAttribute('role');

                return (
                    tag === 'td' ||
                    tag === 'th' ||
                    role === 'cell' ||
                    role === 'columnheader'
                );
            });

            if (!cells.length) {
                cells = Array.from(
                    row.querySelectorAll(
                        'td, th, [role="cell"], [role="columnheader"]'
                    )
                );
            }

            if (!cells.length) return null;

            const parsedCells = cells.map(cell => {
                const images = Array.from(
                    cell.querySelectorAll('img')
                );

                const metadata = [];

                for (const img of images) {
                    metadata.push(img.getAttribute('alt') || '');
                    metadata.push(img.getAttribute('title') || '');
                    metadata.push(img.getAttribute('src') || '');
                }

                metadata.push(
                    cell.getAttribute('data-number') || ''
                );

                metadata.push(
                    cell.getAttribute('title') || ''
                );

                const style = window.getComputedStyle(cell);

                return {
                    text: (cell.innerText || cell.textContent || '').trim(),
                    meta: metadata.join(' '),
                    html: cell.innerHTML || '',
                    className: cell.className || '',
                    strike:
                        style.textDecorationLine.includes('line-through')
                };
            });

            return {
                cells: parsedCells,
                text: (row.innerText || '').trim(),
                className: row.className || ''
            };
        }).filter(row => row && row.cells.length >= 2);
    }
    """)

    return result or []


def parse_horse_rows(rows):
    """
    استخراج اطلاعات اسب‌ها با استفاده از عنوان واقعی ستون‌ها.

    خروجی:
    number, name, pre_b, mian_b, pi_b
    """

    if not rows:
        raise ValueError(
            "هیچ ردیفی از جدول مسابقه دریافت نشد."
        )

    normalized_rows = []

    for row in rows:
        if isinstance(row, dict):
            cells = row.get("cells", [])
        else:
            cells = row

        normalized_rows.append(cells)

    header_index = None
    column_map = {}

    # پیدا کردن سطر عنوان ستون‌ها
    for row_index, cells in enumerate(normalized_rows):

        current_map = {}

        for index, cell in enumerate(cells):
            if isinstance(cell, dict):
                text = cell.get("text", "")
            else:
                text = str(cell)

            kind = _column_type(text)

            if kind:
                current_map[kind] = index

        # وجود شماره و نام اسب برای شناسایی جدول ضروری است
        if "number" in current_map and "name" in current_map:
            header_index = row_index
            column_map = current_map
            break

    if header_index is None:
        raise ValueError(
            "عنوان ستون‌های شماره و نام اسب در جدول پیدا نشد. "
            "ساختار جدول سایت تغییر کرده یا جدول هنوز بارگذاری نشده است."
        )

    parsed = []

    for row in normalized_rows[header_index + 1:]:

        if not row:
            continue

        if _is_scratched(row):
            continue

        def get_cell(kind):
            index = column_map.get(kind)

            if index is None or index >= len(row):
                return None

            return row[index]

        number_cell = get_cell("number")
        name_cell = get_cell("name")

        if isinstance(name_cell, dict):
            horse_name = str(name_cell.get("text", "")).strip()
        else:
            horse_name = str(name_cell or "").strip()

        horse_name = re.sub(
            r"[×✕✖]",
            "",
            horse_name
        ).strip()

        number = _parse_horse_number(number_cell)

        if number is None or not horse_name:
            continue

        # ضرایب دقیقاً از ستون مربوط به خودشان خوانده می‌شوند
        def get_odds(kind):
            cell = get_cell(kind)

            if cell is None:
                return None

            if isinstance(cell, dict):
                value = cell.get("text", "")
            else:
                value = cell

            return _parse_decimal(value)

        pre_b = get_odds("pre")
        mian_b = get_odds("mid")
        pi_b = get_odds("pi")

        parsed.append({
            "number": number,
            "horse_no": number,

            "name": horse_name,
            "horse": horse_name,

            # پیش‌بَر: انتخاب اسب برای مقام اول
            "pre_b": pre_b,

            # میان‌بَر: انتخاب اسب برای مقام اول یا دوم
            "mian_b": mian_b,

            # پی‌بَر: انتخاب اسب برای سه مقام اول
            "pi_b": pi_b,

            # این اطلاعات در این جدول وجود ندارند
            "ior": None,
            "w_odd": None,
            "jockey": None,
            "form": None,

            "scratched": False
        })

    if not parsed:
        raise ValueError(
            "عنوان جدول شناسایی شد اما اسب فعالی استخراج نشد. "
            "ممکن است مسابقه هنوز شروع نشده یا جدول تغییر کرده باشد."
        )

    # محاسبه احتمال نسبی بر اساس ضرایب موجود
    def add_probabilities(odds_key, probability_key):

        valid = [
            item for item in parsed
            if item.get(odds_key) is not None
            and item[odds_key] > 0
        ]

        if not valid:
            for item in parsed:
                item[probability_key] = None
            return

        inverse_sum = sum(
            1.0 / item[odds_key]
            for item in valid
        )

        for item in parsed:
            odds = item.get(odds_key)

            if odds is None or inverse_sum <= 0:
                item[probability_key] = None
            else:
                item[probability_key] = (
                    (1.0 / odds) / inverse_sum
                )

    add_probabilities("pre_b", "p_win")
    add_probabilities("mian_b", "p_top2")
    add_probabilities("pi_b", "p_top3")

    return parsed
