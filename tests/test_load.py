"""اختبار حمولة على مستوى القاعدة (البند ٤-ب، المعيار: وصول ٤٥ جواباً لا ٤٣).

يحاكي ٤٥ متعلّماً يكتبون جوابهم في الآن نفسه (asyncio.gather) على قاعدة SQLite
بوضع WAL و busy_timeout و مجمّع اتّصالات كالإنتاج، ويتحقّق أنّ الأجوبة الـ٤٥ كلّها
وصلت — لا فقدان تحت التزامن. (اختبار الشبكة الحقيقيّ عبر scripts/load_test.py.)
"""

import asyncio

from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Answer, Base, Level, Quiz, QuizQuestion, Student

N = 45


def _make_engine(path):
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{path}",
        pool_size=20, max_overflow=30, pool_timeout=30)

    @event.listens_for(eng.sync_engine, "connect")
    def _pragmas(dbapi_conn, _rec):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()
    return eng


def test_45_concurrent_answers_all_persist(tmp_path):
    async def run():
        eng = _make_engine(tmp_path / "load.db")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)

        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            quiz = Quiz(title="ق", kind="exercise"); s.add(quiz); await s.flush()
            qq = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="س",
                              max_score=2, position=0); s.add(qq); await s.flush()
            students = [Student(full_name=f"ت{i}", level_id=lv.id, group_name="TC1")
                        for i in range(N)]
            s.add_all(students); await s.flush()
            ids = [st.id for st in students]
            qqid = qq.id
            await s.commit()

        async def submit(student_id):
            # كلّ متعلّم في جلسته الخاصّة (كما في الطلبات المتزامنة الحقيقية).
            async with S() as s:
                s.add(Answer(quiz_question_id=qqid, student_id=student_id,
                             auto_score=2, teacher_confirmed=True))
                await s.commit()

        await asyncio.gather(*(submit(i) for i in ids))

        async with S() as s:
            count = await s.scalar(select(func.count()).select_from(Answer))
        await eng.dispose()
        return count

    assert asyncio.run(run()) == N        # ٤٥ لا ٤٣ — لا فقدان تحت التزامن
