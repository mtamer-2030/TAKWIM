"""البند ٣: سؤال اختيار مستورَد (بكفاية) → تصحيح يقينيّ + مصادقة → يظهر في التقارير.
وسؤال مفتوح مستورَد (بعناصر إجابة) → مؤشّرات يبني منها الذكاء الاصطناعيّ إرشاده."""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.constants import SKILLS
from app.models import Answer, Base, Level, Skill, Student
from app.services.quizzes import build_quiz, grade_answer
from app.services.analytics import generate_student_skill_profile


def test_imported_closed_question_flows_into_reports():
    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            skills = {n: Skill(name=n, position=i) for i, n in enumerate(SKILLS)}
            s.add_all(list(skills.values())); await s.flush()
            skill_ids = {n: sk.id for n, sk in skills.items()}
            st = Student(full_name="ت", level_id=lv.id, group_name="TC1", massar_code="Z")
            s.add(st); await s.flush()
            # تقويم مستورَد: سؤال اختيار بكفاية «الحجاج».
            normalized = {"title": "ف", "kind": "exam", "questions": [{
                "type": "mcq_single", "competency": "argumentation",
                "prompt": "من صاحب القولة؟", "max_score": 2,
                "payload": {"options": ["أ", "ب", "ج"], "correct": 1},
                "indicators": [], "penalties": [], "auto_scored": True}]}
            quiz = build_quiz(normalized, level_id=lv.id, group_name="TC1",
                              skill_ids=skill_ids)
            s.add(quiz); await s.flush()
            qq = quiz.questions[0]
            assert qq.skill_id == skill_ids["البنية الحجاجية"]   # الكفاية ربطت المهارة
            # التلميذ يجيب صواباً → تصحيح يقينيّ + مصادقة آليّة (كما عند التسليم).
            graded = grade_answer(qq, {"choice": 1})
            s.add(Answer(quiz_question_id=qq.id, student_id=st.id, raw={"choice": 1},
                         auto_score=graded["score"], teacher_confirmed=True, submitted=True))
            await s.commit()
            prof = await generate_student_skill_profile(s, st.id)
        await eng.dispose()
        return graded, prof

    graded, prof = asyncio.run(run())
    assert graded["score"] == 2 and graded["auto"] is True        # تغذية راجعة يقينيّة
    # يظهر في التقرير الفرديّ تحت مهارته بنسبة كاملة (2/2 = 1.0).
    assert prof["has_data"] and prof["skills"]["البنية الحجاجية"]["avg"] == 1.0


def test_imported_open_question_carries_ai_indicators():
    from app.v2.routers.quizzes import _to_normalized_question
    q = {"type": "long_text", "prompt": "حلّل", "max_score": 6,
         "options": [], "correct": [], "competency": "synthesis",
         "elements": ["تحديد الأطروحة", "ذكر حجّة", "الاستنتاج"]}
    nq = _to_normalized_question(q)
    assert nq["competency"] == "synthesis"
    assert [i["text"] for i in nq["indicators"]] == ["تحديد الأطروحة", "ذكر حجّة", "الاستنتاج"]
    assert nq["indicators"][0]["points"] == 2.0                    # ٦ ÷ ٣ عناصر
