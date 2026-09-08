"""PHILO-TECH v2 — نماذج قاعدة البيانات البيداغوجية (SQLAlchemy async, 2.0).

تعكس المنهاج المغربي للفلسفة بدقّة:
الهيكلة الديداكتيكية: مستوى ← مجزوءة ← (مفهوم اختياري) ← محور ← نصّ ← سؤال تحليل.
والإنشاء: تمرين إنشاء مرتبط بالمستوى.
والتقويم والتتبّع: سياق تقويم ← إنجازات التلاميذ ← تقارير فردية وفصلية.

القيم العربية للـ Enums تُخزَّن نصّاً (values_callable) لتكون القاعدة مقروءة ومصدَّرة.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """القاعدة التصريحية لكلّ النماذج."""


def _ar_enum(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    """Enum يخزّن القيمة العربية نصّاً (لا اسم العضو)، مع تسمية القيد."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,                       # يُخزَّن VARCHAR + CHECK
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


# ═══════════════════════ التعدادات (Enums) ═══════════════════════


class TextType(str, enum.Enum):
    BASIC = "أساسي"
    AXES = "محاور"
    SUPPORT = "داعم"


class TargetSkill(str, enum.Enum):
    PROBLEM = "إشكال"
    CONCEPTS = "مفاهيم"
    THESIS = "أطروحة"
    ARGUMENT_STRUCTURE = "بنية حجاجية"
    CONCLUSION = "استنتاج"


class MethodologyType(str, enum.Enum):
    TEXT_ANALYSIS = "تحليل نص"
    QUOTATION = "قولة"
    PROBLEM_QUESTION = "سؤال إشكالي"
    PARTIAL_SKILL = "مهارة جزئية"


class EventType(str, enum.Enum):
    DIAGNOSTIC = "تقويم تشخيصي"
    EXERCISE = "تمرين"
    EXAM = "فرض"


# ═══════════════════════ الهيكلة الديداكتيكية ═══════════════════════


class Level(Base):
    """المستوى الدراسي: جذع مشترك، أولى باك، ثانية باك."""

    __tablename__ = "levels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)     # «الجذع المشترك»…
    code: Mapped[str] = mapped_column(String(20), unique=True)      # TC / 1BAC / 2BAC
    position: Mapped[int] = mapped_column(Integer, default=0)

    modules: Mapped[list["Module"]] = relationship(
        back_populates="level", cascade="all, delete-orphan")
    essays: Mapped[list["EssayExercise"]] = relationship(back_populates="level")
    students: Mapped[list["Student"]] = relationship(back_populates="level")
    events: Mapped[list["EvaluationEvent"]] = relationship(back_populates="level")


