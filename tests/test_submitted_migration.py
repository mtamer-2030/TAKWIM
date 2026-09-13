"""هجرة عمود Answer.submitted (ح-١١/١٢/١٣): تُضيف العمود وتُملأ السطور القائمة —
المُسلَّم فعلاً (مصادَق أو له نقطة آلية) submitted=1، والمسوّدة 0 — وتتراجع نظيفة.
"""

import sqlite3
import tempfile
from pathlib import Path

import app.database as dbmod

PREV = "b8d3f1a25c67"
HEAD = "c9a1e07b4d52"


def _cfg():
    from alembic.config import Config
    c = Config("alembic.ini"); c.set_main_option("script_location", "migrations")
    return c


def test_submitted_column_added_backfilled_and_reversible(monkeypatch):
    from alembic import command
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    monkeypatch.setattr(dbmod, "DATABASE_URL", f"sqlite+aiosqlite:///{tmp}")
    cfg = _cfg()
    command.upgrade(cfg, PREV)

    c = sqlite3.connect(str(tmp))
    c.execute("INSERT INTO levels (name,code,position) VALUES ('ج','TC',0)")
    c.execute("INSERT INTO quizzes (title,kind,reveal_feedback,published,created_at) "
              "VALUES ('q','exercise',1,0,datetime('now'))")
    c.execute("INSERT INTO quiz_questions (quiz_id,position,qtype,prompt,payload,"
              "max_score,auto_scored) VALUES (1,0,'mcq_single','p','{}',4,1)")
    c.execute("INSERT INTO students (full_name,level_id,active) VALUES ('s1',1,1)")
    c.execute("INSERT INTO students (full_name,level_id,active) VALUES ('s2',1,1)")
    # مُسلَّم فعلاً (له نقطة آلية) / مسوّدة (بلا نقطة، غير مصادَق)
    c.execute("INSERT INTO answers (student_id,quiz_question_id,auto_score,"
              "teacher_confirmed,submitted_at) VALUES (1,1,2,1,datetime('now'))")
    c.execute("INSERT INTO answers (student_id,quiz_question_id,auto_score,"
              "teacher_confirmed,submitted_at) VALUES (2,1,NULL,0,datetime('now'))")
    c.commit(); c.close()

    command.upgrade(cfg, HEAD)
    c = sqlite3.connect(str(tmp))
    assert "submitted" in {r[1] for r in c.execute("PRAGMA table_info(answers)")}
    rows = dict(c.execute("SELECT student_id, submitted FROM answers"))
    assert rows[1] == 1        # المُسلَّم فعلاً
    assert rows[2] == 0        # المسوّدة
    c.close()

    command.downgrade(cfg, PREV)
    c = sqlite3.connect(str(tmp))
    assert "submitted" not in {r[1] for r in c.execute("PRAGMA table_info(answers)")}
    c.close()
