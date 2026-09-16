"""محرّر التقويم المحفوظ: تعديل العنوان ونصّ السؤال والخيارات والصحيح والسلّم، وحذف
سؤال — لإصلاح الأخطاء بعد الحفظ بلا إعادة استيراد."""

import asyncio
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import FormData

from app.models import Base, Level, Quiz, QuizQuestion
from app.v2.web import ADMIN_COOKIE, issue_admin_token


class _FormReq:
    def __init__(self, form):
        self.cookies = {ADMIN_COOKIE: issue_admin_token()}
        self.headers = {}
        self.query_params = {}
        self._form = form

    async def form(self):
        return self._form


def test_quiz_edit_updates_text_options_and_deletes(monkeypatch):
    from app.v2.routers import quizzes as admin_mod

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            quiz = Quiz(title="عنوان قديم", kind="exam", level_id=lv.id); s.add(quiz); await s.flush()
            q1 = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="سؤال خاطئ",
                              payload={"options": ["أ", "ب"], "correct": [0]},
                              max_score=1, position=0)
            q2 = QuizQuestion(quiz_id=quiz.id, qtype="long_text", prompt="حلّل",
                              payload={}, max_score=10, position=1)
            q3 = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="سؤال يُحذف",
                              payload={"options": ["x", "y"], "correct": [1]},
                              max_score=1, position=2)
            s.add_all([q1, q2, q3]); await s.flush()
            ids = (q1.id, q2.id, q3.id)
            await s.commit()

        q1id, q2id, q3id = ids
        form = FormData([
            ("qids", f"{q1id},{q2id},{q3id}"),
            ("title", "عنوان جديد"),
            (f"q_{q1id}_prompt", "سؤال مصحَّح"),
            (f"q_{q1id}_stimulus", "نصّ مرافق"),
            (f"q_{q1id}_max", "3"),
            (f"q_{q1id}_options", "بديل خاطئ\n* الجواب الصحيح\nبديل ثالث"),
            (f"q_{q2id}_prompt", "حلّل النصّ"),
            (f"q_{q2id}_stimulus", ""),
            (f"q_{q2id}_max", "12"),
            (f"q_{q3id}_delete", "on"),
        ])
        resp = await admin_mod.quiz_edit_save(_FormReq(form), q1id and quiz.id)
        async with S() as s:
            q = await s.get(Quiz, quiz.id)
            rows = (await s.execute(select(QuizQuestion)
                    .where(QuizQuestion.quiz_id == quiz.id)
                    .order_by(QuizQuestion.position))).scalars().all()
            data = [(r.qtype, r.prompt, r.stimulus, r.max_score, r.payload) for r in rows]
            title = q.title
        await eng.dispose()
        return resp, title, data

    resp, title, data = asyncio.run(go())
    assert getattr(resp, "status_code", None) == 303
    assert title == "عنوان جديد"
    assert len(data) == 2                                  # حُذف السؤال الثالث
    mcq = data[0]
    assert mcq[1] == "سؤال مصحَّح" and mcq[2] == "نصّ مرافق" and mcq[3] == 3.0
    assert mcq[4]["options"] == ["بديل خاطئ", "الجواب الصحيح", "بديل ثالث"]
    assert mcq[4]["correct"] == [1]                        # النجمة عيّنت الصحيح
    assert data[1][1] == "حلّل النصّ" and data[1][3] == 12.0


def test_opts_text_handles_nonstring_and_none_payload():
    """حصانةٌ ضدّ انهيار «صفحة بيضاء»: خياراتٌ رقميّة أو payload=None في تقويمٍ مستورَد."""
    from app.v2.routers.quizzes import _opts_text
    q1 = QuizQuestion(qtype="mcq_single", prompt="س", position=0, max_score=1,
                      payload={"options": ["أ", "ب", 3], "correct": [0, 2]})
    assert _opts_text(q1) == "* أ\nب\n* 3"          # الرقم 3 حُوِّل نصّاً بلا انهيار
    q2 = QuizQuestion(qtype="passage", prompt="نصّ", position=1, max_score=0, payload=None)
    assert _opts_text(q2) == ""                       # payload=None لا يكسر
    # correct مخزَّنٌ رقماً مفرداً (لا قائمة) — كان يُسقِط المحرّر بـ TypeError
    q3 = QuizQuestion(qtype="mcq_single", prompt="س", position=2, max_score=1,
                      payload={"options": ["أ", "ب", "ج"], "correct": 1})
    assert _opts_text(q3) == "أ\n* ب\nج"
    # correct قائمة عاديّة تبقى تعمل
    q4 = QuizQuestion(qtype="mcq_multi", prompt="س", position=3, max_score=2,
                      payload={"options": ["أ", "ب"], "correct": [0, 1]})
    assert _opts_text(q4) == "* أ\n* ب"
