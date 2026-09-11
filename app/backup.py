"""نسخ احتياطي متّسق لقاعدة v2 (SQLite + WAL) عبر واجهة sqlite3.backup.

مُنقَذ من منطق v1 (كان في routes/teacher.py::backup_db) وموجَّه إلى قاعدة v2
(data/philotech.db). النسخ عبر src.backup(dst) — لا نسخ ملفّ حيّ — فتكون النسخة
متّسقة رغم وضع WAL. عند أي فشل يُحذف الناتج حتى لا يبقى ملفّ نصف مكتوب.

لا يُربط بأي مسار هنا؛ الزرّ في /admin والتدوير وفحص السلامة (PRAGMA
integrity_check) والنسخ التلقائي عند إغلاق الجلسة — كلّها في البند ٢-أ.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .database import DB_PATH


def backup_database(dest_dir: Path | None = None) -> Path:
    """ينشئ نسخة احتياطية متّسقة من قاعدة v2 ويعيد مسارها.

    يحذف الملفّ الناتج إن فشل النسخ (لا نُبقي نسخة نصف مكتوبة).
    """
    dest_dir = dest_dir or (DB_PATH.parent / "backups")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = dest_dir / f"philotech-{stamp}.db"

    src = sqlite3.connect(str(DB_PATH))
    dst = sqlite3.connect(str(target))
    try:
        with dst:
            src.backup(dst)              # نسخة متّسقة تراعي WAL
    except Exception:
        target.unlink(missing_ok=True)   # لا نُبقي ملفّاً نصف مكتوب
        raise
    finally:
        src.close()
        dst.close()
    return target


def backup_bytes() -> tuple[str, bytes]:
    """يعيد (اسم الملفّ، بايتاته) لتنزيله عبر المتصفّح — يُستعمل في البند ٢-أ."""
    path = backup_database()
    return path.name, path.read_bytes()
