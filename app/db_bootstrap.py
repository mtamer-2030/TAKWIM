"""تهيئة قاعدة البيانات عبر Alembic حصراً (البند ٢-ب — يعالج ح-٤: انحراف المخطّط).

بدل ``Base.metadata.create_all`` (الذي يُنشئ الغائب فقط ولا يهاجر ولا يحذف، فينحرف
المخطّط عن الهجرات)، تصير Alembic مصدرَ الحقيقة الوحيد للمخطّط:

- قاعدة جديدة (لا جداول)         → ``upgrade head`` من الصفر.
- قاعدة موسومة (فيها alembic_version) → ``upgrade head`` (يطبّق المعلّق أو لا شيء).
- قاعدة قديمة بُنيت بـ create_all (جداول بلا وسم) → نستنتج نسختها من مخطّطها،
  نَسِمُها بها (``stamp``)، ثمّ ``upgrade head`` — فتُهاجَر بياناتها إلى الأحدث
  دون فقدان (مثلاً quiz_answers → answers، و target_skill → skill_id).

يُستدعى من دورة حياة التطبيق (main.py) ومن سطر الأوامر (start.bat):  python -m app.db_bootstrap
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from .database import DB_PATH

BASE_DIR = Path(__file__).resolve().parent.parent

# استنتاج نسخة الوسم لقاعدة قديمة بلا alembic_version، من أحدث جدول موجود فيها.
# مرتّبة من الأحدث إلى الأقدم: أوّل تطابق هو النسخة.
_SCHEMA_MARKERS: list[tuple[str, str]] = [
    ("answers", "b8d3f1a25c67"),        # بعد توحيد الأجوبة (١-ب)
    ("skills", "a7f2c9d4e1b8"),          # بعد توحيد المهارات (١-ج)
    ("quiz_sessions", "b951428a7459"),   # بعد الجلسات الصفّية
    ("quizzes", "3523b983b688"),         # بعد نظام التقاويم
    ("levels", "c4e584f84120"),          # المخطّط الأوّلي
]


def _alembic_config() -> Config:
    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    return cfg


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _legacy_revision(conn: sqlite3.Connection) -> str | None:
    """يستنتج نسخة الوسم لقاعدة قديمة بلا alembic_version (أو None إن كانت فارغة)."""
    for table, revision in _SCHEMA_MARKERS:
        if _table_exists(conn, table):
            return revision
    return None


def ensure_head() -> str:
    """يضمن أنّ القاعدة على أحدث مخطّط (head) عبر Alembic. يعيد وصف ما جرى."""
    cfg = _alembic_config()

    # قاعدة غير موجودة أصلاً → بناء كامل من الصفر.
    if not DB_PATH.exists():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        command.upgrade(cfg, "head")
        return "fresh: built from base to head"

    conn = sqlite3.connect(str(DB_PATH))
    try:
        stamped = _table_exists(conn, "alembic_version")
        legacy_rev = None if stamped else _legacy_revision(conn)
    finally:
        conn.close()

    if stamped:
        command.upgrade(cfg, "head")
        return "stamped: upgraded to head"

    if legacy_rev is None:
        # فيها ملفّ لكن بلا جداول مستخدم → عاملها كجديدة.
        command.upgrade(cfg, "head")
        return "empty: built from base to head"

    # قاعدة قديمة بُنيت بـ create_all: سِمْها بنسختها المستنتَجة ثمّ هاجِرها للأحدث.
    command.stamp(cfg, legacy_rev)
    command.upgrade(cfg, "head")
    return f"adopted legacy DB at {legacy_rev}: stamped then upgraded to head"


if __name__ == "__main__":  # pragma: no cover — أمر سطر أوامر
    print(ensure_head())
