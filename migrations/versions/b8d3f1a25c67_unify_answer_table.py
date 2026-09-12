"""unify Submission and QuizAnswer into one polymorphic answers table (ROADMAP 1-ب)

توحيد مسار الإنجاز في جدول واحد `answers` متعدّد الأشكال يشير إلى هدف واحد بالضبط:
سؤال تقويم (quiz_question) أو سؤال تحليل نصّ (analysis_question) أو تمرين إنشاء (essay).

- ينشئ `answers` (+ قيد الهدف الواحد + فهارس فريدة جزئية لكلّ هدف).
- ينقل أجوبة `quiz_answers` القائمة (question_id → quiz_question_id).
- ينقل `submissions` أحادية الهدف (question_id → analysis_question_id، essay_id).
- يحذف `quiz_answers` و`submissions` و`evaluation_events` (لا مسار كتابة لها في v2).
- يحذف عمود event_id الميت من student_reports و class_reports.

ملاحظة: في v2 لا يُكتب في submissions/evaluation_events، فالنقل منهما احتياطيّ.
عند التراجع تُستعاد الجداول الثلاثة وتُنقل أجوبة التقويم إلى quiz_answers؛ أجوبة
التحليل/الإنشاء (لا وجود لها في v2) تُسقَط عند التراجع.

Revision ID: b8d3f1a25c67
Revises: a7f2c9d4e1b8
Create Date: 2026-09-12
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8d3f1a25c67"
down_revision: Union[str, Sequence[str], None] = "a7f2c9d4e1b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) جدول الأجوبة الموحّد.
    op.create_table(
        "answers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("quiz_question_id", sa.Integer(), nullable=True),
        sa.Column("analysis_question_id", sa.Integer(), nullable=True),
        sa.Column("essay_id", sa.Integer(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("auto_score", sa.Float(), nullable=True),
        sa.Column("manual_score", sa.Float(), nullable=True),
        sa.Column("skill_deficits", sa.JSON(), nullable=True),
        sa.Column("teacher_confirmed", sa.Boolean(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("submitted_at", sa.DateTime(),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint(
            "((quiz_question_id IS NOT NULL) + (analysis_question_id IS NOT NULL) "
            "+ (essay_id IS NOT NULL)) = 1",
            name="ck_answer_single_target"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["quiz_question_id"], ["quiz_questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["analysis_question_id"], ["analysis_questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["essay_id"], ["essay_exercises.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("answers", schema=None) as b:
        b.create_index(b.f("ix_answers_student_id"), ["student_id"], unique=False)
        b.create_index(b.f("ix_answers_quiz_question_id"), ["quiz_question_id"], unique=False)
        b.create_index(b.f("ix_answers_analysis_question_id"), ["analysis_question_id"], unique=False)
        b.create_index(b.f("ix_answers_essay_id"), ["essay_id"], unique=False)
    # فهارس فريدة جزئية (SQLite يعدّ NULL متمايزة، فالقيد يُطبَّق على غير الفارغ فقط).
    op.create_index("uq_answer_quiz", "answers", ["student_id", "quiz_question_id"],
                    unique=True, sqlite_where=sa.text("quiz_question_id IS NOT NULL"))
    op.create_index("uq_answer_analysis", "answers", ["student_id", "analysis_question_id"],
                    unique=True, sqlite_where=sa.text("analysis_question_id IS NOT NULL"))
    op.create_index("uq_answer_essay", "answers", ["student_id", "essay_id"],
                    unique=True, sqlite_where=sa.text("essay_id IS NOT NULL"))

    # 2) نقل أجوبة التقاويم القائمة.
    op.execute(
        "INSERT INTO answers (student_id, quiz_question_id, raw, auto_score, "
        "manual_score, teacher_confirmed, submitted_at) "
        "SELECT student_id, question_id, raw, auto_score, manual_score, "
        "teacher_confirmed, submitted_at FROM quiz_answers")

    # 3) نقل الإنجازات أحادية الهدف من submissions (احتياطيّ؛ فارغ في v2).
    op.execute(
        "INSERT INTO answers (student_id, analysis_question_id, essay_id, raw, "
        "auto_score, skill_deficits, teacher_confirmed, submitted_at) "
        "SELECT student_id, question_id, essay_id, "
        "CASE WHEN answer_text IS NOT NULL THEN json_object('text', answer_text) END, "
        "score, skill_deficits, teacher_confirmed, submitted_at FROM submissions "
        "WHERE ((question_id IS NOT NULL) + (essay_id IS NOT NULL)) = 1")

    # 4) حذف عمود event_id الميت من التقارير — قبل حذف evaluation_events، لأنّ
    #    إعادة بناء الجدول في وضع batch يعكس مفتاحه الأجنبي نحو ذلك الجدول.
    with op.batch_alter_table("student_reports", schema=None) as b:
        b.drop_index(b.f("ix_student_reports_event_id"))
        b.drop_column("event_id")
    with op.batch_alter_table("class_reports", schema=None) as b:
        b.drop_index(b.f("ix_class_reports_event_id"))
        b.drop_column("event_id")

    # 5) حذف الجداول القديمة (submissions ابن evaluation_events، فيُحذف أوّلاً).
    op.drop_table("submissions")
    op.drop_table("evaluation_events")
    op.drop_table("quiz_answers")


def downgrade() -> None:
    # 4←) إعادة الجداول القديمة. evaluation_events أوّلاً حتى يُعاد المفتاح الأجنبي
    #    من جدولَي التقارير إليه.
    op.create_table(
        "evaluation_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("event_type", sa.Enum(
            "تقويم تشخيصي", "تمرين", "فرض",
            name="event_type_enum", native_enum=False), nullable=False),
        sa.Column("level_id", sa.Integer(), nullable=True),
        sa.Column("group_name", sa.String(length=60), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["level_id"], ["levels.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("evaluation_events", schema=None) as b:
        b.create_index(b.f("ix_evaluation_events_level_id"), ["level_id"], unique=False)

    # 5←) إعادة عمود event_id (بمفتاحه الأجنبي) إلى التقارير + فهرسه.
    with op.batch_alter_table("class_reports", schema=None) as b:
        b.add_column(sa.Column("event_id", sa.Integer(), nullable=True))
        b.create_index(b.f("ix_class_reports_event_id"), ["event_id"], unique=False)
        b.create_foreign_key("fk_class_reports_event_id", "evaluation_events",
                             ["event_id"], ["id"], ondelete="SET NULL")
    with op.batch_alter_table("student_reports", schema=None) as b:
        b.add_column(sa.Column("event_id", sa.Integer(), nullable=True))
        b.create_index(b.f("ix_student_reports_event_id"), ["event_id"], unique=False)
        b.create_foreign_key("fk_student_reports_event_id", "evaluation_events",
                             ["event_id"], ["id"], ondelete="SET NULL")

    op.create_table(
        "submissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=True),
        sa.Column("essay_id", sa.Integer(), nullable=True),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("max_score", sa.Float(), nullable=True),
        sa.Column("skill_deficits", sa.JSON(), nullable=True),
        sa.Column("teacher_confirmed", sa.Boolean(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint(
            "NOT (question_id IS NOT NULL AND essay_id IS NOT NULL)",
            name="ck_submission_single_target"),
        sa.ForeignKeyConstraint(["essay_id"], ["essay_exercises.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["event_id"], ["evaluation_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["analysis_questions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "student_id", "question_id", "essay_id",
                            name="uq_submission_scope"),
    )
    with op.batch_alter_table("submissions", schema=None) as b:
        b.create_index(b.f("ix_submissions_essay_id"), ["essay_id"], unique=False)
        b.create_index(b.f("ix_submissions_event_id"), ["event_id"], unique=False)
        b.create_index(b.f("ix_submissions_question_id"), ["question_id"], unique=False)
        b.create_index(b.f("ix_submissions_student_id"), ["student_id"], unique=False)

    op.create_table(
        "quiz_answers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("auto_score", sa.Float(), nullable=True),
        sa.Column("manual_score", sa.Float(), nullable=True),
        sa.Column("teacher_confirmed", sa.Boolean(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["question_id"], ["quiz_questions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("question_id", "student_id", name="uq_quiz_answer"),
    )
    with op.batch_alter_table("quiz_answers", schema=None) as b:
        b.create_index(b.f("ix_quiz_answers_question_id"), ["question_id"], unique=False)
        b.create_index(b.f("ix_quiz_answers_student_id"), ["student_id"], unique=False)

    # إعادة أجوبة التقويم من الجدول الموحّد (أجوبة التحليل/الإنشاء تُسقَط — لا وجود لها في v2).
    op.execute(
        "INSERT INTO quiz_answers (question_id, student_id, raw, auto_score, "
        "manual_score, teacher_confirmed, submitted_at) "
        "SELECT quiz_question_id, student_id, raw, auto_score, manual_score, "
        "teacher_confirmed, submitted_at FROM answers WHERE quiz_question_id IS NOT NULL")

    # حذف الجدول الموحّد وفهارسه.
    op.drop_index("uq_answer_essay", table_name="answers")
    op.drop_index("uq_answer_analysis", table_name="answers")
    op.drop_index("uq_answer_quiz", table_name="answers")
    with op.batch_alter_table("answers", schema=None) as b:
        b.drop_index(b.f("ix_answers_essay_id"))
        b.drop_index(b.f("ix_answers_analysis_question_id"))
        b.drop_index(b.f("ix_answers_quiz_question_id"))
        b.drop_index(b.f("ix_answers_student_id"))
    op.drop_table("answers")
