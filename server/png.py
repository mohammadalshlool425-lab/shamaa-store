"""
🖼️ توليد صور PNG بدون أي اعتماديات خارجية
=============================================
بطاقات Open Graph يجب أن تكون PNG/JPEG — منصات المشاركة (واتساب، فيسبوك،
X، لينكدإن) لا تقرأ SVG. وبما أن المنصة تلتزم بصفر اعتماديات، يُنفَّذ هنا
مرمّز PNG أدنى (Truecolor + ألفا، بلا تصفية) باستخدام `zlib` و`struct`
من المكتبة القياسية فقط.

يتضمن أيضاً خطاً نقطياً (bitmap) مدمجاً للأرقام والأحرف اللاتينية، لأن
رسم النص العربي يحتاج ملفات خطوط وهي اعتمادية ثقيلة. العنوان العربي
يصل لمنصات المشاركة عبر وسم `og:title` النصي، والصورة تحمل السعر
بالأرقام + الهوية البصرية + رمز QR.
"""
from __future__ import annotations

import math
import struct
import zlib
from typing import List, Optional, Sequence, Tuple

RGB = Tuple[int, int, int]

# ─────────────────────────────────────────────────────────────
# مرمّز PNG
# ─────────────────────────────────────────────────────────────
def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def encode_png(width: int, height: int, rows: List[bytes],
               level: int = 9) -> bytes:
    """
    يرمّز صورة Truecolor+alpha (8 بت/قناة، 4 بايت لكل بكسل).
    `rows` قائمة من كائنات bytes طول كل منها width*4.
    """
    assert len(rows) == height, f"عدد الصفوف {len(rows)} ≠ الارتفاع {height}"
    raw = b"".join(b"\x00" + r for r in rows)          # 0 = بلا تصفية
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw, level))
            + _chunk(b"IEND", b""))


