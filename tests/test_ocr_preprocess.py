"""٤-ج: المعالجة القبلية للـ OCR لم تعُد تُثنّي بعتبة ثابتة تُشوّه العربية.

تُختبَر بلا ثنائي Tesseract (دالّة نقيّة على صورة PIL)."""

from PIL import Image

from app.importer import _preprocess_for_ocr


def _gradient(w=1600, h=200):
    """صورة رماديّة متدرّجة (تحاكي إضاءة غير متجانسة)."""
    img = Image.new("L", (w, h))
    img.putdata([int(255 * (x / w)) for _ in range(h) for x in range(w)])
    return img


def test_preprocess_keeps_grayscale_not_binary():
    out = _preprocess_for_ocr(_gradient())
    assert out.mode == "L"
    # العتبة الثابتة القديمة كانت تترك لونين فقط (0 و255) فتسحق الحروف؛
    # الآن نُبقي تدرّجاً رماديّاً غنيّاً ليُثنّيه Tesseract داخليّاً.
    levels = len(set(out.getdata()))
    assert levels > 2, f"ما زالت الصورة ثنائيّة ({levels} مستوى) — العتبة القاسية لم تُزَل"


def test_preprocess_upscales_small_images():
    small = _preprocess_for_ocr(Image.new("L", (600, 80), 128))
    assert small.width >= 1500        # التكبير لدقّة ~300DPI محفوظ
