"""خدمة التقاويم v2 — إنشاء التقاويم من JSON وتصحيحها يقينياً.

تُعاد محرّكات v1 النقيّة دون تغيير:
- ``app.validation.validate_assessment``: تحقّق صارم من صيغة JSON.
- ``app.scoring.score_answer`` و``answer_text``: تصحيح يقيني للمغلقة وجزئي للمفتوحة.

بذلك نضمن نفس السلوك المجرَّب في v1 داخل بنية v2 (SQLAlchemy async).
"""

from __future__ import annotations

from ..constants import QUESTION_TYPES_CLOSED
from ..models import Quiz, QuizQuestion
from ..scoring import answer_text, score_answer
from ..validation import validate_assessment


class QuizImportError(ValueError):
    """صيغة التقويم غير صحيحة — تُعرَض أخطاؤها للأستاذ."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(" | ".join(errors))


def normalize_quiz_json(data) -> dict:
    """يتحقّق من JSON التقويم ويعيد الصيغة المطبّعة، أو يرفع QuizImportError."""
    result = validate_assessment(data)
    if not result.ok:
        raise QuizImportError(result.errors)
    return result.normalized


def build_quiz(normalized: dict, *, level_id: int | None = None,
               group_name: str | None = None) -> Quiz:
    """يبني كائن Quiz (غير محفوظ) من الصيغة المطبّعة، مع حلّ نصوص الانطلاق."""
    stim_by_id = {s["id"]: s["text"] for s in normalized.get("stimuli", [])}
    quiz = Quiz(
        title=normalized["title"],
        kind=normalized.get("kind") or "exercise",
        level_id=level_id,
        group_name=group_name or None,
        unit=normalized.get("unit"),
        concept=normalized.get("concept"),
    )
    for q in normalized["questions"]:
        quiz.questions.append(QuizQuestion(
            position=q.get("position", 0),
            qtype=q["type"],
            competency=q.get("competency"),
            prompt=q["prompt"],
            stimulus=stim_by_id.get(q.get("stimulus")) if q.get("stimulus") else None,
            payload=q.get("payload") or {},
            indicators=q.get("indicators") or None,
            penalties=q.get("penalties") or None,
            max_score=float(q.get("max_score") or 0),
            auto_scored=bool(q.get("auto_scored")),
        ))
    return quiz


def grade_answer(question: QuizQuestion, raw: dict | None) -> dict:
    """يصحّح جواباً واحداً يقينياً.

    يعيد: {score, max_score, auto (هل صُحّح آلياً)، verdicts، feedback، answer_text}.
    - المغلقة: نقطة كاملة + تشخيصات (تغذية راجعة) من payload.diagnostics.
    - المفتوحة: نقطة جزئية من مؤشّرات لها check_rule؛ الباقي None (ينتظر الأستاذ).
    """
    res = score_answer(
        question.qtype, question.payload or {}, question.indicators or [],
        question.penalties or [], question.max_score, raw)
    out = {
        "score": res.get("auto_score"),
        "max_score": question.max_score,
        "auto": question.qtype in QUESTION_TYPES_CLOSED,
        "verdict": res.get("verdict"),
        "rule_verdicts": res.get("rule_verdicts"),
        "answer_text": answer_text(question.qtype, raw),
        "feedback": _feedback_for(question, raw, res),
    }
    return out


def _feedback_for(question: QuizQuestion, raw: dict | None, res: dict) -> str | None:
    """تغذية راجعة نصّية للمتعلّم: صواب/خطأ للمغلقة + تشخيص البديل المختار إن وُجد."""
    if question.qtype not in QUESTION_TYPES_CLOSED:
        return None
    score = res.get("auto_score")
    full = question.max_score
    if score is not None and full and score >= full:
        return "إجابة صحيحة ✓"
    # للاختيار الأحادي: اعرض تشخيص البديل المختار إن كان مُعرّفاً في payload.
    diagnostics = (question.payload or {}).get("diagnostics") or {}
    if question.qtype == "mcq_single" and isinstance(raw, dict):
        choice = raw.get("choice")
        if choice is not None and str(choice) in diagnostics:
            return f"إجابة غير دقيقة — {diagnostics[str(choice)]}"
    return "إجابة غير صحيحة — راجع عناصر الجواب."
