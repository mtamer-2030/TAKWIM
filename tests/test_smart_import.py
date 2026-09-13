"""الاستيراد المتسامح اليقينيّ: أيّ ملفّ → أسئلة (مفتوحة واختيار) → مراجعة → حفظ.

بلا Ollama وبلا اتصال. نتحقّق من: تخطّي الترويسة، كشف خانات ☐ للاختيار، بناء
payload الاختيار، وطلب تأشير الصواب."""

import asyncio
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import FormData

from app.models import Base, Level, Quiz, QuizQuestion
from app.services.quizzes import heuristic_quiz_from_text
from app.v2.web import ADMIN_COOKIE, issue_admin_token

EXAM = """المستوى: الجذع المشترك (جميع الشعب) | المادة: الفلسفة | المدة: ساعة واحدة
الجغرافيا علم يدرس: ☐ وضع الانسان فوق الأرض ☐ حركة الكواكب ☐ وضع الانسان والطبيعة
ضع علامة أمام كل مدينة ساحلية: ☐ بنسليمان ☐ مراكش ☐ تارودانت ☐ أصيلا
اشرح العلاقة بين الوعي واللاوعي."""


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


# ————— المحلّل المتسامح —————

def test_heuristic_header_becomes_heading_and_detects_mcq():
    q = heuristic_quiz_from_text(EXAM, title_hint="فرض")
    # سطر الترويسة (المستوى/المادة/المدة) يصير عنواناً يُعرَض، لا يختفي.
    header = next(x for x in q["questions"] if "المستوى:" in x["prompt"])
    assert header["type"] == "heading"
    types = [x["type"] for x in q["questions"]]
    assert "mcq_single" in types and "mcq_multi" in types and "long_text" in types


def test_heuristic_detects_heading_passage_and_separator():
    txt = ("2. فهم النص (9 نقط)\n"
           "____________________\n"
           "يمر الأطفال في كل مكان عبر التسلسل نفسه للاكتساب فيتدرج من المناغاة إلى الكلمات "
           "المفردة ومنها إلى الجمل، وهذا التدرج نفسه في كل اللغات مهما اختلفت الثقافات.\n"
           "1- ما موضوع النص؟\n☐ اللغة\n☐ الطبيعة")
    q = heuristic_quiz_from_text(txt)
    types = [x["type"] for x in q["questions"]]
    assert "heading" in types                 # «فهم النص (9 نقط)»
    assert "passage" in types                 # الفقرة الطويلة تُعرَض
    assert "skip" in types                    # سطر «____» يُحذف
    assert "mcq_single" in types
    mcq1 = next(x for x in q["questions"] if x["type"] == "mcq_single")
    assert len(mcq1["options"]) == 2 and mcq1["correct"] == []   # الصواب غير مؤشَّر بعد


def test_heuristic_checked_box_marks_correct():
    q = heuristic_quiz_from_text("العاصمة: ☐ فاس ☑ الرباط ☐ طنجة")
    m = q["questions"][0]
    assert m["type"] == "mcq_single" and m["options"][m["correct"][0]] == "الرباط"


def test_heuristic_flags_titles_as_heading():
    txt = ("المستوى: الجذع | المادة: الفلسفة | المدة: ساعة\n"
           "أولا: أسئلة معرفية\n"
           "معارف عامة (4.5 نقط)\n"
           "اشرح العلاقة بين الوعي واللاوعي.")
    q = heuristic_quiz_from_text(txt)
    by_type = [x["type"] for x in q["questions"]]
    assert by_type.count("heading") == 3       # الترويسة + عنوانان → عناوين تُعرَض
    assert "long_text" in by_type              # السؤال الحقيقيّ بقي


def test_save_heading_passage_persist_skip_excluded(monkeypatch):
    prep = _setup(monkeypatch)

    async def run():
        eng, S, admin_mod, lid = await prep()
        form = FormData([
            ("level_id", str(lid)), ("count", "4"),
            ("r_title", "ف"), ("r_kind", "exam"),
            ("q_prompt_0", "معارف عامة"), ("q_type_0", "heading"), ("q_score_0", "0"),
            ("q_prompt_1", "____"), ("q_type_1", "skip"), ("q_score_1", "0"),
            ("q_prompt_2", "نصّ الأطفال..."), ("q_type_2", "passage"), ("q_score_2", "0"),
            ("q_prompt_3", "حلّل القولة."), ("q_type_3", "long_text"), ("q_score_3", "8"),
        ])
        await admin_mod.quizzes_import_save(_FormReq(form))
        async with S() as s:
            rows = [(qq.qtype, qq.prompt) for qq in
                    (await s.execute(select(QuizQuestion).order_by(QuizQuestion.position))).scalars()]
        await eng.dispose()
        return rows

    rows = asyncio.run(run())
    types = [t for t, _ in rows]
    assert types == ["heading", "passage", "long_text"]   # skip حُذف، والباقي بقي بالترتيب


