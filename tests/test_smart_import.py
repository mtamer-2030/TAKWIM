"""الاستيراد المتسامح: أيّ ملفّ نصّيّ → أسئلة مفتوحة → مراجعة الأستاذ → حفظ.

يقينيّ بالكامل (بلا Ollama، بلا اتصال). نتحقّق من التقسيم والمراجعة والحفظ."""

import asyncio
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import FormData

from app.models import Base, Level, Quiz, QuizQuestion
from app.services.quizzes import heuristic_quiz_from_text
from app.v2.web import ADMIN_COOKIE, issue_admin_token

SAMPLE = """تقويم الوعي واللاوعي
1) عرّف اللاوعي حسب فرويد.
2) هل الأنا سيّد نفسه؟ ناقش القولة.
٣- بيّن العلاقة بين الوعي والإدراك."""


def _req():
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, query_params={})


class _UF:
    def __init__(self, filename, data):
        self.filename = filename
        self._d = data

    async def read(self):
        return self._d


class _FormReq:
    def __init__(self, form):
        self.cookies = {ADMIN_COOKIE: issue_admin_token()}
        self._form = form

    async def form(self):
        return self._form


# ————— المحلّل المتسامح (وحدة نقيّة) —————

def test_heuristic_splits_numbered_questions():
    q = heuristic_quiz_from_text(SAMPLE, title_hint="درس")
    assert q["title"] == "تقويم الوعي واللاوعي"       # أوّل سطر عنوان
    assert len(q["questions"]) == 3
    assert all(x["type"] == "long_text" for x in q["questions"])
    assert "فرويد" in q["questions"][0]["prompt"]


def test_heuristic_no_markers_falls_back_to_single_question():
    q = heuristic_quiz_from_text("نصّ بلا ترقيم يشرح فكرة واحدة متّصلة.")
    assert len(q["questions"]) == 1


# ————— مسار الاستيراد: ملفّ → صفحة مراجعة قابلة للتحرير —————

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
            return eng, S, admin_mod, lv.id
    return prep


def test_import_txt_renders_editable_review(monkeypatch):
    prep = _setup(monkeypatch)
    captured = {}

    async def run():
        eng, S, admin_mod, lid = await prep()
        # لا Tesseract هنا: extract_text قد يفشل على bytes، فيسقط على فكّ النصّ.
        monkeypatch.setattr(admin_mod, "extract_text",
                            lambda fn, data: SimpleNamespace(text=SAMPLE))
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: captured.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        await admin_mod.quizzes_import(
            _req(), file=_UF("درس.docx", b"xx"), level_id=str(lid), group_name="")
        await eng.dispose()

    asyncio.run(run())
    assert captured["name"] == "admin/quiz_import_review.html"
    assert len(captured["questions"]) == 3           # قُسِّمت أسئلةً قابلة للتحرير
    assert captured["r_title"] == "تقويم الوعي واللاوعي"


def test_import_save_builds_quiz_from_edited_rows(monkeypatch):
    prep = _setup(monkeypatch)

    async def run():
        eng, S, admin_mod, lid = await prep()
        form = FormData([
            ("level_id", str(lid)), ("group_name", ""), ("count", "2"),
            ("r_title", "فرض محروس"), ("r_kind", "exam"),
            ("q_prompt_0", "حلّل القولة."), ("q_type_0", "long_text"), ("q_score_0", "8"),
            ("q_prompt_1", "عرّف المفهوم."), ("q_type_1", "short_text"), ("q_score_1", "4"),
        ])
        resp = await admin_mod.quizzes_import_save(_FormReq(form))
        async with S() as s:
            quiz = (await s.execute(select(Quiz))).scalars().first()
            nq = await s.scalar(select(func.count()).select_from(QuizQuestion))
            types = [t for (t,) in (await s.execute(select(QuizQuestion.qtype))).all()]
        await eng.dispose()
        return resp, quiz, nq, sorted(types)

    resp, quiz, nq, types = asyncio.run(run())
    assert resp.status_code == 303 and "saved=" in resp.headers["location"]
    assert quiz.title == "فرض محروس" and quiz.kind == "exam"
    assert nq == 2 and types == ["long_text", "short_text"]


def test_import_save_empty_prompts_returns_review_error(monkeypatch):
    prep = _setup(monkeypatch)
    captured = {}

    async def run():
        eng, S, admin_mod, lid = await prep()
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: captured.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        form = FormData([
            ("level_id", str(lid)), ("count", "1"), ("r_title", "ت"), ("r_kind", "exercise"),
            ("q_prompt_0", "   "), ("q_type_0", "long_text"), ("q_score_0", "4"),
        ])
        await admin_mod.quizzes_import_save(_FormReq(form))
        async with S() as s:
            nq = await s.scalar(select(func.count()).select_from(Quiz))
        await eng.dispose()
        return nq

    nq = asyncio.run(run())
    assert nq == 0                                   # لم يُحفَظ تقويم فارغ
    assert captured["name"] == "admin/quiz_import_review.html"
    assert captured["errors"]
