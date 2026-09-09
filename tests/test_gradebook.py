"""اختبار دفتر النقط التراكمي: نِسب فردية وجماعية من الأجوبة المصادَق عليها."""
import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (Base, Level, Quiz, QuizQuestion, QuizAnswer, Student)
from app.services.gradebook import class_gradebook, student_gradebook


async def _seed():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    S = async_sessionmaker(eng, expire_on_commit=False)
    async with S() as s:
        lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
        st = Student(full_name="تلميذ", level_id=lv.id, group_name="TC1", massar_code="A")
        s.add(st); await s.flush()
        # تقويمان، سؤال مغلق في كلٍّ، مصادَق عليه
        for i in range(2):
            q = Quiz(title=f"تقويم {i}", kind="exercise", group_name="TC1"); s.add(q); await s.flush()
            qq = QuizQuestion(quiz_id=q.id, qtype="mcq_single", prompt="p",
                              competency="problematization", max_score=4, position=0)
            s.add(qq); await s.flush()
            score = 2 if i == 0 else 4     # تحسّن: 50٪ ثمّ 100٪
            s.add(QuizAnswer(question_id=qq.id, student_id=st.id, auto_score=score,
                             teacher_confirmed=True))
        await s.commit()
        return eng, S, st.id


def test_student_and_class_gradebook():
    async def run():
        eng, S, sid = await _seed()
        async with S() as s:
            sg = await student_gradebook(s, sid)
            cg = await class_gradebook(s, "TC1")
        await eng.dispose()
        return sg, cg
    sg, cg = asyncio.run(run())
    assert len(sg["evaluations"]) == 2
    assert sg["evaluations"][0]["pct"] == 50.0 and sg["evaluations"][1]["pct"] == 100.0
    assert sg["overall"] == 75.0 and sg["trend"] == 50.0   # تطوّر +50
    assert cg["has_data"] and len(cg["students"]) == 1
    assert cg["students"][0]["overall"] == 75.0
    assert cg["class_overall"] == 75.0