class Canvas:
    """سطح رسم بسيط: تدرجات، مستطيلات، دوائر، ونص نقطي."""

    def __init__(self, width: int, height: int, bg: RGB = (255, 255, 255)):
        self.w = width
        self.h = height
        self.buf = bytearray(width * height * 4)
        self.fill_rect(0, 0, width, height, bg)

    # ── بكسل ──
    def get_px(self, x: int, y: int) -> tuple[int, int, int, int]:
        """قراءة RGBA لبكسل — تُستخدم في الفحوصات وضبط المحاذاة."""
        if not (0 <= x < self.w and 0 <= y < self.h):
            return (0, 0, 0, 0)
        i = (y * self.w + x) * 4
        return (self.buf[i], self.buf[i + 1], self.buf[i + 2], self.buf[i + 3])

    def set_px(self, x: int, y: int, color: RGB, alpha: int = 255) -> None:
        """
        يرسم بكسلاً. الشفافية تُدمج فوق اللون الموجود (compositing) ولا تُكتب
        في قناة ألفا: بطاقة المشاركة تخرج معتمة دائماً، لأن واتساب وفيسبوك
        يضعان الصور الشفافة فوق خلفية بيضاء فتبهت ألوان الهوية.
        """
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        if alpha >= 255:
            i = (y * self.w + x) * 4
            self.buf[i] = color[0]
            self.buf[i + 1] = color[1]
            self.buf[i + 2] = color[2]
            self.buf[i + 3] = 255
        else:
            self.blend_px(x, y, color, alpha / 255.0)

    def blend_px(self, x: int, y: int, color: RGB, t: float) -> None:
        """مزج لون بنسبة t (0..1) فوق الموجود — لحواف ناعمة."""
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        t = max(0.0, min(1.0, t))
        i = (y * self.w + x) * 4
        self.buf[i] = int(self.buf[i] * (1 - t) + color[0] * t)
        self.buf[i + 1] = int(self.buf[i + 1] * (1 - t) + color[1] * t)
        self.buf[i + 2] = int(self.buf[i + 2] * (1 - t) + color[2] * t)
        self.buf[i + 3] = 255

    # ── أشكال ──
    def fill_rect(self, x: int, y: int, w: int, h: int, color: RGB) -> None:
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + w), min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(color) + b"\xff"
        line = row * (x1 - x0)
        for yy in range(y0, y1):
            i = (yy * self.w + x0) * 4
            self.buf[i:i + len(line)] = line

    def vgradient(self, top: RGB, bottom: RGB, y: int = 0,
                  height: Optional[int] = None) -> None:
        h = height or (self.h - y)
        for j in range(h):
            t = j / max(1, h - 1)
            c = tuple(int(top[k] + (bottom[k] - top[k]) * t) for k in range(3))
            self.fill_rect(0, y + j, self.w, 1, c)  # type: ignore[arg-type]

    def dgradient(self, a: RGB, b: RGB) -> None:
        """
        تدرج قطري — يعطي عمقاً للبطاقة.

        مُنفَّذ عبر جداول إزاحة (translate tables) وقِطع الـ memoryview بدل
        حلقة على كل بكسل: نفس النتيجة لكن أسرع بعشرات المرات، لأن زاحف
        واتساب/فيسبوك يجلب البطاقة عند كل مشاركة.
        """
        denom = max(1, self.w + self.h - 2)

        def clamp(v: int) -> int:
            return 0 if v < 0 else (255 if v > 255 else v)

        def table(delta: int) -> bytes:
            """جدول إزاحة قناة لونية بمقدار ثابت لكل القيم 0..255."""
            return bytes(clamp(v + delta) for v in range(256))

        # صف الأساس (yy = 0)
        base_row = bytearray(self.w * 4)
        for xx in range(self.w):
            t = xx / denom
            i = xx * 4
            base_row[i] = clamp(int(a[0] + (b[0] - a[0]) * t))
            base_row[i + 1] = clamp(int(a[1] + (b[1] - a[1]) * t))
            base_row[i + 2] = clamp(int(a[2] + (b[2] - a[2]) * t))
            base_row[i + 3] = 255
        r0 = bytes(base_row[0::4]); g0 = bytes(base_row[1::4]); b0 = bytes(base_row[2::4])
        alpha = bytes(base_row[3::4])

        dr = (b[0] - a[0]) / denom
        dg = (b[1] - a[1]) / denom
        db = (b[2] - a[2]) / denom

        prev_dy = None
        prev = None
        for yy in range(self.h):
            dy_r, dy_g, dy_b = int(dr * yy), int(dg * yy), int(db * yy)
            if (dy_r, dy_g, dy_b) != prev_dy:
                tr, tg, tb = table(dy_r), table(dy_g), table(dy_b)
                row = bytearray(self.w * 4)
                row[0::4] = r0.translate(tr)
                row[1::4] = g0.translate(tg)
                row[2::4] = b0.translate(tb)
                row[3::4] = alpha
                prev, prev_dy = bytes(row), (dy_r, dy_g, dy_b)
            o = yy * self.w * 4
            self.buf[o:o + len(prev)] = prev

    def circle(self, cx: int, cy: int, r: int, color: RGB,
               alpha: int = 255) -> None:
        for yy in range(max(0, cy - r), min(self.h, cy + r + 1)):
            dy = yy - cy
            span = int((r * r - dy * dy) ** 0.5) if r * r >= dy * dy else 0
            for xx in range(max(0, cx - span), min(self.w, cx + span + 1)):
                self.set_px(xx, yy, color, alpha)

    def ring(self, cx: int, cy: int, r: int, thickness: int,
             color: RGB) -> None:
        for rr in range(max(0, r - thickness), r + 1):
            self.circle_outline(cx, cy, rr, color)

    def circle_outline(self, cx: int, cy: int, r: int, color: RGB) -> None:
        if r <= 0:
            return
        for deg in range(0, 360):
            xx = int(cx + r * math.cos(deg * math.pi / 180))
            yy = int(cy + r * math.sin(deg * math.pi / 180))
            self.set_px(xx, yy, color)

    # ── نص نقطي ──
    def text(self, s: str, x: int, y: int, scale: int = 1,
             color: RGB = (255, 255, 255), spacing: int = 1) -> int:
        """يرسم نصاً لاتينياً/أرقاماً ويعيد العرض الكلي بالبكسل."""
        cx = x
        for ch in s:
            g = FONT.get(ch.upper())
            if g is None:
                cx += (FONT_WIDTH.get(" ", 3) + spacing) * scale
                continue
            for ry, row in enumerate(g):
                for rx, on in enumerate(row):
                    if on == "#":
                        self.fill_rect(cx + rx * scale, y + ry * scale,
                                       scale, scale, color)
            cx += (len(g[0]) + spacing) * scale
        return cx - x

    def text_width(self, s: str, scale: int = 1, spacing: int = 1) -> int:
        w = 0
        for ch in s:
            g = FONT.get(ch.upper())
            w += ((len(g[0]) if g else FONT_WIDTH.get(" ", 3)) + spacing) * scale
        return max(0, w - spacing * scale)

    # ── تصدير ──
    def to_png(self, level: int = 9) -> bytes:
        rows = [bytes(self.buf[y * self.w * 4:(y + 1) * self.w * 4])
                for y in range(self.h)]
        return encode_png(self.w, self.h, rows, level)


