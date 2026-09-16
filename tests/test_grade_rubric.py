"""تصحيح أغنى: تحرير الكفاية ومؤشّرات النجاح في شاشة التصحيح نفسها → تُخزَّن في
السؤال فيستعملها التصحيح المُعان (Ollama)."""

import asyncio
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import FormData

from app.constants import COMPETENCY_TO_SKILL
from app.models import Base, Level, Quiz, QuizQuestion, Skill
from app.v2.web import ADMIN_COOKIE, issue_admin_token


class _FormReq:
    def __init__(self, form):
        self.cookies = {ADMIN_COOKIE: issue_admin_token()}
        self.headers = {}
        self._form = form

    async def form(self):
        return self._form


def test_rubric_save_sets_skill_and_indicators(monkeypatch):
    from app.v2.routers import quizzes as admin_mod
    cap = {}

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        # web.skill_id_map يستعمل جلسته الخاصّة؛ نوجّهه لقاعدتنا أيضاً.
        import app.v2.web as web_mod
        monkeypatch.setattr(web_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                            lambda name, ctx: cap.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        async with S() as s:
            # مهارات مبذورة (اسم المهارة كما في COMPETENCY_TO_SKILL).
            for name in set(COMPETENCY_TO_SKILL.values()):
                s.add(Skill(name=name))
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            quiz = Quiz(title="ق", kind="exam", level_id=lv.id); s.add(quiz); await s.flush()
            qq = QuizQuestion(quiz_id=quiz.id, qtype="long_text", prompt="حلّل",
                              payload={}, max_score=6, position=0); s.add(qq); await s.flush()
            qid = qq.id
            skill_id_of_synth = (await s.execute(
                select(Skill.id).where(Skill.name == COMPETENCY_TO_SKILL["synthesis"]))).scalar_one()
            await s.commit()
        form = FormData([
            ("question_id", str(qid)), ("competency", "synthesis"),
            ("ind_text", "تحديد الأطروحة"), ("ind_points", "2"),
            ("ind_text", "الحجاج"), ("ind_points", "2.5"),
            ("ind_text", ""), ("ind_points", ""),          # صفّ فارغ يُتجاهَل
        ])
        await admin_mod.quiz_grade_rubric(_FormReq(form), quiz.id)
        async with S() as s:
            q = await s.get(QuizQuestion, qid)
            data = (q.skill_id, q.indicators)
        await eng.dispose()
        return data, skill_id_of_synth

    (skill_id, indicators), synth_id = asyncio.run(run())
    assert skill_id == synth_id                              # الكفاية → مهارة صحيحة
    assert len(indicators) == 2                              # الصفّ الفارغ تُجوهِل
    assert indicators[0]["text"] == "تحديد الأطروحة" and indicators[0]["points"] == 2.0
    assert indicators[1]["points"] == 2.5
    assert cap["name"] == "admin/_quiz_grade_rubric.html" and cap["saved"] is True
