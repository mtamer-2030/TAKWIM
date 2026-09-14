"""ميزتان طلبهما الأستاذ:
1) حذف جلسةٍ تجريبيّة يمحو أثرها من التقارير (أجوبة المشاركين لذلك التقويم).
2) استيراد لائحة نقطٍ حقيقيّة (رمز مسار + النقطة) → تدخل دفتر النقط كنقطةٍ مصادَقٍ عليها.
"""

import asyncio
import io
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.importer import parse_marks_excel
from app.models import (Answer, Base, Level, Quiz, QuizQuestion, QuizSession,
                        SessionStudent, Student)
from app.v2.web import ADMIN_COOKIE, issue_admin_token


def _admin_req():
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, headers={},
                           client=SimpleNamespace(host="9.9.9.9"),
                           query_params={})


class _UploadStub:
    def __init__(self, data: bytes, filename: str):
        self._data = data
        self.filename = filename

    async def read(self):
        return self._data


# ═══════════════ محلّل لائحة النقط ═══════════════


def test_parse_marks_csv_matches_columns_and_decimal():
    csv = "رمز مسار,النقطة\nD161055238,14.5\nR987654321,\"11,5\"\n,\nBADCODE,أربعة\n"
    r = parse_marks_excel(csv.encode("utf-8"), "notes.csv")
    assert r.ok
    assert len(r.rows) == 2                       # صفٌّ فارغ وصفٌّ بنقطةٍ غير رقميّة أُسقِطا
    assert r.rows[0].massar_code == "D161055238" and r.rows[0].score == 14.5
    assert r.rows[1].score == 11.5                # فاصلة عربيّة/فرنسيّة
    assert any("غير رقميّة" in e for e in r.errors)


def test_parse_marks_missing_column_reports_error():
    r = parse_marks_excel("الاسم,القسم\nمحمد,TC1\n".encode("utf-8"), "x.csv")
    assert not r.ok and any("النقطة" in e for e in r.errors)


# ═══════════════ استيراد النقط عبر المسار ═══════════════


def test_marks_import_creates_confirmed_scores(monkeypatch):
    from app.v2 import admin as admin_mod
    cap = {}

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(
            admin_mod.templates, "TemplateResponse",
            lambda name, ctx, status_code=200: cap.update(name=name, **ctx)
            or SimpleNamespace(status_code=status_code))
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            s.add_all([
                Student(full_name="أ", level_id=lv.id, group_name="TC1",
                        massar_code="D161055238", active=True),
                Student(full_name="ب", level_id=lv.id, group_name="TC1",
                        massar_code="R987654321", active=True),
            ])
            await s.commit()
        csv = "رمز مسار,النقطة\nD161055238,14\nR987654321,9\nZZZ999,20\n"
        resp = await admin_mod.marks_import(
            _admin_req(), title="الفرض 1", max_score=20.0, kind="exam",
            group="TC1", file=_UploadStub(csv.encode("utf-8"), "n.csv"))
        async with S() as s:
            quizzes = (await s.execute(select(Quiz))).scalars().all()
            answers = (await s.execute(select(Answer))).scalars().all()
            confirmed = [a for a in answers if a.teacher_confirmed and a.submitted]
            total_max = (await s.execute(select(func.sum(QuizQuestion.max_score)))).scalar()
        await eng.dispose()
        return resp, quizzes, answers, confirmed, total_max

    resp, quizzes, answers, confirmed, total_max = asyncio.run(go())
    assert getattr(resp, "status_code", None) == 303           # نجاح → تحويل
    assert "matched=2" in resp.headers["location"]             # طُوبِق تلميذان
    assert "skipped=1" in resp.headers["location"]             # ZZZ999 غير مسجّل
    assert len(quizzes) == 1 and total_max == 20.0             # تقويمٌ واحدٌ بسؤالٍ /20
    assert len(confirmed) == 2                                 # نقطتان مصادَقٌ عليهما
    assert sorted(a.manual_score for a in confirmed) == [9.0, 14.0]


def test_marks_import_bad_file_shows_error(monkeypatch):
    from app.v2 import admin as admin_mod
    cap = {}

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(
            admin_mod.templates, "TemplateResponse",
            lambda name, ctx, status_code=200: cap.update(name=name, status=status_code, **ctx)
            or SimpleNamespace(status_code=status_code))
        await admin_mod.marks_import(
            _admin_req(), title="x", max_score=20.0, kind="exam", group="",
            file=_UploadStub("الاسم\nمحمد\n".encode("utf-8"), "x.csv"))
        await eng.dispose()

    asyncio.run(go())
    assert cap["name"] == "admin/marks.html" and cap["status"] == 400 and cap["error"]


