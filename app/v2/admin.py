"""لوحة الأستاذ v2 (/admin) — Tabler + Chart.js + سير عمل الاستيراد الثنائي.

سير الاستيراد: رفع الملفّ ← استخراج النصّ وعرضه في حقل قابل للتعديل (مراجعة
الأستاذ وإصلاح أخطاء OCR) ← الحفظ النهائي في القاعدة (نماذج SQLAlchemy).
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import case, delete as sa_delete, func, select

from ..ai_feedback import (
    AIUnavailable,
    extract_quiz_json,
    generate_class_plan,
    generate_student_plan,
    ollama_available,
    ping_generate,
    suggest_open_score,
)
from ..cloud_ai import CloudAIUnavailable, cloud_available, extract_quiz_cloud
from ..database import AsyncSessionLocal
from ..docx_import import parse_docx, parse_lines
from ..docx_template import build_template_docx
from ..importer import ImporterError, extract_text, parse_marks_excel, parse_students_excel
from ..models import (
    Answer,
    Axis,
    ClassReport,
    Concept,
    EssayExercise,
    Level,
    Module,
    PhilosophicalText,
    Quiz,
    QuizQuestion,
    QuizSession,
    SessionStudent,
    Skill,
    Student,
    StudentReport,
    TextType,
)
from ..backup import backup_bytes, try_backup_quiet
from ..codes import make_login_code
from .. import presence
from ..netinfo import lan_ips, lan_url
from ..qrcodes import qr_png
from ..services.analytics import generate_class_report, generate_student_skill_profile
from ..services.gradebook import class_gradebook, student_gradebook
from ..constants import (COMPETENCIES, COMPETENCY_TO_SKILL, DISPLAY_TYPES, KINDS,
                         QUESTION_TYPES_CLOSED, level_of_class_label)
from ..services.quizzes import (
    QuizImportError,
    attach_answer_elements,
    build_quiz,
    grade_answer,
    heuristic_quiz_from_text,
    normalize_quiz_json,
    readable_answer,
    resolve_skill_id,
)
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
    skill_id_map,
    templates,
)
from .routers import ai as _ai
from .routers import marks as _marks
from .routers import network as _network
from .routers import quizzes as _quizzes
from .routers import reports as _reports
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


# ═══════════════ المنهاج (إضافة سريعة ليكون للنصوص هدف) ═══════════════


@router.get("/curriculum", response_class=HTMLResponse)
async def curriculum(request: Request):
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        levels = (await s.execute(select(Level).order_by(Level.position))).scalars().all()
        modules = (await s.execute(select(Module))).scalars().all()
        concepts = (await s.execute(select(Concept))).scalars().all()
        axes = (await s.execute(select(Axis))).scalars().all()
    return templates.TemplateResponse(
        "admin/curriculum.html",
        _ctx(request, levels=levels, modules=modules, concepts=concepts, axes=axes))


@router.post("/curriculum/module")
async def add_module(request: Request, level_id: int = Form(...), title: str = Form(...)):
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        s.add(Module(level_id=level_id, title=title.strip()))
        await s.commit()
    return RedirectResponse("/admin/curriculum", status_code=303)


@router.post("/curriculum/concept")
async def add_concept(request: Request, module_id: int = Form(...), title: str = Form(...)):
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        s.add(Concept(module_id=module_id, title=title.strip()))
        await s.commit()
    return RedirectResponse("/admin/curriculum", status_code=303)


@router.post("/curriculum/axis")
async def add_axis(request: Request, module_id: int = Form(...), title: str = Form(...),
                   concept_id: str = Form("")):
    if (g := require_admin(request)):
        return g
    cid = int(concept_id) if concept_id.strip().isdigit() else None
    async with AsyncSessionLocal() as s:
        s.add(Axis(module_id=module_id, concept_id=cid, title=title.strip()))
        await s.commit()
    return RedirectResponse("/admin/curriculum", status_code=303)


# — حذف عناصر المنهاج (حذف على مستوى القاعدة مع تتالي المفاتيح الأجنبية) —
# حذف المجزوءة يحذف محاورها ونصوصها وأسئلتها؛ إنجازات التلاميذ تبقى (question_id=NULL).


@router.post("/curriculum/module/{module_id}/delete")
async def delete_module(request: Request, module_id: int):
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        await s.execute(sa_delete(Module).where(Module.id == module_id))
        await s.commit()
    return RedirectResponse("/admin/curriculum", status_code=303)


@router.post("/curriculum/concept/{concept_id}/delete")
async def delete_concept(request: Request, concept_id: int):
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        await s.execute(sa_delete(Concept).where(Concept.id == concept_id))
        await s.commit()
    return RedirectResponse("/admin/curriculum", status_code=303)


@router.post("/curriculum/axis/{axis_id}/delete")
async def delete_axis(request: Request, axis_id: int):
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        await s.execute(sa_delete(Axis).where(Axis.id == axis_id))
        await s.commit()
    return RedirectResponse("/admin/curriculum", status_code=303)


# ═══════════════ سير عمل الاستيراد ═══════════════


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    if (g := require_admin(request)):
        return g
    return templates.TemplateResponse("admin/import.html", _ctx(request))


@router.post("/import/extract", response_class=HTMLResponse)
async def import_extract(request: Request, kind: str = Form(...),
                         file: UploadFile = File(...)):
    """الخطوة 2: يستخرج ويعرض للمراجعة (نصّ في حقل قابل للتعديل، أو جدول لائحة)."""
    if (g := require_admin(request)):
        return g
    data = await file.read()
    if kind == "roster":
        r = parse_students_excel(data)
        async with AsyncSessionLocal() as s:
            levels = (await s.execute(select(Level).order_by(Level.position))).scalars().all()
        return templates.TemplateResponse(
            "admin/import_roster.html",
            _ctx(request, preview=r, filename=file.filename, levels=levels,
                 rows_json=json.dumps([r_.__dict__ for r_ in r.rows], ensure_ascii=False)))
    # نصّ (docx/pptx/pdf/صورة)
    try:
        extracted = extract_text(file.filename, data)
    except ImporterError as exc:
        return templates.TemplateResponse(
            "admin/import.html", _ctx(request, error=str(exc)))
    async with AsyncSessionLocal() as s:
        axes = (await s.execute(select(Axis))).scalars().all()
    return templates.TemplateResponse(
        "admin/import_review.html",
        _ctx(request, extracted=extracted, axes=axes, text_types=list(TextType)))


@router.post("/import/save/text")
async def import_save_text(request: Request, axis_id: int = Form(...),
                           title: str = Form(...), author: str = Form(""),
                           text_type: str = Form("أساسي"), content: str = Form(...)):
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        s.add(PhilosophicalText(
            axis_id=axis_id, title=title.strip(), author=author.strip() or None,
            content=content, text_type=TextType(text_type)))
        await s.commit()
    return RedirectResponse("/admin/texts", status_code=303)


def _class_label(group: str | None, level: Level | None) -> str:
    """رمز قسمٍ صالحٌ للترميز (TC1، 1BAC2…): القسم إن كان صالحاً، وإلّا من رمز المستوى
    (TC → TC1) — فيبقى توليد رمز الدخول ممكناً حتى لو كان اسم القسم عربيّاً أو فارغاً."""
    g = (group or "").strip().upper()
    if level_of_class_label(g):
        return g
    base = ((level.code if level and level.code else "TC") or "TC").strip().upper()
    return f"{base}1"


@router.post("/import/save/roster")
async def import_save_roster(request: Request, level_id: int = Form(...),
                             group_override: str = Form(""), rows_json: str = Form(...)):
    """الحفظ النهائي للائحة: تراكمي (مطابقة برمز مسار)، بلا حذف.

    يولّد **رمز دخولٍ قصيراً** لكلّ تلميذٍ جديد (مثل TC1-10-JH) — كان ناقصاً فلم
    تُقبَل الرموز القصيرة أصلاً؛ يظهر في اللائحة والبطاقات فيدخل التلميذ بالمسح أو
    بكتابته. القدامى بلا رمزٍ يُمنَحون واحداً أيضاً (ترقية لطيفة، بلا ترحيل)."""
    if (g := require_admin(request)):
        return g
    rows = json.loads(rows_json)
    added = updated = 0
    async with AsyncSessionLocal() as s:
        level = await s.get(Level, level_id)
        # مجموعة الرموز الموجودة لضمان التفرّد (make_login_code تستشير دالّةً متزامنة).
        existing_codes = set((await s.execute(
            select(Student.login_code).where(Student.login_code.is_not(None)))).scalars().all())
        # عدّاد تسلسليّ لكلّ قسمٍ يبدأ بعد الموجودين (لترقيمٍ نظيف: 01، 02…).
        seq: dict[str, int] = dict((grp or "", n) for grp, n in (await s.execute(
            select(Student.group_name, func.count()).group_by(Student.group_name))).all())

        def _new_code(group: str | None) -> str:
            label = _class_label(group, level)
            seq[group or ""] = seq.get(group or "", 0) + 1
            code = make_login_code(f"{label}-{seq[group or '']:02d}", existing_codes.__contains__)
            existing_codes.add(code)
            return code

        for row in rows:
            name = (row.get("full_name") or "").strip()
            if not name:
                continue
            massar = (row.get("massar_code") or None)
            group = group_override.strip() or (row.get("group_name") or None)
            existing = None
            if massar:
                existing = await s.scalar(select(Student).where(Student.massar_code == massar))
            if existing:
                existing.full_name = name
                if group:
                    existing.group_name = group
                if not existing.login_code:          # ترقية القدامى بلا رمز
                    existing.login_code = _new_code(existing.group_name or group)
                updated += 1
            else:
                s.add(Student(full_name=name, level_id=level_id, group_name=group,
                              massar_code=massar, login_code=_new_code(group)))
                added += 1
        await s.commit()
    return RedirectResponse(f"/admin/rosters?added={added}&updated={updated}", status_code=303)


# ═══════════════ عرض ما حُفظ ═══════════════


@router.get("/rosters", response_class=HTMLResponse)
async def rosters(request: Request):
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        students = (await s.execute(
            select(Student).order_by(Student.level_id, Student.group_name,
                                     Student.full_name))).scalars().all()
        level_objs = (await s.execute(select(Level).order_by(Level.position))).scalars().all()
        levels = {lv.id: lv.name for lv in level_objs}
        groups = [r[0] for r in (await s.execute(
            select(Student.group_name).where(Student.group_name.is_not(None))
            .distinct().order_by(Student.group_name))).all()]
    return templates.TemplateResponse(
        "admin/rosters.html",
        _ctx(request, students=students, levels=levels, level_objs=level_objs,
             groups=groups,
             added=request.query_params.get("added"),
             updated=request.query_params.get("updated"),
             new_code=request.query_params.get("new_code"),
             new_name=request.query_params.get("new_name"),
             add_error=request.query_params.get("add_error")))


@router.post("/students/add")
async def student_add(request: Request, full_name: str = Form(...),
                      level_id: str = Form(...), group_name: str = Form(""),
                      massar_code: str = Form("")):
    """إضافة تلميذٍ جديد غير مسجَّل (مثلاً حُوِّل من الإدارة) مع توليد رمز دخوله القصير
    تلقائيّاً — فيلج برمز مسار أو برمزه المختصر مثل بقيّة المتعلّمين."""
    if (g := require_admin(request)):
        return g
    name = full_name.strip()
    group = group_name.strip() or None
    massar = massar_code.strip().upper() or None
    if not name or not str(level_id).strip().isdigit():
        return RedirectResponse(
            "/admin/rosters?add_error=" + quote("الاسم والمستوى مطلوبان."), status_code=303)
    async with AsyncSessionLocal() as s:
        level = await s.get(Level, int(level_id))
        if level is None:
            return RedirectResponse(
                "/admin/rosters?add_error=" + quote("المستوى غير موجود."), status_code=303)
        if massar and await s.scalar(select(Student).where(Student.massar_code == massar)):
            return RedirectResponse(
                "/admin/rosters?add_error=" + quote(f"رمز مسار «{massar}» مسجَّلٌ سلفاً."),
                status_code=303)
        existing_codes = set((await s.execute(
            select(Student.login_code).where(Student.login_code.is_not(None)))).scalars().all())
        n = (await s.scalar(select(func.count()).select_from(Student)
                            .where(Student.group_name == group))) or 0
        label = _class_label(group, level)
        code = make_login_code(f"{label}-{n + 1:02d}", existing_codes.__contains__)
        s.add(Student(full_name=name, level_id=level.id, group_name=group,
                      massar_code=massar, login_code=code, active=True))
        await s.commit()
    return RedirectResponse(
        f"/admin/rosters?new_code={quote(code)}&new_name={quote(name)}", status_code=303)


def _read_backup_students(data: bytes):
    """يقرأ (الاسم، رمز مسار، الفوج) لكلّ تلميذ من ملفّ نسخةٍ احتياطيّة (.db) — للقراءة
    فقط، في ملفٍّ مؤقّت، دون أن يمسّ قاعدة النظام الحيّة."""
    import os
    import sqlite3
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        con = sqlite3.connect(path)
        try:
            return con.execute(
                "SELECT full_name, massar_code, group_name FROM students").fetchall()
        finally:
            con.close()
    finally:
        os.unlink(path)


def _student_key(name: str | None, massar: str | None) -> str:
    """مفتاح مطابقةٍ للتلميذ: رمز مسار إن وُجد، وإلّا الاسم — لمقارنة نسختين."""
    if massar and str(massar).strip():
        return "m:" + str(massar).strip().upper()
    return "n:" + (name or "").strip()


@router.get("/roster-diff", response_class=HTMLResponse)
async def roster_diff_page(request: Request):
    """أداة «تقرير التغييرات»: قارن نسخةً احتياطيّة سابقة بالحالة الحاليّة لترى مَن نُقِل
    (تغيّر فوجه) ومَن أُضيف جديداً — بنظرة، بعد استيراد لائحة."""
    if (g := require_admin(request)):
        return g
    return templates.TemplateResponse("admin/roster_diff.html", _ctx(request))


@router.post("/roster-diff", response_class=HTMLResponse)
async def roster_diff_run(request: Request, backup: UploadFile = File(...)):
    if (g := require_admin(request)):
        return g
    data = await backup.read()
    try:
        old_rows = await asyncio.to_thread(_read_backup_students, data)
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(
            "admin/roster_diff.html",
            _ctx(request, error=f"تعذّرت قراءة الملفّ كنسخة قاعدة بيانات: {exc}"),
            status_code=400)
    old = {}
    for name, massar, group in old_rows:
        old[_student_key(name, massar)] = (name, massar, group)
    async with AsyncSessionLocal() as s:
        cur_rows = (await s.execute(
            select(Student.full_name, Student.massar_code, Student.group_name)
            .order_by(Student.group_name, Student.full_name))).all()
    moved, added = [], []
    for name, massar, group in cur_rows:
        k = _student_key(name, massar)
        if k in old:
            old_group = old[k][2]
            if (old_group or "") != (group or ""):
                moved.append({"name": name, "massar": massar or "—",
                              "old": old_group or "—", "new": group or "—"})
        else:
            added.append({"name": name, "massar": massar or "—", "group": group or "—"})
    moved.sort(key=lambda r: (r["new"], r["name"]))
    added.sort(key=lambda r: (r["group"], r["name"]))
    return templates.TemplateResponse(
        "admin/roster_diff.html",
        _ctx(request, done=True, moved=moved, added=added,
             old_count=len(old), cur_count=len(cur_rows)))


