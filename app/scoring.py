"""التصحيح اليقيني داخل الجلسة (CLAUDE.md §10، §12، §13).

المقفلة تُصحَّح تصحيحاً كاملاً يقينياً. المفتوحة تُفحص مؤشّراتها التي لها
check_rule فقط؛ الباقي ينتظر الأستاذ أو المساعدة المسائية — لا تخمين.
لا نقطة تُعرض على التلميذ إطلاقاً؛ هذه الدوالّ تحسب للأستاذ لا له.
"""

from __future__ import annotations

import re

from .normalize import normalize, visible_length

# ————————————————————— قواعد الفحص —————————————————————


def evaluate_check_rule(rule: dict | None, raw_text: str) -> bool | None:
    """يقيّم قاعدة فحص واحدة. يعيد True/False، أو None إن لا قاعدة (ينتظر الأستاذ).

    التطبيع إلزامي قبل مطابقة الأنماط النصّية (§12).
    """
    if not rule:
        return None
    rtype = rule.get("type")
    norm = normalize(raw_text)

    if rtype == "any_of":
        pats = [normalize(p) for p in rule.get("patterns", [])]
        return any(p and p in norm for p in pats)
    if rtype == "all_of":
        pats = [normalize(p) for p in rule.get("patterns", [])]
        return all(p in norm for p in pats) if pats else False
    if rtype == "none_of":
        pats = [normalize(p) for p in rule.get("patterns", [])]
        return not any(p and p in norm for p in pats)
    if rtype == "min_chars":
        return visible_length(raw_text) >= int(rule.get("value", 0))
    if rtype == "max_chars":
        return visible_length(raw_text) <= int(rule.get("value", 0))
    if rtype == "regex":
        try:
            return re.search(rule.get("pattern", ""), norm) is not None
        except re.error:
            return False
    return None


# ————————————————————— استخراج نصّ الجواب —————————————————————


def answer_text(qtype: str, raw: dict | None) -> str:
    """النصّ الذي تُطبّق عليه قواعد الفحص، حسب نوع السؤال."""
    if not raw:
        return ""
    if qtype in ("short_text", "long_text"):
        return str(raw.get("text") or "")
    if qtype == "grid":
        cells = raw.get("cells") or []
        parts: list[str] = []
        for row in cells:
            if isinstance(row, list):
                parts.extend(str(c) for c in row if c)
        return "\n".join(parts)
    return ""


# ————————————————————— المقفلة —————————————————————


