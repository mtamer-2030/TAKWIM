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
    prompts = " ".join(x["prompt"] for x in q["questions"])
    assert "heading" in types                 # «فهم النص (9 نقط)»
    assert "passage" in types                 # الفقرة الطويلة تُعرَض
    assert "___" not in prompts               # سطر «____» يُحذف تماماً
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


def test_ai_json_maps_to_review_questions():
    from app.v2.admin import _ai_json_to_questions
    js = ('{"title":"فرض","questions":['
          '{"type":"passage","prompt":"نصّ فلسفيّ طويل"},'
          '{"type":"long_text","prompt":"استخرج الأطروحة","max_score":3},'
          '{"type":"mcq_single","prompt":"العاصمة","options":["فاس","الرباط"],"correct":1}]}')
    title, qs = _ai_json_to_questions(js)
    assert title == "فرض" and len(qs) == 3
    assert qs[0]["type"] == "passage"
    assert qs[1]["type"] == "long_text" and qs[1]["max_score"] == 3.0
    assert qs[2]["type"] == "mcq_single" and qs[2]["options"] == ["فاس", "الرباط"] \
        and qs[2]["correct"] == [1]


def test_ai_route_renders_structured_review(monkeypatch):
    prep = _setup(monkeypatch)
    captured = {}

    async def run():
        eng, S, admin_mod, lid = await prep()
        monkeypatch.setattr(admin_mod, "extract_quiz_json",
                            lambda raw, ans="": '{"title":"ف","questions":[{"type":"long_text","prompt":"حلّل","max_score":6}]}')
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: captured.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        form = FormData([("raw_text", "نصّ الفرض"), ("level_id", str(lid)), ("group_name", "")])
        await admin_mod.quizzes_import_ai(_FormReq(form))
        await eng.dispose()

    asyncio.run(run())
    assert captured["name"] == "admin/quiz_import_review.html"
    assert captured["questions"][0]["max_score"] == 6.0


def test_ai_route_falls_back_when_engine_off(monkeypatch):
    prep = _setup(monkeypatch)
    captured = {}

    async def run():
        eng, S, admin_mod, lid = await prep()
        from app.ai_feedback import AIUnavailable

        def _boom(raw, ans=""):
            raise AIUnavailable("مغلق")
        monkeypatch.setattr(admin_mod, "extract_quiz_json", _boom)
        monkeypatch.setattr(admin_mod, "ollama_available", lambda: False)
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: captured.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        form = FormData([("raw_text", "1- سؤال أوّل\n2- سؤال ثانٍ"),
                         ("level_id", str(lid)), ("group_name", "")])
        await admin_mod.quizzes_import_ai(_FormReq(form))
        await eng.dispose()

    asyncio.run(run())
    assert captured["name"] == "admin/quiz_import_review.html"
    assert captured["errors"]                     # رسالة تعذّر واضحة
    assert captured["questions"]                  # التحليل القاعديّ بقي


EXAM_WITH_PASSAGE = """المستوى: الجذع المشترك | المادة: الفلسفة | المدة: ساعة
النص الفلسفي:
يرى الفيلسوف أنّ الإنسان كائن اجتماعيّ بطبعه، لا يقوى على العيش منفرداً بمعزل عن
الجماعة، إذ يجد في التعاون مع غيره سبيلاً إلى تلبية حاجاته المتشعّبة وتحقيق ذاته.
وهذا التلازم بين الفرد والجماعة يجعل الوجود الإنسانيّ وجوداً مشتركاً في جوهره.
1- حلّل مضمون النص.
2- ما علاقة الفرد بالمجتمع؟"""


def test_two_file_import_is_instant_and_attaches_elements(monkeypatch):
    """ملفّان (فرض + عناصر إجابة) → تحليل قاعديّ فوريّ (بلا محرّك) + إسناد المؤشّرات."""
    prep = _setup(monkeypatch)
    captured = {}

    async def run():
        eng, S, admin_mod, lid = await prep()

        def _extract(fn, data):
            if "ans" in fn:
                return SimpleNamespace(text="1- تحديد الأطروحة، شرح المفاهيم، النقد\n"
                                            "2- إبراز التلازم بين الفرد والجماعة")
            return SimpleNamespace(text=EXAM_WITH_PASSAGE)
        monkeypatch.setattr(admin_mod, "extract_text", _extract)

        # المحرّك يجب ألّا يُستدعى إطلاقاً في مسار الاستيراد.
        def _must_not_call(*a, **k):
            raise AssertionError("لا ينبغي استدعاء المحرّك في الاستيراد")
        monkeypatch.setattr(admin_mod, "extract_quiz_json", _must_not_call)
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: captured.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        await admin_mod.quizzes_import(
            _req(), file=_UF("فرض.docx", b"x"),
            answers_file=_UF("ans.docx", b"y"),
            level_id=str(lid), group_name="")
        await eng.dispose()

    asyncio.run(run())
    assert captured["name"] == "admin/quiz_import_review.html"
    types = [q["type"] for q in captured["questions"]]
    assert "passage" in types                              # النصّ الفلسفيّ محفوظ
    passage = next(q for q in captured["questions"] if q["type"] == "passage")
    assert "كائن اجتماعيّ" in passage["prompt"] and "وجوداً مشتركاً" in passage["prompt"]
    # عناصر الإجابة أُسنِدت للسؤال المفتوح الأوّل بالترقيم.
    open_qs = [q for q in captured["questions"] if q["type"] in ("long_text", "short_text")]
    assert open_qs and open_qs[0].get("elements")
    assert "تحديد الأطروحة" in open_qs[0]["elements"]
    assert captured["answers_text"]                        # محفوظ في الحقل المخفيّ