# ————— مسار الاستيراد → مراجعة —————

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


def test_import_renders_editable_review(monkeypatch):
    prep = _setup(monkeypatch)
    captured = {}

    async def run():
        eng, S, admin_mod, lid = await prep()
        monkeypatch.setattr(admin_mod, "extract_text",
                            lambda fn, data: SimpleNamespace(text=EXAM))
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: captured.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        await admin_mod.quizzes_import(
            _req(), file=_UF("فرض.docx", b"xx"), level_id=str(lid), group_name="")
        await eng.dispose()

    asyncio.run(run())
    assert captured["name"] == "admin/quiz_import_review.html"
    types = [q["type"] for q in captured["questions"]]
    assert "mcq_single" in types and "mcq_multi" in types


def test_save_mcq_single_builds_payload(monkeypatch):
    prep = _setup(monkeypatch)

    async def run():
        eng, S, admin_mod, lid = await prep()
        form = FormData([
            ("level_id", str(lid)), ("count", "1"),
            ("r_title", "فرض 1"), ("r_kind", "exam"),
            ("q_prompt_0", "الجغرافيا علم يدرس"), ("q_type_0", "mcq_single"),
            ("q_score_0", "2"), ("q_optcount_0", "3"),
            ("q_opt_0_0", "وضع الانسان"), ("q_opt_0_1", "حركة الكواكب"),
            ("q_opt_0_2", "الطبيعة"), ("q_correct_0", "2"),
        ])
        resp = await admin_mod.quizzes_import_save(_FormReq(form))
        async with S() as s:
            qq = (await s.execute(select(QuizQuestion))).scalars().first()
            data = (qq.qtype, qq.payload)
        await eng.dispose()
        return resp, data

    resp, (qtype, payload) = asyncio.run(run())
    assert resp.status_code == 303 and "saved=" in resp.headers["location"]
    assert qtype == "mcq_single"
    assert payload["options"] == ["وضع الانسان", "حركة الكواكب", "الطبيعة"]
    assert payload["correct"] == 2


def test_save_mcq_multi_builds_list_correct(monkeypatch):
    prep = _setup(monkeypatch)

    async def run():
        eng, S, admin_mod, lid = await prep()
        form = FormData([
            ("level_id", str(lid)), ("count", "1"),
            ("r_title", "ت"), ("r_kind", "exercise"),
            ("q_prompt_0", "المدن الساحلية"), ("q_type_0", "mcq_multi"),
            ("q_score_0", "3"), ("q_optcount_0", "3"),
            ("q_opt_0_0", "بنسليمان"), ("q_opt_0_1", "مراكش"), ("q_opt_0_2", "أصيلا"),
            ("q_correct_0", "0"), ("q_correct_0", "2"),
        ])
        await admin_mod.quizzes_import_save(_FormReq(form))
        async with S() as s:
            qq = (await s.execute(select(QuizQuestion))).scalars().first()
            data = (qq.qtype, qq.payload)
        await eng.dispose()
        return data

    qtype, payload = asyncio.run(run())
    assert qtype == "mcq_multi" and payload["correct"] == [0, 2]


def test_save_mcq_without_correct_returns_error(monkeypatch):
    prep = _setup(monkeypatch)
    captured = {}

    async def run():
        eng, S, admin_mod, lid = await prep()
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: captured.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        form = FormData([
            ("level_id", str(lid)), ("count", "1"),
            ("r_title", "ت"), ("r_kind", "exercise"),
            ("q_prompt_0", "س"), ("q_type_0", "mcq_single"), ("q_score_0", "2"),
            ("q_optcount_0", "2"), ("q_opt_0_0", "أ"), ("q_opt_0_1", "ب"),
            # بلا q_correct_0 → يجب رفض الحفظ وإعادة المراجعة بخطأ.
        ])
        await admin_mod.quizzes_import_save(_FormReq(form))
        async with S() as s:
            nq = await s.scalar(select(func.count()).select_from(Quiz))
        await eng.dispose()
        return nq

    nq = asyncio.run(run())
    assert nq == 0
    assert captured["name"] == "admin/quiz_import_review.html" and captured["errors"]


def test_save_open_question(monkeypatch):
    prep = _setup(monkeypatch)

    async def run():
        eng, S, admin_mod, lid = await prep()
        form = FormData([
            ("level_id", str(lid)), ("count", "1"),
            ("r_title", "مقال"), ("r_kind", "exam"),
            ("q_prompt_0", "حلّل القولة."), ("q_type_0", "long_text"), ("q_score_0", "8"),
        ])
        resp = await admin_mod.quizzes_import_save(_FormReq(form))
        async with S() as s:
            qq = (await s.execute(select(QuizQuestion))).scalars().first()
            d = (qq.qtype, qq.max_score)
        await eng.dispose()
        return resp, d

    resp, (qtype, ms) = asyncio.run(run())
    assert resp.status_code == 303 and qtype == "long_text" and ms == 8.0
