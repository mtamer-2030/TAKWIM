"""إصلاحان: (١) خطط التدخّل لا تُبتَر (سقفُ توليدٍ كافٍ)؛ (٢) الذاكرة التراكميّة —
تطوّرُ معدّل القسم عبر الزمن، وحمايةُ الأجوبة المُسلَّمة من حذف التقويم."""

import asyncio
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (Answer, Base, Level, Quiz, QuizQuestion, Student)
from app.v2.web import ADMIN_COOKIE, issue_admin_token


def _admin_req():
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, headers={},
                           client=SimpleNamespace(host="9.9.9.9"), query_params={})


def test_plan_generation_has_room_not_truncated(monkeypatch):
    """خطّة القسم/التلميذ تُطلَب بسقفِ توليدٍ كبيرٍ (لا 350) فلا تُبتَر في منتصف الجملة."""
    from app import ai_feedback

    captured = {}

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"response": "١) ... ٢) ... ٣) خلاصة مكتملة."}

    def fake_post(url, json=None, timeout=None):
        captured.update(json or {})
        return FakeResp()

    monkeypatch.setattr("httpx.post", fake_post)
    out = ai_feedback.generate_class_plan(
        {"group_name": "TC1", "student_count": 3, "skills": {},
         "dominant_deficit": "المناقشة", "dominant_share": 0.5, "overall": 0.55})
    assert "٣)" in out
    # السقف كبيرٌ بما يكفي لخطّةٍ من ٣ نقاطٍ بالعربيّة (كان 350 يبترها).
    assert captured["options"]["num_predict"] >= 800


def test_class_progress_is_cumulative(monkeypatch):
    """تطوّرُ معدّل القسم عبر تقويمين بترتيب الزمن مع متوسّطٍ جارٍ تراكميّ."""
    from app.v2.routers import ai as admin_mod
    import datetime as dt

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            st = Student(full_name="أ", level_id=lv.id, group_name="TC1", active=True)
            s.add(st); await s.flush()
            # تقويمان: ٦٠٪ (الأقدم) ثمّ ٨٠٪ → تراكميّ ٦٠ ثمّ ٧٠.
            for score, day in [(6, 1), (8, 8)]:
                quiz = Quiz(title=f"فرض {day}", kind="exam", level_id=lv.id,
                            created_at=dt.datetime(2026, 9, day))
                s.add(quiz); await s.flush()
                q = QuizQuestion(quiz_id=quiz.id, qtype="long_text", prompt="س",
                                 payload={}, max_score=10, position=0)
                s.add(q); await s.flush()
                s.add(Answer(quiz_question_id=q.id, student_id=st.id, raw={"text": "a"},
                             manual_score=score, teacher_confirmed=True, submitted=True))
            await s.commit()
            prog = await admin_mod._class_progress(s, "TC1")
        await eng.dispose()
        return prog

    prog = asyncio.run(go())
    assert [p["pct"] for p in prog] == [60.0, 80.0]
    assert [p["cumulative"] for p in prog] == [60.0, 70.0]


def test_quiz_delete_protects_submitted_answers(monkeypatch):
    """حذفُ تقويمٍ فيه أجوبةٌ مُسلَّمة يُرفَض (حماية الذاكرة التراكميّة)؛ والفارغُ يُحذف."""
    from app.v2.routers import quizzes as admin_mod

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            st = Student(full_name="أ", level_id=lv.id, group_name="TC1", active=True)
            s.add(st); await s.flush()
            # تقويمٌ فيه جوابٌ مُسلَّم — لا يُحذف.
            q1 = Quiz(title="بأجوبة", kind="exam", level_id=lv.id); s.add(q1); await s.flush()
            qq = QuizQuestion(quiz_id=q1.id, qtype="long_text", prompt="س", payload={},
                              max_score=4, position=0); s.add(qq); await s.flush()
            s.add(Answer(quiz_question_id=qq.id, student_id=st.id, raw={"text": "x"},
                         manual_score=3, teacher_confirmed=True, submitted=True))
            # تقويمٌ فارغ — يُحذف.
            q2 = Quiz(title="فارغ", kind="exam", level_id=lv.id); s.add(q2); await s.flush()
            await s.commit()
            id1, id2 = q1.id, q2.id
        r1 = await admin_mod.quiz_delete(_admin_req(), id1)
        r2 = await admin_mod.quiz_delete(_admin_req(), id2)
        async with S() as s:
            titles = {q.title for q in (await s.execute(select(Quiz))).scalars()}
            n_ans = await s.scalar(select(func.count()).select_from(Answer))
        await eng.dispose()
        return r1, r2, titles, n_ans

    r1, r2, titles, n_ans = asyncio.run(go())
    assert getattr(r1, "status_code", None) == 303 and "protected=" in r1.headers["location"]
    assert "بأجوبة" in titles          # لم يُحذف (محميّ)
    assert n_ans == 1                    # الجواب المُسلَّم بقي
    assert "فارغ" not in titles          # الفارغ حُذف
