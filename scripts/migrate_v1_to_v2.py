#!/usr/bin/env python3
"""ترحيل بيانات v1 (مِحَكّ · sqlite3) إلى v2 (PHILO-TECH · SQLAlchemy async).

هذا السكربت هو أداة التوحيد: ينقل تلاميذ v1 وإنجازاتهم إلى قاعدة v2 الجديدة
(``data/philotech.db``) ليصبح PHILO-TECH v2 النظام الأساسي والوحيد.

منهجية ETL:

1. النسخ الاحتياطي الإلزامي (Pre-flight Backup):
   قبل لمس أيّ شيء، تُنسخ ``data/mihakk.db`` إلى ``data/mihakk_v1_backup_<ختم زمني>.db``.
   لا يبدأ الاستخراج قبل نجاح النسخ.

2. الاستخراج (Extract): من قاعدة v1 الخام عبر ``sqlite3``:
   - التلاميذ (مع فوجهم ومستواهم من جدول ``classes``).
   - الإنجازات: أجوبة التلاميذ (``answers``) موصولةً بالمحاولات والجلسات
     والأسئلة، للحصول على الكفاية (competency) والنقطة والسقف.

3. التحويل (Transform): بما أنّ v1 لا يملك الهيكلة الديداكتيكية الصارمة لـ v2،
   نُنشئ برمجيّاً كيانات افتراضية «مستوردة» لتفادي انتهاك المفاتيح الأجنبية:
   - مستوى: «مستوى مستورد» (رمز ``IMP``).
   - مجزوءة: «مجزوءة عامة» ومحور «محور عام» ونصّ حامل واحد.
   - خمسة أسئلة تحليل (سؤال لكلّ مهارة) تُسقَط عليها كفايات v1.
   - سياق تقويم واحد: «تقويم مرحل من v1».
   كفايات v1 ↦ مهارات v2:
       problematization → إشكال، conceptualization → مفاهيم،
       argumentation → بنية حجاجية، synthesis → استنتاج، knowledge → أطروحة.
   تُجمَع أجوبة التلميذ في كلّ كفاية في إنجاز واحد (مجموع النقط ومجموع السقوف)
   احتراماً لقيد التفرّد (event_id, student_id, question_id).

4. التحميل (Load): إلى ``data/philotech.db`` عبر SQLAlchemy async/aiosqlite،
   بنفس محرّك v2 (WAL، مفاتيح أجنبية مفعّلة).

التشغيل:  ``python scripts/migrate_v1_to_v2.py``
السكربت متسامح: إن غابت قاعدة v1 أعلن ذلك وخرج بلا خطأ (لا شيء لترحيله).
وهو أيضاً قابل لإعادة التشغيل (idempotent): لا يُكرّر التلاميذ ولا الإنجازات.
"""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# إتاحة استيراد حزمة app عند التشغيل من جذر المشروع.
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select  # noqa: E402

from app.database import DB_PATH as V2_DB_PATH, AsyncSessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    AnalysisQuestion,
    Axis,
    Base,
    EvaluationEvent,
    EventType,
    Level,
    Module,
    PhilosophicalText,
    Student,
    Submission,
    TargetSkill,
    TextType,
)

# قاعدة v1: نستعمل نفس منطق الإعداد (يحترم MIHAKK_DATA_DIR).
from app.constants import LEVELS  # noqa: E402
from app.settings import DB_PATH as V1_DB_PATH  # noqa: E402

# ——— ثوابت الترحيل ———
IMPORTED_LEVEL_NAME = "مستوى مستورد"
IMPORTED_LEVEL_CODE = "IMP"
IMPORTED_MODULE_TITLE = "مجزوءة عامة"
IMPORTED_AXIS_TITLE = "محور عام"
IMPORTED_TEXT_TITLE = "نصّ حامل (بيانات مرحّلة من v1)"
IMPORTED_EVENT_TITLE = "تقويم مرحل من v1"

