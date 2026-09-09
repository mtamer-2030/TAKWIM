"""واجهة المتعلّم v2 (/student) — هاتف أوّلاً، HTMX للتنقّل، Alpine للتفاعلات.

الولوج برمز مسار أو رمز الدخول فقط (بلا كلمة سرّ ولا بريد). التنقّل بين الدروس
والتقاويم والنقط عبر HTMX (SPA سريع). عرض النصوص للقراءة على الهاتف، وpdf.js
وVideo.js جاهزان للوسائط.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select

from sqlalchemy.orm import selectinload

from ..database import AsyncSessionLocal
from ..models import (
    Axis,
    Module,
    PhilosophicalText,
    Quiz,
    QuizAnswer,
    QuizQuestion,
    Student,
    Submission,
)
from ..services.quizzes import grade_answer
from .web import STUDENT_COOKIE, current_student_id, templates

router = APIRouter(prefix="/student")


def _ctx(request: Request, **extra):
    return {"request": request, **extra}


async def _load_student(request: Request) -> Student | None:
    sid = current_student_id(request)
    if not sid:
        return None
    async with AsyncSessionLocal() as s:
        return await s.get(Student, sid)


# ═══════════════ الولوج ═══════════════


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("student/login.html", _ctx(request))


@router.post("/login")
async def login(request: Request, code: str = Form(...)):
    code = code.strip().upper()
    async with AsyncSessionLocal() as s:
        student = await s.scalar(
            select(Student).where(
                Student.active.is_(True),
                or_(Student.massar_code == code, Student.login_code == code)))
    if student is None:
        return templates.TemplateResponse(
            "student/login.html",
            _ctx(request, error="رمز غير معروف. تأكّد من رمز مسار أو رمز الدخول."),
            status_code=401)
    resp = RedirectResponse("/student/home", status_code=303)
    resp.set_cookie(STUDENT_COOKIE, str(student.id), httponly=True, samesite="lax")
    return resp


@router.get("/logout")
def logout(request: Request):
    resp = RedirectResponse("/student", status_code=303)
    resp.delete_cookie(STUDENT_COOKIE)
    return resp


# ═══════════════ الشاشة الرئيسة (قوقعة HTMX) ═══════════════


@router.get("/home", response_class=HTMLResponse)
async def home(request: Request):
    student = await _load_student(request)
    if student is None:
        return RedirectResponse("/student", status_code=303)
    return templates.TemplateResponse("student/home.html", _ctx(request, student=student))


# ——— أجزاء HTMX ———


@router.get("/tab/lessons", response_class=HTMLResponse)
async def tab_lessons(request: Request):
    student = await _load_student(request)
    if student is None:
        return HTMLResponse("انتهت الجلسة", status_code=401)
    async with AsyncSessionLocal() as s:
        # نصوص مستوى التلميذ (عبر المحور←المجزوءة←المستوى)
        rows = (await s.execute(
            select(PhilosophicalText, Axis.title)
            .join(Axis, Axis.id == PhilosophicalText.axis_id)
            .join(Module, Module.id == Axis.module_id)
            .where(Module.level_id == student.level_id)
            .order_by(PhilosophicalText.created_at.desc()))).all()
    return templates.TemplateResponse(
        "student/_lessons.html", _ctx(request, rows=rows))


def _quiz_visible_to(student: Student):
    """شرط رؤية التلميذ للتقويم: منشور + يطابق مستواه (أو عامّ) + فوجه (أو عامّ)."""
    return (
        Quiz.published.is_(True),
        or_(Quiz.level_id.is_(None), Quiz.level_id == student.level_id),
        or_(Quiz.group_name.is_(None), Quiz.group_name == student.group_name),
    )


@router.get("/tab/assessments", response_class=HTMLResponse)
async def tab_assessments(request: Request):
    student = await _load_student(request)
    if student is None:
        return HTMLResponse("انتهت الجلسة", status_code=401)
    async with AsyncSessionLocal() as s:
        quizzes = (await s.execute(
            select(Quiz).where(*_quiz_visible_to(student))
            .order_by(Quiz.created_at.desc()))).scalars().all()
        # التقاويم التي أجاب عنها التلميذ (لعرض «مُنجَز»)
        done = set((await s.execute(
            select(QuizQuestion.quiz_id)
            .join(QuizAnswer, QuizAnswer.question_id == QuizQuestion.id)
            .where(QuizAnswer.student_id == student.id).distinct())).scalars().all())
    return templates.TemplateResponse(
        "student/_assessments.html", _ctx(request, quizzes=quizzes, done=done))


@router.get("/quiz/{quiz_id}", response_class=HTMLResponse)
async def take_quiz(request: Request, quiz_id: int):
    student = await _load_student(request)
    if student is None:
        return RedirectResponse("/student", status_code=303)
    async with AsyncSessionLocal() as s:
        quiz = (await s.execute(
            select(Quiz).where(Quiz.id == quiz_id, *_quiz_visible_to(student))
            .options(selectinload(Quiz.questions)))).scalar_one_or_none()
    if quiz is None:
        return HTMLResponse("التقويم غير متاح", status_code=404)
    return templates.TemplateResponse(
        "student/quiz.html", _ctx(request, quiz=quiz, student=student))


def _raw_from_form(q: QuizQuestion, form) -> dict:
    """يعيد بناء جواب التلميذ الخام حسب نوع السؤال من حقول النموذج."""
    key = f"q_{q.id}"
    payload = q.payload or {}
    if q.qtype == "mcq_single":
        v = form.get(key)
        return {"choice": int(v)} if v not in (None, "") and str(v).lstrip("-").isdigit() else {}
    if q.qtype == "mcq_multi":
        vals = form.getlist(key)
        return {"choices": [int(x) for x in vals if str(x).isdigit()]}
    if q.qtype == "classify":
        n = len(payload.get("items", []))
        out = []
        for k in range(n):
            v = form.get(f"{key}_item_{k}")
            out.append(int(v) if v not in (None, "") and str(v).isdigit() else None)
        return {"assignments": out}
    if q.qtype == "order":
        n = len(payload.get("items", []))
        return {"order": [int(form.get(f"{key}_slot_{k}", -1) or -1) for k in range(n)]}
    if q.qtype in ("short_text", "long_text"):
        return {"text": (form.get(f"{key}_text") or "").strip()}
    if q.qtype == "grid":
        cols = payload.get("columns", [])
        rows = int(payload.get("rows", 0) or 0)
        cells = [[(form.get(f"{key}_c_{ri}_{ci}") or "").strip() for ci in range(len(cols))]
                 for ri in range(rows)]
        return {"cells": cells}
    return {}


@router.post("/quiz/{quiz_id}/submit", response_class=HTMLResponse)
async def submit_quiz(request: Request, quiz_id: int):
    student = await _load_student(request)
    if student is None:
        return RedirectResponse("/student", status_code=303)
    form = await request.form()
    async with AsyncSessionLocal() as s:
        quiz = (await s.execute(
            select(Quiz).where(Quiz.id == quiz_id, *_quiz_visible_to(student))
            .options(selectinload(Quiz.questions)))).scalar_one_or_none()
        if quiz is None:
            return HTMLResponse("التقويم غير متاح", status_code=404)

        results = []
        for q in quiz.questions:
            raw = _raw_from_form(q, form)
            graded = grade_answer(q, raw)
            # حفظ/تحديث الجواب (فريد لكلّ سؤال+تلميذ)
            existing = await s.scalar(select(QuizAnswer).where(
                QuizAnswer.question_id == q.id, QuizAnswer.student_id == student.id))
            if existing:
                existing.raw = raw
                existing.auto_score = graded["score"]
            else:
                s.add(QuizAnswer(question_id=q.id, student_id=student.id,
                                 raw=raw, auto_score=graded["score"]))
            results.append({"q": q, "graded": graded})
        await s.commit()
        # فصل النتائج عن الجلسة قبل الإغلاق: نجمع ما تحتاجه القالب فقط.
        feedback = [{
            "prompt": r["q"].prompt, "qtype": r["q"].qtype,
            "score": r["graded"]["score"], "max_score": r["graded"]["max_score"],
            "auto": r["graded"]["auto"], "feedback": r["graded"]["feedback"],
        } for r in results]
    reveal = quiz.reveal_feedback
    return templates.TemplateResponse(
        "student/quiz_result.html",
        _ctx(request, quiz_title=quiz.title, feedback=feedback, reveal=reveal))


@router.get("/tab/scores", response_class=HTMLResponse)
async def tab_scores(request: Request):
    student = await _load_student(request)
    if student is None:
        return HTMLResponse("انتهت الجلسة", status_code=401)
    async with AsyncSessionLocal() as s:
        subs = (await s.execute(
            select(Submission).where(
                Submission.student_id == student.id,
                Submission.teacher_confirmed.is_(True))
            .order_by(Submission.submitted_at.desc()))).scalars().all()
    return templates.TemplateResponse("student/_scores.html", _ctx(request, subs=subs))


@router.get("/text/{text_id}", response_class=HTMLResponse)
async def read_text(request: Request, text_id: int):
    student = await _load_student(request)
    if student is None:
        return RedirectResponse("/student", status_code=303)
    async with AsyncSessionLocal() as s:
        text = await s.get(PhilosophicalText, text_id)
    if text is None:
        return HTMLResponse("النصّ غير موجود", status_code=404)
    return templates.TemplateResponse("student/text.html", _ctx(request, text=text))


@router.get("/reader", response_class=HTMLResponse)
async def pdf_reader(request: Request):
    """عارض pdf.js لملفّ PDF مرفوع (?file=/static/uploads/...). جاهز للنصوص المصوّرة."""
    if await _load_student(request) is None:
        return RedirectResponse("/student", status_code=303)
    file_url = request.query_params.get("file", "")
    return templates.TemplateResponse("student/reader.html", _ctx(request, file_url=file_url))
