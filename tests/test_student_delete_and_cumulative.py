"""ميزتان: (١) حذف تلميذٍ مكرّر وكلّ أثره؛ (٢) المعدّل التراكميّ في تقرير التلميذ."""

import asyncio
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (Answer, Base, Level, Quiz, QuizQuestion, QuizSession,
                        SessionStudent, Student, StudentReport)
from app.v2.web import ADMIN_COOKIE, issue_admin_token


def _admin_req():
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, headers={},
                           client=SimpleNamespace(host="9.9.9.9"), query_params={})


def test_student_delete_removes_student_and_all_traces(monkeypatch):
    from app.v2.routers import rosters as admin_mod

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            dup = Student(full_name="مكرّر", level_id=lv.id, group_name="TC1", active=True)
            keep = Student(full_name="سليم", level_id=lv.id, group_name="TC1", active=True)
            s.add_all([dup, keep]); await s.flush()
            quiz = Quiz(title="ت", kind="exam", level_id=lv.id); s.add(quiz); await s.flush()
            q = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="س", payload={},
                             max_score=1, position=0); s.add(q); await s.flush()
            sess = QuizSession(quiz_id=quiz.id, group_name="TC1", status="open"); s.add(sess); await s.flush()
            # للمكرّر: جواب + حضور + تقرير — كلّها يجب أن تُحذف
            s.add(Answer(quiz_question_id=q.id, student_id=dup.id, raw={"choice": 0},
                         auto_score=1, teacher_confirmed=True, submitted=True))
            s.add(SessionStudent(session_id=sess.id, student_id=dup.id, present=True))
            s.add(StudentReport(student_id=dup.id, skill_profile={}, ai_intervention_plan="x"))
            # للسليم: جواب يبقى
            s.add(Answer(quiz_question_id=q.id, student_id=keep.id, raw={"choice": 0},
                         auto_score=1, teacher_confirmed=True, submitted=True))
            await s.commit()
            dup_id, keep_id = dup.id, keep.id
        resp = await admin_mod.student_delete(_admin_req(), dup_id)
        async with S() as s:
            students = {st.full_name for st in (await s.execute(select(Student))).scalars()}
            n_ans = await s.scalar(select(func.count()).select_from(Answer))
            n_ss = await s.scalar(select(func.count()).select_from(SessionStudent))
            n_rep = await s.scalar(select(func.count()).select_from(StudentReport))
        await eng.dispose()
        return resp, students, n_ans, n_ss, n_rep

    resp, students, n_ans, n_ss, n_rep = asyncio.run(go())
    assert getattr(resp, "status_code", None) == 303
    assert "deleted=" in resp.headers["location"]
    assert students == {"سليم"}          # المكرّر حُذف، السليم بقي
    assert n_ans == 1                      # جواب المكرّر حُذف، جواب السليم بقي
    assert n_ss == 0 and n_rep == 0        # حضوره وتقريره حُذفا


def test_student_gradebook_chart_has_cumulative(monkeypatch):
    """تقرير التلميذ يمرّر سلسلةً تراكميّةً (متوسّطٌ جارٍ) مع النسب لكلّ تقويم."""
    from app.v2.routers import reports as admin_mod
    import json
    cap = {}

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: cap.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            st = Student(full_name="أ", level_id=lv.id, group_name="TC1", active=True); s.add(st); await s.flush()
            # تقويمان: 60٪ ثمّ 80٪ → تراكميّ 60 ثمّ 70
            import datetime as dt
            for i, (score, day) in enumerate([(6, 1), (8, 8)]):
                quiz = Quiz(title=f"ف{i}", kind="exam", level_id=lv.id,
                            created_at=dt.datetime(2026, 9, day))
                s.add(quiz); await s.flush()
                q = QuizQuestion(quiz_id=quiz.id, qtype="long_text", prompt="س", payload={},
                                 max_score=10, position=0); s.add(q); await s.flush()
                s.add(Answer(quiz_question_id=q.id, student_id=st.id, raw={"text": "a"},
                             manual_score=score, teacher_confirmed=True, submitted=True))
            await s.commit(); sid = st.id
        await admin_mod.gradebook_student(_admin_req(), sid)
        await eng.dispose()

    asyncio.run(go())
    chart = json.loads(cap["chart"])
    assert chart["values"] == [60.0, 80.0]
    assert chart["cumulative"] == [60.0, 70.0]     # متوسّطٌ جارٍ
