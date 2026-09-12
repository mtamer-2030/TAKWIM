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
    Answer,
    QuizQuestion,
    QuizSession,
    SessionStudent,
    Student,
)
from ..constants import QUESTION_TYPES_CLOSED
from ..services.analytics import generate_student_skill_profile
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


async def _find_by_code(code: str) -> Student | None:
    code = code.strip().upper()
    async with AsyncSessionLocal() as s:
        return await s.scalar(
            select(Student).where(
                Student.active.is_(True),
                or_(Student.massar_code == code, Student.login_code == code)))


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request, code: str = Form(...)):
    """الخطوة 1: التحقّق من الرمز وعرض اسم التلميذ للتأكيد (كما في v1)،
    فلا يُثبَّت الدخول قبل أن يؤكّد التلميذ أنّ الاسم اسمه."""
    student = await _find_by_code(code)
    if student is None:
        return templates.TemplateResponse(
            "student/login.html",
            _ctx(request, error="رمز غير معروف. تأكّد من رمز مسار أو رمز الدخول."),
            status_code=401)
    return templates.TemplateResponse(
        "student/confirm.html",
        _ctx(request, student=student, code=code.strip().upper()))


@router.post("/login/confirm")
async def login_confirm(request: Request, code: str = Form(...)):
    """الخطوة 2: بعد أن رأى التلميذ اسمه وأكّده، يُثبَّت الدخول (Cookie)."""
    student = await _find_by_code(code)
    if student is None:
        return RedirectResponse("/student", status_code=303)
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


def _homework_visible_to(student: Student):
    """شرط رؤية التقويم المنشور (منزليّ): منشور + يطابق مستواه/فوجه (أو عامّ)."""
    return (
        Quiz.published.is_(True),
        or_(Quiz.level_id.is_(None), Quiz.level_id == student.level_id),
        or_(Quiz.group_name.is_(None), Quiz.group_name == student.group_name),
    )


DEVICE_COOKIE = "pt_device"


async def _open_session_for(s, student: Student, quiz_id: int):
    """يعيد (الجلسة المفتوحة، سجلّ مشاركة التلميذ) لتقويمٍ في فوج التلميذ، أو (None, None)."""
    sess = await s.scalar(
        select(QuizSession).where(
            QuizSession.quiz_id == quiz_id,
            QuizSession.group_name == student.group_name,
            QuizSession.status == "open"))
    if sess is None:
        return None, None
    part = await s.scalar(select(SessionStudent).where(
        SessionStudent.session_id == sess.id, SessionStudent.student_id == student.id))
    return sess, part


@router.get("/tab/assessments", response_class=HTMLResponse)
async def tab_assessments(request: Request):
    student = await _load_student(request)
    if student is None:
        return HTMLResponse("انتهت الجلسة", status_code=401)
    async with AsyncSessionLocal() as s:
        # (1) تقاويم الجلسات المفتوحة لفوج التلميذ حيث هو حاضر
        session_rows = (await s.execute(
            select(Quiz).join(QuizSession, QuizSession.quiz_id == Quiz.id)
            .join(SessionStudent, SessionStudent.session_id == QuizSession.id)
            .where(QuizSession.status == "open",
                   QuizSession.group_name == student.group_name,
                   SessionStudent.student_id == student.id,
                   SessionStudent.present.is_(True)).distinct())).scalars().all()
        # (2) التقاويم المنشورة (منزليّة)
        homework = (await s.execute(
            select(Quiz).where(*_homework_visible_to(student))
            .order_by(Quiz.created_at.desc()))).scalars().all()
        done = set((await s.execute(
            select(QuizQuestion.quiz_id)
            .join(Answer, Answer.quiz_question_id == QuizQuestion.id)
            .where(Answer.student_id == student.id).distinct())).scalars().all())
    session_ids = {q.id for q in session_rows}
    homework = [q for q in homework if q.id not in session_ids]   # لا تكرار
    return templates.TemplateResponse(
        "student/_assessments.html",
        _ctx(request, live_quizzes=session_rows, quizzes=homework, done=done))


@router.get("/quiz/{quiz_id}", response_class=HTMLResponse)
async def take_quiz(request: Request, quiz_id: int):
    student = await _load_student(request)
    if student is None:
        return RedirectResponse("/student", status_code=303)
    import secrets
    from datetime import datetime
    async with AsyncSessionLocal() as s:
        quiz = (await s.execute(
            select(Quiz).where(Quiz.id == quiz_id)
            .options(selectinload(Quiz.questions)))).scalar_one_or_none()
        if quiz is None:
            return HTMLResponse("التقويم غير متاح", status_code=404)

        sess, part = await _open_session_for(s, student, quiz_id)
        set_token = None
        if sess is not None:
            # وضع الجلسة الصفّية: بوابة حضور + قفل جهاز
            if part is None or not part.present:
                return HTMLResponse(
                    _blocked("أنت مسجّل غائباً في هذه الجلسة. راجع الأستاذ."), status_code=403)
            dev = request.cookies.get(DEVICE_COOKIE)
            if part.device_token is None:
                set_token = dev or secrets.token_hex(16)
                part.device_token = set_token
                part.joined_at = datetime.now()
                await s.commit()
            elif dev != part.device_token:
                return HTMLResponse(
                    _blocked("هذا التقويم مقفل على جهاز آخر. اطلب من الأستاذ فكّ القفل."),
                    status_code=403)
        elif not quiz.published:
            # لا جلسة مفتوحة ولا منشور
            return HTMLResponse(
                _blocked("لا تقويم مفتوح الآن. سيفتحه الأستاذ في حينه."), status_code=403)

        resp = templates.TemplateResponse(
            "student/quiz.html", _ctx(request, quiz=quiz, student=student))
    if set_token:
        resp.set_cookie(DEVICE_COOKIE, set_token, httponly=True, samesite="lax")
    return resp


