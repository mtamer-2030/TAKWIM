"""رادعُ الغشّ: عدّاد مغادرات الشاشة (في الذاكرة) يُبلَّغ من واجهة التلميذ ويظهر
للأستاذ في المتابعة الآنية. بلا مخطّط، وبلا أثرٍ على النتائج."""

import asyncio
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import proctor
from app.models import (Base, Level, Quiz, QuizQuestion, QuizSession,
                        SessionStudent, Student)
from app.v2.web import ADMIN_COOKIE, STUDENT_COOKIE, issue_admin_token, issue_student_cookie


def test_proctor_store_counts_and_filters():
    proctor.reset()
    assert proctor.count(1, 10) == 0
    assert proctor.record(1, 10) == 1
    assert proctor.record(1, 10) == 2
    proctor.record(1, 11)
    proctor.record(2, 10)               # جلسةٌ أخرى — لا تُخلَط
    assert proctor.count(1, 10) == 2
    assert proctor.for_session(1) == {10: 2, 11: 1}
    proctor.reset_session(1)
    assert proctor.for_session(1) == {}
    assert proctor.count(2, 10) == 1    # الجلسة الأخرى سليمة
    proctor.reset()


def _student_req(sid):
    return SimpleNamespace(cookies={STUDENT_COOKIE: issue_student_cookie(sid)},
                           headers={}, client=SimpleNamespace(host="9.9.9.9"), query_params={})


def _admin_req():
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, headers={},
                           client=SimpleNamespace(host="9.9.9.9"), query_params={})


def test_leave_endpoint_records_and_shows_in_live(monkeypatch):
    proctor.reset()
    from app.v2 import student as student_mod
    from app.v2.routers import sessions as sess_mod
    cap = {}

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(student_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(sess_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(sess_mod.templates, "TemplateResponse",
                            lambda name, ctx: cap.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            st = Student(full_name="أ", level_id=lv.id, group_name="TC1", active=True)
            s.add(st); await s.flush()
            quiz = Quiz(title="ت", kind="exam", level_id=lv.id, group_name="TC1")
            s.add(quiz); await s.flush()
            q = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="س",
                             payload={"options": ["a", "b"], "correct": 0},
                             max_score=1, position=0); s.add(q); await s.flush()
            sess = QuizSession(quiz_id=quiz.id, group_name="TC1", status="open")
            s.add(sess); await s.flush()
            s.add(SessionStudent(session_id=sess.id, student_id=st.id, present=True))
            await s.commit()
            sid, stid = sess.id, st.id
        # التلميذ يُبلّغ عن مغادرتين
        r1 = await student_mod.report_leave(_student_req(stid), quiz.id)
        await student_mod.report_leave(_student_req(stid), quiz.id)
        # الأستاذ يفتح المتابعة الآنية
        await sess_mod.session_live_status(_admin_req(), sid)
        await eng.dispose()
        return r1, sid, stid

    r1, sid, stid = asyncio.run(go())
    assert getattr(r1, "status_code", None) == 204        # الإبلاغ بلا محتوى
    assert proctor.count(sid, stid) == 2                   # سُجّلت مغادرتان
    row = next(r for r in cap["rows"] if r["p"].student_id == stid)
    assert row["leaves"] == 2                              # يظهر للأستاذ في الصفّ
    proctor.reset()
