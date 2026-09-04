"""تحقّق صارم من JSON التقاويم قبل الحفظ (CLAUDE.md §9).

رسائل خطأ تحدّد رقم السؤال والحقل. أخطاء تُرفض: correct خارج المدى،
مجموع points لا يساوي max_score، competency غير معروفة، type غير مدعوم،
بديل مشخِّص بلا رمز خطأ. التحقّق يقيني ولا يستدعي أي نموذج.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .constants import (
    CHECK_RULE_TYPES,
    COMPETENCIES,
    KINDS,
    QUESTION_TYPES,
    QUESTION_TYPES_CLOSED,
    QUESTION_TYPES_OPEN,
)

_POINT_TOL = 1e-6


@dataclass
class ValidationResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    normalized: dict | None = None

    def fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)


def _is_str(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_assessment(data) -> ValidationResult:
    r = ValidationResult()
    if not isinstance(data, dict):
        r.fail("الجذر يجب أن يكون كائن JSON واحداً.")
        return r

    if not _is_str(data.get("title")):
        r.fail("العنوان (title) مطلوب.")
    kind = data.get("kind")
    if kind not in KINDS:
        r.fail(f"النوع (kind) غير مدعوم: {kind!r} — المتوقّع أحد {KINDS}.")

    # نصوص الانطلاق المشتركة
    stimuli = data.get("stimuli", [])
    stim_ids: set[str] = set()
    norm_stimuli: list[dict] = []
    if stimuli:
        if not isinstance(stimuli, list):
            r.fail("stimuli يجب أن يكون قائمة.")
        else:
            for j, st in enumerate(stimuli, start=1):
                if not isinstance(st, dict) or not _is_str(st.get("id")) \
                        or not _is_str(st.get("text")):
                    r.fail(f"نصّ الانطلاق {j}: يلزم id و text نصّيّان.")
                    continue
                sid = st["id"].strip()
                if sid in stim_ids:
                    r.fail(f"نصّ الانطلاق {j}: المعرّف «{sid}» مكرّر.")
                    continue
                stim_ids.add(sid)
                norm_stimuli.append({"id": sid, "text": st["text"]})

    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        r.fail("questions يجب أن تكون قائمة غير فارغة.")
        return r

    norm_questions: list[dict] = []
    for i, q in enumerate(questions, start=1):
        nq = _validate_question(i, q, stim_ids, r)
        if nq is not None:
            nq["position"] = i
            norm_questions.append(nq)

    if r.ok:
        r.normalized = {
            "title": data["title"].strip(),
            "kind": kind,
            "level": (data.get("level") or "").strip() or None,
            "unit": (data.get("unit") or "").strip() or None,
            "concept": (data.get("concept") or "").strip() or None,
            "stimuli": norm_stimuli,
            "questions": norm_questions,
        }
    return r


def _validate_question(i: int, q, stim_ids: set[str], r: ValidationResult) -> dict | None:
    where = f"السؤال {i}"
    if not isinstance(q, dict):
        r.fail(f"{where}: يجب أن يكون كائناً.")
        return None

    qtype = q.get("type")
    if qtype not in QUESTION_TYPES:
        r.fail(f"{where}: النوع (type) غير مدعوم: {qtype!r}.")
        return None

    competency = q.get("competency")
    if competency not in COMPETENCIES:
        r.fail(f"{where}: الكفاية (competency) غير معروفة: {competency!r}.")

    if not _is_str(q.get("prompt")):
        r.fail(f"{where}: نصّ السؤال (prompt) مطلوب.")

    max_score = q.get("max_score")
    if not _is_num(max_score) or max_score < 0:
        r.fail(f"{where}: max_score يجب أن يكون عدداً غير سالب.")
        max_score = 0

    stimulus = q.get("stimulus")
    if stimulus is not None:
        if not isinstance(stimulus, str) or stimulus.strip() not in stim_ids:
            r.fail(f"{where}: stimulus «{stimulus}» لا يشير إلى نصّ انطلاق معرّف.")

    payload = q.get("payload", {})
    if not isinstance(payload, dict):
        r.fail(f"{where}: payload يجب أن يكون كائناً.")
        payload = {}

    if qtype in QUESTION_TYPES_CLOSED:
        _validate_closed_payload(i, qtype, payload, r)
        indicators, penalties = [], []
    else:
        _validate_open_payload(i, qtype, payload, r)
        indicators = _validate_indicators(i, q.get("indicators", []), r)
        penalties = _validate_penalties(i, q.get("penalties", []), r)
        _check_points_sum(i, indicators, max_score, r)

    auto_scored = _compute_auto_scored(qtype, indicators, penalties)

    return {
        "type": qtype,
        "competency": competency,
        "prompt": (q.get("prompt") or "").strip(),
        "stimulus": stimulus.strip() if isinstance(stimulus, str) and stimulus.strip() else None,
        "max_score": float(max_score),
        "payload": payload,
        "indicators": indicators,
        "penalties": penalties,
        "auto_scored": auto_scored,
    }


def _validate_closed_payload(i, qtype, payload, r: ValidationResult) -> None:
    where = f"السؤال {i}"
    if qtype in ("mcq_single", "mcq_multi"):
        options = payload.get("options")
        if not isinstance(options, list) or len(options) < 2 \
                or not all(_is_str(o) for o in options):
            r.fail(f"{where}: options يجب أن تكون قائمة نصوص طولها ≥ ٢.")
            return
        n = len(options)
        if qtype == "mcq_single":
            correct = payload.get("correct")
            if not _is_int(correct) or not (0 <= correct < n):
                r.fail(f"{where}: correct خارج مدى الخيارات (٠..{n - 1}).")
            diagnostics = payload.get("diagnostics", {})
            _validate_diagnostics(i, diagnostics, n, {correct} if _is_int(correct) else set(), r)
        else:  # mcq_multi
            correct = payload.get("correct")
            if not isinstance(correct, list) or not correct \
                    or not all(_is_int(c) and 0 <= c < n for c in correct):
                r.fail(f"{where}: correct يجب أن تكون قائمة مؤشّرات صحيحة داخل المدى.")
            elif len(set(correct)) != len(correct):
                r.fail(f"{where}: correct فيها تكرار.")
    elif qtype == "classify":
        categories = payload.get("categories")
        items = payload.get("items")
        if not isinstance(categories, list) or len(categories) < 2 \
                or not all(_is_str(c) for c in categories):
            r.fail(f"{where}: categories يجب أن تكون قائمة نصوص طولها ≥ ٢.")
            return
        if not isinstance(items, list) or not items:
            r.fail(f"{where}: items يجب أن تكون قائمة غير فارغة.")
            return
        for k, it in enumerate(items, start=1):
            if not isinstance(it, dict) or not _is_str(it.get("text")) \
                    or not _is_int(it.get("category")) \
                    or not (0 <= it["category"] < len(categories)):
                r.fail(f"{where}: العنصر {k} في classify: نصّ أو تصنيف غير صالح.")
    elif qtype == "order":
        items = payload.get("items")
        if not isinstance(items, list) or len(items) < 2 \
                or not all(_is_str(x) for x in items):
            r.fail(f"{where}: items يجب أن تكون قائمة نصوص طولها ≥ ٢.")
            return
        order = payload.get("correct_order")
        n = len(items)
        if not isinstance(order, list) or sorted(order) != list(range(n)):
            r.fail(f"{where}: correct_order يجب أن يكون ترتيباً كاملاً لمؤشّرات العناصر (٠..{n - 1}).")


def _validate_diagnostics(i, diagnostics, n, correct_set, r: ValidationResult) -> None:
    where = f"السؤال {i}"
    if not diagnostics:
        return
    if not isinstance(diagnostics, dict):
        r.fail(f"{where}: diagnostics يجب أن يكون كائناً {{مؤشّر: رمز خطأ}}.")
        return
    for key, code in diagnostics.items():
        try:
            idx = int(key)
        except (ValueError, TypeError):
            r.fail(f"{where}: مفتاح diagnostics «{key}» ليس مؤشّراً صحيحاً.")
            continue
        if not (0 <= idx < n):
            r.fail(f"{where}: diagnostics يشير إلى خيار خارج المدى ({idx}).")
        if idx in correct_set:
            r.fail(f"{where}: البديل الصحيح ({idx}) لا يحمل رمز خطأ مشخِّصاً.")
        if not _is_str(code):
            # بديل مشخِّص بلا رمز خطأ (§9)
            r.fail(f"{where}: البديل {idx} مشخِّص بلا رمز خطأ.")


def _validate_open_payload(i, qtype, payload, r: ValidationResult) -> None:
    where = f"السؤال {i}"
    if qtype in ("short_text", "long_text"):
        mc = payload.get("max_chars")
        if mc is not None and (not _is_int(mc) or mc <= 0):
            r.fail(f"{where}: max_chars يجب أن يكون عدداً صحيحاً موجباً.")
        if qtype == "long_text":
            scaffold = payload.get("scaffold")
            if scaffold is not None and (
                not isinstance(scaffold, list) or not all(_is_str(s) for s in scaffold)
            ):
                r.fail(f"{where}: scaffold يجب أن يكون قائمة نصوص.")
    elif qtype == "grid":
        cols = payload.get("columns")
        rows = payload.get("rows")
        if not isinstance(cols, list) or not cols or not all(_is_str(c) for c in cols):
            r.fail(f"{where}: columns يجب أن تكون قائمة نصوص غير فارغة.")
        if not _is_int(rows) or rows <= 0:
            r.fail(f"{where}: rows يجب أن يكون عدداً صحيحاً موجباً.")
        mcc = payload.get("max_chars_per_cell")
        if mcc is not None and (not _is_int(mcc) or mcc <= 0):
            r.fail(f"{where}: max_chars_per_cell يجب أن يكون عدداً صحيحاً موجباً.")


def _validate_indicators(i, indicators, r: ValidationResult) -> list[dict]:
    where = f"السؤال {i}"
    out: list[dict] = []
    if not indicators:
        return out
    if not isinstance(indicators, list):
        r.fail(f"{where}: indicators يجب أن تكون قائمة.")
        return out
    seen: set[str] = set()
    for k, ind in enumerate(indicators, start=1):
        if not isinstance(ind, dict):
            r.fail(f"{where}: المؤشّر {k} يجب أن يكون كائناً.")
            continue
        iid = ind.get("id")
        if not _is_str(iid):
            r.fail(f"{where}: المؤشّر {k} بلا معرّف id.")
            continue
        iid = iid.strip()
        if iid in seen:
            r.fail(f"{where}: معرّف المؤشّر «{iid}» مكرّر.")
            continue
        seen.add(iid)
        if not _is_str(ind.get("text")):
            r.fail(f"{where}: المؤشّر «{iid}» بلا نصّ.")
        pts = ind.get("points")
        if not _is_num(pts) or pts < 0:
            r.fail(f"{where}: نقاط المؤشّر «{iid}» يجب أن تكون عدداً غير سالب.")
            pts = 0
        rule = ind.get("check_rule")
        if rule is not None:
            _validate_check_rule(i, iid, rule, r)
        out.append({
            "id": iid,
            "text": (ind.get("text") or "").strip(),
            "points": float(pts),
            "check_rule": rule,
        })
    return out


def _validate_penalties(i, penalties, r: ValidationResult) -> list[dict]:
    where = f"السؤال {i}"
    out: list[dict] = []
    if not penalties:
        return out
    if not isinstance(penalties, list):
        r.fail(f"{where}: penalties يجب أن تكون قائمة.")
        return out
    for k, pen in enumerate(penalties, start=1):
        if not isinstance(pen, dict) or not _is_str(pen.get("text")):
            r.fail(f"{where}: الخصم {k} يجب أن يكون كائناً بنصّ.")
            continue
        pts = pen.get("points")
        if not _is_num(pts) or pts > 0:
            r.fail(f"{where}: نقاط الخصم {k} يجب أن تكون عدداً ≤ ٠.")
            pts = 0
        rule = pen.get("check_rule")
        if rule is not None:
            _validate_check_rule(i, f"penalty{k}", rule, r)
        out.append({
            "text": pen["text"].strip(),
            "points": float(pts),
            "check_rule": rule,
        })
    return out


def _validate_check_rule(i, owner, rule, r: ValidationResult) -> None:
    where = f"السؤال {i}/{owner}"
    if not isinstance(rule, dict):
        r.fail(f"{where}: check_rule يجب أن يكون كائناً.")
        return
    rtype = rule.get("type")
    if rtype not in CHECK_RULE_TYPES:
        r.fail(f"{where}: نوع check_rule غير مدعوم: {rtype!r}.")
        return
    if rtype in ("any_of", "all_of", "none_of"):
        patterns = rule.get("patterns")
        if not isinstance(patterns, list) or not patterns \
                or not all(_is_str(p) for p in patterns):
            r.fail(f"{where}: patterns يجب أن تكون قائمة نصوص غير فارغة.")
    elif rtype in ("min_chars", "max_chars"):
        val = rule.get("value")
        if not _is_int(val) or val < 0:
            r.fail(f"{where}: value يجب أن يكون عدداً صحيحاً غير سالب.")
    elif rtype == "regex":
        pat = rule.get("pattern")
        if not _is_str(pat):
            r.fail(f"{where}: pattern مطلوب.")
        else:
            try:
                re.compile(pat)
            except re.error as exc:
                r.fail(f"{where}: regex غير صالح ({exc}).")


def _check_points_sum(i, indicators, max_score, r: ValidationResult) -> None:
    if not indicators:
        return
    total = sum(ind["points"] for ind in indicators)
    if abs(total - float(max_score)) > _POINT_TOL:
        r.fail(
            f"السؤال {i}: مجموع نقاط المؤشّرات ({total:g}) "
            f"لا يساوي max_score ({float(max_score):g})."
        )


def _compute_auto_scored(qtype, indicators, penalties) -> int:
    if qtype in QUESTION_TYPES_CLOSED:
        return 1
    if qtype in QUESTION_TYPES_OPEN:
        if indicators and all(ind.get("check_rule") for ind in indicators) \
                and all(pen.get("check_rule") for pen in penalties):
            return 1
        return 0
    return 0
