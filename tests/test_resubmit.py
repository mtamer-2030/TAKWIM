"""اختبار ح-٥ (المرحلة ٣-ج): رفض التسليم مرّتين في الجلسة نفسها.

يستدعي مسار submit_quiz مباشرةً على قاعدة في الذاكرة، بمشاركٍ سبق أن سلّم
(part.submitted_at مضبوط)، ويتوقّع 403 بلا كتابة جواب جديد.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import FormData

from app.models import (Answer, Base, Level, Quiz, QuizQuestion, QuizSession,
                        SessionStudent, Student)
from app.v2.web import STUDENT_COOKIE, issue_student_cookie


class _Req:
    """طلب مصغّر: كوكيز موقَّعة + form() لامتزامنة فارغة."""
    def __init__(self, cookies):
        self.cookies = cookies
    async def form(self):
        return FormData([])


def test_submit_rejected_when_already_submitted(monkeypatch):
    from app.v2 import student as student_mod

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        # يستعمل المسار AsyncSessionLocal من وحدة student — نوجّهها لقاعدة الذاكرة.
        monkeypatch.setattr(student_mod, "AsyncSessionLocal", S)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            st = Student(full_name="ت", level_id=lv.id, group_name="TC1"); s.add(st); await s.flush()
            quiz = Quiz(title="ق", kind="exercise", published=False); s.add(quiz); await s.flush()
            s.add(QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="س",
                               payload={"options": ["a", "b"], "correct": 0},
                               max_score=2, position=0))
            sess = QuizSession(quiz_id=quiz.id, group_name="TC1", status="open")
            s.add(sess); await s.flush()
            # مشاركٌ حاضر سبق أن سلّم.
            s.add(SessionStudent(session_id=sess.id, student_id=st.id, present=True,
                                 submitted_at=datetime.now()))
            await s.commit()
            sid, qid = st.id, quiz.id

        req = _Req({STUDENT_COOKIE: issue_student_cookie(sid)})
        resp = await student_mod.submit_quiz(req, qid)

        # لم يُكتَب أيّ جواب جديد رغم محاولة التسليم الثانية.
        async with S() as s:
            from sqlalchemy import func, select
            count = await s.scalar(select(func.count()).select_from(Answer))
        await eng.dispose()
        return resp, count

    resp, count = asyncio.run(run())
    assert resp.status_code == 403
    assert "سلّمت" in resp.body.decode("utf-8")
    assert count == 0        # التسليم الثاني مرفوض — لا جواب مكتوب
