"""قسم «التدخل AI»: قراءة تقارير التدخّل (جماعيّة + فرديّة) وتصديرها Word/نصّ.
تعمل الخلاصة الحتميّة للمهارات بلا Ollama؛ وتظهر خطط الذكاء إن وُلِّدت وخُزّنت."""

import asyncio
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (Answer, Base, ClassReport, Level, Quiz, QuizQuestion,
                        Skill, Student, StudentReport)
from app.services.report_export import ai_reports_docx, ai_reports_txt
from app.v2.web import ADMIN_COOKIE, issue_admin_token


def _admin_req(**q):
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, headers={},
                           client=SimpleNamespace(host="9.9.9.9"), query_params=q)


def test_ai_reports_export_functions():
    data = {
        "group": "TC1",
        "class": {"skills": {"الأشكلة": {"avg": 0.6, "count": 2}},
                  "weakest": "المفهمة", "plan": "خطّة دعم القسم", "has_data": True},
        "students": [
            {"student": SimpleNamespace(full_name="أمين", id=1),
             "skills": {"الأشكلة": {"avg": 0.7, "count": 1}}, "has_data": True,
             "plan": "خطّة أمين"},
            {"student": SimpleNamespace(full_name="سعاد", id=2),
             "skills": {}, "has_data": False, "plan": None},
        ],
    }
    body = ai_reports_docx(data)
    assert body[:2] == b"PK" and len(body) > 500
    txt = ai_reports_txt(data)
    assert "التقرير الجماعيّ" in txt and "أمين" in txt and "خطّة دعم القسم" in txt


def test_ai_reports_view_route_builds_data(monkeypatch):
    from app.v2.routers import ai as admin_mod
    cap = {}

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        # web.generate_student_skill_profile يستعمل جلسته؛ الدوالّ تأخذ الجلسة كوسيط هنا
        monkeypatch.setattr(
            admin_mod.templates, "TemplateResponse",
            lambda name, ctx: cap.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        async with S() as s:
            for nm in ["الأشكلة", "المفهمة", "المحاجة", "التركيب", "المعرفة"]:
                s.add(Skill(name=nm))
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            st = Student(full_name="تلميذ التقرير", level_id=lv.id, group_name="TC1", active=True)
            s.add(st); await s.flush()
            quiz = Quiz(title="ت", kind="exam", level_id=lv.id); s.add(quiz); await s.flush()
            sk = (await s.execute(select(Skill).where(Skill.name == "الأشكلة"))).scalar_one()
            qq = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="س", payload={},
                              max_score=1, position=0, skill_id=sk.id); s.add(qq); await s.flush()
            s.add(Answer(quiz_question_id=qq.id, student_id=st.id, raw={"choice": 0},
                         auto_score=1, teacher_confirmed=True, submitted=True))
            await s.commit()
        await admin_mod.ai_reports_view(_admin_req(group="TC1"))
        await eng.dispose()

    asyncio.run(go())
    assert cap["name"] == "admin/ai_reports.html"
    assert cap["data"]["group"] == "TC1"
    assert cap["data"]["class"]["has_data"] is True
    assert len(cap["data"]["students"]) == 1
    assert cap["data"]["students"][0]["student"].full_name == "تلميذ التقرير"
