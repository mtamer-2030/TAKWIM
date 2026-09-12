"""اختبار نظام التقاويم بالأسئلة v2: الاستيراد (تحقّق) والتصحيح اليقيني.

يُعاد استعمال محرّكات v1 (validation/scoring) داخل بنية v2، فنتأكّد أنّ
JSON صحيح يُبنى إلى أسئلة، وأنّ التصحيح المغلق يقينيّ مع تغذية راجعة،
وأنّ المفتوح ينتظر الأستاذ (لا نقطة آلية إلا من check_rule).
"""

import json

import pytest

from app.models import Quiz, QuizQuestion
from app.services.quizzes import (
    QuizImportError,
    build_quiz,
    grade_answer,
    normalize_quiz_json,
)

SAMPLE = {
    "title": "تقويم تجريبي", "kind": "exercise", "level": "1BAC",
    "stimuli": [{"id": "s1", "text": "نصّ الانطلاق."}],
    "questions": [
        {"type": "mcq_single", "competency": "conceptualization", "stimulus": "s1",
         "prompt": "اختر", "max_score": 2,
         "payload": {"options": ["أ", "ب", "ج"], "correct": 2,
                     "diagnostics": {"0": "خلط", "1": "تسرّع"}}},
        {"type": "mcq_multi", "competency": "argumentation", "prompt": "اختر ما يصحّ",
         "max_score": 2, "payload": {"options": ["أ", "ب", "ج", "د"], "correct": [0, 2]}},
        {"type": "short_text", "competency": "problematization", "prompt": "أجب",
         "max_score": 3, "indicators": [
             {"id": "i1", "text": "ذكر الإشكال", "points": 3,
              "check_rule": {"type": "any_of", "patterns": ["إشكال"]}}]},
    ],
}


def test_normalize_and_build_quiz():
    norm = normalize_quiz_json(SAMPLE)
    quiz = build_quiz(norm, level_id=None, group_name="1BAC2")
    assert isinstance(quiz, Quiz)
    assert quiz.title == "تقويم تجريبي" and quiz.group_name == "1BAC2"
    assert len(quiz.questions) == 3
    q0 = quiz.questions[0]
    assert q0.qtype == "mcq_single" and q0.stimulus == "نصّ الانطلاق."  # حُلّ النصّ
    assert q0.auto_scored is True


def test_build_quiz_resolves_skill_id_from_map():
    # خريطة (اسم المهارة → id) كما تأتي من جدول skills المبذور.
    skill_ids = {"صياغة الإشكال": 1, "البنية المفاهيمية": 2, "البنية الحجاجية": 4}
    quiz = build_quiz(normalize_quiz_json(SAMPLE), skill_ids=skill_ids)
    # conceptualization → البنية المفاهيمية (2)، argumentation → البنية الحجاجية (4)،
    # problematization → صياغة الإشكال (1).
    assert quiz.questions[0].skill_id == 2
    assert quiz.questions[1].skill_id == 4
    assert quiz.questions[2].skill_id == 1


def test_build_quiz_without_map_leaves_skill_none():
    quiz = build_quiz(normalize_quiz_json(SAMPLE))
    assert all(q.skill_id is None for q in quiz.questions)


def test_word_template_builds_valid_docx():
    """٥-د: قالب Word لتأليف تقويم يُبنى كملفّ docx صالح (بادئة ZIP، غير فارغ)."""
    from app.docx_template import build_template_docx
    data = build_template_docx()
    assert data[:2] == b"PK" and len(data) > 1000


def test_invalid_json_raises_with_errors():
    bad = {"title": "x", "kind": "لا_يوجد", "questions": []}
    with pytest.raises(QuizImportError) as exc:
        normalize_quiz_json(bad)
    assert exc.value.errors  # قائمة أسباب غير فارغة


def test_grade_mcq_single_correct_and_wrong():
    quiz = build_quiz(normalize_quiz_json(SAMPLE))
    q = quiz.questions[0]
    ok = grade_answer(q, {"choice": 2})
    assert ok["score"] == 2 and ok["auto"] and "صحيحة" in ok["feedback"]
    bad = grade_answer(q, {"choice": 0})
    assert bad["score"] == 0 and "خلط" in bad["feedback"]     # تشخيص البديل المختار


def test_grade_mcq_multi_partial():
    quiz = build_quiz(normalize_quiz_json(SAMPLE))
    q = quiz.questions[1]                                    # correct = {0,2}
    full = grade_answer(q, {"choices": [0, 2]})
    assert full["score"] == 2
    partial = grade_answer(q, {"choices": [0]})             # نصف
    assert 0 < partial["score"] < 2


def test_open_waits_for_teacher_but_check_rule_awards():
    quiz = build_quiz(normalize_quiz_json(SAMPLE))
    q = quiz.questions[2]                                    # short_text مع check_rule
    hit = grade_answer(q, {"text": "هذا إشكال فلسفي واضح"})
    assert hit["score"] == 3 and hit["auto"] is False        # مفتوح: يُحتسب المحسوم يقيناً فقط
    miss = grade_answer(q, {"text": "لا شيء"})
    assert miss["score"] == 0
    # التغذية الراجعة للمفتوح لا تُعرَض للتلميذ (يراجعها الأستاذ)
    assert hit["feedback"] is None
