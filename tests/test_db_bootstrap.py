"""اختبار تهيئة القاعدة عبر Alembic حصراً (البند ٢-ب).

يغطّي المسارات: قاعدة جديدة تُبنى من الصفر إلى head؛ وقاعدة قديمة بُنيت بـ
create_all (جداول بلا وسم) تُتبنّى بوسمها ثمّ ترقيتها مع الحفاظ على بياناتها.
"""

import sqlite3
from pathlib import Path

import app.database as dbmod
import app.db_bootstrap as boot
from alembic import command
from alembic.config import Config


def _point_to(tmp_path: Path, monkeypatch) -> Path:
    db = tmp_path / "philotech.db"
    monkeypatch.setattr(dbmod, "DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setattr(dbmod, "DB_PATH", db)
    monkeypatch.setattr(boot, "DB_PATH", db)
    return db


def test_fresh_db_built_to_head(tmp_path, monkeypatch):
    db = _point_to(tmp_path, monkeypatch)
    msg = boot.ensure_head()
    assert "fresh" in msg
    c = sqlite3.connect(str(db))
    try:
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "b8d3f1a25c67"
        assert boot._table_exists(c, "answers")
        assert not boot._table_exists(c, "quiz_answers")   # المخطّط الأحدث
    finally:
        c.close()


def test_legacy_create_all_db_adopted_and_migrated(tmp_path, monkeypatch):
    """قاعدة قديمة بلا وسم (create_all بمخطّط ما قبل توحيد المهارات/الأجوبة) تُتبنّى
    وتُهاجَر إلى head دون فقدان بيانات: quiz_answers→answers، competency→skill_id."""
    db = _point_to(tmp_path, monkeypatch)
    cfg = Config("alembic.ini"); cfg.set_main_option("script_location", "migrations")
    command.upgrade(cfg, "b951428a7459")     # يبني مخطّطاً قديماً
    c = sqlite3.connect(str(db))
    c.execute("INSERT INTO levels (name,code,position) VALUES ('ج','TC',0)")
    c.execute("INSERT INTO students (full_name,level_id,active) VALUES ('s',1,1)")
    c.execute("INSERT INTO quizzes (title,kind,reveal_feedback,published,created_at) "
              "VALUES ('q','exercise',0,1,datetime('now'))")
    c.execute("INSERT INTO quiz_questions (quiz_id,position,qtype,competency,prompt,"
              "payload,max_score,auto_scored) VALUES (1,0,'mcq_single','argumentation','qp','{}',4,1)")
    c.execute("INSERT INTO quiz_answers (question_id,student_id,raw,auto_score,"
              "manual_score,teacher_confirmed,submitted_at) VALUES (1,1,'{}',3,NULL,1,datetime('now'))")
    c.execute("DROP TABLE alembic_version")   # محاكاة قاعدة create_all غير موسومة
    c.commit(); c.close()

    msg = boot.ensure_head()
    assert "adopted legacy DB at b951428a7459" in msg

    c = sqlite3.connect(str(db))
    try:
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "b8d3f1a25c67"
        # الجواب القديم هاجَر إلى الجدول الموحّد.
        assert c.execute("SELECT student_id, quiz_question_id, auto_score FROM answers").fetchall() \
            == [(1, 1, 3.0)]
        assert c.execute("SELECT COUNT(*) FROM skills").fetchone()[0] == 6
        # الكفاية القديمة صارت مهارة عبر skill_id.
        assert c.execute("SELECT s.name FROM quiz_questions q JOIN skills s "
                         "ON s.id=q.skill_id").fetchone()[0] == "البنية الحجاجية"
    finally:
        c.close()
