"""قاعدة أنماط الفروض: يُشغَّل المحلّل الحتميّ على ١٠ فروض حقيقيّة متنوّعة، ويثبت
أنّها تُستورَد ببنية معقولة **دون سحابة** — حارسٌ ضدّ الانحدار عند أيّ تعديل لاحق.

المصدر: tests/fixtures/exams/exam_01..10.txt (نصوص مستخرَجة من فروض الأستاذ).
"""

from collections import Counter
from pathlib import Path

import pytest

from app.services.quizzes import heuristic_quiz_from_text

FIXTURES = Path(__file__).parent / "fixtures" / "exams"


def _parse(n: int):
    txt = (FIXTURES / f"exam_{n:02d}.txt").read_text(encoding="utf-8")
    return heuristic_quiz_from_text(txt, f"exam{n}")


def test_all_ten_exams_present():
    assert sorted(p.name for p in FIXTURES.glob("exam_*.txt")) == \
        [f"exam_{i:02d}.txt" for i in range(1, 11)]


@pytest.mark.parametrize("n", range(1, 11))
def test_exam_parses_without_explosion(n):
    """كلّ فرضٍ يُنتج بنيةً معقولة: عناصر محدودة، لا انفجار سطرٍ-سؤال."""
    qs = _parse(n)["questions"]
    assert qs, f"exam_{n:02d}: لا عناصر"
    # لا انفجار: العدد الإجماليّ محدود (كان exam_01 يعطي ٨٨ قبل قاعدة الأنماط).
    assert len(qs) <= 45, f"exam_{n:02d}: انفجار محتمَل ({len(qs)})"
    # لا سطر يحمل «(صحيح)» بقي سؤالاً مفتوحاً (لم يُهضَم في اختيار).
    leaked = [q for q in qs if q["type"] in ("long_text", "short_text")
              and "(صحيح)" in q["prompt"]]
    assert not leaked, f"exam_{n:02d}: خيارات لم تُجمَع: {[q['prompt'][:30] for q in leaked]}"


def test_exam_01_diagnostic_mcq_corpus():
    """exam_01 (تشخيصيّ الوزارة): خيارات بلا خانات، الصواب «(صحيح)» → اختيارٌ مصحَّح.
    كان ينفجر إلى ٧٨ سؤالاً مفتوحاً؛ الآن ≥٢٠ اختياراً بأجوبةٍ صحيحة + نصّ + مؤشّرات."""
    qs = _parse(1)["questions"]
    c = Counter(q["type"] for q in qs)
    assert c["mcq_single"] + c["mcq_multi"] >= 20        # استُخرجت أغلب الاختيارات
    assert c["mcq_multi"] >= 2                            # «اختر كل ما يصح» → متعدّد
    assert c["passage"] >= 1                              # نصّ القراءة محفوظ
    # كلّ اختيار له جواب صحيح واحد على الأقلّ (من «(صحيح)»).
    for q in qs:
        if q["type"].startswith("mcq"):
            assert q["correct"], f"اختيار بلا صواب: {q['prompt'][:30]}"
    # عناصر إجابة السؤال المفتوح أُسنِدت مؤشّرات.
    assert any(q.get("elements") for q in qs), "عناصر الإجابة لم تُسنَد"


def test_exam_07_kashida_numbering_recovers_questions():
    """exam_07 (أسئلة مرقّمة بالكشيدة «1ـ 2ـ»): كانت الأسئلة تُبتلَع في نصّ؛ الآن تظهر."""
    qs = _parse(7)["questions"]
    opens = [q for q in qs if q["type"] == "long_text"]
    assert len(opens) >= 5                               # الأسئلة الخمسة استُرجِعت
    assert any(q["type"] == "passage" for q in qs)       # نصّ فروم محفوظ


def test_exam_08_essay_models_corpus():
    """exam_08 (قولة/مطلب + نموذج إجابة محلول): كان ٣٦ عنصراً فوضويّاً؛ الآن ٣ أسئلة
    إنشائيّة — القولة سندٌ معروض، المطلب سؤالٌ، ومؤشّرات شبكة التصحيح عناصرَ إجابة."""
    qs = _parse(8)["questions"]
    essays = [q for q in qs if q["type"] == "long_text"]
    assert len(essays) == 3                              # النماذج الثلاثة
    for q in essays:
        assert q.get("stimulus") and len(q["stimulus"]) > 20   # القولة محفوظة سنداً
        assert q.get("elements") and len(q["elements"]) >= 4   # مؤشّرات التصحيح
        assert q["max_score"] == 20                            # مقال على ٢٠


def test_essay_detector_does_not_trigger_without_markers():
    """المحلّل المتخصّص لا يُفعَّل إلّا مع «القولة» و«المطلب» معاً (لا يمسّ بقيّة الفروض)."""
    from app.services.quizzes import essay_models_from_text
    assert essay_models_from_text("نصّ عاديّ بلا علامات") is None
    assert essay_models_from_text("المطلب فقط بلا قولة") is None
