"""اختبار الحفظ التدريجيّ (ح-٦، المرحلة ٥-أ): تُحفَظ المسوّدة بلا تصحيح ولا
مصادقة أثناء الجلسة، وتُحدَّث، ولا تُحفَظ بعد التسليم. + فحص تركيب قالب الاستئناف.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import FormData

from app.models import (Answer, Base, Level, Quiz, QuizQuestion, QuizSession,
                        SessionStudent, Student)
from app.v2.web import STUDENT_COOKIE, issue_student_cookie


class _Req:
    def __init__(self, cookies, form):
        self.cookies = cookies
        self._form = form
    async def form(self):
        return self._form


async def _seed(S, *, submitted=False):
    async with S() as s:
        lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
        st = Student(full_name="ت", level_id=lv.id, group_name="TC1"); s.add(st); await s.flush()
        quiz = Quiz(title="ق", kind="exercise", published=False); s.add(quiz); await s.flush()
        qq = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="س",
                          payload={"options": ["a", "b", "c"], "correct": 0},
                          max_score=2, position=0)
        s.add(qq); await s.flush()
        sess = QuizSession(quiz_id=quiz.id, group_name="TC1", status="open")
        s.add(sess); await s.flush()
        s.add(SessionStudent(session_id=sess.id, student_id=st.id, present=True,
                             submitted_at=datetime.now() if submitted else None))
        await s.commit()
        return st.id, quiz.id, qq.id


def test_draft_saved_without_scoring_then_updated(monkeypatch):
    from app.v2 import student as student_mod

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(student_mod, "AsyncSessionLocal", S)
        sid, qid, qqid = await _seed(S)
        cookie = {STUDENT_COOKIE: issue_student_cookie(sid)}

        # حفظ أوّل: اختيار البديل 1
        r1 = await student_mod.save_quiz_draft(
            _Req(cookie, FormData([(f"q_{qqid}", "1")])), qid)
        # حفظ ثانٍ: غيّر إلى البديل 2
        r2 = await student_mod.save_quiz_draft(
            _Req(cookie, FormData([(f"q_{qqid}", "2")])), qid)

        async with S() as s:
            ans = (await s.execute(select(Answer))).scalars().all()
        await eng.dispose()
        return r1, r2, ans

    r1, r2, ans = asyncio.run(run())
    assert r1.status_code == 200 and "حُفظ" in r1.body.decode("utf-8")
    assert len(ans) == 1                       # سطر واحد يُحدَّث لا يتكرّر
    assert ans[0].raw == {"choice": 2}         # آخر مسوّدة
    assert ans[0].auto_score is None           # بلا تصحيح
    assert ans[0].teacher_confirmed is False   # بلا مصادقة → لا يدخل التقارير


def test_no_draft_after_submitted(monkeypatch):
    from app.v2 import student as student_mod

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(student_mod, "AsyncSessionLocal", S)
        sid, qid, qqid = await _seed(S, submitted=True)
        cookie = {STUDENT_COOKIE: issue_student_cookie(sid)}
        resp = await student_mod.save_quiz_draft(
            _Req(cookie, FormData([(f"q_{qqid}", "1")])), qid)
        async with S() as s:
            count = await s.scalar(select(func.count()).select_from(Answer))
        await eng.dispose()
        return resp, count

    resp, count = asyncio.run(run())
    assert resp.status_code == 204          # لا حفظ بعد التسليم
    assert count == 0


def test_quiz_template_compiles():
    """قالب الاستئناف (prefill + autosave) يُترجَم بلا خطأ تركيبيّ في Jinja."""
    from app.v2.web import templates
    templates.env.get_template("student/quiz.html")
