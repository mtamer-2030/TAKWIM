"""add Answer.submitted flag (draft vs final submission) — ح-١١/١٢/١٣

يميّز المسوّدة (حفظ تدريجيّ) من التسليم النهائيّ. تُملأ السطور القائمة:
submitted=1 حيث teacher_confirmed أو auto_score IS NOT NULL (مرّ بتسليم فعليّ)،
وإلّا 0 (مسوّدة).

Revision ID: c9a1e07b4d52
Revises: b8d3f1a25c67
Create Date: 2026-09-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9a1e07b4d52"
down_revision: Union[str, Sequence[str], None] = "b8d3f1a25c67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("answers", schema=None) as b:
        b.add_column(sa.Column("submitted", sa.Boolean(), nullable=False,
                               server_default=sa.text("0")))
    # ملء السطور القائمة: ما مرّ بتسليم فعليّ (مصادَق أو له نقطة آلية) يُعدّ مُسلَّماً.
    op.execute(
        "UPDATE answers SET submitted = 1 "
        "WHERE teacher_confirmed = 1 OR auto_score IS NOT NULL")


def downgrade() -> None:
    with op.batch_alter_table("answers", schema=None) as b:
        b.drop_column("submitted")