# ═══════════════ حذف جلسةٍ يمحو أثرها ═══════════════


def test_session_delete_purges_participant_answers(monkeypatch):
    from app.v2 import admin as admin_mod

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            st1 = Student(full_name="مشارك", level_id=lv.id, group_name="TC1", active=True)
            st2 = Student(full_name="غير مشارك", level_id=lv.id, group_name="TC1", active=True)
            s.add_all([st1, st2]); await s.flush()
            quiz = Quiz(title="ت", kind="exam", level_id=lv.id); s.add(quiz); await s.flush()
            q = QuizQuestion(quiz_id=quiz.id, qtype="mcq_single", prompt="س",
                             payload={}, max_score=1, position=0); s.add(q); await s.flush()
            sess = QuizSession(quiz_id=quiz.id, group_name="TC1", status="open")
            s.add(sess); await s.flush()
            s.add(SessionStudent(session_id=sess.id, student_id=st1.id, present=True))
            # جواب المشارك (يجب أن يُحذف) وجواب غير المشارك (يجب أن يبقى)
            s.add(Answer(quiz_question_id=q.id, student_id=st1.id, raw={"choice": 0},
                         auto_score=1, teacher_confirmed=True, submitted=True))
            s.add(Answer(quiz_question_id=q.id, student_id=st2.id, raw={"choice": 0},
                         auto_score=1, teacher_confirmed=True, submitted=True))
            await s.commit()
            sid = sess.id
        await admin_mod.session_delete(_admin_req(), sid)
        async with S() as s:
            remaining = (await s.execute(select(Answer))).scalars().all()
            sessions = (await s.execute(select(QuizSession))).scalars().all()
            names = {(await s.get(Student, a.student_id)).full_name for a in remaining}
        await eng.dispose()
        return remaining, sessions, names

    remaining, sessions, names = asyncio.run(go())
    assert sessions == []                              # الجلسة حُذفت
    assert len(remaining) == 1 and names == {"غير مشارك"}   # أثر المشارك فقط مُحي


def test_grading_clear_answers_removes_stuck_answers(monkeypatch):
    """زرّ «حذف الأجوبة» في قائمة التصحيح يمسح كلّ أجوبة تقويمٍ (جلسات مشوّهة) ويُبقي
    أسئلته وأجوبةَ تقويمٍ آخر."""
    from app.v2 import admin as admin_mod

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            st = Student(full_name="ت", level_id=lv.id, group_name="TC1", active=True)
            s.add(st); await s.flush()
            qa = Quiz(title="مشوّه", kind="exam", level_id=lv.id)
            qb = Quiz(title="سليم", kind="exam", level_id=lv.id)
            s.add_all([qa, qb]); await s.flush()
            a1 = QuizQuestion(quiz_id=qa.id, qtype="mcq_single", prompt="س", payload={},
                              max_score=1, position=0)
            b1 = QuizQuestion(quiz_id=qb.id, qtype="mcq_single", prompt="س", payload={},
                              max_score=1, position=0)
            s.add_all([a1, b1]); await s.flush()
            s.add(Answer(quiz_question_id=a1.id, student_id=st.id, raw={"choice": 0},
                         auto_score=1, teacher_confirmed=True, submitted=True))
            s.add(Answer(quiz_question_id=b1.id, student_id=st.id, raw={"choice": 0},
                         auto_score=1, teacher_confirmed=True, submitted=True))
            await s.commit()
            qa_id, aq_id, bq_id = qa.id, a1.id, b1.id
        resp = await admin_mod.grading_clear_answers(_admin_req(), qa_id)
        async with S() as s:
            ans = (await s.execute(select(Answer))).scalars().all()
            qq = (await s.execute(select(QuizQuestion))).scalars().all()
            left_qids = {a.quiz_question_id for a in ans}
        await eng.dispose()
        return resp, len(ans), left_qids, aq_id, bq_id, len(qq)

    resp, n_ans, left_qids, aq_id, bq_id, n_qq = asyncio.run(go())
    assert getattr(resp, "status_code", None) == 303
    assert "cleared=1" in resp.headers["location"]     # حُذفت إجابة واحدة
    assert n_ans == 1 and left_qids == {bq_id}          # بقيت أجوبة التقويم السليم فقط
    assert n_qq == 2                                    # الأسئلة لم تُمسّ (التقويم يبقى)