def score_closed(qtype: str, payload: dict, max_score: float, raw: dict | None) -> dict:
    """تصحيح يقيني كامل لنوع مقفل. يعيد auto_score وverdict وrule_verdicts وerror_tags."""
    raw = raw or {}
    diagnostics = payload.get("diagnostics", {}) if isinstance(payload, dict) else {}
    error_tags: list[str] = []

    if qtype == "mcq_single":
        options = payload.get("options", [])
        correct = payload.get("correct")
        choice = raw.get("choice")
        is_correct = (choice == correct)
        score = max_score if is_correct else 0.0
        if not is_correct and choice is not None:
            code = diagnostics.get(str(choice))
            if code:
                error_tags.append(code)
        return {
            "auto_score": float(score),
            "verdict": "correct" if is_correct else "incorrect",
            "rule_verdicts": {"choice": choice, "is_correct": is_correct,
                              "n_options": len(options)},
            "error_tags": error_tags,
        }

    if qtype == "mcq_multi":
        correct = set(payload.get("correct", []))
        chosen = set(c for c in raw.get("choices", []) if isinstance(c, int))
        total = len(correct) or 1
        hit = len(chosen & correct)
        wrong = len(chosen - correct)
        fraction = max(0.0, (hit - wrong) / total)
        score = round(max_score * fraction, 4)
        for c in chosen - correct:
            code = diagnostics.get(str(c))
            if code:
                error_tags.append(code)
        verdict = "correct" if chosen == correct else ("partial" if fraction > 0 else "incorrect")
        return {
            "auto_score": float(score),
            "verdict": verdict,
            "rule_verdicts": {"selected": sorted(chosen), "correct_count": hit,
                              "wrong_count": wrong},
            "error_tags": error_tags,
        }

    if qtype == "classify":
        items = payload.get("items", [])
        assignments = raw.get("assignments", [])
        n = len(items) or 1
        per_item = []
        correct_count = 0
        for k, it in enumerate(items):
            chosen = assignments[k] if k < len(assignments) else None
            ok = (chosen == it.get("category"))
            per_item.append({"chosen": chosen, "is_correct": ok})
            if ok:
                correct_count += 1
        fraction = correct_count / n
        score = round(max_score * fraction, 4)
        verdict = "correct" if correct_count == len(items) else (
            "partial" if correct_count > 0 else "incorrect")
        return {
            "auto_score": float(score),
            "verdict": verdict,
            "rule_verdicts": {"items": per_item, "correct_count": correct_count},
            "error_tags": error_tags,
        }

    if qtype == "order":
        correct_order = payload.get("correct_order", [])
        partial = bool(payload.get("partial_credit", False))
        student = raw.get("order", [])
        n = len(correct_order) or 1
        matches = sum(
            1 for k in range(min(len(student), len(correct_order)))
            if student[k] == correct_order[k]
        )
        if partial:
            fraction = matches / n
        else:
            fraction = 1.0 if (student == correct_order) else 0.0
        score = round(max_score * fraction, 4)
        verdict = "correct" if student == correct_order else (
            "partial" if (partial and matches > 0) else "incorrect")
        return {
            "auto_score": float(score),
            "verdict": verdict,
            "rule_verdicts": {"matches": matches, "n": n},
            "error_tags": error_tags,
        }

    return {"auto_score": None, "verdict": None, "rule_verdicts": {}, "error_tags": []}


# ————————————————————— المفتوحة —————————————————————


def score_open(qtype: str, indicators: list[dict], penalties: list[dict],
               raw: dict | None) -> dict:
    """يفحص فقط المؤشّرات/الخصوم التي لها check_rule. الباقي ينتظر الأستاذ.

    auto_score هنا جزئي (المحسوم يقيناً فقط) ولا يدخل تقريراً قبل تصديق الأستاذ.
    """
    text = answer_text(qtype, raw)
    verdicts: dict[str, dict] = {}
    pending: list[str] = []
    auto_portion = 0.0

    for ind in indicators or []:
        iid = ind.get("id")
        rule = ind.get("check_rule")
        result = evaluate_check_rule(rule, text)
        if result is None:
            pending.append(iid)
            verdicts[iid] = {"met": None, "auto": False, "points": ind.get("points", 0)}
        else:
            verdicts[iid] = {"met": bool(result), "auto": True,
                             "points": ind.get("points", 0)}
            if result:
                auto_portion += float(ind.get("points", 0) or 0)

    for k, pen in enumerate(penalties or []):
        pid = f"penalty{k + 1}"
        rule = pen.get("check_rule")
        result = evaluate_check_rule(rule, text)
        if result is None:
            verdicts[pid] = {"met": None, "auto": False, "points": pen.get("points", 0)}
        else:
            verdicts[pid] = {"met": bool(result), "auto": True,
                             "points": pen.get("points", 0)}
            if result:
                auto_portion += float(pen.get("points", 0) or 0)

    return {
        "auto_score": round(auto_portion, 4) if verdicts else None,
        "verdict": "open",
        "rule_verdicts": verdicts,
        "pending": pending,
        "error_tags": [],
    }


def score_answer(qtype: str, payload: dict, indicators: list[dict],
                 penalties: list[dict], max_score: float, raw: dict | None) -> dict:
    """موزّع: يصحّح أي جواب حسب نوعه، تصحيحاً يقينياً بحتاً."""
    from .constants import QUESTION_TYPES_CLOSED
    if qtype in QUESTION_TYPES_CLOSED:
        return score_closed(qtype, payload, max_score, raw)
    return score_open(qtype, indicators, penalties, raw)
