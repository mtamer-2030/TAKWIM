"""اختبار التصحيح اليقيني (CLAUDE.md §10، §12، §13)."""

from app.scoring import evaluate_check_rule, score_answer, score_closed, score_open


# ————— المقفلة —————

def test_mcq_single_correct_and_error_tag():
    payload = {"options": ["أ", "ب", "ج", "د"], "correct": 3,
               "diagnostics": {"0": "copy_verbatim", "1": "argument_as_thesis"}}
    right = score_closed("mcq_single", payload, 2, {"choice": 3})
    assert right["auto_score"] == 2 and right["verdict"] == "correct"
    assert right["error_tags"] == []

    wrong = score_closed("mcq_single", payload, 2, {"choice": 1})
    assert wrong["auto_score"] == 0 and wrong["verdict"] == "incorrect"
    assert wrong["error_tags"] == ["argument_as_thesis"]


def test_mcq_multi_partial_credit():
    payload = {"options": ["أ", "ب", "ج", "د"], "correct": [0, 3]}
    # اختيار صحيح واحد فقط ← نصف
    res = score_closed("mcq_multi", payload, 4, {"choices": [0]})
    assert res["auto_score"] == 2 and res["verdict"] == "partial"
    # اختيار صحيحين وخاطئ ← (2-1)/2 = نصف
    res2 = score_closed("mcq_multi", payload, 4, {"choices": [0, 3, 1]})
    assert res2["auto_score"] == 2
    # كامل
    res3 = score_closed("mcq_multi", payload, 4, {"choices": [0, 3]})
    assert res3["auto_score"] == 4 and res3["verdict"] == "correct"
    # سالب يُقصّ عند الصفر
    res4 = score_closed("mcq_multi", payload, 4, {"choices": [1, 2]})
    assert res4["auto_score"] == 0 and res4["verdict"] == "incorrect"


def test_classify_fraction():
    payload = {"categories": ["إشكال", "سؤال معرفي", "تكرار"],
               "items": [{"text": "a", "category": 0}, {"text": "b", "category": 2},
                         {"text": "c", "category": 1}]}
    res = score_closed("classify", payload, 3, {"assignments": [0, 2, 0]})
    assert res["auto_score"] == 2  # اثنان من ثلاثة
    assert res["verdict"] == "partial"


def test_order_partial_and_exact():
    payload = {"items": ["a", "b", "c"], "correct_order": [2, 0, 1],
               "partial_credit": True}
    exact = score_closed("order", payload, 3, {"order": [2, 0, 1]})
    assert exact["auto_score"] == 3 and exact["verdict"] == "correct"
    partial = score_closed("order", payload, 3, {"order": [2, 1, 0]})
    assert 0 < partial["auto_score"] < 3  # موضع واحد صحيح

    payload_no = dict(payload, partial_credit=False)
    none = score_closed("order", payload_no, 3, {"order": [0, 1, 2]})
    assert none["auto_score"] == 0 and none["verdict"] == "incorrect"


# ————— قواعد الفحص —————

def test_check_rule_any_of_with_normalization():
    rule = {"type": "any_of", "patterns": ["وعي"]}
    assert evaluate_check_rule(rule, "تحدّث عن الوعى") is True
    assert evaluate_check_rule(rule, "وعيه الباطن") is True
    assert evaluate_check_rule(rule, "لا شيء هنا") is False


def test_check_rule_min_max_chars():
    assert evaluate_check_rule({"type": "min_chars", "value": 5}, "abcdef") is True
    assert evaluate_check_rule({"type": "min_chars", "value": 5}, "abc") is False
    assert evaluate_check_rule({"type": "max_chars", "value": 3}, "abc") is True


def test_check_rule_none_of_and_all_of():
    assert evaluate_check_rule({"type": "none_of", "patterns": ["نسخ"]}, "تحليل حرّ") is True
    assert evaluate_check_rule({"type": "all_of", "patterns": ["ديكارت", "فرويد"]},
                               "قارن ديكارت مع فرويد") is True
    assert evaluate_check_rule({"type": "all_of", "patterns": ["ديكارت", "فرويد"]},
                               "ديكارت وحده") is False


def test_no_rule_returns_none():
    assert evaluate_check_rule(None, "أيّ نصّ") is None


# ————— المفتوحة —————

def test_score_open_only_ruled_indicators():
    indicators = [
        {"id": "i1", "text": "استفهام", "points": 1,
         "check_rule": {"type": "any_of", "patterns": ["؟"]}},
        {"id": "i2", "text": "حجم", "points": 1,
         "check_rule": {"type": "min_chars", "value": 10}},
        {"id": "i4", "text": "توتّر بين موقفين", "points": 2},  # بلا قاعدة ← ينتظر الأستاذ
    ]
    res = score_open("short_text", indicators, [], {"text": "هل الوعي كافٍ لمعرفة النفس؟"})
    assert res["rule_verdicts"]["i1"]["met"] is True
    assert res["rule_verdicts"]["i2"]["met"] is True
    assert res["rule_verdicts"]["i4"]["met"] is None  # لا تخمين
    assert "i4" in res["pending"]
    assert res["auto_score"] == 2  # i1 + i2 فقط


def test_score_answer_dispatch():
    payload = {"options": ["أ", "ب"], "correct": 0}
    res = score_answer("mcq_single", payload, [], [], 1, {"choice": 0})
    assert res["auto_score"] == 1
