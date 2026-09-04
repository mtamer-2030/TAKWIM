"""اختبار التحقّق الصارم من JSON (CLAUDE.md §9، §19/7)."""

import copy

from app.validation import validate_assessment

VALID = {
    "title": "تمرين — استخراج الأطروحة",
    "kind": "exercise",
    "level": "1BAC",
    "unit": "الوضع البشري",
    "concept": "الوعي واللاوعي",
    "stimuli": [{"id": "s1", "text": "نصّ الانطلاق..."}],
    "questions": [
        {
            "type": "mcq_single",
            "competency": "conceptualization",
            "stimulus": "s1",
            "prompt": "ما أطروحة صاحب النص؟",
            "max_score": 2,
            "payload": {
                "options": ["أ", "ب", "ج", "د"],
                "correct": 3,
                "diagnostics": {"0": "copy_verbatim", "1": "argument_as_thesis"},
            },
        }
    ],
}


def test_valid_passes():
    r = validate_assessment(copy.deepcopy(VALID))
    assert r.ok, r.errors
    assert r.normalized["questions"][0]["auto_scored"] == 1


def test_correct_out_of_range_rejected():
    data = copy.deepcopy(VALID)
    data["questions"][0]["payload"]["correct"] = 9
    r = validate_assessment(data)
    assert not r.ok
    assert any("correct" in e for e in r.errors)
    assert any("السؤال 1" in e for e in r.errors)


def test_unknown_competency_rejected():
    data = copy.deepcopy(VALID)
    data["questions"][0]["competency"] = "creativity"
    r = validate_assessment(data)
    assert not r.ok
    assert any("competency" in e for e in r.errors)


def test_unsupported_type_rejected():
    data = copy.deepcopy(VALID)
    data["questions"][0]["type"] = "essay"
    r = validate_assessment(data)
    assert not r.ok
    assert any("type" in e.lower() for e in r.errors)


def test_diagnostic_without_code_rejected():
    data = copy.deepcopy(VALID)
    data["questions"][0]["payload"]["diagnostics"]["2"] = ""
    r = validate_assessment(data)
    assert not r.ok
    assert any("رمز خطأ" in e for e in r.errors)


def test_points_sum_must_equal_max_score():
    data = {
        "title": "فرض",
        "kind": "exam",
        "questions": [
            {
                "type": "short_text",
                "competency": "problematization",
                "prompt": "صغ الإشكال",
                "max_score": 3,
                "payload": {"max_chars": 250},
                "indicators": [
                    {"id": "i1", "text": "أ", "points": 1},
                    {"id": "i2", "text": "ب", "points": 1},
                ],  # المجموع 2 ≠ 3
            }
        ],
    }
    r = validate_assessment(data)
    assert not r.ok
    assert any("مجموع نقاط المؤشّرات" in e for e in r.errors)


def test_points_sum_ok_and_open_auto_scored_flag():
    data = {
        "title": "تمرين",
        "kind": "exercise",
        "questions": [
            {
                "type": "short_text",
                "competency": "problematization",
                "prompt": "صغ الإشكال",
                "max_score": 2,
                "payload": {"max_chars": 250},
                "indicators": [
                    {"id": "i1", "text": "صيغة استفهامية", "points": 1,
                     "check_rule": {"type": "any_of", "patterns": ["؟", "هل"]}},
                    {"id": "i2", "text": "حجم أدنى", "points": 1,
                     "check_rule": {"type": "min_chars", "value": 50}},
                ],
            }
        ],
    }
    r = validate_assessment(data)
    assert r.ok, r.errors
    # كل المؤشّرات لها check_rule ← السؤال قابل للتصحيح الآلي الكامل
    assert r.normalized["questions"][0]["auto_scored"] == 1


def test_bad_stimulus_reference_rejected():
    data = copy.deepcopy(VALID)
    data["questions"][0]["stimulus"] = "sX"
    r = validate_assessment(data)
    assert not r.ok
    assert any("stimulus" in e for e in r.errors)


def test_bad_check_rule_regex_rejected():
    data = {
        "title": "ت",
        "kind": "exercise",
        "questions": [{
            "type": "short_text", "competency": "knowledge",
            "prompt": "س", "max_score": 1,
            "indicators": [{"id": "i1", "text": "t", "points": 1,
                            "check_rule": {"type": "regex", "pattern": "("}}],
        }],
    }
    r = validate_assessment(data)
    assert not r.ok
    assert any("regex" in e for e in r.errors)
