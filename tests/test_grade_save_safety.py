"""٤-ب: سلامة حفظ التصحيح — لا مصادقة على مفتوح بلا نقطة، ولا إلغاء تصديق جوابٍ
لم يُعرَض (سباق التسليم المتأخّر)."""

import asyncio
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import FormData

from app.models import Answer, Base, Level, Quiz, QuizQuestion, Student
from app.v2.web import ADMIN_COOKIE, issue_admin_token


class _Req:
    def __init__(self, form, headers=None):
        self.cookies = {ADMIN_COOKIE: issue_admin_token()}
        self._form = form
        self.headers = headers or {}

    async def form(self):
        return self._form


def _setup(monkeypatch):
    from app.v2.routers import quizzes as admin_mod

    async def build():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            st = Student(full_name="ت", level_id=lv.id, group_name="TC1"); s.add(st); await s.flush()
            quiz = Quiz(title="ق", kind="exercise", level_id=lv.id); s.add(quiz); await s.flush()
            q_open = QuizQuestion(quiz_id=quiz.id, qtype="long_text", prompt="فسّر",
                                  payload={}, max_score=4, position=0)
            s.add(q_open); await s.flush()
            # جواب مفتوح بلا نقطة (auto_score None, manual_score None).
            a_open = Answer(quiz_question_id=q_open.id, student_id=st.id, raw={"text": "..."},
                            submitted=True, teacher_confirmed=False)
            s.add(a_open); await s.flush()
            await s.commit()
            return eng, S, admin_mod, quiz.id, a_open.id
    return build


def test_confirm_open_without_score_is_refused(monkeypatch):
    build = _setup(monkeypatch)

    async def run():
        eng, S, admin_mod, qid, aid = await build()
        # الأستاذ أشّر «مصادَق» بلا كتابة نقطة.
        form = FormData([("shown", str(aid)), (f"confirm_{aid}", "on")])
        await admin_mod.quiz_grade_save(_Req(form), qid)
        async with S() as s:
            ans = await s.get(Answer, aid)
            state = (ans.teacher_confirmed, ans.manual_score, ans.auto_score)
        await eng.dispose()
        return state

    confirmed, manual, auto = asyncio.run(run())
    assert confirmed is False        # لم يُصادَق: لا نقطة
    assert manual is None and auto is None


def test_confirm_open_with_score_succeeds(monkeypatch):
    build = _setup(monkeypatch)

    async def run():
        eng, S, admin_mod, qid, aid = await build()
        form = FormData([("shown", str(aid)), (f"score_{aid}", "3"), (f"confirm_{aid}", "on")])
        await admin_mod.quiz_grade_save(_Req(form), qid)
        async with S() as s:
            ans = await s.get(Answer, aid)
            state = (ans.teacher_confirmed, ans.manual_score)
        await eng.dispose()
        return state

    confirmed, manual = asyncio.run(run())
    assert confirmed is True and manual == 3.0


def test_answer_not_shown_is_untouched(monkeypatch):
    """جوابٌ سلّمه تلميذ متأخّراً (ليس في shown) لا يُلغى تصديقه عند حفظ الشاشة."""
    from app.v2.routers import quizzes as admin_mod

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            st1 = Student(full_name="أ", level_id=lv.id, group_name="TC1")
            st2 = Student(full_name="ب", level_id=lv.id, group_name="TC1")
            s.add_all([st1, st2]); await s.flush()
            quiz = Quiz(title="ق", kind="exercise", level_id=lv.id); s.add(quiz); await s.flush()
            qq = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="س",
                              payload={"options": ["a", "b"], "correct": 1}, max_score=2, position=0)
            s.add(qq); await s.flush()
            shown_ans = Answer(quiz_question_id=qq.id, student_id=st1.id, raw={"choice": 1},
                               auto_score=2, submitted=True, teacher_confirmed=False)
            # جواب متأخّر: مصادَق عليه سلفاً، ولم يكن معروضاً حين فُتِحت الشاشة.
            late_ans = Answer(quiz_question_id=qq.id, student_id=st2.id, raw={"choice": 1},
                              auto_score=2, submitted=True, teacher_confirmed=True)
            s.add_all([shown_ans, late_ans]); await s.flush()
            await s.commit()
            qid, shown_id, late_id = quiz.id, shown_ans.id, late_ans.id
        # الحفظ يذكر المعروض فقط، ويصادق عليه؛ لا يذكر الجواب المتأخّر إطلاقاً.
        form = FormData([("shown", str(shown_id)), (f"confirm_{shown_id}", "on")])
        await admin_mod.quiz_grade_save(_Req(form), qid)
        async with S() as s:
            shown = await s.get(Answer, shown_id)
            late = await s.get(Answer, late_id)
            state = (shown.teacher_confirmed, late.teacher_confirmed)
        await eng.dispose()
        return state

    shown_confirmed, late_still_confirmed = asyncio.run(run())
    assert shown_confirmed is True            # المعروض صودِق عليه
    assert late_still_confirmed is True        # المتأخّر بقي مصادَقاً — لم يُلغَ سهواً


def test_htmx_save_returns_rows_partial(monkeypatch):
    build = _setup(monkeypatch)

    async def run():
        eng, S, admin_mod, qid, aid = await build()
        captured = {}
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: captured.update({"name": name}) or SimpleNamespace())
        form = FormData([("shown", str(aid))])
        await admin_mod.quiz_grade_save(_Req(form, headers={"HX-Request": "true"}), qid)
        await eng.dispose()
        return captured["name"]

    assert asyncio.run(run()) == "admin/_quiz_grade_rows.html"
