"""دفتر النقط التراكمي (Gradebook) — تتبّع الأداء عبر الموسم الدراسي.

يجمع نقط التلاميذ من التقاويم بالأسئلة (المصادَق عليها) مرتّبةً زمنيّاً، من
التقويم التشخيصي إلى باقي التعلّمات، ويحسب نسباً واضحة (٪) للأستاذ — فرديّاً
وجماعيّاً. النقطة الفعلية = يدوية الأستاذ إن وُجدت وإلّا الآلية اليقينية.

هذه تقارير للأستاذ (نقط صريحة)؛ التلميذ يبقى يرى الرادار النوعيّ فقط.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Quiz, QuizAnswer, QuizQuestion, Student
from .analytics import generate_student_skill_profile


def _pct(score: float | None, maxs: float | None) -> float | None:
    if not maxs:
        return None
    return round(max(0.0, min(1.0, (score or 0) / maxs)) * 100, 1)


async def _quiz_score_rows(session: AsyncSession, student_ids: list[int] | None):
    """صفوف (quiz_id, title, kind, created_at, student_id, score, maxs) المصادَق عليها."""
    eff = func.coalesce(QuizAnswer.manual_score, QuizAnswer.auto_score)
    stmt = (
        select(Quiz.id, Quiz.title, Quiz.kind, Quiz.created_at,
               QuizAnswer.student_id, func.sum(eff), func.sum(QuizQuestion.max_score))
        .join(QuizQuestion, QuizQuestion.id == QuizAnswer.question_id)
        .join(Quiz, Quiz.id == QuizQuestion.quiz_id)
        .where(QuizAnswer.teacher_confirmed.is_(True),
               eff.is_not(None), QuizQuestion.max_score > 0)
        .group_by(Quiz.id, QuizAnswer.student_id)
    )
    if student_ids is not None:
        stmt = stmt.where(QuizAnswer.student_id.in_(student_ids))
    return (await session.execute(stmt)).all()


async def student_gradebook(session: AsyncSession, student_id: int) -> dict:
    """تقرير تلميذ تراكميّ: نسبته في كل تقويم زمنيّاً + المهارات + المعدّل العامّ."""
    student = await session.get(Student, student_id)
    rows = await _quiz_score_rows(session, [student_id])
    rows.sort(key=lambda r: (r[3] or 0, r[0]))          # ترتيب زمنيّ
    evaluations = [{
        "quiz_id": r[0], "title": r[1], "kind": r[2],
        "date": r[3].strftime("%Y-%m-%d") if r[3] else "—",
        "pct": _pct(r[5], r[6]),
    } for r in rows]
    pcts = [e["pct"] for e in evaluations if e["pct"] is not None]
    overall = round(sum(pcts) / len(pcts), 1) if pcts else None
    trend = None
    if len(pcts) >= 2:
        trend = round(pcts[-1] - pcts[0], 1)            # فرق آخر تقويم عن الأوّل
    profile = await generate_student_skill_profile(session, student_id)
    return {
        "student": student, "evaluations": evaluations, "overall": overall,
        "trend": trend, "skills": profile.get("skills", {}),
        "has_data": bool(evaluations),
    }


async def class_gradebook(session: AsyncSession, group_name: str) -> dict:
    """تقرير فوج تراكميّ: مصفوفة (تلاميذ × تقاويم) بالنِّسب + معدّل القسم لكل تقويم."""
    students = (await session.execute(
        select(Student).where(Student.group_name == group_name, Student.active.is_(True))
        .order_by(Student.full_name))).scalars().all()
    sid_list = [st.id for st in students]
    rows = await _quiz_score_rows(session, sid_list or None)

    # التقاويم المرتّبة زمنيّاً
    quizzes: dict[int, dict] = {}
    for qid, title, kind, created, _sid, _sc, _mx in rows:
        quizzes.setdefault(qid, {"quiz_id": qid, "title": title, "kind": kind,
                                 "created": created})
    evals = sorted(quizzes.values(), key=lambda q: (q["created"] or 0, q["quiz_id"]))
    for e in evals:
        e["date"] = e["created"].strftime("%Y-%m-%d") if e["created"] else "—"

    # نسبة كل تلميذ في كل تقويم
    cell: dict[tuple[int, int], float | None] = {}
    for qid, _t, _k, _c, sid, score, maxs in rows:
        cell[(sid, qid)] = _pct(score, maxs)

    student_rows = []
    for st in students:
        per = {e["quiz_id"]: cell.get((st.id, e["quiz_id"])) for e in evals}
        vals = [v for v in per.values() if v is not None]
        student_rows.append({
            "student": st, "per": per,
            "overall": round(sum(vals) / len(vals), 1) if vals else None,
        })

    # معدّل القسم لكل تقويم + عدد المسلّمين
    for e in evals:
        vals = [cell[(st.id, e["quiz_id"])] for st in students
                if cell.get((st.id, e["quiz_id"])) is not None]
        e["class_avg"] = round(sum(vals) / len(vals), 1) if vals else None
        e["count"] = len(vals)

    class_overall = None
    all_overall = [s["overall"] for s in student_rows if s["overall"] is not None]
    if all_overall:
        class_overall = round(sum(all_overall) / len(all_overall), 1)

    return {
        "group_name": group_name, "evaluations": evals, "students": student_rows,
        "class_overall": class_overall, "has_data": bool(evals),
    }
