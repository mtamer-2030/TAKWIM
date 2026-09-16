"""ميزات ما بعد التجربة الحيّة:
1) إضافة تلميذٍ جديد (مُحوَّل/غير مسجَّل) مع توليد رمز دخوله القصير.
2) تصدير التقارير الجماعيّة والفرديّة إلى Word (.docx) ونصّ (.txt)."""

import asyncio
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.codes import split_login_code
from app.models import (Answer, Base, Level, Quiz, QuizQuestion, Student)
from app.services.report_export import (class_report_docx, class_report_txt,
                                        student_report_docx, student_report_txt)
from app.v2.web import ADMIN_COOKIE, issue_admin_token


def _admin_req(**q):
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, headers={},
                           client=SimpleNamespace(host="9.9.9.9"), query_params=q)


def test_add_student_generates_login_code(monkeypatch):
    from app.v2 import admin as admin_mod

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        async with S() as s:
            lv = Level(name="جذع مشترك", code="TC"); s.add(lv); await s.flush()
            lid = lv.id
            await s.commit()
        resp = await admin_mod.student_add(
            _admin_req(), full_name="وليد التحويلي", level_id=str(lid),
            group_name="TC3", massar_code="G123456789")
        async with S() as s:
            st = (await s.execute(select(Student).where(
                Student.full_name == "وليد التحويلي"))).scalar_one()
            code, group, massar = st.login_code, st.group_name, st.massar_code
        await eng.dispose()
        return resp, code, group, massar

    resp, code, group, massar = asyncio.run(go())
    assert getattr(resp, "status_code", None) == 303 and "new_code=" in resp.headers["location"]
    assert code and split_login_code(code) is not None and code.startswith("TC3-")
    assert group == "TC3" and massar == "G123456789"


def test_add_student_rejects_duplicate_massar(monkeypatch):
    from app.v2 import admin as admin_mod

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            s.add(Student(full_name="قديم", level_id=lv.id, massar_code="D9", active=True))
            await s.commit(); lid = lv.id
        resp = await admin_mod.student_add(
            _admin_req(), full_name="جديد", level_id=str(lid), group_name="TC1",
            massar_code="d9")
        async with S() as s:
            n = await s.scalar(select(__import__("sqlalchemy").func.count()).select_from(Student))
        await eng.dispose()
        return resp, n

    resp, n = asyncio.run(go())
    assert "add_error=" in resp.headers["location"] and n == 1     # لم يُضَف مكرّر


def _sample_class_data():
    return {
        "group_name": "TC1",
        "evaluations": [{"quiz_id": 1, "title": "فرض 1", "kind": "exam",
                         "date": "2026-09-10", "class_avg": 62.5, "count": 2}],
        "students": [
            {"student": SimpleNamespace(full_name="أ", id=1), "per": {1: 70.0}, "overall": 70.0},
            {"student": SimpleNamespace(full_name="ب", id=2), "per": {1: 55.0}, "overall": 55.0},
        ],
        "class_overall": 62.5, "has_data": True,
    }


def _sample_student_data():
    return {
        "student": SimpleNamespace(full_name="أمين", id=1, group_name="TC1"),
        "evaluations": [{"quiz_id": 1, "title": "فرض 1", "kind": "exam",
                         "date": "2026-09-10", "pct": 70.0}],
        "overall": 70.0, "trend": 5.0,
        "skills": {"الأشكلة": {"avg": 0.7, "count": 1},
                   "المفهمة": {"avg": None, "count": 0}},
        "has_data": True,
    }


def test_class_report_docx_and_txt():
    body = class_report_docx(_sample_class_data())
    assert body[:2] == b"PK" and len(body) > 500          # ملفّ docx صالح (zip)
    txt = class_report_txt(_sample_class_data())
    assert "الفوج TC1" in txt and "فرض 1" in txt and "معدّل القسم" in txt


def test_student_report_docx_and_txt():
    body = student_report_docx(_sample_student_data())
    assert body[:2] == b"PK" and len(body) > 500
    txt = student_report_txt(_sample_student_data())
    assert "أمين" in txt and "ملمح المهارات" in txt and "الأشكلة" in txt


def test_student_export_route_returns_docx(monkeypatch):
    from app.v2.routers import reports as admin_mod

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            st = Student(full_name="سعاد", level_id=lv.id, group_name="TC1", active=True)
            s.add(st); await s.flush()
            quiz = Quiz(title="ت", kind="exam", level_id=lv.id); s.add(quiz); await s.flush()
            qq = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="س", payload={},
                              max_score=1, position=0); s.add(qq); await s.flush()
            s.add(Answer(quiz_question_id=qq.id, student_id=st.id, raw={"choice": 0},
                         auto_score=1, teacher_confirmed=True, submitted=True))
            await s.commit(); sid = st.id
        resp = await admin_mod.gradebook_student_export(_admin_req(fmt="docx"), sid)
        await eng.dispose()
        return resp

    resp = asyncio.run(go())
    assert resp.body[:2] == b"PK"
    assert "wordprocessingml" in resp.media_type
