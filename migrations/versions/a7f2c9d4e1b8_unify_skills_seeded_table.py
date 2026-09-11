"""unify skills into a seeded skills table (ROADMAP 1-ج)

توحيد المهارات الستّ عبر المستويات الثلاثة في جدول skills مبذور (بدل Enum):
- ينشئ جدول skills ويبذر المهارات الستّ الموحّدة.
- يحوّل analysis_questions.target_skill (Enum) → skill_id (FK) مع ترحيل القيم.
- يحوّل quiz_questions.competency (String) → skill_id (FK) مع ترحيل الكفايات.

Revision ID: a7f2c9d4e1b8
Revises: b951428a7459
Create Date: 2026-09-11
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7f2c9d4e1b8"
down_revision: Union[str, Sequence[str], None] = "b951428a7459"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# المهارات الستّ الموحّدة (مصدر واحد للحقيقة = app.constants.SKILLS).
SKILLS = [
    "صياغة الإشكال",
    "البنية المفاهيمية",
    "الأطروحة",
    "البنية الحجاجية",
    "المناقشة",
    "التركيب",
]

# قيمة Enum القديمة (target_skill) → اسم المهارة الجديد.
TARGET_SKILL_TO_NAME = {
    "إشكال": "صياغة الإشكال",
    "مفاهيم": "البنية المفاهيمية",
    "أطروحة": "الأطروحة",
    "بنية حجاجية": "البنية الحجاجية",
    "استنتاج": "التركيب",
}

# كفاية v1 (competency) → اسم المهارة الجديد.
COMPETENCY_TO_NAME = {
    "problematization": "صياغة الإشكال",
    "conceptualization": "البنية المفاهيمية",
    "argumentation": "البنية الحجاجية",
    "synthesis": "التركيب",
    "knowledge": "الأطروحة",
}


def upgrade() -> None:
    # 1) جدول المهارات + بذره.
    op.create_table(
        "skills",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.bulk_insert(
        sa.table(
            "skills",
            sa.column("name", sa.String),
            sa.column("position", sa.Integer),
        ),
        [{"name": n, "position": i} for i, n in enumerate(SKILLS)],
    )

    # 2) analysis_questions: أضف skill_id، رحّل من target_skill، ثمّ احذف العمود القديم.
    with op.batch_alter_table("analysis_questions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("skill_id", sa.Integer(), nullable=True))

    for value, name in TARGET_SKILL_TO_NAME.items():
        op.execute(
            sa.text(
                "UPDATE analysis_questions SET skill_id = "
                "(SELECT id FROM skills WHERE name = :name) "
                "WHERE target_skill = :value"
            ).bindparams(name=name, value=value)
        )

    with op.batch_alter_table("analysis_questions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_analysis_questions_skill_id"), ["skill_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_analysis_questions_skill_id", "skills",
            ["skill_id"], ["id"], ondelete="SET NULL")
        batch_op.drop_column("target_skill")

    # 3) quiz_questions: أضف skill_id، رحّل من competency، ثمّ احذف العمود القديم.
    with op.batch_alter_table("quiz_questions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("skill_id", sa.Integer(), nullable=True))

    for value, name in COMPETENCY_TO_NAME.items():
        op.execute(
            sa.text(
                "UPDATE quiz_questions SET skill_id = "
                "(SELECT id FROM skills WHERE name = :name) "
                "WHERE competency = :value"
            ).bindparams(name=name, value=value)
        )

    with op.batch_alter_table("quiz_questions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_quiz_questions_skill_id"), ["skill_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_quiz_questions_skill_id", "skills",
            ["skill_id"], ["id"], ondelete="SET NULL")
        batch_op.drop_column("competency")


def downgrade() -> None:
    # 3←) quiz_questions: أعد competency، رحّل عكسياً، ثمّ احذف skill_id.
    name_to_competency = {v: k for k, v in COMPETENCY_TO_NAME.items()}
    with op.batch_alter_table("quiz_questions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("competency", sa.String(length=30), nullable=True))
    for name, comp in name_to_competency.items():
        op.execute(
            sa.text(
                "UPDATE quiz_questions SET competency = :comp WHERE skill_id = "
                "(SELECT id FROM skills WHERE name = :name)"
            ).bindparams(comp=comp, name=name)
        )
    with op.batch_alter_table("quiz_questions", schema=None) as batch_op:
        batch_op.drop_constraint("fk_quiz_questions_skill_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_quiz_questions_skill_id"))
        batch_op.drop_column("skill_id")

    # 2←) analysis_questions: أعد target_skill، رحّل عكسياً، ثمّ احذف skill_id.
    name_to_target = {v: k for k, v in TARGET_SKILL_TO_NAME.items()}
    enum_type = sa.Enum(*TARGET_SKILL_TO_NAME.keys(),
                        name="target_skill_enum", native_enum=False)
    with op.batch_alter_table("analysis_questions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("target_skill", enum_type, nullable=True))
    for name, value in name_to_target.items():
        op.execute(
            sa.text(
                "UPDATE analysis_questions SET target_skill = :value WHERE skill_id = "
                "(SELECT id FROM skills WHERE name = :name)"
            ).bindparams(value=value, name=name)
        )
    with op.batch_alter_table("analysis_questions", schema=None) as batch_op:
        batch_op.drop_constraint("fk_analysis_questions_skill_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_analysis_questions_skill_id"))
        batch_op.drop_column("skill_id")

    op.drop_table("skills")
