"""لوحة الأستاذ v2 (/admin) — المُجمِّع: الاستيثاق واللوحة الرئيسة فقط، وضمّ مجالات
المسارات المفكَّكة في `routers/*` عبر include_router (السلوك والمسارات لا تتغيّر).

كلّ مجالٍ (الشبكة/التقارير/النقط/الجلسات/التقاويم/النصوص/الذكاء/اللوائح/المنهاج) في
وحدةٍ مستقلّةٍ تحت `app/v2/routers/` — للصيانة. لتفاصيل الخريطة: `docs/فهرس_المشروع.md`.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from ..database import AsyncSessionLocal
from ..models import (Answer, EssayExercise, PhilosophicalText, Quiz, QuizSession,
                      SessionStudent, Student)
from ..netinfo import lan_url
from ..settings import settings
from .web import (
    ADMIN_COOKIE,
    _ctx,
    check_admin_password,
    issue_admin_token,
    next_login_delay,
    record_login_result,
    require_admin,
    revoke_admin_token,
    templates,
)
from .routers import ai as _ai
from .routers import curriculum as _curriculum
from .routers import marks as _marks
from .routers import network as _network
from .routers import quizzes as _quizzes
from .routers import reports as _reports
from .routers import rosters as _rosters
from .routers import sessions as _sessions
from .routers import texts as _texts
from .routers.network import _student_url

router = APIRouter(prefix="/admin")
# مجالات مفكَّكةٌ في routers/* (السلوك والمسارات لا تتغيّر بالتفكيك).
router.include_router(_network.router)      # الشبكة/QR/البطاقات/النسخ
router.include_router(_reports.router)      # التقارير التراكمية (دفتر النقط)
router.include_router(_marks.router)        # استيراد لائحة النقط
router.include_router(_sessions.router)     # الجلسات الصفّية والمتابعة الآنية
router.include_router(_quizzes.router)      # التقاويم بالأسئلة + التصحيح
router.include_router(_texts.router)        # النصوص الفلسفيّة
router.include_router(_ai.router)           # التدخّل العلاجي (الذكاء الاصطناعي)
router.include_router(_rosters.router)      # اللوائح والاستيراد وأداة المقارنة
router.include_router(_curriculum.router)   # المنهاج (مستويات/مجزوءات/محاور)


# ═══════════════ الاستيثاق ═══════════════


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("admin/login.html", _ctx(request))


@router.post("/login")
async def login(request: Request, password: str = Form(...)):
    # تأخير تصاعدي على الفشل المتتالي (ح-٨) — يبطّئ التخمين قبل الردّ.
    delay = next_login_delay()
    if delay:
        await asyncio.sleep(delay)
    if not check_admin_password(password):
        record_login_result(False)
        return templates.TemplateResponse(
            "admin/login.html", _ctx(request, error="كلمة السرّ غير صحيحة."),
            status_code=401)
    record_login_result(True)
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(ADMIN_COOKIE, issue_admin_token(), httponly=True, samesite="lax")
    return resp


@router.get("/logout")
def logout(request: Request):
    revoke_admin_token(request.cookies.get(ADMIN_COOKIE))
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie(ADMIN_COOKIE)
    return resp


# ═══════════════ اللوحة ═══════════════


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if (g := require_admin(request)):
        return g
    from datetime import date, datetime, time
    today_start = datetime.combine(date.today(), time.min)
    async with AsyncSessionLocal() as s:
        counts = {
            "students": await s.scalar(select(func.count()).select_from(Student)),
            "texts": await s.scalar(select(func.count()).select_from(PhilosophicalText)),
            "essays": await s.scalar(select(func.count()).select_from(EssayExercise)),
            # الأجوبة المُسلَّمة نهائياً فقط، لا مسوّدات الحفظ التدريجي (ح-١٣).
            "submissions": await s.scalar(
                select(func.count()).select_from(Answer).where(Answer.submitted.is_(True))),
        }
        # ٤-ب: بدل توزيع المستويات التجريبيّ — ما يهمّ الأستاذ اليوم فعلاً.
        today = {
            # جلسات فُتِحت اليوم.
            "sessions": await s.scalar(
                select(func.count()).select_from(QuizSession)
                .where(QuizSession.opened_at >= today_start)),
            # أجوبة سُلِّمت نهائياً وتنتظر مصادقة الأستاذ (لم تُحتسب في التحليل بعد).
            "ungraded": await s.scalar(
                select(func.count()).select_from(Answer)
                .where(Answer.submitted.is_(True), Answer.teacher_confirmed.is_(False))),
            # حاضرون في جلسات مفتوحة لم يُسلّموا بعد.
            "not_submitted": await s.scalar(
                select(func.count()).select_from(SessionStudent)
                .join(QuizSession, QuizSession.id == SessionStudent.session_id)
                .where(QuizSession.status == "open", SessionStudent.present.is_(True),
                       SessionStudent.submitted_at.is_(None))),
        }
        # قائمة الجلسات المفتوحة الآن مع حضورها وتسليمها (روابط للمتابعة الآنية).
        open_sessions = []
        for sid, title, group in (await s.execute(
                select(QuizSession.id, Quiz.title, QuizSession.group_name)
                .join(Quiz, Quiz.id == QuizSession.quiz_id)
                .where(QuizSession.status == "open")
                .order_by(QuizSession.opened_at.desc()))).all():
            present = await s.scalar(select(func.count()).select_from(SessionStudent)
                .where(SessionStudent.session_id == sid, SessionStudent.present.is_(True)))
            subs = await s.scalar(select(func.count()).select_from(SessionStudent)
                .where(SessionStudent.session_id == sid,
                       SessionStudent.submitted_at.isnot(None)))
            open_sessions.append({"id": sid, "title": title, "group": group,
                                  "present": present, "subs": subs})
    base = _student_url()                       # عنوان الشبكة المحلّية المكتشَف تلقائياً
    return templates.TemplateResponse(
        "admin/dashboard.html",
        _ctx(request, counts=counts, today=today, open_sessions=open_sessions,
             lan_base=base, student_url=f"{base}/student", auto=bool(lan_url(settings.port))))


@router.get("/open-session-badge", response_class=HTMLResponse)
async def open_session_badge(request: Request):
    """٤-ب: شارة «جلسة مفتوحة الآن» تظهر في كلّ صفحات اللوحة (تُحدَّث بـ HTMX).
    ترجع فراغاً إن لم تُوجَد جلسة مفتوحة أو لم يكن الطلب مستوثَقاً (بلا إعادة توجيه)."""
    if require_admin(request):
        return HTMLResponse("")
    async with AsyncSessionLocal() as s:
        n = await s.scalar(select(func.count()).select_from(QuizSession)
                           .where(QuizSession.status == "open"))
        row = (await s.execute(
            select(QuizSession.id, Quiz.title, QuizSession.group_name)
            .join(Quiz, Quiz.id == QuizSession.quiz_id)
            .where(QuizSession.status == "open")
            .order_by(QuizSession.opened_at.desc()).limit(1))).first()
    if not row:
        return HTMLResponse("")
    sid, title, group = row
    more = f" (+{n - 1})" if n and n > 1 else ""
    return templates.TemplateResponse(
        "admin/_open_session_badge.html",
        _ctx(request, sid=sid, title=title, group=group, more=more))



