"""تهيئة الاختبارات: توجيه البيانات إلى مجلّد مؤقّت قبل تحميل التطبيق."""

import os
import tempfile

# يجب ضبط البيئة قبل استيراد app.settings/app.db (تُقرأ عند الاستيراد).
_TMP = tempfile.mkdtemp(prefix="mihakk-test-")
os.environ.setdefault("MIHAKK_DATA_DIR", _TMP)
os.environ.setdefault("MIHAKK_BACKUP_DIR", os.path.join(_TMP, "backups"))
