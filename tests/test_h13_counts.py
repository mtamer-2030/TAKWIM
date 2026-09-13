"""ح-١٣: المسوّدة لا تُعدّ إنجازاً ولا تنفخ العدّادات، والحفظ التدريجيّ لا يُنشئ
سطراً لسؤال فارغ لم يُلمس.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import FormData

from app.models import (Answer, Base, Level, Quiz, QuizQuestion, QuizSession,
                        SessionStudent, Student)
from app.v2.student import _raw_is_empty
from app.v2.web import STUDENT_COOKIE, issue_student_cookie


class _Req:
    def __init__(self, cookies, form):
        self.cookies = cookies
        self._form = form
    async def form(self):
        return self._form


def test_raw_is_empty_per_type():
    assert _raw_is_empty(None) and _raw_is_empty({})
    assert _raw_is_empty({"choices": []}) and not _raw_is_empty({"choices": [0]})
    assert _raw_is_empty({"choice": None}) and not _raw_is_empty({"choice": 1})
    assert _raw_is_empty({"text": "   "}) and not _raw_is_empty({"text": "x"})
    assert _raw_is_empty({"assignments": [None, None]}) and not _raw_is_empty({"assignments": [None, 1]})
    assert _raw_is_empty({"order": [-1, -1]}) and not _raw_is_empty({"order": [-1, 0]})
    assert _raw_is_empty({"cells": [["", " "]]}) and not _raw_is_empty({"cells": [["", "x"]]})


async def _seed(S):
    async with S() as s:
        lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
        st = Student(full_name="ت", level_id=lv.id, group_name="TC1"); s.add(st); await s.flush()
        quiz = Quiz(title="ق", kind="exercise", published=False); s.add(quiz); await s.flush()
        qq = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="س",
                          payload={"options": ["a", "b"]}, max_score=2, position=0)
        s.add(qq); await s.flush()
        sess = QuizSession(quiz_id=quiz.id, group_name="TC1", status="open")
        s.add(sess); await s.flush()
        s.add(SessionStudent(session_id=sess.id, student_id=st.id, present=True))
        await s.commit()
        return st.id, quiz.id, qq.id


def test_empty_draft_creates_no_row_and_draft_not_counted(monkeypatch):
    from app.v2 import student as student_mod

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(student_mod, "AsyncSessionLocal", S)
        sid, qid, qqid = await _seed(S)
        cookie = {STUDENT_COOKIE: issue_student_cookie(sid)}

        # (أ) حفظ نموذج فارغ تماماً → لا سطر
        await student_mod.save_quiz_draft(_Req(cookie, FormData([])), qid)
        async with S() as s:
            n_empty = await s.scalar(select(func.count()).select_from(Answer))

        # (ب) حفظ مسوّدة حقيقية → سطر submitted=False
        await student_mod.save_quiz_draft(_Req(cookie, FormData([(f"q_{qqid}", "1")])), qid)
        async with S() as s:
            rows = (await s.execute(select(Answer))).scalars().all()
            submitted_count = await s.scalar(
                select(func.count()).select_from(Answer).where(Answer.submitted.is_(True)))
        await eng.dispose()
        return n_empty, rows, submitted_count

    n_empty, rows, submitted_count = asyncio.run(run())
    assert n_empty == 0                       # الفارغ لم يُنشئ سطراً
    assert len(rows) == 1 and rows[0].submitted is False   # المسوّدة سطر غير مُسلَّم
    assert submitted_count == 0               # لا تُعدّ المسوّدة في عدّاد المُسلَّم
