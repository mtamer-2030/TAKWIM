"""اختبار تصدير التقرير التراكميّ (المرحلة ٧): Excel و CSV للأستاذ لنقل النقط
إلى السجلّ الرسميّ — من بيانات موجودة أصلاً، لا اختراع بيداغوجيّ.
"""

import asyncio
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Answer, Base, Level, Quiz, QuizQuestion, Student
from app.v2.web import ADMIN_COOKIE, issue_admin_token


def _req(group, fmt):
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()},
                           query_params={"group": group, "fmt": fmt})


def _run_export(fmt, monkeypatch):
    from app.v2 import admin as admin_mod

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            quiz = Quiz(title="تشخيصي", kind="diagnostic", group_name="TC1")
            s.add(quiz); await s.flush()
            qq = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="س",
                              max_score=4, position=0); s.add(qq); await s.flush()
            for i, sc in enumerate((2, 4)):
                st = Student(full_name=f"تلميذ {i}", level_id=lv.id, group_name="TC1")
                s.add(st); await s.flush()
                s.add(Answer(quiz_question_id=qq.id, student_id=st.id,
                             auto_score=sc, teacher_confirmed=True))
            await s.commit()
        resp = await admin_mod.gradebook_export(_req("TC1", fmt))
        await eng.dispose()
        return resp

    return asyncio.run(run())


def test_export_xlsx(monkeypatch):
    resp = _run_export("xlsx", monkeypatch)
    assert resp.body[:2] == b"PK"                       # ملفّ xlsx (ZIP)
    assert "spreadsheetml" in resp.media_type


def test_export_csv(monkeypatch):
    resp = _run_export("csv", monkeypatch)
    text = resp.body.decode("utf-8-sig")
    assert "التلميذ" in text and "معدّل القسم" in text  # رأس + صفّ المعدّل
    assert "تلميذ 0" in text