# كفاية v1 (competency) ↦ مهارة v2 (TargetSkill)
COMPETENCY_TO_SKILL: dict[str, TargetSkill] = {
    "problematization": TargetSkill.PROBLEM,        # الأشكلة  → إشكال
    "conceptualization": TargetSkill.CONCEPTS,      # المفهمة  → مفاهيم
    "argumentation": TargetSkill.ARGUMENT_STRUCTURE,  # الحجاج → بنية حجاجية
    "synthesis": TargetSkill.CONCLUSION,            # التركيب  → استنتاج
    "knowledge": TargetSkill.THESIS,                # الاستحضار → أطروحة (أقرب خانة متاحة)
}


class Report:
    """عدّادات تقرير الترحيل — تُطبع في النهاية على الطرفية."""

    def __init__(self) -> None:
        self.backup_path: str | None = None
        self.v1_students = 0
        self.v1_answers = 0
        self.students_migrated = 0
        self.students_skipped = 0
        self.submissions_migrated = 0
        self.submissions_skipped = 0
        self.notes: list[str] = []


# ═══════════════════════ 1) النسخ الاحتياطي الإلزامي ═══════════════════════


def backup_v1(report: Report) -> bool:
    """ينسخ قاعدة v1 قبل أيّ عملية. يعيد False إن غابت القاعدة (لا شيء لترحيله)."""
    if not V1_DB_PATH.exists():
        report.notes.append(
            f"لم تُوجد قاعدة v1 في {V1_DB_PATH} — لا بيانات قديمة لترحيلها.")
        return False
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = V1_DB_PATH.parent / f"mihakk_v1_backup_{stamp}.db"
    shutil.copy2(V1_DB_PATH, backup)
    # انسخ ملفّي WAL/SHM إن وُجدا حتى تكون النسخة متّسقة تماماً.
    for suffix in ("-wal", "-shm"):
        side = Path(str(V1_DB_PATH) + suffix)
        if side.exists():
            shutil.copy2(side, Path(str(backup) + suffix))
    report.backup_path = str(backup)
    return True


# ═══════════════════════ 2) الاستخراج من v1 ═══════════════════════


def _table_exists(cx: sqlite3.Connection, name: str) -> bool:
    row = cx.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def extract_v1(report: Report) -> tuple[list[dict], dict[int, list[dict]]]:
    """يستخرج التلاميذ وإنجازاتهم من v1.

    يعيد: (قائمة التلاميذ، خريطة student_id → قائمة أجوبته).
    كلّ تلميذ: {v1_id, full_name, massar_id, login_code, group_label, level_code}.
    كلّ جواب: {competency, score, max_score, confirmed}.
    """
    cx = sqlite3.connect(f"file:{V1_DB_PATH}?mode=ro", uri=True)
    cx.row_factory = sqlite3.Row
    try:
        students: list[dict] = []
        for r in cx.execute(
            """
            SELECT s.id, s.full_name, s.massar_id, s.login_code, s.active,
                   c.label AS class_label, c.level AS class_level
            FROM students s
            JOIN classes c ON c.id = s.class_id
            ORDER BY s.id
            """
        ):
            students.append({
                "v1_id": r["id"],
                "full_name": r["full_name"],
                "massar_id": (r["massar_id"] or "").strip() or None,
                "login_code": (r["login_code"] or "").strip() or None,
                "group_label": r["class_label"],
                "level_code": (r["class_level"] or "").strip() or None,
                "active": bool(r["active"]),
            })
        report.v1_students = len(students)

        answers: dict[int, list[dict]] = {}
        if _table_exists(cx, "answers"):
            for r in cx.execute(
                """
                SELECT at.student_id      AS student_id,
                       q.competency       AS competency,
                       q.max_score        AS max_score,
                       a.manual_score     AS manual_score,
                       a.auto_score       AS auto_score,
                       a.ai_score         AS ai_score,
                       a.teacher_confirmed AS teacher_confirmed
                FROM answers a
                JOIN attempts at ON at.id = a.attempt_id
                JOIN questions q ON q.id = a.question_id
                """
            ):
                # نقطة محسومة: يدوية ← تلقائية ← ذكاء اصطناعي (أوّل متاح).
                score = r["manual_score"]
                if score is None:
                    score = r["auto_score"]
                if score is None:
                    score = r["ai_score"]
                if score is None:
                    continue  # لا نقطة ⇐ لا شيء لترحيله من هذا الجواب
                comp = r["competency"]
                if comp not in COMPETENCY_TO_SKILL:
                    continue
                answers.setdefault(r["student_id"], []).append({
                    "competency": comp,
                    "score": float(score),
                    "max_score": float(r["max_score"] or 0) or None,
                    "confirmed": bool(r["teacher_confirmed"]),
                })
                report.v1_answers += 1
        return students, answers
    finally:
        cx.close()


