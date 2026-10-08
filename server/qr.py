"""
📱 ترميز QR من الصفر — بدون أي اعتماديات خارجية
=================================================
يولّد مصفوفة رمز QR (وضع بايت، مستوى تصحيح M، إصدارات 1–6) لعناوين
روابط المنصة. مكتوب يدوياً التزاماً بمبدأ «صفر اعتماديات» الذي تقوم عليه
الواجهة كلها، ولكي يعمل في أي بيئة نشر بدون تثبيت حزم.

مستوى التصحيح M يستعيد حتى 15% من التلف — مناسب لرمز يُطبع أو يُعرض
على شاشة هاتف تحت الشمس.

الصحة مُتحقَّق منها بطريقتين (انظر tests/test_qr.py):
  1) فك ترميز ذاتي: يُقرأ الرمز المولَّد عكسياً ويُقارن بالنص الأصلي.
  2) مقارنة بنيوية مع مرجع موثوق (Nayuki QR-Code-generator، رخصة MIT)
     لمواضع معلومات التنسيق وثوابت العقوبات.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

# ─────────────────────────────────────────────────────────────
# حقل غالوا GF(256) — أساس تصحيح ريد-سولومون
# ─────────────────────────────────────────────────────────────
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D          # كثيرة الحدود المولّدة لـ QR
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(degree: int) -> List[int]:
    g = [1]
    for i in range(degree):
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            ng[j] ^= c
            ng[j + 1] ^= _gf_mul(c, _EXP[i])
        g = ng
    return g


def _rs_ec(data: List[int], n: int) -> List[int]:
    """بقية قسمة كثيرة الحدود -> كلمات تصحيح الأخطاء."""
    g = _rs_generator(n)
    res = list(data) + [0] * n
    for i in range(len(data)):
        c = res[i]
        if c:
            for j, coef in enumerate(g):
                res[i + j] ^= _gf_mul(coef, c)
    return res[len(data):]


# ─────────────────────────────────────────────────────────────
# جداول المواصفة (مستوى التصحيح M)
# ─────────────────────────────────────────────────────────────
# الإصدار: (إجمالي الكلمات, كلمات التصحيح لكل كتلة, عدد الكتلات)
_VERSIONS_M = {
    1: (26, 10, 1),
    2: (44, 16, 1),
    3: (70, 26, 1),
    4: (100, 18, 2),
    5: (134, 26, 2),
    6: (172, 18, 4),
}

# مواضع أنماط المحاذاة (مركز النمط)
_ALIGNMENT = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34]}

# بتات حشو بعد البيانات في بعض الإصدارات
_REMAINDER_BITS = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7}

EC_LEVEL_BITS = 0b00          # M
FMT_MASK_XOR = 0x5412


def _data_codewords(version: int) -> int:
    total, ecw, blocks = _VERSIONS_M[version]
    return total - ecw * blocks


def pick_version(nbytes: int) -> int:
    """أصغر إصدار يتسع لـ nbytes في وضع بايت بمستوى M."""
    for v in range(1, 7):
        cap = _data_codewords(v) * 8
        need = 4 + (8 if v <= 9 else 16) + nbytes * 8
        if need <= cap:
            return v
    raise ValueError("النص أطول من دعم الإصدارات 1–6 (اختصر الرابط)")


# ─────────────────────────────────────────────────────────────
# الترميز
# ─────────────────────────────────────────────────────────────
def encode(text: str) -> Tuple[int, List[int]]:
    """يعيد (الإصدار، سلسلة الكلمات المشفّرة والمتشابكة)."""
    data = text.encode("utf-8")
    version = pick_version(len(data))
    total, ecw, nblocks = _VERSIONS_M[version]
    data_cw = _data_codewords(version)

    bits: List[int] = []

    def push(value: int, count: int) -> None:
        for i in range(count - 1, -1, -1):
            bits.append((value >> i) & 1)

    push(0b0100, 4)                                    # وضع البايت
    push(len(data), 8 if version <= 9 else 16)         # عدد المحارف
    for b in data:
        push(b, 8)
    push(0, min(4, data_cw * 8 - len(bits)))           # فاصل النهاية
    while len(bits) % 8:
        bits.append(0)                                 # إكمال البايت الأخير

    codewords = [int("".join(map(str, bits[i:i + 8])), 2)
                 for i in range(0, len(bits), 8)]
    padding = (0xEC, 0x11)
    i = 0
    while len(codewords) < data_cw:
        codewords.append(padding[i % 2])
        i += 1

    # تقسيم إلى كتل وحساب تصحيح الأخطاء لكل كتلة
    base, extra = divmod(data_cw, nblocks)
    blocks: List[List[int]] = []
    eccs: List[List[int]] = []
    offset = 0
    for b in range(nblocks):
        # الكتل الأخيرة هي الأكبر (حسب المواصفة)
        size = base + (1 if b >= nblocks - extra else 0)
        block = codewords[offset:offset + size]
        offset += size
        blocks.append(block)
        eccs.append(_rs_ec(block, ecw))

    # التشابك: كلمة من كل كتلة بالتناوب
    final: List[int] = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                final.append(b[i])
    for i in range(ecw):
        for e in eccs:
            if i < len(e):
                final.append(e[i])
    return version, final


# ─────────────────────────────────────────────────────────────
# بناء المصفوفة
# ─────────────────────────────────────────────────────────────
def _new_matrix(version: int):
    size = 17 + 4 * version
    modules = [[0] * size for _ in range(size)]
    is_fn = [[False] * size for _ in range(size)]       # خلية وظيفية (لا تحمل بيانات)
    return modules, is_fn, size


def _draw_finder(modules, is_fn, size, row, col) -> None:
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            r, c = row + dr, col + dc
            if not (0 <= r < size and 0 <= c < size):
                continue
            inside = 0 <= dr <= 6 and 0 <= dc <= 6
            ring = inside and (dr in (0, 6) or dc in (0, 6))
            core = inside and 2 <= dr <= 4 and 2 <= dc <= 4
            modules[r][c] = 1 if (ring or core) else 0
            is_fn[r][c] = True


def _draw_patterns(modules, is_fn, size, version) -> None:
    # أنماط التوقيت أولاً، ثم الباحثات تطمس ما يتقاطع معها (ترتيب المرجع)
    for i in range(8, size - 8):
        v = 1 if i % 2 == 0 else 0
        modules[6][i] = modules[i][6] = v
        is_fn[6][i] = is_fn[i][6] = True

    _draw_finder(modules, is_fn, size, 0, 0)
    _draw_finder(modules, is_fn, size, 0, size - 7)
    _draw_finder(modules, is_fn, size, size - 7, 0)

    # أنماط المحاذاة
    pos = _ALIGNMENT[version]
    for r in pos:
        for c in pos:
            # تُتخطى الزوايا الثلاث التي تشغلها أنماط البحث
            if (r <= 8 and c <= 8) or (r <= 8 and c >= size - 9) or (r >= size - 9 and c <= 8):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    modules[r + dr][c + dc] = 1 if max(abs(dr), abs(dc)) != 1 else 0
                    is_fn[r + dr][c + dc] = True

    # حجز مناطق معلومات التنسيق + الوحدة الداكنة الدائمة
    for i in range(9):
        is_fn[8][i] = True
        is_fn[i][8] = True
    for i in range(8):
        is_fn[8][size - 1 - i] = True
    for i in range(8):
        is_fn[size - 1 - i][8] = True
    modules[size - 8][8] = 1
    is_fn[size - 8][8] = True


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _format_bits(mask: int) -> int:
    """15 بت: بيانات + تصحيح BCH ثم قناع — مطابق لخوارزمية المواصفة."""
    data = (EC_LEVEL_BITS << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ FMT_MASK_XOR


def _draw_format(modules, size, mask) -> None:
    """
    نسختان من معلومات التنسيق (15 بت).

    ⚠️ المواضع مكتوبة هنا بالصيغة (صف، عمود) مباشرة. انتبه: المرجع الذي
    طوبقنا معه يستخدم `_set_function_module(x, y)` التي تضبط `_modules[y][x]`،
    فاستدعاؤه `(8, i)` يعني **العمود 8** لا الصف 8. النقل الخاطئ هنا كان
    يجعل الرمز غير مقروء لأي ماسح رغم أنه يبدو صحيحاً للعين.
    """
    bits = _format_bits(mask)

    def bit(i: int) -> int:
        return (bits >> i) & 1

    # النسخة الأولى: شريط عمودي على العمود 8 (يتخطى خلية التوقيت عند الصف 6)
    for i in range(6):
        modules[i][8] = bit(i)
    modules[7][8] = bit(6)
    modules[8][8] = bit(7)
    modules[8][7] = bit(8)
    for i in range(9, 15):
        modules[8][14 - i] = bit(i)

    # النسخة الثانية: شريط أفقي على الصف 8 (يمين) + عمودي (أسفل اليسار)
    for i in range(8):
        modules[8][size - 1 - i] = bit(i)
    for i in range(8, 15):
        modules[size - 15 + i][8] = bit(i)
    modules[size - 8][8] = 1                      # الوحدة الداكنة الدائمة


def _place_data(modules, is_fn, size, codewords, mask, version) -> None:
    bits: List[int] = []
    for cw in codewords:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)
    bits.extend([0] * _REMAINDER_BITS[version])

    fn = _MASKS[mask]
    idx = 0
    upward = True
    col = size - 1
    while col >= 1:
        if col == 6:                              # عمود التوقيت يُتخطى
            col = 5
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for c in (col, col - 1):
                if is_fn[r][c]:
                    continue
                b = bits[idx] if idx < len(bits) else 0
                idx += 1
                modules[r][c] = b ^ (1 if fn(r, c) else 0)
        upward = not upward
        col -= 2


def _penalty(modules, size) -> int:
    """قواعد العقوبات الأربع — الأوزان 3/3/40/10 مطابقة للمواصفة."""
    score = 0

    def lines():
        for r in range(size):
            yield [modules[r][c] for c in range(size)]
        for c in range(size):
            yield [modules[r][c] for r in range(size)]

    # 1) سلسلة أحادية اللون بطول ≥ 5
    for line in lines():
        run, prev = 1, line[0]
        for v in line[1:]:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run, prev = 1, v
        if run >= 5:
            score += 3 + (run - 5)

    # 2) مربعات 2×2 أحادية اللون
    for r in range(size - 1):
        for c in range(size - 1):
            v = modules[r][c]
            if v == modules[r][c + 1] == modules[r + 1][c] == modules[r + 1][c + 1]:
                score += 3

    # 3) نمط شبيه بالباحث 1011101
    pattern = [1, 0, 1, 1, 1, 0, 1]
    for line in lines():
        for i in range(len(line) - 6):
            if line[i:i + 7] == pattern:
                score += 40

    # 4) توازن الوحدات الداكنة حول 50%
    dark = sum(sum(row) for row in modules)
    total = size * size
    score += 10 * (abs(dark * 100 // total - 50) // 5)
    return score


def make_matrix(text: str) -> Tuple[List[List[int]], int, int]:
    """
    يولّد مصفوفة رمز QR.
    يعيد (المصفوفة، الحجم، القناع) بعد اختيار أقل الأقنعة عقوبة.
    """
    version, codewords = encode(text)
    best = None
    best_score = None
    for mask in range(8):
        modules, is_fn, size = _new_matrix(version)
        _draw_patterns(modules, is_fn, size, version)
        _draw_format(modules, size, mask)
        _place_data(modules, is_fn, size, codewords, mask, version)
        score = _penalty(modules, size)
        if best_score is None or score < best_score:
            best, best_score = (modules, size, mask, version), score
    modules, size, mask, version = best
    return modules, size, mask


def to_ascii(text: str, border: int = 2) -> str:
    """تمثيل نصي للتحقق البصري في الطرفية (██ داكن / فراغ فاتح)."""
    modules, size, _ = make_matrix(text)
    out = []
    for _ in range(border):
        out.append("  " * (size + border * 2))
    for r in range(size):
        row = "  " * border + "".join("██" if modules[r][c] else "  " for c in range(size)) + "  " * border
        out.append(row)
    for _ in range(border):
        out.append("  " * (size + border * 2))
    return "\n".join(out)


def to_svg(text: str, border: int = 2, scale: int = 6,
         dark: str = "#12212E", light: str = "#FFFFFF") -> str:
    """SVG متجهي نظيف — يُعرض مباشرة في صفحة الإعلان بدون أي مكتبة."""
    modules, size, _ = make_matrix(text)
    dim = (size + border * 2) * scale
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{dim}" height="{dim}" '
        f'viewBox="0 0 {dim} {dim}" shape-rendering="crispEdges" role="img" '
        f'aria-label="رمز QR لفتح الإعلان على الهاتف">',
        f'<rect width="{dim}" height="{dim}" fill="{light}"/>',
    ]
    # دمج الوحدات الداكنة المتجاورة في مستطيلات أفقية -> ملف أصغر بكثير
    for r in range(size):
        c = 0
        while c < size:
            if modules[r][c]:
                start = c
                while c < size and modules[r][c]:
                    c += 1
                x = (start + border) * scale
                y = (r + border) * scale
                w = (c - start) * scale
                parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{scale}" fill="{dark}"/>')
            else:
                c += 1
    parts.append("</svg>")
    return "".join(parts)


# ─────────────────────────────────────────────────────────────
# فك الترميز الذاتي — للتحقق من صحة الترميز في الاختبارات
# ─────────────────────────────────────────────────────────────
def decode(modules: List[List[int]], size: int, mask: int,
           version: Optional[int] = None) -> str:
    """
    يفكّ رمزاً ولّدناه بأنفسنا — يُستخدم للتحقق أن الترميز صحيح.
    ليس فاكّاً عاماً (يفترض مستوى M ولا يصحح أخطاء).
    """
    if version is None:
        version = (size - 17) // 4
    _, is_fn, _ = _matrix_skeleton(version)

    fn = _MASKS[mask]
    bits: List[int] = []
    upward = True
    col = size - 1
    while col >= 1:
        if col == 6:
            col = 5
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for c in (col, col - 1):
                if is_fn[r][c]:
                    continue
                bits.append(modules[r][c] ^ (1 if fn(r, c) else 0))
        upward = not upward
        col -= 2

    codewords = [int("".join(map(str, bits[i:i + 8])), 2)
                 for i in range(0, len(bits) - len(bits) % 8, 8)]

    total, ecw, nblocks = _VERSIONS_M[version]
    data_cw = _data_codewords(version)
    base, extra = divmod(data_cw, nblocks)
    sizes = [base + (1 if b >= nblocks - extra else 0) for b in range(nblocks)]

    # فك التشابك
    blocks: List[List[int]] = [[] for _ in range(nblocks)]
    ecc_blocks: List[List[int]] = [[] for _ in range(nblocks)]
    idx = 0
    for i in range(max(sizes)):
        for b in range(nblocks):
            if i < sizes[b]:
                blocks[b].append(codewords[idx])
                idx += 1
    for _ in range(ecw):
        for b in range(nblocks):
            ecc_blocks[b].append(codewords[idx])
            idx += 1

    flat = [c for b in blocks for c in b]
    payload = flat[:data_cw]

    bitstr = "".join(f"{c:08b}" for c in payload)
    mode = int(bitstr[0:4], 2)
    if mode != 0b0100:
        raise ValueError(f"وضع غير مدعوم: {mode}")
    length_bits = 8 if version <= 9 else 16
    count = int(bitstr[4:4 + length_bits], 2)
    body = bitstr[4 + length_bits:4 + length_bits + count * 8]
    raw = bytes(int(body[i:i + 8], 2) for i in range(0, len(body), 8))

    # تحقّق إضافي: تصحيح الأخطاء المحسوب يجب أن يطابق المحفوظ
    for b in range(nblocks):
        assert _rs_ec(blocks[b], ecw) == ecc_blocks[b], "عدم تطابق في تصحيح الأخطاء"
    return raw.decode("utf-8")


def _matrix_skeleton(version: int):
    """مصفوفة وظيفية فقط (لتحديد الخلايا غير البيانية أثناء فك الترميز)."""
    modules, is_fn, size = _new_matrix(version)
    _draw_patterns(modules, is_fn, size, version)
    return modules, is_fn, size


def roundtrip(text: str) -> bool:
    """يتحقق أن النص يُرمَّز ثم يُفكّ إلى نفسه حرفياً."""
    modules, size, mask = make_matrix(text)
    version = (size - 17) // 4
    return decode(modules, size, mask, version) == text
