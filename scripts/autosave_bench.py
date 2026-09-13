"""ح-١٦: قياس حِمل الحفظ التزايُديّ (autosave) داخل العمليّة، لا عبر الشبكة.

اختبار الحمل القديم (load_test.py) يقيس تسليماً واحداً لكلّ عميل عبر الشبكة، ولا
يمسّ النمط الحقيقيّ: أثناء الإنجاز يُطلق كلّ هاتف طلبات حفظ متتالية (POST
/student/quiz/{id}/save) كلّما تغيّر جواب. هذا القياس يشغّل **دالّة الحفظ نفسها**
على قاعدة SQLite ملفّيّة بنفس براغماتها الإنتاجيّة (WAL + busy_timeout)، فيكشف
تنازع الكاتب الوحيد تحت ٤٥ تلميذاً متزامناً على تقويم من ١٥ سؤالاً.

يقيس زمن كلّ طلب حفظ (وسيط/p95/أقصى)، والفشل، والإنتاجيّة. **يُعرَض القياس قبل أيّ
تحسين** (أمر شغل شتنبر 2026، §ح-١٦): لا يُحسَّن ما لم يُثبِت القياس اختناقاً.

الاستعمال:
  python scripts/autosave_bench.py                     # ٤٥ تلميذاً · ١٥ سؤالاً · ١٥ حفظاً
  python scripts/autosave_bench.py --students 45 --questions 15 --saves 15

لا يمسّ قاعدة الإنتاج: يبني قاعدة مؤقّتة في مجلّد مؤقّت ويحذفها.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import FormData

from app.models import Answer, Base, Level, Quiz, QuizQuestion, Student
from app.v2.web import STUDENT_COOKIE, issue_student_cookie


class _Req:
    """أدنى طلب يحتاجه المسار: كوكي موقَّع + form() غير متزامنة."""
    def __init__(self, cookies, form):
        self.cookies = cookies
        self._form = form

    async def form(self):
        return self._form


def _build_engine(db_path: Path):
    """محرّك مطابق لـ app/database.py: WAL + busy_timeout + نفس حجم التجمّع."""
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", echo=False, future=True,
        pool_size=20, max_overflow=30, pool_timeout=30, pool_recycle=1800,
    )

    @event.listens_for(eng.sync_engine, "connect")
    def _pragmas(dbapi_conn, _rec):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()

    return eng


async def _seed(S, n_students: int, n_questions: int):
    """مستوى واحد + تقويم منزليّ منشور بـ n أسئلة + n تلميذاً في المستوى."""
    async with S() as s:
        lv = Level(name="الجذع المشترك", code="TC", position=0)
        s.add(lv); await s.flush()
        quiz = Quiz(title="تقويم الحمل", kind="exercise", published=True, level_id=lv.id)
        s.add(quiz); await s.flush()
        qids = []
        for i in range(n_questions):
            qq = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt=f"سؤال {i+1}",
                              payload={"options": ["أ", "ب", "ج", "د"], "correct": i % 4},
                              max_score=2, position=i)
            s.add(qq); await s.flush()
            qids.append(qq.id)
        sids = []
        for i in range(n_students):
            st = Student(full_name=f"تلميذ {i+1}", level_id=lv.id, group_name="TC1")
            s.add(st); await s.flush()
            sids.append(st.id)
        await s.commit()
        return quiz.id, qids, sids


async def _one_student(student_mod, quiz_id, qids, sid, n_saves, latencies, fails):
    """يحاكي هاتفاً: n_saves حفظاً تزايُديّاً (يزداد المُجاب سؤالاً كلّ مرّة)."""
    cookie = {STUDENT_COOKIE: issue_student_cookie(sid)}
    per_save = max(1, len(qids) // n_saves)
    answered = 0
    for k in range(n_saves):
        answered = min(len(qids), answered + per_save)
        # الحمولة تنمو كما في الواقع: كلّ الأسئلة المُجابة حتّى الآن تُرسَل.
        fields = [(f"q_{qids[j]}", str(j % 4)) for j in range(answered)]
        t0 = time.perf_counter()
        try:
            resp = await student_mod.save_quiz_draft(_Req(cookie, FormData(fields)), quiz_id)
            dt = time.perf_counter() - t0
            latencies.append(dt)
            if resp.status_code not in (200, 204):
                fails.append((sid, resp.status_code))
        except Exception as e:  # noqa: BLE001
            fails.append((sid, f"{type(e).__name__}: {e}"))


async def _run(n_students, n_questions, n_saves):
    from app.v2 import student as student_mod

    tmp = Path(tempfile.mkdtemp(prefix="autosave-bench-"))
    db_path = tmp / "bench.db"
    eng = _build_engine(db_path)
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    S = async_sessionmaker(eng, expire_on_commit=False, autoflush=False)
    old = student_mod.AsyncSessionLocal
    student_mod.AsyncSessionLocal = S
    try:
        quiz_id, qids, sids = await _seed(S, n_students, n_questions)
        latencies: list[float] = []
        fails: list = []
        t0 = time.perf_counter()
        await asyncio.gather(*(
            _one_student(student_mod, quiz_id, qids, sid, n_saves, latencies, fails)
            for sid in sids))
        wall = time.perf_counter() - t0
        # تأكّد من سلامة البيانات: كلّ تلميذ خزّن كلّ أسئلته مسوّدةً.
        from sqlalchemy import func, select
        async with S() as s:
            rows = await s.scalar(select(func.count()).select_from(Answer))
            drafts = await s.scalar(select(func.count()).select_from(Answer)
                                    .where(Answer.submitted.is_(False)))
    finally:
        student_mod.AsyncSessionLocal = old
        await eng.dispose()
        for f in tmp.glob("*"):
            f.unlink(missing_ok=True)
        tmp.rmdir()
    return latencies, fails, wall, rows, drafts


def _pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


def main():
    ap = argparse.ArgumentParser(description="قياس حِمل الحفظ التزايُديّ")
    ap.add_argument("--students", type=int, default=45)
    ap.add_argument("--questions", type=int, default=15)
    ap.add_argument("--saves", type=int, default=15, help="عدد الحفظ التزايُديّ لكلّ تلميذ")
    a = ap.parse_args()

    latencies, fails, wall, rows, drafts = asyncio.run(
        _run(a.students, a.questions, a.saves))

    total = len(latencies) + len(fails)
    print(f"\n=== قياس الحفظ التزايُديّ: {a.students} تلميذاً × {a.saves} حفظاً "
          f"على {a.questions} سؤالاً ===")
    print(f"طلبات الحفظ: {total}  ·  نجح: {len(latencies)}  ·  فشل: {len(fails)}")
    if latencies:
        print(f"زمن الطلب (مِلّي ثانية):  وسيط {statistics.median(latencies)*1000:.0f}"
              f"  ·  p95 {_pct(latencies, 95)*1000:.0f}"
              f"  ·  أقصى {max(latencies)*1000:.0f}"
              f"  ·  متوسّط {statistics.mean(latencies)*1000:.0f}")
    print(f"الزمن الكلّيّ: {wall:.2f}ث  ·  الإنتاجيّة: {total/max(wall,1e-9):.0f} حفظ/ث")
    print(f"سلامة البيانات: {rows} جواباً مخزّناً ({drafts} مسوّدة) "
          f"— المتوقَّع {a.students * a.questions}")
    if fails:
        print("\n⚠ إخفاقات (عيّنة):")
        for sid, info in fails[:10]:
            print(f"  تلميذ {sid}: {info}")
    ok = not fails and rows == a.students * a.questions
    print("\n" + ("✓ لا اختناق ولا فقد بيانات." if ok
                  else "⚠ راجِع النتيجة أعلاه — قد يلزم تحسين مسار الحفظ."))


if __name__ == "__main__":
    main()