def test_heuristic_groups_multiline_passage():
    """البند «نسي النص»: أسطر النصّ الفلسفيّ القصيرة تُجمَع في passage واحد لا تضيع."""
    parsed = heuristic_quiz_from_text(EXAM_WITH_PASSAGE, title_hint="فرض")
    passages = [q for q in parsed["questions"] if q["type"] == "passage"]
    assert len(passages) == 1
    assert "الإنسان كائن اجتماعيّ" in passages[0]["prompt"]
    assert "التلازم بين الفرد والجماعة" in passages[0]["prompt"]
    # السؤالان المفتوحان بقيا سؤالين (لم يُبتلعا في النصّ).
    opens = [q for q in parsed["questions"] if q["type"] == "long_text"]
    assert len(opens) == 2


def test_attach_answer_elements_by_number():
    """إسناد يقينيّ: الكتلة رقم k → السؤال المفتوح ذو الترتيب k بين القابلة للإجابة."""
    from app.services.quizzes import attach_answer_elements
    qs = [{"type": "heading", "prompt": "قسم"},
          {"type": "long_text", "prompt": "س١"},
          {"type": "mcq_single", "prompt": "س٢"},
          {"type": "long_text", "prompt": "س٣"}]
    ans = "1- تحديد الأطروحة، شرح المفاهيم\n2- علاقة\n3- المقدمة، العرض، الخاتمة"
    matched = attach_answer_elements(qs, ans)
    assert matched == 2                                    # سؤالان مفتوحان فقط
    assert qs[1]["elements"] == ["تحديد الأطروحة", "شرح المفاهيم"]
    assert qs[3]["elements"] == ["المقدمة", "العرض", "الخاتمة"]
    assert "elements" not in qs[2]                         # الاختيار لا يأخذ عناصر هنا


def test_cloud_import_handles_any_structure(monkeypatch):
    """الاستيراد السحابيّ (مُهيّأ) يعالج أيّ بنية: يُستدعى ويُعرَض ناتجه للمراجعة."""
    prep = _setup(monkeypatch)
    captured = {}
    seen = {}

    async def run():
        eng, S, admin_mod, lid = await prep()
        monkeypatch.setattr(admin_mod, "extract_text",
                            lambda fn, data: SimpleNamespace(text="فرض ببنية غريبة ❓"))
        monkeypatch.setattr(admin_mod, "cloud_available", lambda: True)

        def _fake_cloud(raw, ans=""):
            seen["raw"], seen["ans"] = raw, ans
            return ('{"title":"فرض","questions":['
                    '{"type":"passage","prompt":"نصّ فلسفيّ محفوظ"},'
                    '{"type":"mcq_single","prompt":"سؤال","options":["أ","ب"],"correct":1,'
                    '"competency":"knowledge"},'
                    '{"type":"long_text","prompt":"حلّل","max_score":6,'
                    '"competency":"synthesis","elements":["الأطروحة","الحجاج"]}]}')
        monkeypatch.setattr(admin_mod, "extract_quiz_cloud", _fake_cloud)
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: captured.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        await admin_mod.quizzes_import(
            _req(), file=_UF("فرض.docx", b"x"), level_id=str(lid), group_name="")
        await eng.dispose()

    asyncio.run(run())
    assert seen["raw"]                                    # النصّ وصل للنموذج السحابيّ
    assert captured["name"] == "admin/quiz_import_review.html"
    types = [q["type"] for q in captured["questions"]]
    assert types == ["passage", "mcq_single", "long_text"]
    assert captured["questions"][1]["correct"] == [1]     # جواب صحيح مملوء
    assert captured["questions"][2]["elements"] == ["الأطروحة", "الحجاج"]


