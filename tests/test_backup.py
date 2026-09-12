"""اختبار النسخ الاحتياطي (البند ٢-أ) — معيار «منجَز»: تُؤخذ نسخة على قاعدة فيها
٤٥ جواباً، ثمّ تُفتح النسخة ويُقارن عدد الأجوبة بالأصل، ويُتحقّق من سلامتها
واستقلالها عن الأصل (لقطة لا مقبض حيّ)، ومن التدوير وفحص السلامة.

«إن لم يُختبر الاسترجاع فلا نسخة احتياطية.»
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import backup as backup_mod
from app.models import Answer, Base, Level, Quiz, QuizQuestion, Student


def _seed_source(db_path: Path, n: int = 45) -> None:
    """يبني قاعدة حقيقية (مخطّط v2) فيها n جواباً لـ n تلاميذ على سؤال تقويم واحد."""
    eng = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        lv = Level(name="ج", code="TC"); s.add(lv); s.flush()
        q = Quiz(title="ق", kind="exercise"); s.add(q); s.flush()
        qq = QuizQuestion(quiz_id=q.id, qtype="mcq_single", prompt="س",
                          max_score=4, position=0); s.add(qq); s.flush()
        for i in range(n):
            st = Student(full_name=f"تلميذ {i}", level_id=lv.id); s.add(st); s.flush()
            s.add(Answer(student_id=st.id, quiz_question_id=qq.id,
                         auto_score=2, teacher_confirmed=True))
        s.commit()
    eng.dispose()


def _count_answers(db_path: Path) -> int:
    c = sqlite3.connect(str(db_path))
    try:
        return c.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
    finally:
        c.close()


def test_backup_restores_exact_answer_count(tmp_path, monkeypatch):
    src = tmp_path / "philotech.db"
    _seed_source(src, 45)
    monkeypatch.setattr(backup_mod, "DB_PATH", src)

    dest = tmp_path / "backups"
    out = backup_mod.backup_database(dest_dir=dest)

    # النسخة موجودة، وسليمة، وعدد أجوبتها مطابق للأصل تماماً.
    assert out.exists()
    assert backup_mod._integrity_ok(out)
    assert _count_answers(out) == 45 == _count_answers(src)


def test_backup_is_independent_snapshot(tmp_path, monkeypatch):
    """النسخة لقطة مستقلّة: كتابةٌ في الأصل بعد النسخ لا تغيّر النسخة."""
    src = tmp_path / "philotech.db"
    _seed_source(src, 45)
    monkeypatch.setattr(backup_mod, "DB_PATH", src)
    out = backup_mod.backup_database(dest_dir=tmp_path / "backups")

    # كتابة إضافية في الأصل بعد أخذ النسخة
    c = sqlite3.connect(str(src))
    c.execute("INSERT INTO students (full_name, level_id, active) VALUES ('زائد',1,1)")
    sid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.execute("INSERT INTO answers (student_id, quiz_question_id, teacher_confirmed) "
              "VALUES (?,1,1)", (sid,))
    c.commit(); c.close()

    assert _count_answers(src) == 46      # الأصل تغيّر
    assert _count_answers(out) == 45      # النسخة لم تتغيّر (لقطة مستقلّة)


def test_integrity_check_rejects_corrupt_backup(tmp_path, monkeypatch):
    """إن كانت القاعدة تالفة تُرفض النسخة ولا تبقى (لا نُعلن نجاحاً كاذباً)."""
    src = tmp_path / "philotech.db"
    src.write_bytes(b"SQLite format 3\x00 this is not a valid database")
    monkeypatch.setattr(backup_mod, "DB_PATH", src)
    dest = tmp_path / "backups"
    # sqlite3.backup على ملفّ تالف يفشل → يُرفع خطأ ولا تبقى نسخة نصف مكتوبة.
    try:
        backup_mod.backup_database(dest_dir=dest)
        assert False, "كان يجب أن تفشل النسخة على قاعدة تالفة"
    except Exception:
        pass
    assert list(dest.glob("philotech-*.db")) == []


def test_rotation_keeps_last_14_days(tmp_path, monkeypatch):
    src = tmp_path / "philotech.db"
    _seed_source(src, 1)
    monkeypatch.setattr(backup_mod, "DB_PATH", src)
    dest = tmp_path / "backups"
    dest.mkdir()
    # نسخة قديمة (20 يوماً) يجب أن تُحذف عند التدوير.
    old_stamp = (datetime.now() - timedelta(days=20)).strftime(backup_mod._STAMP_FMT)
    old = dest / f"{backup_mod._PREFIX}{old_stamp}.db"
    old.write_bytes(b"old")

    backup_mod.backup_database(dest_dir=dest, keep_days=14)

    assert not old.exists()                                # القديمة دُوّرت
    assert len(list(dest.glob(f"{backup_mod._PREFIX}*.db"))) == 1   # الجديدة فقط
