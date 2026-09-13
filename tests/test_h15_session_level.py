"""ح-١٥: منع صارم لفتح جلسة بتقويم لا يطابق مستوى الفوج."""

import asyncio
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Level, Quiz, QuizSession, Student
from app.v2.web import ADMIN_COOKIE, issue_admin_token


def _req():
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, query_params={})


def _run(monkeypatch, quiz_level_code):
    from app.v2 import admin as admin_mod

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        async with S() as s:
            tc = Level(name="الجذع", code="TC", position=0)
            bac = Level(name="أولى باك", code="1BAC", position=1)
            s.add_all([tc, bac]); await s.flush()
            lvl_id = tc.id if quiz_level_code == "TC" else bac.id
            quiz = Quiz(title="ق", kind="exercise", level_id=lvl_id); s.add(quiz); await s.flush()
            s.add(Student(full_name="ت", level_id=tc.id, group_name="TC1"))
            await s.commit()
            qid = quiz.id
        resp = await admin_mod.session_create(_req(), quiz_id=qid, group_name="TC1")
        async with S() as s:
            n = await s.scalar(select(func.count()).select_from(QuizSession))
        await eng.dispose()
        return resp, n

    return asyncio.run(run())


def test_mismatched_level_session_rejected(monkeypatch):
    # فوج TC1 (مستواه TC) + تقويم مستواه 1BAC → رفض، لا جلسة.
    resp, n = _run(monkeypatch, "1BAC")
    assert n == 0
    assert "error=" in resp.headers["location"]


def test_matching_level_session_created(monkeypatch):
    # فوج TC1 + تقويم مستواه TC → تُنشأ الجلسة.
    resp, n = _run(monkeypatch, "TC")
    assert n == 1
    assert "/admin/sessions/" in resp.headers["location"]
