"""واجهة المتعلّم v2 (/student) — هاتف أوّلاً، HTMX للتنقّل، Alpine للتفاعلات.

الولوج برمز مسار أو رمز الدخول فقط (بلا كلمة سرّ ولا بريد). التنقّل بين الدروس
والتقاويم والنقط عبر HTMX (SPA سريع). عرض النصوص للقراءة على الهاتف، وpdf.js
وVideo.js جاهزان للوسائط.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select

from ..database import AsyncSessionLocal
from ..models import (
    Axis,
    Level,
    Module,
    PhilosophicalText,
    Student,
    Submission,
)
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


@router.get("/tab/assessments", response_class=HTMLResponse)
async def tab_assessments(request: Request):
    student = await _load_student(request)
    if student is None:
        return HTMLResponse("انتهت الجلسة", status_code=401)
    # تدفّق التقاويم/الإجابة يُبنى لاحقاً (أو يُخدَم من نظام v1). عرض تمهيدي.
    return templates.TemplateResponse("student/_assessments.html", _ctx(request))


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