def _blocked(msg: str) -> str:
    """صفحة رفض بسيطة للتلميذ مع رابط رجوع."""
    return (f'<div style="font-family:sans-serif;direction:rtl;text-align:center;padding:2rem">'
            f'<p style="font-size:1.1rem">{msg}</p>'
            f'<a href="/student/home" style="color:#0d47a1">→ رجوع</a></div>')


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
    from datetime import datetime
    form = await request.form()
    async with AsyncSessionLocal() as s:
        quiz = (await s.execute(
            select(Quiz).where(Quiz.id == quiz_id)
            .options(selectinload(Quiz.questions)))).scalar_one_or_none()
        if quiz is None:
            return HTMLResponse("التقويم غير متاح", status_code=404)

        # يجب أن يكون التقويم متاحاً: جلسة مفتوحة (حاضر) أو منشور منزليّ.
        sess, part = await _open_session_for(s, student, quiz_id)
        if sess is not None:
            if part is None or not part.present:
                return HTMLResponse(_blocked("لست مسجّلاً في هذه الجلسة."), status_code=403)
        elif not quiz.published:
            return HTMLResponse(_blocked("انتهت الجلسة أو أُغلقت."), status_code=403)

        results = []
        for q in quiz.questions:
            raw = _raw_from_form(q, form)
            graded = grade_answer(q, raw)
            # المصادقة الآلية للأسئلة المغلقة (تصحيح يقيني) — تدخل التقارير فوراً كما في v1.
            # المفتوحة تبقى غير مصادَقة حتى يراجعها الأستاذ في شاشة التصحيح.
            auto_confirm = q.qtype in QUESTION_TYPES_CLOSED
            # حفظ/تحديث الجواب (فريد لكلّ سؤال+تلميذ)
            existing = await s.scalar(select(Answer).where(
                Answer.quiz_question_id == q.id, Answer.student_id == student.id))
            if existing:
                existing.raw = raw
                existing.auto_score = graded["score"]
                existing.teacher_confirmed = auto_confirm
            else:
                s.add(Answer(quiz_question_id=q.id, student_id=student.id,
                             raw=raw, auto_score=graded["score"],
                             teacher_confirmed=auto_confirm))
            results.append({"q": q, "graded": graded})
        # تعليم التسليم في الجلسة (للمتابعة الآنية)
        if sess is not None and part is not None:
            part.submitted_at = datetime.now()
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


_SKILL_BANDS = [
    (0.8, "متمكّن", "#2e7d32"),
    (0.6, "جيّد", "#558b2f"),
    (0.4, "في تطوّر", "#f9a825"),
    (0.0, "يحتاج تدعيماً", "#c62828"),
]


def _band(avg: float | None) -> tuple[str, str]:
    """يحوّل متوسّط المهارة (0..1) إلى وصف نوعيّ ولون — بلا رقم."""
    if avg is None:
        return ("لم يُقيَّم بعد", "#9e9e9e")
    for threshold, label, color in _SKILL_BANDS:
        if avg >= threshold:
            return (label, color)
    return ("يحتاج تدعيماً", "#c62828")


@router.get("/tab/progress", response_class=HTMLResponse)
async def tab_progress(request: Request):
    """تقدّم التلميذ في المهارات الخمس: رادار بصريّ + وصف نوعيّ — بلا أي نقطة رقمية."""
    student = await _load_student(request)
    if student is None:
        return HTMLResponse("انتهت الجلسة", status_code=401)
    async with AsyncSessionLocal() as s:
        profile = await generate_student_skill_profile(s, student.id)
    skills = profile.get("skills", {})
    labels = list(skills.keys())
    # القيم للرادار فقط (المحور مخفيّ الأرقام)؛ الغياب يُرسم صفراً بصريّاً.
    values = [round((skills[k]["avg"] or 0.0), 4) for k in labels]
    bands = []
    for k in labels:
        label, color = _band(skills[k]["avg"])
        bands.append({"skill": k, "label": label, "color": color,
                      "has_data": skills[k]["count"] > 0})
    import json as _json
    return templates.TemplateResponse(
        "student/_progress.html",
        _ctx(request, has_data=profile.get("has_data", False), bands=bands,
             radar=_json.dumps({"labels": labels, "values": values})))


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
