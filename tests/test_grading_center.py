"""قسم التصحيح: يسرد التقاويم ذات الأجوبة المُسلَّمة مع عدّ المنتظِر للمصادقة."""

import asyncio
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Answer, Base, Level, Quiz, QuizQuestion, Student
from app.v2.web import ADMIN_COOKIE, issue_admin_token


def _req():
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, query_params={})


def test_grading_center_lists_pending(monkeypatch):
    from app.v2 import admin as admin_mod
    captured = {}

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: captured.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            st = Student(full_name="ت", level_id=lv.id, group_name="TC1"); s.add(st); await s.flush()
            quiz = Quiz(title="فرض", kind="exam", level_id=lv.id, group_name="TC1")
            s.add(quiz); await s.flush()
            qq = QuizQuestion(quiz_id=quiz.id, qtype="long_text", prompt="س",
                              payload={}, max_score=4, position=0); s.add(qq); await s.flush()
            # جوابان مُسلَّمان: واحد مصادَق، وواحد ينتظر.
            s.add(Answer(quiz_question_id=qq.id, student_id=st.id, raw={"text": "a"},
                         submitted=True, teacher_confirmed=True))
            st2 = Student(full_name="ت٢", level_id=lv.id, group_name="TC1"); s.add(st2); await s.flush()
            s.add(Answer(quiz_question_id=qq.id, student_id=st2.id, raw={"text": "b"},
                         submitted=True, teacher_confirmed=False))
            await s.commit()
        await admin_mod.grading_center(_req())
        await eng.dispose()

    asyncio.run(run())
    assert captured["name"] == "admin/grading.html"
    assert len(captured["items"]) == 1
    it = captured["items"][0]
    assert it["submitted"] == 2 and it["pending"] == 1 and it["title"] == "فرض"
