"""ح-١١: التقويم المنزليّ محاولة واحدة كالفرض — التسليم الثاني يُرفَض (403) بلا
كتابة فوق المحاولة الأولى.
"""

import asyncio
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import FormData

from app.models import Answer, Base, Level, Quiz, QuizQuestion, Student
from app.v2.web import STUDENT_COOKIE, issue_student_cookie


class _Req:
    def __init__(self, cookies, form):
        self.cookies = cookies
        self._form = form
    async def form(self):
        return self._form


def test_homework_second_submit_rejected(monkeypatch):
    from app.v2 import student as student_mod

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(student_mod, "AsyncSessionLocal", S)
        # تفادي تصيير قالب النتيجة عند التسليم الأوّل.
        monkeypatch.setattr(student_mod.templates, "TemplateResponse",
                            lambda *a, **k: SimpleNamespace(status_code=200))
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            st = Student(full_name="ت", level_id=lv.id, group_name="TC1"); s.add(st); await s.flush()
            # واجب منزليّ منشور لمستوى التلميذ (لا جلسة)
            quiz = Quiz(title="واجب", kind="exercise", published=True, level_id=lv.id)
            s.add(quiz); await s.flush()
            qq = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="س",
                              payload={"options": ["a", "b", "c"], "correct": 2},
                              max_score=2, position=0)
            s.add(qq); await s.flush()
            await s.commit()
            sid, qid, qqid = st.id, quiz.id, qq.id

        cookie = {STUDENT_COOKIE: issue_student_cookie(sid)}
        r1 = await student_mod.submit_quiz(_Req(cookie, FormData([(f"q_{qqid}", "2")])), qid)
        # محاولة ثانية بإجابة مختلفة — يجب أن تُرفَض ولا تُكتب.
        r2 = await student_mod.submit_quiz(_Req(cookie, FormData([(f"q_{qqid}", "0")])), qid)

        async with S() as s:
            n = await s.scalar(select(func.count()).select_from(Answer))
            ans = (await s.execute(select(Answer))).scalars().first()
        await eng.dispose()
        return r1, r2, n, ans

    r1, r2, n, ans = asyncio.run(run())
    assert r1.status_code == 200                 # التسليم الأوّل نجح
    assert r2.status_code == 403                  # الثاني مرفوض
    assert "سلّمت" in r2.body.decode("utf-8")
    assert n == 1 and ans.submitted is True       # لا تكرار ولا كتابة فوق
    assert ans.raw == {"choice": 2}               # بقيت إجابة المحاولة الأولى
