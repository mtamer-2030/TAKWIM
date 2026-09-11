"""خدمة التقاويم v2 — إنشاء التقاويم من JSON وتصحيحها يقينياً.

تُعاد محرّكات v1 النقيّة دون تغيير:
- ``app.validation.validate_assessment``: تحقّق صارم من صيغة JSON.
- ``app.scoring.score_answer`` و``answer_text``: تصحيح يقيني للمغلقة وجزئي للمفتوحة.

بذلك نضمن نفس السلوك المجرَّب في v1 داخل بنية v2 (SQLAlchemy async).
"""

from __future__ import annotations

from ..constants import COMPETENCY_TO_SKILL, QUESTION_TYPES_CLOSED, SKILLS
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


def resolve_skill_id(value: str | None, skill_ids: dict[str, int]) -> int | None:
    """يحلّ قيمة الكفاية/المهارة إلى skill_id من خريطة (اسم المهارة → id).

    يقبل مفتاح كفاية v1 (مثل ``argumentation``) أو اسم مهارة عربيّاً مباشرةً.
    يعيد None إن تعذّر التطابق (فيبقى السؤال بلا مهارة، لا ينهار الاستيراد).
    """
    if not value:
        return None
    name = value if value in SKILLS else COMPETENCY_TO_SKILL.get(value)
    return skill_ids.get(name) if name else None


def build_quiz(normalized: dict, *, level_id: int | None = None,
               group_name: str | None = None,
               skill_ids: dict[str, int] | None = None) -> Quiz:
    """يبني كائن Quiz (غير محفوظ) من الصيغة المطبّعة، مع حلّ نصوص الانطلاق.

    ``skill_ids``: خريطة (اسم المهارة العربيّ → id) من جدول skills المبذور،
    تُحلّ بها كفاية كل سؤال إلى skill_id؛ إن غابت بقيت الأسئلة بلا مهارة.
    """
    skill_ids = skill_ids or {}
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
            skill_id=resolve_skill_id(q.get("competency"), skill_ids),
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


def readable_answer(question: QuizQuestion, raw: dict | None) -> str:
    """يحوّل جواب التلميذ الخام إلى نصّ مقروء للأستاذ (لشاشة التصحيح)."""
    raw = raw or {}
    payload = question.payload or {}
    opts = payload.get("options", [])
    if question.qtype == "mcq_single":
        c = raw.get("choice")
        return opts[c] if isinstance(c, int) and 0 <= c < len(opts) else "— بلا جواب —"
    if question.qtype == "mcq_multi":
        chosen = [opts[i] for i in raw.get("choices", []) if isinstance(i, int) and 0 <= i < len(opts)]
        return "، ".join(chosen) or "— بلا جواب —"
    if question.qtype == "classify":
        cats = payload.get("categories", [])
        items = payload.get("items", [])
        assigns = raw.get("assignments", [])
        parts = []
        for k, it in enumerate(items):
            a = assigns[k] if k < len(assigns) else None
            cat = cats[a] if isinstance(a, int) and 0 <= a < len(cats) else "—"
            parts.append(f"{it.get('text', '')} → {cat}")
        return " | ".join(parts) or "— بلا جواب —"
    if question.qtype == "order":
        items = payload.get("items", [])
        order = raw.get("order", [])
        seq = [items[i] for i in order if isinstance(i, int) and 0 <= i < len(items)]
        return " ثمّ ".join(seq) or "— بلا جواب —"
    if question.qtype in ("short_text", "long_text"):
        return (raw.get("text") or "").strip() or "— بلا جواب —"
    if question.qtype == "grid":
        cells = raw.get("cells", [])
        return " / ".join(" ، ".join(str(c) for c in row) for row in cells) or "— بلا جواب —"
    return "—"


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
