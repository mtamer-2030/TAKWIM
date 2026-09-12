"""اختبار تحسينات OCR (البند ٥-ب): معالجة قبلية للصور + عدم إرجاع PDF ممسوح
نصّاً فارغاً صامتاً بل رسالة واضحة.
"""

import io

import pytest

from app.importer import ImporterError, _preprocess_for_ocr, extract_text_from_pdf


def test_preprocess_upscales_grayscales_and_binarizes():
    from PIL import Image
    # صورة صغيرة ملوّنة → بعد المعالجة: رمادية، مكبَّرة (≥1500 عرضاً)، ثنائية القيم.
    img = Image.new("RGB", (300, 120), (200, 180, 160))
    out = _preprocess_for_ocr(img)
    assert out.mode == "L"
    assert out.width >= 1500                       # صُغِّرت الدقّة فكُبِّرت
    assert set(out.getdata()) <= {0, 255}          # عتبة ثنائية


class _FakePage:
    def extract_text(self):
        return None                                # PDF ممسوح: لا نصّ مضمَّن
    def to_image(self, resolution=300):
        raise RuntimeError("لا مُصيّر مثبَّت")       # يحاكي غياب مُصيّر


class _FakePDF:
    pages = [_FakePage()]
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_scanned_pdf_raises_clear_error_not_silent_empty(monkeypatch):
    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda *a, **k: _FakePDF())
    with pytest.raises(ImporterError) as exc:
        extract_text_from_pdf(b"%PDF-1.4 fake")
    assert "ممسوح" in str(exc.value)               # رسالة واضحة لا نصّ فارغ صامت
