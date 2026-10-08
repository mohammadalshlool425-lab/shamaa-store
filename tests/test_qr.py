"""
فحوصات وحدات سريعة لطبقة المشاركة — مرمّز QR ومرمّز PNG
=======================================================
تُنفَّذ في أقل من ثانية وبلا خادم، وتلتقط انحدارين خطيرين:
  1. تغيّر ثوابت معلومات التنسيق (format info) → رمز لا يقرؤه أي ماسح.
  2. كسر بنية المصفوفة (finders/timing) → رمز يبدو سليماً وهو ميت.

ثوابت معلومات التنسيق أدناه مُسجلة من مرجع مستقل (Nayuki QR-Code-generator،
رخصة MIT) لمستوى التصحيح M — أي انحراف عنها يعني أن الرمز الناتج لن يُقرأ
على هاتف حقيقي حتى لو نجح فكّه ذاتياً (الفك الذاتي يشارك نفس الافتراضات
ولا يكشف هذا الصنف من الأخطاء).

التشغيل:  python tests/test_qr.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import png, qr  # noqa: E402

PASS = FAIL = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    PASS += bool(ok)
    FAIL += (not ok)
    print(f"  {'✅' if ok else '❌'} {label}{(' — ' + extra) if extra and not ok else ''}")


def main() -> int:
    print("─" * 60)
    print("  1) ثوابت معلومات التنسيق (مرجع مستقل، مستوى M)")
    print("─" * 60)
    # القيم الخمس عشرة بتاً لكل قناع — من المرجع الخارجي
    REF = {
        0: 0b101010000010010, 1: 0b101000100100101, 2: 0b101111001111100,
        3: 0b101101101001011, 4: 0b100010111111001, 5: 0b100000011001110,
        6: 0b100111110010111, 7: 0b100101010100000,
    }
    for mask, want in REF.items():
        got = qr._format_bits(mask)
        check(f"قناع {mask}: {got:015b}", got == want, f"المتوقع {want:015b}")

    print("─" * 60)
    print("  2) بنية المصفوفة (finders + timing + الوحدة الداكنة)")
    print("─" * 60)
    modules, size, mask = qr.make_matrix("https://shamaa.example/l/7")
    n = size

    def finder(r0: int, c0: int) -> bool:
        pat = ["#######", "#.....#", "#.###.#", "#.###.#", "#.###.#", "#.....#", "#######"]
        return all(modules[r0 + i][c0 + j] == (1 if pat[i][j] == "#" else 0)
                   for i in range(7) for j in range(7))

    check("finder أعلى-يسار", finder(0, 0))
    check("finder أعلى-يمين", finder(0, n - 7))
    check("finder أسفل-يسار", finder(n - 7, 0))
    check("صف التوقيت متناوب", all(modules[6][c] == (c % 2 == 0) for c in range(8, n - 8)))
    check("عمود التوقيت متناوب", all(modules[r][6] == (r % 2 == 0) for r in range(8, n - 8)))
    check("الوحدة الداكنة ثابتة", modules[n - 8][8] == 1)

    print("─" * 60)
    print("  3) الذهاب والعودة (ترميز ← فك)")
    print("─" * 60)
    cases = [
        "https://shamaa.example/l/1",
        "https://shamaa.example/l/99999?ref=whatsapp-campaign-2026",
        "https://xn--mgba7fjn.example/l/3",          # نطاق معرّب (punycode)
        "A" * 80,                                     # قرب حد سعة الإصدار 6
    ]
    for text in cases:
        try:
            ok = qr.roundtrip(text)
            check(f"roundtrip({text[:34]}…)" if len(text) > 34 else f"roundtrip({text})", ok)
        except ValueError as e:
            check(f"roundtrip({text[:20]}…)", False, str(e))
    too_long = "X" * 200
    try:
        qr.encode(too_long)
        check("رفض نص يتجاوز سعة الإصدار 6", False, "لم يُرفض!")
    except ValueError:
        check("رفض نص يتجاوز سعة الإصدار 6", True)

    print("─" * 60)
    print("  4) مرمّز PNG")
    print("─" * 60)
    c = png.Canvas(40, 24, (10, 20, 30))
    check("الخلفية تُملأ عند الإنشاء", c.get_px(0, 0)[:3] == (10, 20, 30))
    c.set_px(5, 5, (255, 0, 0))
    check("set_px يكتب اللون", c.get_px(5, 5)[:3] == (255, 0, 0))
    g = png.Canvas(8, 8, (0, 0, 0))
    g.set_px(3, 3, (255, 255, 255), alpha=128)   # دمج ~50% فوق أسود
    px = g.get_px(3, 3)
    check("ألفا تُدمج فوق الموجود", px[:3] == (128, 128, 128), str(px[:3]))
    check("ألفا لا تُخزن في القناة الرابعة", px[3] == 255, str(px[3]))
    check("خارج الحدود آمن", c.get_px(999, 999) == (0, 0, 0, 0))
    c.dgradient((0, 0, 0), (255, 255, 255))
    check("التدرج القطري أحادي الاتجاه",
          c.get_px(0, 0)[:3] == (0, 0, 0) and c.get_px(39, 23)[:3] >= (250, 250, 250))
    data = c.to_png()
    check("ترويسة PNG", data[:8] == b"\x89PNG\r\n\x1a\n")
    import struct
    w, h = struct.unpack(">II", data[16:24])
    check("أبعاد IHDR", (w, h) == (40, 24), f"{w}x{h}")
    check("نوع اللون RGBA 8بت", data[24] == 8 and data[25] == 6)
    check("عرض النص يُحسب", c.text_width("AB", scale=2) == (6 + 6) * 2 - 2)

    print("─" * 60)
    print(f"  النتيجة: {PASS} ناجح · {FAIL} فاشل")
    print("─" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
