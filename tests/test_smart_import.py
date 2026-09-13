"""الاستيراد الذكيّ: أيّ ملفّ → استخراج بالمحرّك المحلّي → مراجعة الأستاذ → حفظ.

يُختبَر بلا Ollama: نُبدّل دالّة الاستخراج بمزيّفة، ونتحقّق من مسار المراجعة والحفظ."""

import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai_feedback import AIUnavailable
from app.models import Base, Level, Quiz, QuizQuestion
from app.v2.web import ADMIN_COOKIE, issue_admin_token

VALID = {
    "title": "تقويم تجريبيّ", "kind": "exercise",
    "questions": [
        {"type": "long_text", "competency": "argumentation",
         "prompt": "حلّل القولة", "max_score": 4, "payload": {}},
        {"type": "mcq_single", "competency": "knowledge", "prompt": "من قال؟",
         "max_score": 2, "payload": {"options": ["أ", "ب", "ج"], "correct": 1}},
    ],
}


def _req():
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, query_params={})


class _UF:
    def __init__(self, filename, data):
        self.filename = filename
        self._d = data

    async def read(self):
        return self._d


def _setup(monkeypatch):
    from app.v2 import admin as admin_mod

    async def prep():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)

        async def _skills():
            return {}
        monkeypatch.setattr(admin_mod, "skill_id_map", _skills)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            await s.commit()
            lid = lv.id
        return eng, S, admin_mod, lid
    return prep


def test_import_save_valid_json_creates_quiz(monkeypatch):
    prep = _setup(monkeypatch)

    async def run():
        eng, S, admin_mod, lid = await prep()
        resp = await admin_mod.quizzes_import_save(
            _req(), json_text=json.dumps(VALID), level_id=str(lid), group_name="")
        async with S() as s:
            nq = await s.scalar(select(func.count()).select_from(Quiz))
            nquest = await s.scalar(select(func.count()).select_from(QuizQuestion))
        await eng.dispose()
        return resp, nq, nquest

    resp, nq, nquest = asyncio.run(run())
    assert resp.status_code == 303 and "saved=" in resp.headers["location"]
    assert nq == 1 and nquest == 2


def test_import_save_invalid_json_returns_review_with_errors(monkeypatch):
    prep = _setup(monkeypatch)
    captured = {}

    async def run():
        eng, S, admin_mod, lid = await prep()
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: captured.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        # نوع سؤال غير مدعوم → التحقّق يرفض، فتُعاد صفحة المراجعة بالأخطاء.
        bad = {"title": "ت", "kind": "exercise",
               "questions": [{"type": "essay", "prompt": "x", "max_score": 4}]}
        await admin_mod.quizzes_import_save(
            _req(), json_text=json.dumps(bad), level_id=str(lid), group_name="")
        async with S() as s:
            nq = await s.scalar(select(func.count()).select_from(Quiz))
        await eng.dispose()
        return nq

    nq = asyncio.run(run())
    assert nq == 0                                   # لم يُحفَظ شيء
    assert captured["name"] == "admin/quiz_import_review.html"
    assert captured["errors"]                        # عُرِضت الأخطاء
    assert "essay" in " ".join(captured["errors"]) or captured["errors"]


def test_smart_import_docx_uses_ai_then_review(monkeypatch):
    prep = _setup(monkeypatch)
    captured = {}

    async def run():
        eng, S, admin_mod, lid = await prep()
        monkeypatch.setattr(admin_mod, "extract_text",
                            lambda fn, data: SimpleNamespace(text="سؤال: حلّل القولة."))
        monkeypatch.setattr(admin_mod, "extract_quiz_json", lambda raw: json.dumps(VALID))
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: captured.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        await admin_mod.quizzes_import(
            _req(), file=_UF("درس.docx", b"not a real docx"),
            level_id=str(lid), group_name="")
        await eng.dispose()

    asyncio.run(run())
    assert captured["name"] == "admin/quiz_import_review.html"
    assert captured["is_json"] is True
    assert "تقويم تجريبيّ" in captured["draft"]       # مسوّدة المحرّك معروضة للمراجعة


def test_smart_import_ai_unavailable_shows_raw_text(monkeypatch):
    prep = _setup(monkeypatch)
    captured = {}

    async def run():
        eng, S, admin_mod, lid = await prep()
        monkeypatch.setattr(admin_mod, "extract_text",
                            lambda fn, data: SimpleNamespace(text="نصّ الدرس الخام"))

        def _boom(raw):
            raise AIUnavailable("المحرّك مغلق")
        monkeypatch.setattr(admin_mod, "extract_quiz_json", _boom)
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: captured.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        await admin_mod.quizzes_import(
            _req(), file=_UF("درس.pdf", b"%PDF fake"),
            level_id=str(lid), group_name="")
        await eng.dispose()

    asyncio.run(run())
    assert captured["name"] == "admin/quiz_import_review.html"
    assert captured["is_json"] is False               # تدهور لطيف
    assert captured["draft"] == "نصّ الدرس الخام"       # النصّ الخام معروض للمساعدة
