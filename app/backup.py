"""نسخ احتياطي متّسق لقاعدة v2 (SQLite + WAL) عبر واجهة sqlite3.backup.

مُنقَذ من منطق v1 وموجَّه إلى قاعدة v2 (data/philotech.db). النسخ عبر
src.backup(dst) — لا نسخ ملفّ حيّ — فتكون النسخة متّسقة رغم وضع WAL وأثناء الكتابة.
بعد الكتابة يُفحَص PRAGMA integrity_check، ولا يُعلَن النجاح إلّا إن مرّ الفحص.
عند أي فشل يُحذف الناتج حتى لا يبقى ملفّ نصف مكتوب.

الاستعمالات (البند ٢-أ):
- زرّ «نسخة احتياطية الآن» في /admin (backup_bytes).
- نسخة تلقائية عند إغلاق كلّ جلسة صفّية (backup_database، تدوير ١٤ يوماً).
- أمر واحد للنسخ إلى مفتاح USB خارج القرص:  python -m app.backup --to E:\\
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .database import DB_PATH

# عدد أيّام الاحتفاظ بالنسخ قبل التدوير (حذف الأقدم).
KEEP_DAYS = 14
_PREFIX = "philotech-"
_STAMP_FMT = "%Y%m%d-%H%M%S"


def _integrity_ok(path: Path) -> bool:
    """يفحص سلامة قاعدة SQLite: يعيد True إن أرجع integrity_check «ok»."""
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row) and row[0] == "ok"
    finally:
        conn.close()


def _rotate(dest_dir: Path, keep_days: int = KEEP_DAYS) -> int:
    """يحذف النسخ الأقدم من keep_days يوماً. يعيد عدد المحذوف."""
    cutoff = datetime.now() - timedelta(days=keep_days)
    removed = 0
    for f in dest_dir.glob(f"{_PREFIX}*.db"):
        stamp = f.stem[len(_PREFIX):]
        try:
            when = datetime.strptime(stamp, _STAMP_FMT)
        except ValueError:
            continue                         # اسم غير متوقّع — لا نلمسه
        if when < cutoff:
            f.unlink(missing_ok=True)
            removed += 1
    return removed


def backup_database(dest_dir: Path | None = None, keep_days: int = KEEP_DAYS) -> Path:
    """ينشئ نسخة احتياطية متّسقة من قاعدة v2 ويعيد مسارها.

    - النسخ عبر sqlite3.backup (متّسق أثناء الكتابة، يراعي WAL).
    - يفحص PRAGMA integrity_check على النسخة؛ إن فشل يحذفها ويرفع RuntimeError.
    - يدوّر النسخ فيُبقي آخر keep_days يوماً.
    """
    dest_dir = dest_dir or (DB_PATH.parent / "backups")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime(_STAMP_FMT)
    target = dest_dir / f"{_PREFIX}{stamp}.db"

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

    if not _integrity_ok(target):
        target.unlink(missing_ok=True)
        raise RuntimeError(f"فشل فحص سلامة النسخة الاحتياطية: {target.name}")

    _rotate(dest_dir, keep_days)
    return target


def backup_bytes() -> tuple[str, bytes]:
    """يعيد (اسم الملفّ، بايتاته) لتنزيله عبر المتصفّح — زرّ /admin."""
    path = backup_database()
    return path.name, path.read_bytes()


def backup_to(dest: Path) -> Path:
    """يأخذ نسخة ثمّ ينسخها إلى مسار خارجي (مفتاح USB مثلاً) ويعيد المسار هناك.

    dest قد يكون مجلّداً (فيُحفظ فيه بالاسم نفسه) أو مسار ملفّ صريح.
    """
    src = backup_database()
    dest = Path(dest)
    if dest.is_dir() or dest.suffix == "":
        dest.mkdir(parents=True, exist_ok=True)
        dest = dest / src.name
    shutil.copy2(src, dest)
    return dest


def try_backup_quiet(dest_dir: Path | None = None) -> Path | None:
    """نسخة «أفضل جهد» لا ترمي استثناءً — للاستدعاء عند إغلاق الجلسة الصفّية.

    فشل النسخ يجب ألّا يمنع إغلاق الجلسة؛ يُبتلَع الخطأ ويُعاد None.
    """
    try:
        return backup_database(dest_dir)
    except Exception:  # noqa: BLE001 — لا نُفشِل إغلاق الجلسة بسبب النسخ
        return None


if __name__ == "__main__":  # pragma: no cover — أمر سطر أوامر
    # الاستعمال: python -m app.backup            → نسخة إلى data/backups
    #            python -m app.backup --to E:\\  → نسخة ثمّ نقلها إلى USB
    if len(sys.argv) >= 3 and sys.argv[1] == "--to":
        out = backup_to(Path(sys.argv[2]))
        print(f"نُسخت القاعدة إلى: {out}")
    else:
        out = backup_database()
        print(f"نسخة احتياطية متّسقة (فُحصت سلامتها): {out}")
