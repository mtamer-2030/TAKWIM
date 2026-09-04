"""اختبار استيراد Word (تحويل يقيني بلا ذكاء اصطناعي)."""

import io

from docx import Document

from app.docx_import import parse_docx
from app.docx_template import build_template_docx


def _docx(lines: list[str]) -> bytes:
    doc = Document()
    for ln in lines:
        doc.add_paragraph(ln)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_template_round_trips_to_valid_assessment():
    r = parse_docx(build_template_docx())
    assert r.ok, r.errors
    assert len(r.normalized["questions"]) == 7
    types = [q["type"] for q in r.normalized["questions"]]
    assert types == ["mcq_single", "mcq_multi", "classify", "order",
                     "short_text", "long_text", "grid"]


def test_mcq_single_correct_and_diagnostics():
    data = _docx([
        "العنوان: ت", "النوع: تمرين", "المستوى: TC",
        "س) اختيار | الكفاية=المفهمة | النقطة=2",
        "ما الأطروحة؟",
        "- بديل خاطئ | خطأ=copy_verbatim",
        "* البديل الصحيح",
        "- بديل آخر",
    ])
    r = parse_docx(data)
    assert r.ok, r.errors
    q = r.normalized["questions"][0]
    assert q["type"] == "mcq_single" and q["payload"]["correct"] == 1
    assert q["payload"]["diagnostics"] == {"0": "copy_verbatim"}
    assert q["competency"] == "conceptualization" and q["max_score"] == 2


def test_missing_correct_star_rejected():
    data = _docx([
        "العنوان: ت", "النوع: تمرين",
        "س) اختيار | الكفاية=المفهمة | النقطة=1",
        "بلا نجمة؟", "- أ", "- ب",
    ])
    r = parse_docx(data)
    assert not r.ok
    assert any("نجمة" in e for e in r.errors)


def test_short_text_indicator_rule_parsed():
    data = _docx([
        "العنوان: ت", "النوع: تمرين",
        "س) قصير | الكفاية=الأشكلة | النقطة=2 | حد=250",
        "صغ الإشكال.",
        "مؤشر: صيغة استفهامية | 1 | قاعدة=يحتوي: ؟ ؛ هل",
        "مؤشر: يقرّره الأستاذ | 1",
    ])
    r = parse_docx(data)
    assert r.ok, r.errors
    inds = r.normalized["questions"][0]["indicators"]
    assert inds[0]["check_rule"]["type"] == "any_of"
    assert "؟" in inds[0]["check_rule"]["patterns"]
    assert inds[1].get("check_rule") is None


def test_points_sum_mismatch_reported_with_number():
    data = _docx([
        "العنوان: ت", "النوع: فرض",
        "س) قصير | الكفاية=الأشكلة | النقطة=5",
        "صغ الإشكال.",
        "مؤشر: أ | 1",
        "مؤشر: ب | 1",
    ])
    r = parse_docx(data)
    assert not r.ok
    assert any("مجموع نقاط المؤشّرات" in e and "السؤال 1" in e for e in r.errors)


def test_corrupt_file_is_reported_not_crash():
    r = parse_docx(b"this is not a docx")
    assert not r.ok
    assert any("Word" in e for e in r.errors)