def test_cloud_import_falls_back_to_heuristic_on_error(monkeypatch):
    """السحابيّ مُهيّأ لكن فشل (لا إنترنت مثلاً) → لا يضيع العمل: تحليل قاعديّ + سبب."""
    prep = _setup(monkeypatch)
    captured = {}

    async def run():
        eng, S, admin_mod, lid = await prep()
        from app.cloud_ai import CloudAIUnavailable
        monkeypatch.setattr(admin_mod, "extract_text",
                            lambda fn, data: SimpleNamespace(text=EXAM))
        monkeypatch.setattr(admin_mod, "cloud_available", lambda: True)

        def _boom(raw, ans=""):
            raise CloudAIUnavailable("لا إنترنت")
        monkeypatch.setattr(admin_mod, "extract_quiz_cloud", _boom)
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: captured.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        await admin_mod.quizzes_import(
            _req(), file=_UF("فرض.docx", b"x"), level_id=str(lid), group_name="")
        await eng.dispose()

    asyncio.run(run())
    assert captured["name"] == "admin/quiz_import_review.html"
    assert captured["errors"] and "السحابيّ" in captured["errors"][0]   # سبب واضح
    assert captured["questions"]                                        # القاعديّ بقي


def test_cloud_available_reads_key(monkeypatch):
    """cloud_available يعكس وجود المفتاح في الإعداد."""
    import app.cloud_ai as C
    from app.settings import CloudAI
    monkeypatch.setattr(C.settings, "cloud_ai", CloudAI(api_key="", model="claude-sonnet-5"))
    assert C.cloud_available() is False
    monkeypatch.setattr(C.settings, "cloud_ai", CloudAI(api_key="sk-ant-xxx", model="claude-sonnet-5"))
    assert C.cloud_available() is True


def test_cloud_extract_without_key_raises(monkeypatch):
    """بلا مفتاح: يرفع CloudAIUnavailable برسالة إرشاديّة (لا ينهار)."""
    import app.cloud_ai as C
    from app.settings import CloudAI
    monkeypatch.setattr(C.settings, "cloud_ai", CloudAI(api_key="", model="claude-sonnet-5"))
    try:
        C.extract_quiz_cloud("نصّ فرض")
        assert False, "كان ينبغي رفع استثناء"
    except C.CloudAIUnavailable as e:
        assert "مفتاح" in str(e)


def test_student_quiz_numbers_only_answerable(monkeypatch):
    """البند ٢: التلميذ يرى ترقيماً متسلسلاً للأسئلة فقط (العناوين/النصوص لا تُرقَّم)."""
    from types import SimpleNamespace as N
    from app.v2.web import templates
    qs = [N(id=1, qtype="heading", prompt="القسم الأوّل", max_score=0, stimulus=None, payload={}),
          N(id=2, qtype="long_text", prompt="سؤال أ", max_score=4, stimulus=None, payload={}),
          N(id=3, qtype="passage", prompt="نصّ", max_score=0, stimulus=None, payload={}),
          N(id=4, qtype="long_text", prompt="سؤال ب", max_score=4, stimulus=None, payload={})]
    quiz = N(id=7, title="ت", questions=qs)
    html = templates.get_template("student/quiz.html").render(quiz=quiz, saved={})
    # ترقيم متسلسل للأسئلة القابلة للجواب فقط (شارة qz-num): ١ ثمّ ٢ (لا ٢ و٤).
    assert 'qz-num">1</span>سؤال أ' in html and 'qz-num">2</span>سؤال ب' in html
    assert 'qz-num">3</span>' not in html


def test_save_open_with_competency_and_elements(monkeypatch):
    """البند ٣: الكفاية → مهارة (للتقارير)، وعناصر الإجابة → مؤشّرات (لتصحيح AI)."""
    from app.v2 import admin as admin_mod

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)

        async def _skills():
            return {"البنية الحجاجية": 99}       # خريطة مهارة مبذورة
        monkeypatch.setattr(admin_mod, "skill_id_map", _skills)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            await s.commit()
            lid = lv.id
        form = FormData([
            ("level_id", str(lid)), ("count", "1"), ("r_title", "ف"), ("r_kind", "exam"),
            ("q_prompt_0", "استخرج الأطروحة"), ("q_type_0", "long_text"), ("q_score_0", "4"),
            ("q_comp_0", "argumentation"),
            ("q_elem_0", "تحديد الأطروحة\nذكر الحجّة\nالاستنتاج"),
        ])
        await admin_mod.quizzes_import_save(_FormReq(form))
        async with S() as s:
            qq = (await s.execute(select(QuizQuestion))).scalars().first()
            data = (qq.skill_id, qq.indicators)
        await eng.dispose()
        return data

    skill_id, indicators = asyncio.run(run())
    assert skill_id == 99                          # الكفاية «الحجاج» → مهارة «البنية الحجاجية»
    assert len(indicators) == 3                    # ثلاثة عناصر إجابة → ثلاثة مؤشّرات
    assert indicators[0]["text"] == "تحديد الأطروحة"


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
