"""المتابعة الآنية: تقدّم كلّ تلميذ (كم سؤالاً أجاب وأين وقف) من مسوّدات أجوبته."""

import asyncio
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (Answer, Base, Level, Quiz, QuizQuestion, QuizSession,
                        SessionStudent, Student)
from app.v2.web import ADMIN_COOKIE, issue_admin_token


def _admin_req():
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, query_params={})


def test_live_status_reports_per_student_progress(monkeypatch):
    from app.v2.routers import sessions as admin_mod
    cap = {}

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: cap.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            a = Student(full_name="أحمد", level_id=lv.id, group_name="TC1")
            b = Student(full_name="بشرى", level_id=lv.id, group_name="TC1")
            s.add_all([a, b]); await s.flush()
            quiz = Quiz(title="ق", kind="exercise"); s.add(quiz); await s.flush()
            # عنوان (لا يُعدّ) + ٤ أسئلة قابلة للإجابة
            head = QuizQuestion(quiz_id=quiz.id, qtype="heading", prompt="قسم", payload={}, position=0)
            qs = [QuizQuestion(quiz_id=quiz.id, qtype="long_text", prompt=f"س{i}",
                              payload={}, max_score=4, position=i) for i in range(1, 5)]
            s.add(head); s.add_all(qs); await s.flush()
            sess = QuizSession(quiz_id=quiz.id, group_name="TC1", status="open")
            s.add(sess); await s.flush()
            s.add_all([SessionStudent(session_id=sess.id, student_id=a.id, present=True),
                       SessionStudent(session_id=sess.id, student_id=b.id, present=True)])
            # أحمد أجاب على أوّل سؤالين؛ بشرى أجابت على ٣ (وصلت للسؤال الثالث)
            s.add_all([Answer(quiz_question_id=qs[0].id, student_id=a.id, raw={"text": "x"}),
                       Answer(quiz_question_id=qs[1].id, student_id=a.id, raw={"text": "y"}),
                       Answer(quiz_question_id=qs[0].id, student_id=b.id, raw={"text": "x"}),
                       Answer(quiz_question_id=qs[1].id, student_id=b.id, raw={"text": "y"}),
                       Answer(quiz_question_id=qs[2].id, student_id=b.id, raw={"text": "z"})])
            await s.commit()
            sid = sess.id
        await admin_mod.session_live_status(_admin_req(), sid)
        await eng.dispose()

    asyncio.run(run())
    assert cap["name"] == "admin/_session_live_status.html"
    assert cap["total_q"] == 4                         # العنوان لا يُعدّ
    by = {r["name"]: r for r in cap["rows"]}
    assert by["أحمد"]["done"] == 2 and by["أحمد"]["last"] == 2 and by["أحمد"]["pct"] == 50
    assert by["بشرى"]["done"] == 3 and by["بشرى"]["last"] == 3 and by["بشرى"]["pct"] == 75
