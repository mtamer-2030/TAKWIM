"""طبقة التحليل الرياضي (المرحلة 5) — بروفايل مهارات التلميذ وتقرير القسم.

المهارات المستهدَفة موحّدة عبر المستويات الثلاثة وتُقرأ من جدول skills عبر
skill_id في سؤال التحليل (AnalysisQuestion) وسؤال التقويم (QuizQuestion):
صياغة الإشكال، البنية المفاهيمية، الأطروحة، البنية الحجاجية، المناقشة، التركيب.
النقطة تُطبَّع كنسبة score/max_score (0..1) قبل حساب المتوسّطات، فتُقارَن المهارات
بعدل رغم اختلاف السلالم. لا يدخل الحساب إلّا إنجاز مصادَق عليه (teacher_confirmed).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..constants import SKILLS
from ..models import (
    AnalysisQuestion,
    Answer,
    QuizQuestion,
    Skill,
    Student,
)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


async def _confirmed_ratios_by_skill(
    session: AsyncSession, student_ids: list[int] | None = None,
) -> dict[str, list[float]]:
    """يجمع نِسب (score/max_score) المصادَق عليها من جدول الأجوبة الموحّد.

    النقطة الفعلية = يدوية الأستاذ إن وُجدت وإلّا الآلية. تُصنَّف الأجوبة حسب اسم
    مهارة السؤال (سؤال تقويم أو سؤال تحليل نصّ)؛ الأجوبة الإنشائية بلا مهارة
    لا تدخل الرادار. لا يُحتسب إلّا جواب مصادَق عليه (teacher_confirmed).
    """
    eff = func.coalesce(Answer.manual_score, Answer.auto_score)
    buckets: dict[str, list[float]] = {s: [] for s in SKILLS}

    # (1) أجوبة أسئلة التقويم → مهارة السؤال + سقفه.
    qstmt = (
        select(Skill.name, eff, QuizQuestion.max_score)
        .join(QuizQuestion, QuizQuestion.id == Answer.quiz_question_id)
        .join(Skill, Skill.id == QuizQuestion.skill_id)
        .where(Answer.teacher_confirmed.is_(True), eff.is_not(None),
               QuizQuestion.max_score > 0)
    )
    # (2) أجوبة أسئلة تحليل النصّ → مهارة السؤال + سقفه.
    astmt = (
        select(Skill.name, eff, AnalysisQuestion.max_score)
        .join(AnalysisQuestion, AnalysisQuestion.id == Answer.analysis_question_id)
        .join(Skill, Skill.id == AnalysisQuestion.skill_id)
        .where(Answer.teacher_confirmed.is_(True), eff.is_not(None),
               AnalysisQuestion.max_score > 0)
    )
    if student_ids is not None:
        qstmt = qstmt.where(Answer.student_id.in_(student_ids))
        astmt = astmt.where(Answer.student_id.in_(student_ids))

    for stmt in (qstmt, astmt):
        for name, score, max_score in (await session.execute(stmt)).all():
            if score is not None and max_score:
                buckets.setdefault(name, []).append(max(0.0, min(1.0, score / max_score)))
    return buckets


def _profile_from_buckets(buckets: dict[str, list[float]]) -> dict:
    """يحوّل الدلاء إلى بروفايل: متوسّط كل مهارة + نقطة القوة + القصور الحرج."""
    per_skill = {
        skill: {"avg": _mean(vals), "count": len(vals)}
        for skill, vals in buckets.items()
    }
    scored = {k: v["avg"] for k, v in per_skill.items() if v["avg"] is not None}
    strength = max(scored, key=scored.get) if scored else None
    critical_deficit = min(scored, key=scored.get) if scored else None
    overall = _mean([r for vals in buckets.values() for r in vals])
    return {
        "skills": per_skill,               # {مهارة: {avg, count}}
        "strength": strength,              # أقوى مهارة
        "critical_deficit": critical_deficit,  # أضعف مهارة (القصور الحرج)
        "overall": overall,                # المتوسّط العامّ (0..1)
        "has_data": bool(scored),
    }


async def generate_student_skill_profile(session: AsyncSession, student_id: int) -> dict:
    """بروفايل مهارات تلميذ: متوسّط كل مهارة + نقطة القوة + القصور الحرج."""
    student = await session.get(Student, student_id)
    buckets = await _confirmed_ratios_by_skill(session, [student_id])
    profile = _profile_from_buckets(buckets)
    profile["student_id"] = student_id
    profile["student_name"] = student.full_name if student else None
    return profile


async def generate_class_report(
    session: AsyncSession, group_name: str, evaluation_id: int | None = None,
) -> dict:
    """تقرير قسم: متوسّط عامّ + القصور المنهجي المهيمن (أضعف مهارة لدى الأغلبية)."""
    students = (await session.execute(
        select(Student).where(Student.group_name == group_name,
                              Student.active.is_(True)))).scalars().all()
    ids = [s.id for s in students]
    if not ids:
        return {"group_name": group_name, "has_data": False,
                "student_count": 0, "skills": {}, "dominant_deficit": None}

    # متوسّطات القسم لكل مهارة
    buckets = await _confirmed_ratios_by_skill(session, ids)
    class_profile = _profile_from_buckets(buckets)

    # «القصور المنهجي المهيمن»: أضعف مهارة عند كلّ تلميذ ثمّ الأكثر تكراراً.
    weakest_counter: dict[str, int] = {}
    students_with_data = 0
    for sid in ids:
        sb = await _confirmed_ratios_by_skill(session, [sid])
        sp = _profile_from_buckets(sb)
        if sp["critical_deficit"]:
            students_with_data += 1
            weakest_counter[sp["critical_deficit"]] = \
                weakest_counter.get(sp["critical_deficit"], 0) + 1

    dominant = max(weakest_counter, key=weakest_counter.get) if weakest_counter else None
    dominant_share = (weakest_counter.get(dominant, 0) / students_with_data
                      if students_with_data else 0)

    return {
        "group_name": group_name,
        "evaluation_id": evaluation_id,
        "student_count": len(ids),
        "students_with_data": students_with_data,
        "skills": class_profile["skills"],           # متوسّطات القسم لكل مهارة
        "overall": class_profile["overall"],
        "dominant_deficit": dominant,                # القصور المنهجي المهيمن
        "dominant_share": round(dominant_share, 3),  # نسبة التلاميذ الذين هو أضعف مهارتهم
        "weakest_distribution": weakest_counter,
        "has_data": class_profile["has_data"],
    }