# ─────────────────────────────────────────────────────────────
# خط نقطي مدمج (7 صفوف) — أرقام + أحرف لاتينية + رموز
# ─────────────────────────────────────────────────────────────
def _g(*rows: str) -> List[str]:
    return list(rows)


FONT = {
    "0": _g(".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": _g("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": _g(".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": _g("#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."),
    "4": _g("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": _g("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": _g("..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."),
    "7": _g("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": _g(".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": _g(".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."),
    "A": _g(".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": _g("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": _g(".####", "#....", "#....", "#....", "#....", "#....", ".####"),
    "D": _g("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": _g("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": _g("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": _g(".####", "#....", "#....", "#..##", "#...#", "#...#", ".###."),
    "H": _g("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": _g("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "J": _g("..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": _g("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": _g("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": _g("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": _g("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": _g(".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": _g("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": _g(".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": _g("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": _g(".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": _g("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": _g("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": _g("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": _g("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"),
    "X": _g("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": _g("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": _g("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    ".": _g(".....", ".....", ".....", ".....", ".....", ".##..", ".##.."),
    ",": _g(".....", ".....", ".....", ".....", ".##..", ".##..", ".#..."),
    "-": _g(".....", ".....", ".....", "#####", ".....", ".....", "....."),
    "+": _g(".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."),
    ":": _g(".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."),
    "/": _g("....#", "...#.", "...#.", "..#..", ".#...", ".#...", "#...."),
    "%": _g("#...#", "#..#.", "...#.", "..#..", ".#...", ".#..#", "#...#"),
    "(": _g("..##.", ".#...", "#....", "#....", "#....", ".#...", "..##."),
    ")": _g(".##..", "...#.", "....#", "....#", "....#", "...#.", ".##.."),
    "*": _g(".....", "#.#.#", ".###.", "#####", ".###.", "#.#.#", "....."),
    "=": _g(".....", ".....", "#####", ".....", "#####", ".....", "....."),
    " ": _g("...", "...", "...", "...", "...", "...", "..."),
}
FONT_WIDTH = {" ": 3}


# ─────────────────────────────────────────────────────────────
# رسم رمز QR على السطح
# ─────────────────────────────────────────────────────────────
def draw_qr(canvas: Canvas, modules: Sequence[Sequence[int]],
            x: int, y: int, scale: int, border: int = 2,
            dark: RGB = (18, 33, 46), light: RGB = (255, 255, 255)) -> None:
    """يرسم مصفوفة QR مع إطار هادئ (quiet zone) إلزامي حوله."""
    n = len(modules)
    total = (n + border * 2) * scale
    canvas.fill_rect(x, y, total, total, light)
    for r in range(n):
        c = 0
        while c < n:
            if modules[r][c]:
                start = c
                while c < n and modules[r][c]:
                    c += 1
                canvas.fill_rect(x + (start + border) * scale,
                                 y + (r + border) * scale,
                                 (c - start) * scale, scale, dark)
            else:
                c += 1
    return total
