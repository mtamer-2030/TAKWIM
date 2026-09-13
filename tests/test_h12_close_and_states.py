"""ح-١٢: إغلاق الجلسة يُسلّم مسوّدات الحاضرين آلياً (المغلقة تُصحَّح وتُصادَق)،
وشاشة التصحيح تُميّز ثلاث حالات: سلّم / مسوّدة لم تُسلَّم / لم يجب.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (Answer, Base, Level, Quiz, QuizQuestion, QuizSession,
                        SessionStudent, Student)
from app.v2.web import ADMIN_COOKIE, issue_admin_token


def _admin_req(**qp):
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, query_params=qp)


def test_close_auto_submits_present_drafts(monkeypatch):
    from app.v2 import admin as admin_mod

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(admin_mod, "try_backup_quiet", lambda *a, **k: None)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            st = Student(full_name="ت", level_id=lv.id, group_name="TC1"); s.add(st); await s.flush()
            quiz = Quiz(title="ق", kind="exercise"); s.add(quiz); await s.flush()
            qq = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="س",
                              payload={"options": ["a", "b", "c"], "correct": 2},
                              max_score=2, position=0)
            s.add(qq); await s.flush()
            sess = QuizSession(quiz_id=quiz.id, group_name="TC1", status="open")
            s.add(sess); await s.flush()
            s.add(SessionStudent(session_id=sess.id, student_id=st.id, present=True))
            # مسوّدة إجابة صحيحة، غير مُسلَّمة
            s.add(Answer(quiz_question_id=qq.id, student_id=st.id,
                         raw={"choice": 2}, submitted=False))
            await s.commit()
            sid = sess.id

        await admin_mod.session_close(_admin_req(), sid)

        async with S() as s:
            ans = (await s.execute(select(Answer))).scalars().one()
            part = (await s.execute(select(SessionStudent))).scalars().one()
        await eng.dispose()
        return ans, part

    ans, part = asyncio.run(run())
    assert ans.submitted is True                 # سُلّمت المسوّدة آلياً
    assert ans.auto_score == 2                    # المغلقة صُحّحت يقينياً
    assert ans.teacher_confirmed is True          # وصودق عليها
    assert part.submitted_at is not None          # عُلّم المشارك مُسلِّماً


def test_grade_screen_three_states(monkeypatch):
    from app.v2 import admin as admin_mod

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(admin_mod, "ollama_available", lambda: False)
        captured = {}
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda *a, **k: captured.update(ctx=a[1]) or SimpleNamespace(status_code=200))
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            s1 = Student(full_name="سلّم", level_id=lv.id, group_name="TC1")
            s2 = Student(full_name="مسوّدة", level_id=lv.id, group_name="TC1")
            s3 = Student(full_name="غائب الجواب", level_id=lv.id, group_name="TC1")
            s.add_all([s1, s2, s3]); await s.flush()
            quiz = Quiz(title="ق", kind="exercise"); s.add(quiz); await s.flush()
            qq = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="س",
                              payload={"options": ["a", "b"]}, max_score=2, position=0)
            s.add(qq); await s.flush()
            sess = QuizSession(quiz_id=quiz.id, group_name="TC1", status="open")
            s.add(sess); await s.flush()
            for st in (s1, s2, s3):
                s.add(SessionStudent(session_id=sess.id, student_id=st.id, present=True))
            s.add(Answer(quiz_question_id=qq.id, student_id=s1.id, raw={"choice": 0},
                         auto_score=0, submitted=True))       # سلّم
            s.add(Answer(quiz_question_id=qq.id, student_id=s2.id, raw={"choice": 1},
                         submitted=False))                    # مسوّدة لم تُسلَّم
            await s.commit()
            qid = quiz.id

        await admin_mod.quiz_grade_page(_admin_req(), qid)
        await eng.dispose()
        return captured["ctx"]

    ctx = asyncio.run(run())
    item = ctx["questions"][0]
    states = {a["student"].full_name: a["submitted"] for a in item["answers"]}
    assert states["سلّم"] is True                 # الحالة ١
    assert states["مسوّدة"] is False               # الحالة ٢
    assert "غائب الجواب" in item["missing"]         # الحالة ٣ (لم يجب)