class Module(Base):
    """المجزوءة: مرتبطة بالمستوى."""

    __tablename__ = "modules"

    id: Mapped[int] = mapped_column(primary_key=True)
    level_id: Mapped[int] = mapped_column(
        ForeignKey("levels.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=0)

    level: Mapped["Level"] = relationship(back_populates="modules")
    concepts: Mapped[list["Concept"]] = relationship(
        back_populates="module", cascade="all, delete-orphan")
    axes: Mapped[list["Axis"]] = relationship(
        back_populates="module", cascade="all, delete-orphan")


class Concept(Base):
    """المفهوم: مرتبط بالمجزوءة.

    اختياري في السلسلة: في الجذع المشترك يُنتقل من المجزوءة إلى المحور مباشرة،
    فيكون محور الجذع المشترك بلا مفهوم (Axis.concept_id = NULL).
    """

    __tablename__ = "concepts"

    id: Mapped[int] = mapped_column(primary_key=True)
    module_id: Mapped[int] = mapped_column(
        ForeignKey("modules.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=0)

    module: Mapped["Module"] = relationship(back_populates="concepts")
    axes: Mapped[list["Axis"]] = relationship(back_populates="concept")


class Axis(Base):
    """المحور: مرتبط بالمجزوءة (إلزامي) والمفهوم (اختياري للجذع المشترك)."""

    __tablename__ = "axes"

    id: Mapped[int] = mapped_column(primary_key=True)
    module_id: Mapped[int] = mapped_column(
        ForeignKey("modules.id", ondelete="CASCADE"), index=True)
    concept_id: Mapped[int | None] = mapped_column(
        ForeignKey("concepts.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=0)

    module: Mapped["Module"] = relationship(back_populates="axes")
    concept: Mapped["Concept | None"] = relationship(back_populates="axes")
    texts: Mapped[list["PhilosophicalText"]] = relationship(
        back_populates="axis", cascade="all, delete-orphan")


class PhilosophicalText(Base):
    """نصّ فلسفي: مرتبط بالمحور، بنوع (أساسي/محاور/داعم)."""

    __tablename__ = "philosophical_texts"

    id: Mapped[int] = mapped_column(primary_key=True)
    axis_id: Mapped[int] = mapped_column(
        ForeignKey("axes.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    text_type: Mapped[TextType] = mapped_column(
        _ar_enum(TextType, "text_type_enum"), default=TextType.BASIC)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    axis: Mapped["Axis"] = relationship(back_populates="texts")
    questions: Mapped[list["AnalysisQuestion"]] = relationship(
        back_populates="text", cascade="all, delete-orphan")


class AnalysisQuestion(Base):
    """سؤال تحليل نصّ: يستهدف مهارة محدّدة (إشكال/مفاهيم/أطروحة/بنية حجاجية/استنتاج)."""

    __tablename__ = "analysis_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    text_id: Mapped[int] = mapped_column(
        ForeignKey("philosophical_texts.id", ondelete="CASCADE"), index=True)
    prompt: Mapped[str] = mapped_column(Text)
    target_skill: Mapped[TargetSkill] = mapped_column(_ar_enum(TargetSkill, "target_skill_enum"))
    guidance: Mapped[str | None] = mapped_column(Text, nullable=True)  # عناصر الإجابة/توجيه
    max_score: Mapped[float] = mapped_column(Float, default=0)
    position: Mapped[int] = mapped_column(Integer, default=0)

    text: Mapped["PhilosophicalText"] = relationship(back_populates="questions")
    submissions: Mapped[list["Submission"]] = relationship(back_populates="question")


class EssayExercise(Base):
    """تمرين الإنشاء الفلسفي: مرتبط بالمستوى، بمنهجية محدّدة."""

    __tablename__ = "essay_exercises"

    id: Mapped[int] = mapped_column(primary_key=True)
    level_id: Mapped[int] = mapped_column(
        ForeignKey("levels.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    statement: Mapped[str] = mapped_column(Text)                    # القولة/النص/السؤال
    methodology_type: Mapped[MethodologyType] = mapped_column(
        _ar_enum(MethodologyType, "methodology_type_enum"))
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_score: Mapped[float] = mapped_column(Float, default=20)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    level: Mapped["Level"] = relationship(back_populates="essays")
    submissions: Mapped[list["Submission"]] = relationship(back_populates="essay")


# ═══════════════════════ التقويم والتتبّع ═══════════════════════


class EvaluationEvent(Base):
    """سياق التقويم: تشخيصي / تمرين / فرض. قد يخصّ مستوى وفوجاً محدّدين."""

    __tablename__ = "evaluation_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    event_type: Mapped[EventType] = mapped_column(_ar_enum(EventType, "event_type_enum"))
    level_id: Mapped[int | None] = mapped_column(
        ForeignKey("levels.id", ondelete="SET NULL"), nullable=True, index=True)
    group_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    level: Mapped["Level | None"] = relationship(back_populates="events")
    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="event", cascade="all, delete-orphan")


class Student(Base):
    """التلميذ. أُضيف massar_code وlogin_code (اختياريان) لدعم الدخول المحلّي بلا حساب."""

    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200))
    level_id: Mapped[int] = mapped_column(
        ForeignKey("levels.id", ondelete="CASCADE"), index=True)
    group_name: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    massar_code: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True)
    login_code: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    level: Mapped["Level"] = relationship(back_populates="students")
    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="student", cascade="all, delete-orphan")
    reports: Mapped[list["StudentReport"]] = relationship(
        back_populates="student", cascade="all, delete-orphan")


class Submission(Base):
    """إنجاز التلميذ: مرتبط بالتلميذ وبسياق التقويم، وبسؤال تحليل أو تمرين إنشاء.

    قيد: لا يجوز أن يشير الإنجاز إلى سؤال وتمرين معاً (أحدهما على الأكثر).
    skill_deficits: القصور المرصود (JSON) — يغذّي التقارير وخطط التدخّل.
    """

    __tablename__ = "submissions"
    __table_args__ = (
        CheckConstraint(
            "NOT (question_id IS NOT NULL AND essay_id IS NOT NULL)",
            name="ck_submission_single_target"),
        UniqueConstraint("event_id", "student_id", "question_id", "essay_id",
                         name="uq_submission_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_events.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_questions.id", ondelete="SET NULL"), nullable=True, index=True)
    essay_id: Mapped[int | None] = mapped_column(
        ForeignKey("essay_exercises.id", ondelete="SET NULL"), nullable=True, index=True)

    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # جواب التلميذ الخام
    score: Mapped[float | None] = mapped_column(Float, nullable=True)     # النقطة
    max_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    skill_deficits: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # القصور المرصود
    teacher_confirmed: Mapped[bool] = mapped_column(default=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    student: Mapped["Student"] = relationship(back_populates="submissions")
    event: Mapped["EvaluationEvent"] = relationship(back_populates="submissions")
    question: Mapped["AnalysisQuestion | None"] = relationship(back_populates="submissions")
    essay: Mapped["EssayExercise | None"] = relationship(back_populates="submissions")


class StudentReport(Base):
    """تقرير تشخيصي فردي + خلاصة الذكاء الاصطناعي للتدخّل العلاجي (JSON)."""

    __tablename__ = "student_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_events.id", ondelete="SET NULL"), nullable=True, index=True)
    skill_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # متوسّط كل مهارة
    ai_intervention_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    student: Mapped["Student"] = relationship(back_populates="reports")


class ClassReport(Base):
    """تقرير فصلي: أضعف مهارة للفوج + خلاصة الذكاء الاصطناعي للتدخّل الجماعي (JSON)."""

    __tablename__ = "class_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    level_id: Mapped[int] = mapped_column(
        ForeignKey("levels.id", ondelete="CASCADE"), index=True)
    group_name: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_events.id", ondelete="SET NULL"), nullable=True, index=True)
    skills_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # متوسّطات المهارات
    weakest_skill: Mapped[str | None] = mapped_column(String(60), nullable=True)
    ai_intervention_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# للاستيراد المريح في Alembic: كلّ الجداول عبر Base.metadata
__all__ = [
    "Base", "Level", "Module", "Concept", "Axis", "PhilosophicalText",
    "AnalysisQuestion", "EssayExercise", "EvaluationEvent", "Student",
    "Submission", "StudentReport", "ClassReport",
    "TextType", "TargetSkill", "MethodologyType", "EventType",
]