# ═══════════════════════ 3+4) التحويل والتحميل ═══════════════════════


async def _ensure_schema() -> None:
    """يُنشئ جداول v2 إن لم تكن موجودة (أوّل ترحيل قبل تشغيل الخادم)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _get_or_create_scaffold(session) -> tuple[Level, EvaluationEvent, dict[TargetSkill, AnalysisQuestion]]:
    """يُنشئ (أو يستعيد) الكيانات الافتراضية المستوردة ويعيد الأسئلة لكلّ مهارة."""
    # المستويات الحقيقية (TC/1BAC/2BAC) — تُبذَر هنا حتى يلتحق التلميذ بمستواه
    # الفعلي بدل المستوى المستورد إن كان رمز قسمه معروفاً.
    have = {lv.code for lv in (await session.scalars(select(Level))).all()}
    for pos, (code, name) in enumerate(LEVELS.items()):
        if code not in have:
            session.add(Level(name=name, code=code, position=pos))
    await session.flush()

    # المستوى المستورد
    level = (await session.scalars(
        select(Level).where(Level.code == IMPORTED_LEVEL_CODE))).first()
    if level is None:
        level = Level(name=IMPORTED_LEVEL_NAME, code=IMPORTED_LEVEL_CODE, position=99)
        session.add(level)
        await session.flush()

    # المجزوءة → المحور → النصّ الحامل
    module = (await session.scalars(
        select(Module).where(Module.level_id == level.id,
                             Module.title == IMPORTED_MODULE_TITLE))).first()
    if module is None:
        module = Module(level_id=level.id, title=IMPORTED_MODULE_TITLE)
        session.add(module)
        await session.flush()

    axis = (await session.scalars(
        select(Axis).where(Axis.module_id == module.id,
                           Axis.title == IMPORTED_AXIS_TITLE))).first()
    if axis is None:
        axis = Axis(module_id=module.id, title=IMPORTED_AXIS_TITLE)
        session.add(axis)
        await session.flush()

    text = (await session.scalars(
        select(PhilosophicalText).where(PhilosophicalText.axis_id == axis.id,
                                        PhilosophicalText.title == IMPORTED_TEXT_TITLE))).first()
    if text is None:
        text = PhilosophicalText(
            axis_id=axis.id, title=IMPORTED_TEXT_TITLE,
            content="نصّ حامل أُنشئ آليّاً لاستيعاب إنجازات v1 المرحّلة.",
            text_type=TextType.SUPPORT)
        session.add(text)
        await session.flush()

    # سؤال تحليل لكلّ مهارة (خمسة)
    questions: dict[TargetSkill, AnalysisQuestion] = {}
    existing = (await session.scalars(
        select(AnalysisQuestion).where(AnalysisQuestion.text_id == text.id))).all()
    by_skill = {q.target_skill: q for q in existing}
    for pos, skill in enumerate(TargetSkill):
        q = by_skill.get(skill)
        if q is None:
            q = AnalysisQuestion(
                text_id=text.id, prompt=f"إنجاز مرحّل — مهارة: {skill.value}",
                target_skill=skill, max_score=0, position=pos)
            session.add(q)
            await session.flush()
        questions[skill] = q

    # سياق التقويم المستورد
    event = (await session.scalars(
        select(EvaluationEvent).where(EvaluationEvent.title == IMPORTED_EVENT_TITLE))).first()
    if event is None:
        event = EvaluationEvent(
            title=IMPORTED_EVENT_TITLE, event_type=EventType.DIAGNOSTIC,
            level_id=level.id, group_name=None)
        session.add(event)
        await session.flush()

    return level, event, questions


async def load_v2(students: list[dict], answers: dict[int, list[dict]], report: Report) -> None:
    """يحمّل التلاميذ والإنجازات المجمّعة إلى قاعدة v2."""
    await _ensure_schema()
    async with AsyncSessionLocal() as session:
        level, event, questions = await _get_or_create_scaffold(session)

        # المستويات الحقيقية المبذورة (TC/1BAC/2BAC) لربط التلميذ بمستواه إن أمكن.
        real_levels = {
            lv.code: lv for lv in (await session.scalars(select(Level))).all()}

        for st in students:
            # اربط بالمستوى الحقيقي إن طابق رمز القسم، وإلّا بالمستوى المستورد.
            target_level = real_levels.get(st["level_code"] or "", level)

            # تفادي التكرار: بحث بالمسار ثمّ برمز الدخول.
            existing_student = None
            if st["massar_id"]:
                existing_student = (await session.scalars(
                    select(Student).where(Student.massar_code == st["massar_id"]))).first()
            if existing_student is None and st["login_code"]:
                existing_student = (await session.scalars(
                    select(Student).where(Student.login_code == st["login_code"]))).first()

            if existing_student is not None:
                student = existing_student
                report.students_skipped += 1
            else:
                student = Student(
                    full_name=st["full_name"], level_id=target_level.id,
                    group_name=st["group_label"], massar_code=st["massar_id"],
                    login_code=st["login_code"], active=st["active"])
                session.add(student)
                await session.flush()
                report.students_migrated += 1

            # تجميع أجوبة التلميذ في كلّ مهارة → إنجاز واحد (مجموع النقط والسقوف).
            agg: dict[TargetSkill, dict] = {}
            for ans in answers.get(st["v1_id"], []):
                skill = COMPETENCY_TO_SKILL[ans["competency"]]
                bucket = agg.setdefault(
                    skill, {"score": 0.0, "max": 0.0, "confirmed": False, "n": 0})
                bucket["score"] += ans["score"]
                bucket["max"] += ans["max_score"] or 0.0
                bucket["confirmed"] = bucket["confirmed"] or ans["confirmed"]
                bucket["n"] += 1

            for skill, b in agg.items():
                q = questions[skill]
                # هل يوجد إنجاز مسبق لهذا (التقويم، التلميذ، السؤال)؟ (idempotent)
                dup = (await session.scalars(
                    select(Submission).where(
                        Submission.event_id == event.id,
                        Submission.student_id == student.id,
                        Submission.question_id == q.id))).first()
                if dup is not None:
                    report.submissions_skipped += 1
                    continue
                session.add(Submission(
                    student_id=student.id, event_id=event.id, question_id=q.id,
                    answer_text=f"مجمّع من {b['n']} جواب/أجوبة في v1.",
                    score=round(b["score"], 3),
                    max_score=round(b["max"], 3) or None,
                    teacher_confirmed=b["confirmed"]))
                report.submissions_migrated += 1

        await session.commit()
    await engine.dispose()


# ═══════════════════════ الطباعة النهائية ═══════════════════════


def print_report(report: Report) -> None:
    line = "═" * 56
    print("\n" + line)
    print("           تقرير ترحيل البيانات · v1 → PHILO-TECH v2")
    print(line)
    if report.backup_path:
        print(f"  ✓ نسخة احتياطية: {report.backup_path}")
    for note in report.notes:
        print(f"  • {note}")
    print(f"  • قاعدة v2 الهدف: {V2_DB_PATH}")
    print(line)
    print(f"  تلاميذ v1 المقروؤون        : {report.v1_students}")
    print(f"  أجوبة v1 المقروءة          : {report.v1_answers}")
    print("  " + "-" * 52)
    print(f"  ✅ تلاميذ مُرحّلون          : {report.students_migrated}")
    print(f"  ↔  تلاميذ موجودون (تُخطّوا) : {report.students_skipped}")
    print(f"  ✅ إنجازات مُرحّلة          : {report.submissions_migrated}")
    print(f"  ↔  إنجازات موجودة (تُخطّت)  : {report.submissions_skipped}")
    print(line + "\n")


async def _amain() -> Report:
    report = Report()
    has_v1 = backup_v1(report)
    if not has_v1:
        # لا قاعدة v1 — نتأكّد فقط من وجود مخطّط v2 ثمّ نخرج.
        await _ensure_schema()
        await engine.dispose()
        return report
    students, answers = extract_v1(report)
    await load_v2(students, answers, report)
    return report


def main() -> None:
    report = asyncio.run(_amain())
    print_report(report)


if __name__ == "__main__":
    main()
