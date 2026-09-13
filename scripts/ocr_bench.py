"""٤-ج: مقارنة طرق تهيئة الصورة قبل OCR على صور هواتف حقيقيّة.

المعالجة الحاليّة (app/importer.py::_preprocess_for_ocr) تستعمل عتبة ثنائيّة ثابتة
(p > 160). على صور الهواتف بإضاءة غير متجانسة (ظلّ، وهج) تُتلِف هذه العتبة العالميّة
النصّ: المناطق الداكنة تصير سواداً كاملاً والفاتحة بياضاً. هذا السكربت يقيس أربع طرق
على صورك الفعليّة ليُبنى القرار على قياس لا على تخمين (أمر شغل شتنبر 2026، §٤-ج):

  gray      — رماديّ + رفع تباين، بلا تحويل ثنائيّ.
  fixed160  — الطريقة الحاليّة (عتبة عالميّة ثابتة p>160).
  otsu      — عتبة عالميّة تُحسَب من الصورة (Otsu، بلا مكتبة جديدة).
  adaptive  — عتبة محلّيّة (متوسّط الجوار عبر BoxBlur — تعالج الإضاءة غير المتجانسة).

لكلّ صورة وطريقة: عدد الأحرف المستخرَجة ومتوسّط ثقة Tesseract، وتُحفَظ الصور المعالَجة
للمعاينة بالعين. **لا توصية قبل رؤية الأرقام على صورك.**

الاستعمال:
  python scripts/ocr_bench.py --dir صورك/ --out /tmp/ocr-out
  (يتطلّب ثنائي Tesseract + حزمة 'ara' لقياس النصّ؛ بدونهما يحفظ الصور المعالَجة فقط.)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _base_gray(img: Image.Image) -> Image.Image:
    """رماديّ + تصحيح دوران + تكبير منخفض الدقّة + رفع تباين — أساس مشترك للكلّ."""
    img = ImageOps.exif_transpose(img).convert("L")
    if img.width < 1500:
        scale = 1500 / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    return ImageOps.autocontrast(img)


def variant_gray(g: Image.Image) -> Image.Image:
    return g


def variant_fixed160(g: Image.Image) -> Image.Image:
    return g.point(lambda p: 255 if p > 160 else 0)   # الطريقة الحاليّة


def _otsu_threshold(arr: np.ndarray) -> int:
    """عتبة Otsu: تعظّم التباين البينيّ للصنفين من مدرّج الرماديّ."""
    hist = np.histogram(arr, bins=256, range=(0, 256))[0].astype(np.float64)
    total = arr.size
    sum_total = np.dot(np.arange(256), hist)
    w_b = 0.0
    sum_b = 0.0
    max_var = -1.0
    thr = 127
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_total - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > max_var:
            max_var = var
            thr = t
    return thr


def variant_otsu(g: Image.Image) -> Image.Image:
    arr = np.asarray(g)
    thr = _otsu_threshold(arr)
    return Image.fromarray(((arr > thr) * 255).astype("uint8"))


def variant_adaptive(g: Image.Image, radius: int = 25, c: int = 10) -> Image.Image:
    """عتبة محلّيّة: أبيض إن كان البكسل أفتح من متوسّط جواره بفارق c.
    تعالج الظلّ/الوهج لأنّ العتبة تتغيّر مع مكان البكسل، لا عتبة واحدة للكلّ."""
    arr = np.asarray(g, dtype=np.int16)
    local_mean = np.asarray(g.filter(ImageFilter.BoxBlur(radius)), dtype=np.int16)
    out = (arr > (local_mean - c)) * 255
    return Image.fromarray(out.astype("uint8"))


VARIANTS = {
    "gray": variant_gray,
    "fixed160": variant_fixed160,
    "otsu": variant_otsu,
    "adaptive": variant_adaptive,
}


def _score_ocr(img: Image.Image):
    """(عدد الأحرف، متوسّط الثقة) عبر Tesseract، أو None إن غاب الثنائيّ."""
    try:
        import pytesseract
        data = pytesseract.image_to_data(img, lang="ara", config="--psm 6",
                                         output_type=pytesseract.Output.DICT)
    except Exception:  # noqa: BLE001 — الثنائيّ غائب أو اللغة غير مثبّتة
        return None
    words = [w for w in data.get("text", []) if w.strip()]
    confs = [float(c) for c in data.get("conf", []) if str(c).replace(".", "").lstrip("-").isdigit() and float(c) >= 0]
    chars = sum(len(w) for w in words)
    mean_conf = round(sum(confs) / len(confs), 1) if confs else 0.0
    return chars, mean_conf


def main():
    ap = argparse.ArgumentParser(description="مقارنة تهيئة OCR على صور حقيقيّة")
    ap.add_argument("--dir", required=True, help="مجلّد صور الهواتف (jpg/png)")
    ap.add_argument("--out", default="/tmp/ocr-out", help="مجلّد حفظ الصور المعالَجة للمعاينة")
    a = ap.parse_args()

    src = Path(a.dir)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    imgs = sorted(p for p in src.iterdir()
                  if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if not imgs:
        ap.error(f"لا صور jpg/png في {src}")

    ocr_ready = _score_ocr(Image.new("L", (10, 10), 255)) is not None
    if not ocr_ready:
        print("⚠ Tesseract غير متاح هنا — تُحفَظ الصور المعالَجة للمعاينة فقط، بلا قياس نصّ.\n")

    print(f"{'الصورة':<22} {'الطريقة':<10} {'أحرف':>6} {'ثقة%':>6}")
    print("-" * 48)
    for p in imgs:
        g = _base_gray(Image.open(p))
        best = None
        for name, fn in VARIANTS.items():
            proc = fn(g)
            proc.save(out / f"{p.stem}__{name}.png")
            if ocr_ready:
                chars, conf = _score_ocr(proc)
                print(f"{p.name:<22} {name:<10} {chars:>6} {conf:>6}")
                score = (chars, conf)
                if best is None or score > best[1]:
                    best = (name, score)
        if best:
            print(f"  ← الأفضل لهذه الصورة: {best[0]} (أحرف={best[1][0]}, ثقة={best[1][1]})\n")
    print(f"\nالصور المعالَجة في: {out}")
    print("راجِع الأرقام والصور، ثمّ قرّر الطريقة (لا توصية آليّة — القرار على قياسك).")


if __name__ == "__main__":
    main()
