"""ح-١٠: اقتراح الذكاء الاصطناعي لا يمحو تنقيط الأستاذ اليدويّ ولا يمسّ جواباً
مصادَقاً عليه. يفشل قبل الإصلاح (كان يكتب على الكلّ)، وينجح بعده.
"""

import asyncio
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Answer, Base, Level, Quiz, QuizQuestion, Student
from app.v2.web import ADMIN_COOKIE, issue_admin_token


def _req():
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, query_params={})


def test_ai_suggestion_does_not_overwrite_teacher_scores(monkeypatch):
    from app.v2 import admin as admin_mod

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(admin_mod, "ollama_available", lambda: True)
        # المحرّك «يقترح» دائماً 1.0 — لو مسّ المنقَّط يدوياً لتغيّر إلى 1.0.
        monkeypatch.setattr(admin_mod, "suggest_open_score", lambda *a, **k: (1.0, "note"))

        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            quiz = Quiz(title="ق", kind="exercise"); s.add(quiz); await s.flush()
            q1 = QuizQuestion(quiz_id=quiz.id, qtype="long_text", prompt="حلّل",
                              max_score=4, position=0)
            q2 = QuizQuestion(quiz_id=quiz.id, qtype="long_text", prompt="ناقش",
                              max_score=4, position=1)
            q3 = QuizQuestion(quiz_id=quiz.id, qtype="long_text", prompt="ركّب",
                              max_score=4, position=2)
            s.add_all([q1, q2, q3]); await s.flush()
            st = Student(full_name="ت", level_id=lv.id, group_name="TC1")
            s.add(st); await s.flush()
            # (أ) نقّطه الأستاذ يدوياً 3        → يجب أن يبقى 3
            s.add(Answer(quiz_question_id=q1.id, student_id=st.id,
                         raw={"text": "جواب ١"}, manual_score=3.0))
            # (ب) صادق عليه الأستاذ (بلا manual) → يجب ألّا يُمسّ
            s.add(Answer(quiz_question_id=q2.id, student_id=st.id,
                         raw={"text": "جواب ٢"}, teacher_confirmed=True))
            # (ج) غير مصحَّح                     → يأخذ الاقتراح 1.0
            s.add(Answer(quiz_question_id=q3.id, student_id=st.id,
                         raw={"text": "جواب ٣"}))
            await s.commit()
            q1id, q2id, q3id = q1.id, q2.id, q3.id
            qid = quiz.id

        resp = await admin_mod.quiz_grade_ai(_req(), qid)

        async with S() as s:
            by_q = {a.quiz_question_id: a for a in
                    (await s.execute(select(Answer))).scalars().all()}
        await eng.dispose()
        return resp, by_q, (q1id, q2id, q3id)

    resp, by_q, (q1id, q2id, q3id) = asyncio.run(run())
    assert by_q[q1id].manual_score == 3.0        # تنقيط الأستاذ اليدويّ لم يُمسّ
    assert by_q[q2id].manual_score is None        # المصادَق عليه لم يُكتب عليه
    assert by_q[q3id].manual_score == 1.0         # غير المصحَّح أخذ الاقتراح
    from urllib.parse import unquote
    assert "تُركت 2" in unquote(resp.headers["location"])   # الرسالة تُميّز المتروك يدوياً
