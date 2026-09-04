"""طبقة قاعدة البيانات — SQLite ملفّ واحد، بلا ORM (CLAUDE.md §5)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .constants import DIAGNOSTIC_SEED
from .settings import DATA_DIR, DB_PATH

_SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """اتصال لكل طلب، يُغلق حتماً."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """معاملة صريحة: BEGIN ... COMMIT/ROLLBACK."""
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def init_db() -> None:
    """ينشئ المخطّط والبيانات البذرية إن لم تكن موجودة."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
        # الأستاذ الافتراضي (id=1).
        conn.execute(
            "INSERT OR IGNORE INTO teachers (id, label) VALUES (1, 'الأستاذ')"
        )
        # بذر لائحة رموز الأخطاء المشخِّصة (تبقى قابلة للتوسيع).
        for code, label in DIAGNOSTIC_SEED.items():
            conn.execute(
                "INSERT OR IGNORE INTO error_codes (code, label) VALUES (?, ?)",
                (code, label),
            )
        # ترحيل خفيف: إضافة massar_id لقواعد أُنشئت قبل دعم أرقام مسار،
        # ثمّ إنشاء الفهرس الفريد (بعد ضمان وجود العمود، للقواعد القديمة والجديدة).
        cols = [r[1] for r in conn.execute("PRAGMA table_info(students)")]
        if "massar_id" not in cols:
            conn.execute("ALTER TABLE students ADD COLUMN massar_id TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_students_massar "
            "ON students(massar_id) WHERE massar_id IS NOT NULL"
        )
    finally:
        conn.close()


def login_code_exists(conn: sqlite3.Connection, code: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM students WHERE login_code = ?", (code,)
    ).fetchone()
    return row is not None
