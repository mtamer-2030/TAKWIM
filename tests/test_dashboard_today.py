"""٤-ب: لوحة الأستاذ تعرض «يومك الدراسيّ» بدل توزيع المستويات التجريبيّ.

تتحقّق من العدّادات الفعليّة: جلسات اليوم، أجوبة تنتظر التصحيح (سُلِّمت ولم تُصادَق)،
حاضرون لم يُسلّموا، وقائمة الجلسات المفتوحة.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (Answer, Base, Level, Quiz, QuizQuestion, QuizSession,
                        SessionStudent, Student)
from app.v2.web import ADMIN_COOKIE, issue_admin_token


def _req():
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, query_params={})


def test_dashboard_today_metrics(monkeypatch):
    from app.v2 import admin as admin_mod

    captured = {}

    def fake_tr(name, ctx):
        captured.update(ctx)
        return SimpleNamespace(status_code=200)

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse", fake_tr)
        # عنوان الشبكة لا يهمّ هنا.
        monkeypatch.setattr(admin_mod, "lan_url", lambda *_: None)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            st1 = Student(full_name="أ", level_id=lv.id, group_name="TC1")
            st2 = Student(full_name="ب", level_id=lv.id, group_name="TC1")
            s.add_all([st1, st2]); await s.flush()
            quiz = Quiz(title="ت", kind="exercise", level_id=lv.id); s.add(quiz); await s.flush()
            qq = QuizQuestion(quiz_id=quiz.id, qtype="long_text", prompt="س",
                              payload={}, max_score=4, position=0); s.add(qq); await s.flush()
            # جلسة مفتوحة فُتِحت الآن (تُحتسب في «جلسات اليوم»).
            sess = QuizSession(quiz_id=quiz.id, group_name="TC1", status="open",
                               opened_at=datetime.now()); s.add(sess); await s.flush()
            # حاضران: واحد سلّم، وواحد لم يُسلّم.
            s.add(SessionStudent(session_id=sess.id, student_id=st1.id, present=True,
                                 submitted_at=datetime.now()))
            s.add(SessionStudent(session_id=sess.id, student_id=st2.id, present=True,
                                 submitted_at=None))
            # جواب سُلِّم نهائياً ولم يُصادَق عليه بعد → تنتظر التصحيح.
            s.add(Answer(quiz_question_id=qq.id, student_id=st1.id, raw={"text": "x"},
                         submitted=True, teacher_confirmed=False))
            await s.commit()
        resp = await admin_mod.dashboard(_req())
        await eng.dispose()
        return resp

    resp = asyncio.run(run())
    assert resp.status_code == 200
    assert captured["today"]["sessions"] == 1
    assert captured["today"]["ungraded"] == 1
    assert captured["today"]["not_submitted"] == 1
    assert len(captured["open_sessions"]) == 1
    o = captured["open_sessions"][0]
    assert o["present"] == 2 and o["subs"] == 1 and o["group"] == "TC1"
