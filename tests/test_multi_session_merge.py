"""دمج أجوبة جلستين لنفس التقويم ونفس الفوج في التقارير:
الأجوبة تُخزَّن بمفتاح (تلميذ + سؤال) لا بمفتاح الجلسة، فكلّ التقارير (دفتر النقط،
التحليلات، شاشة التصحيح) تجمعها بالتقويم لا بالجلسة — فجلستان لنفس التقويم تظهران
كجلسةٍ واحدةٍ تلقائيّاً، ولا يضيع أيّ جواب. هذا الاختبار يقفل هذا السلوك ضدّ أيّ انحدار.
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (Answer, Base, Level, Quiz, QuizQuestion, QuizSession,
                        SessionStudent, Student)
from app.services.gradebook import class_gradebook


def test_two_sessions_same_quiz_merge_in_gradebook():
    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            # ثلاثة تلاميذ من نفس الفوج
            s1 = Student(full_name="أ", level_id=lv.id, group_name="TC1", active=True)
            s2 = Student(full_name="ب", level_id=lv.id, group_name="TC1", active=True)
            s3 = Student(full_name="ج", level_id=lv.id, group_name="TC1", active=True)
            s.add_all([s1, s2, s3]); await s.flush()
            # تقويمٌ واحدٌ بسؤالٍ مغلقٍ على /2
            quiz = Quiz(title="فرض", kind="exam", level_id=lv.id, group_name="TC1")
            s.add(quiz); await s.flush()
            q = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="س",
                             payload={"options": ["a", "b"], "correct": 0},
                             max_score=2, position=0)
            s.add(q); await s.flush()

            # جلستان لنفس التقويم ونفس الفوج
            sess_a = QuizSession(quiz_id=quiz.id, group_name="TC1", status="closed")
            sess_b = QuizSession(quiz_id=quiz.id, group_name="TC1", status="closed")
            s.add_all([sess_a, sess_b]); await s.flush()
            # الجلسة أ: التلميذان أ وب حاضران وأجابا
            s.add_all([
                SessionStudent(session_id=sess_a.id, student_id=s1.id, present=True),
                SessionStudent(session_id=sess_a.id, student_id=s2.id, present=True),
            ])
            # الجلسة ب: التلميذ ج حاضرٌ وأجاب (والتلميذ أ أُعيد إدراجه لكنّه لم يُجب فيها)
            s.add_all([
                SessionStudent(session_id=sess_b.id, student_id=s3.id, present=True),
                SessionStudent(session_id=sess_b.id, student_id=s1.id, present=True),
            ])
            # أجوبةٌ مصادَقٌ عليها (مغلقة → مؤكَّدة): أ=2/2، ب=0/2، ج=2/2
            s.add_all([
                Answer(quiz_question_id=q.id, student_id=s1.id, raw={"choice": 0},
                       auto_score=2, teacher_confirmed=True, submitted=True),
                Answer(quiz_question_id=q.id, student_id=s2.id, raw={"choice": 1},
                       auto_score=0, teacher_confirmed=True, submitted=True),
                Answer(quiz_question_id=q.id, student_id=s3.id, raw={"choice": 0},
                       auto_score=2, teacher_confirmed=True, submitted=True),
            ])
            await s.commit()

        async with S() as s:
            data = await class_gradebook(s, "TC1")
        await eng.dispose()
        return data

    data = asyncio.run(go())

    # تقويمٌ واحدٌ فقط (جلستان → عمودٌ واحد مدموج، لا عمودان)
    assert len(data["evaluations"]) == 1
    ev = data["evaluations"][0]
    assert ev["title"] == "فرض"
    # التلاميذ الثلاثة من الجلستين كلّهم يظهرون بنِسبهم — لا يضيع أحد
    by_name = {r["student"].full_name: r for r in data["students"]}
    assert by_name["أ"]["per"][ev["quiz_id"]] == 100.0   # 2/2 (الجلسة أ)
    assert by_name["ب"]["per"][ev["quiz_id"]] == 0.0     # 0/2 (الجلسة أ)
    assert by_name["ج"]["per"][ev["quiz_id"]] == 100.0   # 2/2 (الجلسة ب)
    # معدّل القسم يحسب الثلاثة معاً (جلسةٌ واحدةٌ ظاهريّاً): (100+0+100)/3
    assert ev["count"] == 3
    assert ev["class_avg"] == round((100 + 0 + 100) / 3, 1)
