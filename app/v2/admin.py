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
from .routers import marks as _marks
from .routers import network as _network
from .routers import reports as _reports
from .routers.network import _student_url

router = APIRouter(prefix="/admin")
# مجالات مفكَّكةٌ في routers/* (السلوك والمسارات لا تتغيّر بالتفكيك).
router.include_router(_network.router)      # الشبكة/QR/البطاقات/النسخ
router.include_router(_reports.router)      # التقارير التراكمية (دفتر النقط)
router.include_router(_marks.router)        # استيراد لائحة النقط


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


# ═══════════════ التدخّل العلاجي (الذكاء الاصطناعي المحلّي) ═══════════════


@router.get("/ai", response_class=HTMLResponse)
async def ai_page(request: Request):
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        groups = [r[0] for r in (await s.execute(
            select(Student.group_name).where(Student.group_name.is_not(None))
            .distinct().order_by(Student.group_name))).all()]
        report_count = await s.scalar(select(func.count()).select_from(StudentReport))
    return templates.TemplateResponse(
        "admin/ai.html",
        _ctx(request, groups=groups, online=ollama_available(),
             model=settings.local_ai.model, report_count=report_count, result=None,
             test=request.query_params.get("test")))


@router.post("/ai/test")
async def ai_test(request: Request):
    """اختبار سريع للمحرّك المحلّي: توليد قصير يكشف الخطأ الحقيقي إن وُجد."""
    if (g := require_admin(request)):
        return g
    ok, msg = await asyncio.to_thread(ping_generate)
    prefix = "✓ المحرّك يعمل: " if ok else "✗ "
    return RedirectResponse(f"/admin/ai?test={prefix}{msg}", status_code=303)


# حالة مهامّ الذكاء الاصطناعي الجارية (في الذاكرة) — مفتاحها اسم الفوج.
_AI_JOBS: dict[str, dict] = {}


async def _run_ai_job(group_name: str, scope: str = "full") -> None:
    """مهمّة خلفية: تولّد تقارير التدخّل دون تجميد الواجهة.

    الترتيب: تقرير القسم **أوّلاً** (نداء واحد يظهر بسرعة)، ثمّ التقارير الفردية
    إن كان النطاق «full». نداءات Ollama الحاجبة تُنفَّذ في خيط (to_thread).
    scope="class" يكتفي بتقرير القسم (الأسرع).
    """
    do_class = scope in ("class", "full")
    do_students = scope in ("students", "full")
    job = _AI_JOBS[group_name]
    try:
        async with AsyncSessionLocal() as s:
            students = (await s.execute(
                select(Student).where(Student.group_name == group_name,
                                      Student.active.is_(True))
                .order_by(Student.full_name))).scalars().all()

            # (1) التقرير الجماعي أوّلاً — يظهر بسرعة (نداء واحد)
            if do_class:
                report = await generate_class_report(s, group_name)
                if report["has_data"]:
                    try:
                        plan = await asyncio.to_thread(generate_class_plan, report)
                        s.add(ClassReport(
                            level_id=(students[0].level_id if students else None),
                            group_name=group_name, skills_summary=report["skills"],
                            weakest_skill=report["dominant_deficit"],
                            ai_intervention_plan=plan, ai_model=settings.local_ai.model))
                        job["class_report"] = {"dominant": report["dominant_deficit"], "plan": plan}
                        await s.commit()
                    except AIUnavailable as exc:
                        job.update(status="error", offline=True,
                                   message=f"توقّفت المعالجة: {exc}")
                        return

            # (2) التقارير الفردية — تسلسليّاً
            if do_students:
                job["total"] = len(students)
                for st in students:
                    profile = await generate_student_skill_profile(s, st.id)
                    job["done"] += 1
                    if not profile["has_data"]:
                        continue
                    try:
                        plan = await asyncio.to_thread(generate_student_plan, profile)
                    except AIUnavailable as exc:
                        job.update(status="error", offline=True,
                                   message=f"توقّفت المعالجة (حُفظ ما تمّ): {exc}")
                        await s.commit()
                        return
                    s.add(StudentReport(
                        student_id=st.id, skill_profile=profile["skills"],
                        ai_intervention_plan=plan, ai_model=settings.local_ai.model))
                    job["processed"] += 1
                await s.commit()

        parts = []
        if do_class:
            parts.append("تقرير القسم" if job["class_report"] else "(لا بيانات للقسم)")
        if do_students:
            parts.append(f"{job['processed']} تقرير فردي")
        job.update(status="done", message="تمّ توليد " + " و".join(parts) + ".")
    except Exception as exc:  # noqa: BLE001
        job.update(status="error", message=f"خطأ أثناء المعالجة: {exc}")


@router.post("/ai/run", response_class=HTMLResponse)
async def ai_run(request: Request, group_name: str = Form(...),
                 do_class: str = Form(""), do_students: str = Form("")):
    """يطلق توليد التقارير كمهمّة خلفية ويعرض شريط تقدّم (لا تتجمّد الواجهة).

    يختار الأستاذ التقرير الجماعي و/أو الفردي بخانتَي اختيار مستقلّتين."""
    if (g := require_admin(request)):
        return g
    want_class = do_class == "on"
    want_students = do_students == "on"
    if want_class and want_students:
        scope = "full"
    elif want_students:
        scope = "students"
    else:
        scope = "class"          # الافتراض عند عدم الاختيار: الجماعي (الأسرع)

    async def render(**extra):
        async with AsyncSessionLocal() as s:
            groups = [r[0] for r in (await s.execute(
                select(Student.group_name).where(Student.group_name.is_not(None))
                .distinct().order_by(Student.group_name))).all()]
        return templates.TemplateResponse(
            "admin/ai.html",
            _ctx(request, groups=groups, online=ollama_available(),
                 model=settings.local_ai.model, report_count=None, **extra))

    # ق-٢ (فرض برمجيّ): لا يشتغل الذكاء الاصطناعي وأيّ جلسة صفّية مفتوحة — كل
    # معالجة ذكية بعد إغلاق الحصّة، لا أثناءها (حفاظاً على العتاد واليقين).
    async with AsyncSessionLocal() as s:
        open_sess = await s.scalar(
            select(func.count()).select_from(QuizSession)
            .where(QuizSession.status == "open"))
    if open_sess:
        return await render(result={
            "offline": False,
            "message": "توجد جلسة صفّية مفتوحة. أغلِق الجلسات أوّلاً — "
                       "المعالجة الذكية تكون بعد الحصّة لا أثناءها (ق-٢)."})

    if not ollama_available():
        return await render(result={"offline": True,
                                    "message": "محرك الذكاء الاصطناعي غير مشغل. "
                                               "يرجى تشغيل Ollama أولاً."})

    _AI_JOBS[group_name] = {"status": "running", "total": 0, "done": 0,
                            "processed": 0, "offline": False, "message": "",
                            "class_report": None,
                            "scope": scope}
    asyncio.create_task(_run_ai_job(group_name, scope))
    return await render(job_group=group_name)


async def _hardest_questions(s, group: str, limit: int = 6) -> list[dict]:
    """أصعب الأسئلة على القسم (الأخطاء الشائعة): نسبة نجاحٍ منخفضة عبر أجوبةٍ مصادَقة."""
    eff = func.coalesce(Answer.manual_score, Answer.auto_score)
    ratio = func.avg(eff / QuizQuestion.max_score)
    rows = (await s.execute(
        select(QuizQuestion.prompt, ratio, func.count(Answer.id))
        .join(Answer, Answer.quiz_question_id == QuizQuestion.id)
        .join(Student, Student.id == Answer.student_id)
        .where(Student.group_name == group, Answer.teacher_confirmed.is_(True),
               eff.is_not(None), QuizQuestion.max_score > 0)
        .group_by(QuizQuestion.id)
        .having(func.count(Answer.id) >= 2)
        .order_by(ratio.asc()).limit(limit))).all()
    return [{"prompt": (p or "").strip()[:100], "success": round((r or 0) * 100, 1),
             "count": n} for p, r, n in rows if (r or 0) < 0.7]


async def _ai_reports_data(s, group: str) -> dict:
    """يجمع تقارير التدخّل للقراءة/التصدير مع قراءةٍ بيداغوجيّة كاملة (بلا Ollama):
    خلاصةٌ سرديّة، تصنيف نوعيّ للمهارات، توزيع المستويات، القوّة/القصور، أصعب الأسئلة،
    وتوصيات دعمٍ عمليّة — جماعيّاً وفرديّاً؛ مع خطط الذكاء المخزّنة إن وُلِّدت."""
    from ..services.pedagogy import (class_narrative, class_pedagogy,
                                     student_narrative, student_pedagogy)
    class_rep = await generate_class_report(s, group)
    class_plan = await s.scalar(
        select(ClassReport.ai_intervention_plan)
        .where(ClassReport.group_name == group,
               ClassReport.ai_intervention_plan.is_not(None))
        .order_by(ClassReport.created_at.desc()).limit(1))
    students = (await s.execute(
        select(Student).where(Student.group_name == group, Student.active.is_(True))
        .order_by(Student.full_name))).scalars().all()
    stu = []
    overalls: list = []
    for st in students:
        prof = await generate_student_skill_profile(s, st.id)
        overalls.append(prof.get("overall"))
        plan = await s.scalar(
            select(StudentReport.ai_intervention_plan)
            .where(StudentReport.student_id == st.id,
                   StudentReport.ai_intervention_plan.is_not(None))
            .order_by(StudentReport.created_at.desc()).limit(1))
        sp = student_pedagogy(prof)
        stu.append({"student": st, "skills": prof.get("skills") or {},
                    "has_data": prof.get("has_data"), "plan": plan,
                    "peda": sp, "narrative": student_narrative(sp, st.full_name)})
    cp = class_pedagogy(class_rep, overalls)
    hardest = await _hardest_questions(s, group)
    return {
        "group": group,
        "class": {"skills": class_rep.get("skills") or {},
                  "weakest": class_rep.get("dominant_deficit"),
                  "plan": class_plan, "has_data": class_rep.get("has_data"),
                  "peda": cp, "narrative": class_narrative(cp, group),
                  "hardest": hardest},
        "students": stu,
    }


@router.get("/ai/reports", response_class=HTMLResponse)
async def ai_reports_view(request: Request):
    """يعرض تقارير التدخّل (جماعيّة + فرديّة) للقسم للقراءة على الشاشة."""
    if (g := require_admin(request)):
        return g
    group = request.query_params.get("group") or ""
    async with AsyncSessionLocal() as s:
        groups = [r[0] for r in (await s.execute(
            select(Student.group_name).where(Student.group_name.is_not(None))
            .distinct().order_by(Student.group_name))).all()]
        if not group and groups:
            # الافتراض: الفوج الأكثر بياناتٍ مصادَقة (لا فوجاً فارغاً) — تقريرٌ ذو معنى فوراً.
            eff = func.coalesce(Answer.manual_score, Answer.auto_score)
            top = (await s.execute(
                select(Student.group_name, func.count(Answer.id).label("n"))
                .join(Answer, Answer.student_id == Student.id)
                .where(Answer.teacher_confirmed.is_(True), eff.is_not(None),
                       Student.group_name.is_not(None))
                .group_by(Student.group_name).order_by(func.count(Answer.id).desc())
                .limit(1))).first()
            group = top[0] if top else groups[0]
        data = await _ai_reports_data(s, group) if group else None
    return templates.TemplateResponse(
        "admin/ai_reports.html",
        _ctx(request, groups=groups, group=group, data=data,
             has_plans=bool(data and (data["class"]["plan"] or
                            any(x["plan"] for x in data["students"]))) if data else False))


@router.get("/ai/reports/export")
async def ai_reports_export(request: Request):
    """تصدير تقارير التدخّل للقسم إلى Word (fmt=docx، الافتراض) أو نصّ (fmt=txt)."""
    if (g := require_admin(request)):
        return g
    group = request.query_params.get("group") or ""
    fmt = (request.query_params.get("fmt") or "docx").lower()
    if not group:
        return RedirectResponse("/admin/ai/reports", status_code=303)
    async with AsyncSessionLocal() as s:
        data = await _ai_reports_data(s, group)
    if fmt == "txt":
        from ..services.report_export import ai_reports_txt
        return Response(
            content=ai_reports_txt(data).encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="ai-{group}.txt"'})
    from ..services.report_export import ai_reports_docx
    return Response(
        content=ai_reports_docx(data),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="ai-{group}.docx"'})


@router.get("/ai/status", response_class=HTMLResponse)
async def ai_status(request: Request):
    """جزء HTMX: يعرض تقدّم مهمّة الذكاء الاصطناعي، ويتوقّف عن الاستطلاع عند الانتهاء."""
    if (g := require_admin(request)):
        return g
    group = request.query_params.get("group", "")
    job = _AI_JOBS.get(group)
    return templates.TemplateResponse(
        "admin/_ai_status.html", _ctx(request, group=group, job=job))


@router.get("/texts", response_class=HTMLResponse)
async def texts(request: Request):
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(PhilosophicalText, Axis.title)
            .join(Axis, Axis.id == PhilosophicalText.axis_id, isouter=True)
            .order_by(PhilosophicalText.created_at.desc()))).all()
    return templates.TemplateResponse("admin/texts.html", _ctx(request, rows=rows))


@router.post("/texts/{text_id}/delete")
async def delete_text(request: Request, text_id: int):
    """حذف نصّ فلسفي (وأسئلته بالتتالي)؛ إنجازات التلاميذ تبقى بلا سؤال."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        await s.execute(sa_delete(PhilosophicalText).where(PhilosophicalText.id == text_id))
        await s.commit()
    return RedirectResponse("/admin/texts", status_code=303)


# ═══════════════ التقاويم بالأسئلة (استيراد JSON/Word/PDF → أسئلة مغلقة/مفتوحة) ═══════════════


def _normalize_quiz_upload(filename: str, data: bytes) -> tuple[dict | None, list[str]]:
    """يحوّل ملفّاً مرفوعاً إلى صيغة تقويم مطبّعة. يدعم JSON وWord وPDF.

    يعيد (normalized أو None، قائمة أخطاء). التحويل يقينيّ بالكامل — لا ذكاء اصطناعي.
    """
    name = (filename or "").lower()
    try:
        if name.endswith(".json"):
            payload = json.loads(data.decode("utf-8"))
            return normalize_quiz_json(payload), []
        if name.endswith(".docx"):
            r = parse_docx(data)                       # قالب Word اليقيني
            return (r.normalized, []) if r.ok else (None, r.errors)
        # PDF أو أي نصّ: نستخرج النصّ ثمّ نحلّله بمنطق القالب نفسه.
        text = extract_text(filename, data)
        r = parse_lines(text.splitlines())
        return (r.normalized, []) if r.ok else (None, r.errors)
    except QuizImportError as exc:
        return None, exc.errors
    except json.JSONDecodeError as exc:
        return None, [f"ملفّ JSON غير صالح: {exc}"]
    except ImporterError as exc:
        return None, [str(exc)]
    except Exception as exc:  # noqa: BLE001
        return None, [f"تعذّر تحويل الملفّ: {exc}"]


@router.get("/quizzes", response_class=HTMLResponse)
async def quizzes(request: Request):
    """لائحة التقاويم بالأسئلة، مع عدد الأسئلة، وإسناد الفوج/المستوى، والنشر، والحذف."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(Quiz, func.count(QuizQuestion.id))
            .join(QuizQuestion, QuizQuestion.quiz_id == Quiz.id, isouter=True)
            .group_by(Quiz.id).order_by(Quiz.created_at.desc()))).all()
        levels = (await s.execute(select(Level).order_by(Level.position))).scalars().all()
        groups = [r[0] for r in (await s.execute(
            select(Student.group_name).where(Student.group_name.is_not(None))
            .distinct().order_by(Student.group_name))).all()]
    return templates.TemplateResponse(
        "admin/quizzes.html",
        _ctx(request, rows=rows, levels=levels, groups=groups,
             error=request.query_params.get("error"),
             saved=request.query_params.get("saved")))


async def _save_normalized_quiz(normalized: dict, lid: int, group_name: str) -> tuple[str, int]:
    """يبني التقويم من صيغة مطبّعة ويحفظه؛ يعيد (العنوان، عدد الأسئلة)."""
    skills = await skill_id_map()
    async with AsyncSessionLocal() as s:
        quiz = build_quiz(normalized, level_id=lid,
                          group_name=group_name.strip() or None, skill_ids=skills)
        s.add(quiz)
        await s.commit()
        return normalized["title"], len(quiz.questions)


def _review_page(request: Request, *, title: str, kind: str, questions: list[dict],
                 level_id: str, group_name: str, note: str = "",
                 errors: list[str] | None = None, raw_text: str = "",
                 answers_text: str = "", ai_online: bool | None = None):
    """صفحة مراجعة الاستيراد: عنوان + نوع + أسئلة قابلة للتحرير (بلا JSON)."""
    if ai_online is None:
        ai_online = ollama_available()
    return templates.TemplateResponse(
        "admin/quiz_import_review.html",
        _ctx(request, r_title=title, r_kind=kind, questions=questions,
             level_id=level_id, group_name=group_name, note=note,
             errors=errors or [], raw_text=raw_text, answers_text=answers_text,
             ai_online=ai_online, cloud_online=cloud_available(),
             competencies=list(COMPETENCIES.items())))


_AI_TYPE_MAP = {"heading": "heading", "passage": "passage",
                "mcq_single": "mcq_single", "mcq_multi": "mcq_multi",
                "long_text": "long_text", "short_text": "short_text",
                "essay": "long_text", "mcq": "mcq_single", "text": "passage",
                "title": "heading"}


def _ai_json_to_questions(raw_json: str) -> tuple[str, list[dict]]:
    """يحوّل مخرَج المحرّك (JSON) إلى (عنوان، أسئلة للمراجعة). متسامح مع نقص المخطّط."""
    data = json.loads(raw_json)
    if isinstance(data, list):
        data = {"questions": data}
    title = str(data.get("title") or "").strip() or "تقويم مستورد"
    out: list[dict] = []
    for q in (data.get("questions") or []):
        if not isinstance(q, dict):
            continue
        prompt = str(q.get("prompt") or q.get("text") or q.get("question") or "").strip()
        if not prompt:
            continue
        qtype = _AI_TYPE_MAP.get(str(q.get("type") or "").strip().lower(), "long_text")
        try:
            ms = max(0.0, float(q.get("max_score") or (0 if qtype in DISPLAY_TYPES else 4)))
        except (TypeError, ValueError):
            ms = 0.0 if qtype in DISPLAY_TYPES else 4.0
        comp = str(q.get("competency") or "").strip()
        elems = [str(e).strip() for e in (q.get("elements") or []) if str(e).strip()]
        item = {"type": qtype, "prompt": prompt, "max_score": ms,
                "options": [], "correct": [],
                "competency": comp if comp in COMPETENCIES else None, "elements": elems}
        if qtype in ("mcq_single", "mcq_multi"):
            opts = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
            raw_correct = q.get("correct")
            if isinstance(raw_correct, int):
                raw_correct = [raw_correct]
            correct = [c for c in (raw_correct or [])
                       if isinstance(c, int) and 0 <= c < len(opts)]
            if len(opts) < 2:                    # اختيار بلا خيارات كافية → مفتوح
                item["type"] = "long_text"
            else:
                item["options"] = opts
                item["correct"] = correct[:1] if qtype == "mcq_single" else correct
        out.append(item)
    return title, out


def _extract_upload_text(filename: str, data: bytes) -> str:
    try:
        return extract_text(filename, data).text
    except Exception:  # noqa: BLE001 — txt أو أيّ ملفّ قابل للفكّ نصّاً
        return data.decode("utf-8", "replace")


@router.post("/quizzes/import")
async def quizzes_import(request: Request, file: UploadFile = File(...),
                         answers_file: UploadFile | None = File(None),
                         level_id: str = Form(""), group_name: str = Form("")):
    """رفع تقويم (JSON/Word/PDF/صورة) + ملفّ عناصر إجابة اختياريّ → أسئلة.

    (١) يقينيّ — JSON/قالب Word يُحلَّل حرفيّاً فيُحفَظ مباشرةً.
    (٢) ذكيّ سحابيّ — إن هُيّئ مفتاح Claude: يحوّل **أيّ بنية** فرضٍ إلى أسئلة مع
        الأجوبة الصحيحة ومؤشّرات النجاح (وقت التحضير، يحتاج إنترنت)، للمراجعة.
    (٣) متسامح — وإلّا: تحليل قاعديّ يقينيّ (بلا اتصال) + إسناد عناصر الإجابة بالترقيم."""
    if (g := require_admin(request)):
        return g
    data = await file.read()
    lid = int(level_id) if level_id.strip().isdigit() else None
    if lid is None:
        # ح-٢: لا يُحفَظ تقويم بلا مستوى (لئلّا يُنشَر لاحقاً فيتسرّب لكلّ المستويات).
        return RedirectResponse(
            "/admin/quizzes?error=اختر المستوى قبل الاستيراد.", status_code=303)

    # (١) المسار اليقينيّ: JSON صالح أو قالب Word/PDF يُحلَّل حرفيّاً → حفظ مباشر.
    normalized, errors = _normalize_quiz_upload(file.filename, data)
    if normalized is not None:
        title, n = await _save_normalized_quiz(normalized, lid, group_name)
        return RedirectResponse(
            f"/admin/quizzes?saved=تمّ استيراد «{title}» بـ{n} سؤالاً.", status_code=303)

    import os
    raw = _extract_upload_text(file.filename, data)
    hint = os.path.splitext(os.path.basename(file.filename or ""))[0]
    # ملفّ عناصر الإجابة (اختياريّ) — نصّه يُمرَّر للمحرّك لبناء مؤشّرات التصحيح.
    answers_text = ""
    afn = getattr(answers_file, "filename", None)   # تجاهُل قيمة File(None) الافتراضيّة
    if afn:
        answers_text = _extract_upload_text(afn, await answers_file.read())

    # (٢) الاستيراد الذكيّ السحابيّ (وقت التحضير، يحتاج إنترنت): يعالج **أيّ بنية** فرضٍ
    #     — عناوين ونصوص وأسئلة وأجوبة صحيحة ومؤشّرات — عبر نموذج Claude قويّ. يُشغَّل
    #     تلقائيّاً حين يكون المفتاح مهيّأً. إن تعذّر، نمرّ للتحليل القاعديّ (لا يضيع العمل).
    cloud_note = ""
    if cloud_available():
        try:
            title, questions = _ai_json_to_questions(
                await asyncio.to_thread(extract_quiz_cloud, raw, answers_text))
            if not questions:
                raise CloudAIUnavailable("لم يُرجِع النموذج أسئلة صالحة.")
            note = ("هيكلة ذكيّة لأيّ بنية فرضٍ: أسئلة مفتوحة/مغلقة مع الأجوبة الصحيحة "
                    "ومؤشّرات النجاح. راجِعها وصادِق عليها قبل الحفظ.")
            return _review_page(request, title=title, kind="exercise", questions=questions,
                                level_id=level_id, group_name=group_name, note=note,
                                raw_text=raw, answers_text=answers_text)
        except (CloudAIUnavailable, ValueError, json.JSONDecodeError) as exc:
            cloud_note = f"تعذّر الاستيراد الذكيّ السحابيّ: {exc}"

    # (٣) المسار اليقينيّ (فوريّ، بلا اتصال): تحليل قاعديّ يحفظ النصّ الفلسفيّ والعناوين
    # ويكشف الاختيار. إن رُفِع ملفّ عناصر الإجابة أُسنِدت عناصره للأسئلة المفتوحة بالترقيم.
    parsed = heuristic_quiz_from_text(raw, title_hint=hint)
    if not parsed["questions"]:
        return RedirectResponse(
            "/admin/quizzes?error=تعذّر إيجاد نصّ أسئلة في الملفّ. جرّب ملفّاً آخر أو القالب.",
            status_code=303)
    errs = [cloud_note] if cloud_note else None
    if answers_text.strip():
        matched = attach_answer_elements(parsed["questions"], answers_text)
        if matched:
            note = (f"استُخرج الفرض ونصوصه، وأُسنِدت عناصر الإجابة لـ{matched} سؤالاً مفتوحاً "
                    "مؤشّراتٍ للتصحيح الآليّ. راجِعها، أشّر الصواب في الاختيار، واختر "
                    "الكفاية، ثمّ احفظ.")
        else:
            note = ("استُخرج الفرض ونصوصه. تعذّر مطابقة ترقيم عناصر الإجابة بالأسئلة "
                    "آليّاً — انسخ العناصر في خانة «عناصر الإجابة» لكلّ سؤال مفتوح ثمّ احفظ.")
        return _review_page(request, title=parsed["title"], kind=parsed["kind"],
                            questions=parsed["questions"], level_id=level_id,
                            group_name=group_name, note=note, errors=errs,
                            raw_text=raw, answers_text=answers_text)

    note = ("استُخرج الملفّ فوريّاً: خانات الاختيار، العناوين والنصوص الفلسفيّة، حذف "
            "أسطر الإجابة، واستخراج النقط. في الاختيار **أشّر الصواب**؛ وفي المفتوحة "
            "اكتب **عناصر الإجابة** واختر **الكفاية**. (للهيكلة الذكيّة لأيّ بنية: "
            "هيّئ مفتاح Claude في config.ini ثمّ اضغط «استخراج ذكيّ».)")
    return _review_page(request, title=parsed["title"], kind=parsed["kind"],
                        questions=parsed["questions"], level_id=level_id,
                        group_name=group_name, note=note, errors=errs, raw_text=raw)


@router.post("/quizzes/import/ai")
async def quizzes_import_ai(request: Request):
    """استخراج ذكيّ يعيد هيكلة نصّ الفرض لأيّ بنية ويعرضها للمراجعة.

    يفضّل المحرّك السحابيّ القويّ (Claude) إن كان المفتاح مهيّأً — يعالج أيّ بنية؛
    وإلّا يجرّب المحرّك المحلّي. تدهور لطيف: عند التعذّر يبقى التحليل القاعديّ ويُعرَض السبب."""
    if (g := require_admin(request)):
        return g
    form = await request.form()
    raw = form.get("raw_text") or ""
    answers_text = form.get("answers_text") or ""
    level_id = (form.get("level_id") or "").strip()
    group_name = form.get("group_name") or ""

    # (١) السحابيّ أوّلاً إن هُيّئ — يعالج أيّ بنية بدقّة وسرعة.
    if cloud_available():
        try:
            title, questions = _ai_json_to_questions(
                await asyncio.to_thread(extract_quiz_cloud, raw, answers_text))
            if not questions:
                raise CloudAIUnavailable("لم يُرجِع النموذج أسئلة صالحة.")
            note = ("هيكلة ذكيّة لأيّ بنية — أسئلة مع الأجوبة الصحيحة ومؤشّرات النجاح. "
                    "راجِعها وصادِق عليها قبل الحفظ.")
            return _review_page(request, title=title, kind="exercise", questions=questions,
                                level_id=level_id, group_name=group_name, note=note,
                                raw_text=raw, answers_text=answers_text)
        except (CloudAIUnavailable, json.JSONDecodeError, ValueError) as exc:
            cloud_err = f"تعذّر الاستيراد الذكيّ السحابيّ: {exc}"
    else:
        cloud_err = ""

    # (٢) المحلّي (Ollama) إن توفّر — أبطأ وأضعف، لكن بلا إنترنت.
    try:
        title, questions = _ai_json_to_questions(
            await asyncio.to_thread(extract_quiz_json, raw, answers_text))
        if not questions:
            raise AIUnavailable("لم يُرجِع المحرّك أسئلة صالحة.")
        note = "هيكلة المحرّك المحلّي — راجِعها وصحّحها وأشّر الصواب قبل الحفظ."
        return _review_page(request, title=title, kind="exercise", questions=questions,
                            level_id=level_id, group_name=group_name, note=note,
                            raw_text=raw, answers_text=answers_text)
    except (AIUnavailable, json.JSONDecodeError, ValueError) as exc:
        # (٣) نرجع للتحليل القاعديّ مع رسالة واضحة (لا نفقد عمل الأستاذ).
        parsed = heuristic_quiz_from_text(raw, title_hint="")
        errs = [e for e in (cloud_err, f"تعذّر الاستخراج المحلّي: {exc}") if e]
        return _review_page(
            request, title=parsed["title"], kind="exercise",
            questions=parsed["questions"], level_id=level_id, group_name=group_name,
            raw_text=raw, answers_text=answers_text, errors=errs,
            note="عُرِض التحليل القاعديّ. هيّئ مفتاح Claude للهيكلة الذكيّة لأيّ بنية.")


_REVIEW_OPEN = ("long_text", "short_text")
_REVIEW_MCQ = ("mcq_single", "mcq_multi")


def _parse_review_form(form) -> tuple[str, str, list[dict], list[str]]:
    """يقرأ نموذج المراجعة → (العنوان، النوع، أسئلة للعرض، أخطاء).

    كلّ سؤال في القائمة يحمل type/prompt/max_score وoptions/correct (للاختيار)، فتُعاد
    الصفحة بالتحرير نفسه إن وُجد خطأ. الأخطاء تخصّ الاختيار (خيارات ناقصة/بلا صحيح)."""
    title = (form.get("r_title") or "").strip() or "تقويم مستورد"
    kind = form.get("r_kind") or "exercise"
    if kind not in KINDS:
        kind = "exercise"
    try:
        count = int(form.get("count") or 0)
    except ValueError:
        count = 0
    questions: list[dict] = []
    errors: list[str] = []
    for i in range(count):
        prompt = (form.get(f"q_prompt_{i}") or "").strip()
        if not prompt:                          # صفّ حُذف نصّه → يُتجاهَل
            continue
        qtype = form.get(f"q_type_{i}") or "long_text"
        if qtype == "skip":                     # سطر يُتجاهَل — لا يُحفَظ إطلاقاً
            continue
        if qtype in DISPLAY_TYPES:              # عنوان/نصّ للقراءة — يُعرَض بلا تصحيح
            questions.append({"type": qtype, "prompt": prompt, "max_score": 0.0,
                              "options": [], "correct": [], "competency": None,
                              "elements": []})
            continue
        if qtype not in _REVIEW_OPEN and qtype not in _REVIEW_MCQ:
            qtype = "long_text"
        try:
            ms = max(0.0, float(form.get(f"q_score_{i}") or 4))
        except ValueError:
            ms = 4.0
        comp = form.get(f"q_comp_{i}") or ""
        elems = [ln.strip() for ln in (form.get(f"q_elem_{i}") or "").splitlines()
                 if ln.strip()]
        q = {"type": qtype, "prompt": prompt, "max_score": ms,
             "options": [], "correct": [],
             "competency": comp if comp in COMPETENCIES else None,
             "elements": elems}
        if qtype in _REVIEW_MCQ:
            try:
                oc = int(form.get(f"q_optcount_{i}") or 0)
            except ValueError:
                oc = 0
            correct_raw = {int(v) for v in form.getlist(f"q_correct_{i}") if v.isdigit()}
            for k in range(oc):
                opt = (form.get(f"q_opt_{i}_{k}") or "").strip()
                if not opt:
                    continue
                if k in correct_raw:
                    q["correct"].append(len(q["options"]))
                q["options"].append(opt)
            where = f"السؤال {len(questions) + 1}"
            if len(q["options"]) < 2:
                errors.append(f"{where}: سؤال الاختيار يحتاج خيارين على الأقلّ.")
            elif not q["correct"]:
                errors.append(f"{where}: أشّر الإجابة الصحيحة.")
            elif qtype == "mcq_single":
                q["correct"] = [q["correct"][0]]   # واحدة فقط للاختيار الأحاديّ
        questions.append(q)
    return title, kind, questions, errors


def _to_normalized_question(q: dict) -> dict:
    """يحوّل سؤال المراجعة إلى صيغة build_quiz (payload الاختيار + عناصر إجابة المفتوحة)."""
    qtype = q["type"]
    payload: dict = {}
    if qtype == "mcq_single":
        payload = {"options": q["options"], "correct": q["correct"][0]}
    elif qtype == "mcq_multi":
        payload = {"options": q["options"], "correct": q["correct"]}
    # عناصر الإجابة (للأسئلة المفتوحة) → مؤشّرات يصحّح بها الذكاء الاصطناعيّ لاحقاً.
    ms = float(q["max_score"])
    elems = q.get("elements") or []
    per = round(ms / len(elems), 2) if elems else 0
    indicators = [{"text": t, "points": per} for t in elems]
    return {"type": qtype, "competency": q.get("competency"), "prompt": q["prompt"],
            "stimulus": None, "max_score": ms, "payload": payload,
            "indicators": indicators, "penalties": [],
            "auto_scored": qtype in QUESTION_TYPES_CLOSED}


@router.post("/quizzes/import/save")
async def quizzes_import_save(request: Request):
    """يحفظ التقويم بعد مراجعة الأستاذ لأسئلته المستخرَجة وتحريرها (وتأشير الصواب)."""
    if (g := require_admin(request)):
        return g
    form = await request.form()
    level_id = (form.get("level_id") or "").strip()
    group_name = form.get("group_name") or ""
    lid = int(level_id) if level_id.isdigit() else None
    if lid is None:
        return RedirectResponse(
            "/admin/quizzes?error=اختر المستوى قبل الحفظ.", status_code=303)

    title, kind, questions, errors = _parse_review_form(form)
    if not questions:
        errors.append("أضِف نصّ سؤال واحد على الأقلّ قبل الحفظ.")
    if errors:
        if not questions:
            questions = [{"type": "long_text", "prompt": "", "max_score": 4.0,
                          "options": [], "correct": []}]
        return _review_page(request, title=title, kind=kind, questions=questions,
                            level_id=level_id, group_name=group_name, errors=errors,
                            raw_text=form.get("raw_text") or "")

    normalized = {"title": title, "kind": kind, "level": None, "unit": None,
                  "concept": None, "stimuli": [],
                  "questions": [_to_normalized_question(q) for q in questions]}
    saved_title, n = await _save_normalized_quiz(normalized, lid, group_name)
    return RedirectResponse(
        f"/admin/quizzes?saved=تمّ استيراد «{saved_title}» بـ{n} سؤالاً.", status_code=303)


@router.get("/grading", response_class=HTMLResponse)
async def grading_center(request: Request):
    """قسم التصحيح: كلّ تقويم فيه أجوبة مُسلَّمة، مع عدد المُسلَّم والمنتظِر للمصادقة،
    ورابطٌ مباشر لشاشة تصحيحه. مركزٌ واضح بدل أزرار متفرّقة."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(Quiz.id, Quiz.title, Quiz.group_name,
                   func.count(Answer.id),
                   func.sum(case((Answer.teacher_confirmed.is_(False), 1), else_=0)))
            .join(QuizQuestion, QuizQuestion.quiz_id == Quiz.id)
            .join(Answer, Answer.quiz_question_id == QuizQuestion.id)
            .where(Answer.submitted.is_(True))
            .group_by(Quiz.id).order_by(Quiz.created_at.desc()))).all()
    items = [{"id": r[0], "title": r[1], "group": r[2], "submitted": r[3],
              "pending": int(r[4] or 0)} for r in rows]
    return templates.TemplateResponse(
        "admin/grading.html",
        _ctx(request, items=items, cleared=request.query_params.get("cleared")))


@router.post("/grading/{quiz_id}/clear")
async def grading_clear_answers(request: Request, quiz_id: int):
    """يحذف **كلّ أجوبة** هذا التقويم (أيّاً كان مصدرها) — لإزالة أجوبةٍ عالقةٍ ناتجةٍ
    عن جلساتٍ مشوّهة أو غير مكتملة أو محذوفة. لا يمسّ أسئلة التقويم نفسه، فيبقى قابلاً
    لإعادة التمرير من جديد. لا رجعة في حذف الأجوبة."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        qids = select(QuizQuestion.id).where(QuizQuestion.quiz_id == quiz_id)
        res = await s.execute(sa_delete(Answer).where(Answer.quiz_question_id.in_(qids)))
        await s.commit()
    n = res.rowcount if res.rowcount is not None else 0
    return RedirectResponse(f"/admin/grading?cleared={n}", status_code=303)


@router.get("/quizzes/template")
async def quiz_template(request: Request):
    """تنزيل قالب Word لتأليف تقويم يدوياً (ميزة أُعيدت بعد أن ضاعت في الانتقال، ٥-د)."""
    if (g := require_admin(request)):
        return g
    data = await asyncio.to_thread(build_template_docx)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="philotech-quiz-template.docx"'})


@router.post("/quizzes/{quiz_id}/assign")
async def quiz_assign(request: Request, quiz_id: int, level_id: str = Form(""),
                      group_name: str = Form(""), published: str = Form("")):
    """إسناد التقويم إلى مستوى/فوج وضبط نشره (رؤية التلاميذ له)."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        quiz = await s.get(Quiz, quiz_id)
        if quiz:
            lid = int(level_id) if level_id.strip().isdigit() else None
            quiz.level_id = lid
            quiz.group_name = group_name.strip() or None
            # ح-٢: لا نشر لتقويم بلا مستوى (يمنع التسريب لكلّ المستويات).
            quiz.published = (published == "on") and lid is not None
            await s.commit()
            if published == "on" and lid is None:
                return RedirectResponse(
                    "/admin/quizzes?error=لا يمكن نشر تقويم بلا مستوى — اختر المستوى أوّلاً.",
                    status_code=303)
    return RedirectResponse("/admin/quizzes", status_code=303)


def _correct_set(value) -> set:
    """يطبّع فهارس الأجوبة الصحيحة إلى مجموعة، مهما كان تخزينها: قائمة، رقمٌ مفرد
    (بعض التقويمات تخزّن correct=2 لا [2])، أو غياب."""
    if isinstance(value, bool):
        return set()
    if isinstance(value, int):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return {v for v in value if isinstance(v, int) and not isinstance(v, bool)}
    return set()


def _opts_text(q: QuizQuestion) -> str:
    """يبني نصّ الخيارات للمحرّر: سطرٌ لكلّ خيار، والصحيح مسبوقٌ بنجمة (*).
    متسامحٌ مع بنياتٍ مختلفة (خيارات رقميّة، correct رقمٌ مفرد، payload غير قاموسيّ)."""
    payload = q.payload if isinstance(q.payload, dict) else {}
    options = payload.get("options")
    if not isinstance(options, (list, tuple)):
        options = []
    correct = _correct_set(payload.get("correct"))
    lines = []
    for i, o in enumerate(options):
        text = str(o)
        lines.append(("* " + text) if i in correct else text)
    return "\n".join(lines)


@router.get("/quizzes/{quiz_id}/edit", response_class=HTMLResponse)
async def quiz_edit_page(request: Request, quiz_id: int):
    """محرّر التقويم المحفوظ: تعديل العنوان ونصوص الأسئلة والنصّ المرافق والسلّم
    وخيارات الاختيار، وحذف سؤال — لإصلاح أخطاءٍ بعد الحفظ بلا إعادة استيراد."""
    if (g := require_admin(request)):
        return g
    from sqlalchemy.orm import selectinload
    try:
        async with AsyncSessionLocal() as s:
            quiz = (await s.execute(
                select(Quiz).where(Quiz.id == quiz_id)
                .options(selectinload(Quiz.questions)))).scalar_one_or_none()
            if quiz is None:
                return HTMLResponse("التقويم غير موجود", status_code=404)
            qs = sorted(quiz.questions, key=lambda q: (q.position, q.id))
            items = [{
                "q": q,
                "is_mcq": q.qtype in ("mcq_single", "mcq_multi"),
                "is_display": q.qtype in DISPLAY_TYPES,
                "opts_text": _opts_text(q),
            } for q in qs]
        return templates.TemplateResponse(
            "admin/quiz_edit.html",
            _ctx(request, quiz=quiz, items=items,
                 saved=request.query_params.get("saved")))
    except Exception as exc:  # noqa: BLE001 — بدل «Internal Server Error» الغامض: أظهر السبب
        import html as _html
        import traceback as _tb
        tb = _html.escape(_tb.format_exc())
        return HTMLResponse(
            "<div dir='rtl' style='font-family:sans-serif;padding:1.5rem;max-width:60rem;margin:auto'>"
            "<h2 style='color:#b71c1c'>تعذّر فتح محرّر هذا التقويم</h2>"
            f"<p><b>السبب:</b> {_html.escape(type(exc).__name__)}: {_html.escape(str(exc))}</p>"
            "<p>أرسِل هذا النصّ للمطوّر لإصلاحه فوراً، أو استعمل «حذف» لهذا التقويم "
            "وأعِد استيراده. بقيّة التقويمات غير متأثّرة.</p>"
            f"<pre style='background:#f6f8fa;padding:1rem;overflow:auto;font-size:.8rem'>{tb}</pre>"
            "<a href='/admin/quizzes'>→ رجوع إلى التقويمات</a></div>",
            status_code=200)


@router.post("/quizzes/{quiz_id}/edit")
async def quiz_edit_save(request: Request, quiz_id: int):
    """يطبّق تعديلات المحرّر على التقويم المحفوظ."""
    if (g := require_admin(request)):
        return g
    from sqlalchemy.orm import selectinload
    form = await request.form()
    async with AsyncSessionLocal() as s:
        quiz = (await s.execute(
            select(Quiz).where(Quiz.id == quiz_id)
            .options(selectinload(Quiz.questions)))).scalar_one_or_none()
        if quiz is None:
            return HTMLResponse("التقويم غير موجود", status_code=404)
        title = (form.get("title") or "").strip()
        if title:
            quiz.title = title
        by_id = {q.id: q for q in quiz.questions}
        for qid_str in (form.get("qids") or "").split(","):
            if not qid_str.strip().isdigit():
                continue
            q = by_id.get(int(qid_str))
            if q is None:
                continue
            if form.get(f"q_{q.id}_delete") == "on":
                await s.delete(q)
                continue
            prompt = (form.get(f"q_{q.id}_prompt") or "").strip()
            if prompt:
                q.prompt = prompt
            stim = (form.get(f"q_{q.id}_stimulus") or "").strip()
            q.stimulus = stim or None
            ms = form.get(f"q_{q.id}_max")
            if ms not in (None, ""):
                try:
                    q.max_score = max(0.0, float(str(ms).replace(",", ".")))
                except ValueError:
                    pass
            if q.qtype in ("mcq_single", "mcq_multi"):
                options, correct = [], []
                for line in (form.get(f"q_{q.id}_options") or "").splitlines():
                    t = line.strip()
                    if not t:
                        continue
                    is_c = t.startswith("*")
                    t = t[1:].strip() if is_c else t
                    if not t:
                        continue
                    if is_c:
                        correct.append(len(options))
                    options.append(t)
                if len(options) >= 2 and correct:
                    payload = dict(q.payload or {})
                    payload["options"] = options
                    payload["correct"] = correct
                    q.payload = payload            # إسناد قاموسٍ جديد يُعلِّم العمود متغيّراً
        await s.commit()
    return RedirectResponse(f"/admin/quizzes/{quiz_id}/edit?saved=1", status_code=303)


@router.post("/quizzes/{quiz_id}/delete")
async def quiz_delete(request: Request, quiz_id: int):
    """حذف تقويم وكلّ أسئلته وأجوبته بالتتالي."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        await s.execute(sa_delete(Quiz).where(Quiz.id == quiz_id))
        await s.commit()
    return RedirectResponse("/admin/quizzes", status_code=303)


# ═══════════════ تصحيح أجوبة التلاميذ (مصادقة + تنقيط المفتوحة) ═══════════════


async def _grade_view(s, quiz_id: int):
    """يبني بيانات شاشة التصحيح (quiz + questions) — مصدر واحد يشترك فيه العرض
    والحفظ الجزئيّ (HTMX)، فلا يفترق ما يُعرَض عمّا يُعاد بعد الحفظ."""
    from sqlalchemy.orm import selectinload
    quiz = (await s.execute(
        select(Quiz).where(Quiz.id == quiz_id)
        .options(selectinload(Quiz.questions)))).scalar_one_or_none()
    if quiz is None:
        return None, None
    rows = (await s.execute(
        select(Answer, Student)
        .join(Student, Student.id == Answer.student_id)
        .join(QuizQuestion, QuizQuestion.id == Answer.quiz_question_id)
        .where(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.position, Student.full_name))).all()
    # قائمة المتوقَّع منهم الإجابة (ح-١٢: لتمييز «لم يجب»): مشاركو جلسات هذا
    # التقويم الحاضرون + تلاميذ فوجه إن كان منزليّاً.
    expected: dict[int, str] = {}
    for sid_, name in (await s.execute(
            select(Student.id, Student.full_name)
            .join(SessionStudent, SessionStudent.student_id == Student.id)
            .join(QuizSession, QuizSession.id == SessionStudent.session_id)
            .where(QuizSession.quiz_id == quiz_id,
                   SessionStudent.present.is_(True)))).all():
        expected[sid_] = name
    if quiz.group_name:
        for sid_, name in (await s.execute(
                select(Student.id, Student.full_name).where(
                    Student.group_name == quiz.group_name,
                    Student.active.is_(True)))).all():
            expected[sid_] = name
    # تجميع الأجوبة حسب السؤال، مع نصّ مقروء وحالة الإغلاق والتسليم (سلّم/مسوّدة).
    by_q: dict[int, list] = {}
    answered: dict[int, set] = {}
    for ans, student in rows:
        q = next((qq for qq in quiz.questions if qq.id == ans.quiz_question_id), None)
        if q is None:
            continue
        by_q.setdefault(q.id, []).append({
            "ans": ans, "student": student,
            "readable": readable_answer(q, ans.raw),
            "effective": ans.manual_score if ans.manual_score is not None else ans.auto_score,
            "submitted": ans.submitted,          # سلّم؟ أم مسوّدة لم تُسلَّم؟
        })
        answered.setdefault(q.id, set()).add(student.id)
    # لكلّ سؤال: من لم يجب (متوقَّع بلا أيّ جواب) — الحالة الثالثة.
    # عناصر العرض (عنوان/نصّ) لا تُصحَّح فلا تظهر في شاشة التصحيح.
    questions = []
    for q in quiz.questions:
        if q.qtype in DISPLAY_TYPES:
            continue
        missing = [name for sid_, name in expected.items()
                   if sid_ not in answered.get(q.id, set())]
        questions.append({"q": q, "closed": q.qtype in QUESTION_TYPES_CLOSED,
                          "answers": by_q.get(q.id, []), "missing": missing})
    return quiz, questions


@router.get("/quizzes/{quiz_id}/grade", response_class=HTMLResponse)
async def quiz_grade_page(request: Request, quiz_id: int):
    """شاشة تصحيح: لكلّ سؤال أجوبة التلاميذ. المغلقة مصحّحة آليّاً (تُصادَق)،
    والمفتوحة يُدخل الأستاذ نقطتها ويصادق. تغذّي التقارير والذكاء الاصطناعي."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        quiz, questions = await _grade_view(s, quiz_id)
        if quiz is None:
            return HTMLResponse("التقويم غير موجود", status_code=404)
        # محرّر المعايير للأسئلة المفتوحة فقط (الكفاية + مؤشّرات النجاح للتصحيح الآليّ).
        rubric = [await _rubric_item(s, it["q"]) for it in questions if not it["closed"]]
    return templates.TemplateResponse(
        "admin/quiz_grade.html",
        _ctx(request, quiz=quiz, questions=questions, rubric=rubric,
             competencies=list(COMPETENCIES.items()),
             online=ollama_available(),
             ai_msg=request.query_params.get("ai")))


@router.post("/quizzes/{quiz_id}/grade/ai")
async def quiz_grade_ai(request: Request, quiz_id: int):
    """التصحيح المسائي المُعان: يقترح المحرّك المحلّي (Ollama) نقط الأسئلة المفتوحة.

    اقتراحٌ لا حكم — يُملأ في حقول النقط ليراجعها الأستاذ ويصادق. تسلسليّاً،
    وتدهور لطيف إن أُغلق المحرّك. المغلقة لا تُمسّ (مصحّحة يقينيّاً)."""
    if (g := require_admin(request)):
        return g
    if not ollama_available():
        return RedirectResponse(
            f"/admin/quizzes/{quiz_id}/grade?ai=المحرّك المحلّي غير مشغّل — شغّل Ollama.",
            status_code=303)
    from sqlalchemy.orm import selectinload
    suggested = 0
    skipped = 0
    async with AsyncSessionLocal() as s:
        quiz = (await s.execute(
            select(Quiz).where(Quiz.id == quiz_id)
            .options(selectinload(Quiz.questions)))).scalar_one_or_none()
        if quiz is None:
            return HTMLResponse("التقويم غير موجود", status_code=404)
        open_qs = {q.id: q for q in quiz.questions
                   if q.qtype not in QUESTION_TYPES_CLOSED
                   and q.qtype not in DISPLAY_TYPES}
        if open_qs:
            answers = (await s.execute(
                select(Answer).where(Answer.quiz_question_id.in_(list(open_qs))))).scalars().all()
            for ans in answers:
                # ح-١٠: لا يُكتب اقتراحٌ على جوابٍ نقّطه الأستاذ يدوياً أو صادق عليه —
                # الاقتراح مساعدةٌ للأجوبة غير المصحّحة فقط، لا يمحو عمل الأستاذ.
                if ans.manual_score is not None or ans.teacher_confirmed:
                    skipped += 1
                    continue
                q = open_qs[ans.quiz_question_id]
                answer_text = (ans.raw or {}).get("text", "") if isinstance(ans.raw, dict) else ""
                guidance = "\n".join(
                    f"- {i.get('text', '')} ({i.get('points', 0)} ن)"
                    for i in (q.indicators or []))
                try:
                    score, _note = suggest_open_score(q.prompt, guidance, answer_text, q.max_score)
                except AIUnavailable:
                    break
                ans.manual_score = score      # اقتراح؛ يبقى غير مصادَق حتى يراجعه الأستاذ
                suggested += 1
        await s.commit()
    msg = f"اقتُرحت {suggested} نقطة للأسئلة المفتوحة — راجِعها وصادِق."
    if skipped:
        msg += f" وتُركت {skipped} نقطة مصحّحة يدوياً كما هي."
    return RedirectResponse(f"/admin/quizzes/{quiz_id}/grade?ai={msg}", status_code=303)


@router.post("/quizzes/{quiz_id}/grade")
async def quiz_grade_save(request: Request, quiz_id: int):
    """يحفظ نقط الأستاذ للمفتوحة ويصادق على الأجوبة المؤشَّرة.

    ٤-ب: لا يمسّ إلّا الأجوبة المعروضة فعلاً في الصفحة (حقل shown) — فجوابٌ سلّمه
    تلميذ متأخّراً بعد فتح الشاشة لا يُلغى تصديقه سهواً (سباق التسليم المتأخّر).
    ولا يُصادَق على جوابٍ مفتوح بلا نقطة (تفادي صفر صامت)."""
    if (g := require_admin(request)):
        return g
    form = await request.form()
    shown: set[int] = set()
    for v in form.getlist("shown"):
        try:
            shown.add(int(v))
        except (TypeError, ValueError):
            pass
    async with AsyncSessionLocal() as s:
        if shown:
            answers = (await s.execute(
                select(Answer)
                .join(QuizQuestion, QuizQuestion.id == Answer.quiz_question_id)
                .where(QuizQuestion.quiz_id == quiz_id,
                       Answer.id.in_(shown)))).scalars().all()
            qmap = {q.id: q for q in (await s.execute(
                select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id))).scalars().all()}
            for ans in answers:
                raw_score = form.get(f"score_{ans.id}")
                if raw_score not in (None, ""):
                    try:
                        ans.manual_score = float(raw_score)
                    except ValueError:
                        pass
                want = form.get(f"confirm_{ans.id}") == "on"
                q = qmap.get(ans.quiz_question_id)
                is_closed = q is not None and q.qtype in QUESTION_TYPES_CLOSED
                eff = ans.manual_score if ans.manual_score is not None else ans.auto_score
                # مصادقة فقط إن كان مغلقاً (نقطته آليّة) أو كانت له نقطة فعليّة.
                ans.teacher_confirmed = want and (is_closed or eff is not None)
            await s.commit()
    if request.headers.get("HX-Request"):
        async with AsyncSessionLocal() as s:
            quiz, questions = await _grade_view(s, quiz_id)
        return templates.TemplateResponse(
            "admin/_quiz_grade_rows.html", _ctx(request, quiz=quiz, questions=questions))
    return RedirectResponse(f"/admin/quizzes/{quiz_id}/grade", status_code=303)


def _skill_to_competency() -> dict:
    """عكس COMPETENCY_TO_SKILL (اسم المهارة → مفتاح الكفاية) لاختيار الكفاية مسبقاً."""
    return {v: k for k, v in COMPETENCY_TO_SKILL.items()}


async def _rubric_item(s, q) -> dict:
    """يبني بيانات محرّر المعايير لسؤالٍ مفتوح: الكفاية الحاليّة + مؤشّرات النجاح."""
    comp = ""
    if q.skill_id is not None:
        sk = await s.get(Skill, q.skill_id)
        if sk is not None:
            comp = _skill_to_competency().get(sk.name, "")
    return {"q": q, "competency": comp, "indicators": q.indicators or []}


@router.post("/quizzes/{quiz_id}/grade/rubric")
async def quiz_grade_rubric(request: Request, quiz_id: int):
    """يحفظ معايير تصحيح سؤالٍ مفتوح: الكفاية + مؤشّرات النجاح (نصّ + نقطة لكلّ مؤشّر).

    تُخزَّن في السؤال نفسه فيستعملها التصحيح المُعان (Ollama) لاقتراح النقط، ثمّ
    يصادق الأستاذ. عناصر الإجابة = مؤشّرات النجاح (تخزينٌ موحَّد)."""
    if (g := require_admin(request)):
        return g
    form = await request.form()
    try:
        qid = int(form.get("question_id") or 0)
    except (TypeError, ValueError):
        qid = 0
    competency = (form.get("competency") or "").strip()
    texts = form.getlist("ind_text")
    points = form.getlist("ind_points")
    skills = await skill_id_map()
    async with AsyncSessionLocal() as s:
        q = await s.get(QuizQuestion, qid)
        if q is None or q.quiz_id != quiz_id:
            return HTMLResponse("السؤال غير موجود", status_code=404)
        q.skill_id = resolve_skill_id(competency, skills)
        inds = []
        for t, p in zip(texts, points):
            t = (t or "").strip()
            if not t:
                continue
            try:
                pts = round(float(p), 2)
            except (TypeError, ValueError):
                pts = 0.0
            inds.append({"id": f"i{len(inds) + 1}", "text": t, "points": pts})
        q.indicators = inds or None
        await s.commit()
        item = await _rubric_item(s, q)
        return templates.TemplateResponse(
            "admin/_quiz_grade_rubric.html",
            _ctx(request, quiz_id=quiz_id, item=item,
                 competencies=list(COMPETENCIES.items()), saved=True))


# ═══════════════ الجلسات الصفّية (فتح/إغلاق + حضور + متابعة آنية + قفل) ═══════════════


def _live_status(p: SessionStudent) -> tuple[str, str]:
    """حالة التلميذ في الجلسة للمتابعة الآنية: (وصف، لون)."""
    if not p.present:
        return ("غائب", "#9e9e9e")
    if p.submitted_at is not None:
        return ("سلّم ✓", "#2e7d32")
    if p.device_token:
        return ("يجيب…", "#1565c0")
    return ("لم يدخل", "#c62828")


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_list(request: Request):
    """لائحة الجلسات الصفّية، وإنشاء جلسة جديدة لتقويم على فوج."""
    if (g := require_admin(request)):
        return g
    from sqlalchemy.orm import selectinload
    async with AsyncSessionLocal() as s:
        sessions = (await s.execute(
            select(QuizSession).options(selectinload(QuizSession.quiz),
                                        selectinload(QuizSession.participants))
            .order_by(QuizSession.created_at.desc()))).scalars().all()
        quizzes = (await s.execute(select(Quiz).order_by(Quiz.created_at.desc()))).scalars().all()
        groups = [r[0] for r in (await s.execute(
            select(Student.group_name).where(Student.group_name.is_not(None))
            .distinct().order_by(Student.group_name))).all()]
    rows = []
    for sess in sessions:
        subs = sum(1 for p in sess.participants if p.submitted_at)
        present = sum(1 for p in sess.participants if p.present)
        rows.append({"s": sess, "subs": subs, "present": present,
                     "total": len(sess.participants)})
    return templates.TemplateResponse(
        "admin/sessions.html", _ctx(request, rows=rows, quizzes=quizzes, groups=groups,
                                    error=request.query_params.get("error")))


@router.post("/sessions/create")
async def session_create(request: Request, quiz_id: int = Form(...),
                         group_name: str = Form(...)):
    """ينشئ جلسة (مسودّة) ويضيف تلاميذ الفوج النشطين مشاركين حاضرين افتراضياً."""
    if (g := require_admin(request)):
        return g
    group_name = group_name.strip()
    async with AsyncSessionLocal() as s:
        # ح-١٥: منع صارم لفتح جلسة بتقويم لا يطابق مستوى الفوج (المستوى يُشتقّ من
        # بادئة رمز القسم). إن كان للتقويم مستوى وعُرف مستوى الفوج واختلفا → رفض.
        quiz = await s.get(Quiz, quiz_id)
        if quiz is None:
            return RedirectResponse("/admin/sessions?error=التقويم غير موجود.",
                                    status_code=303)
        lvl_code = level_of_class_label(group_name)
        if quiz.level_id is not None and lvl_code is not None:
            grp_level = await s.scalar(select(Level).where(Level.code == lvl_code))
            if grp_level is not None and grp_level.id != quiz.level_id:
                return RedirectResponse(
                    "/admin/sessions?error=مستوى التقويم لا يطابق مستوى الفوج — "
                    "اختر تقويماً من مستوى الفوج نفسه.", status_code=303)
        sess = QuizSession(quiz_id=quiz_id, group_name=group_name, status="draft")
        s.add(sess)
        await s.flush()
        students = (await s.execute(
            select(Student).where(Student.group_name == group_name,
                                  Student.active.is_(True)))).scalars().all()
        for st in students:
            s.add(SessionStudent(session_id=sess.id, student_id=st.id, present=True))
        await s.commit()
        sid = sess.id
    return RedirectResponse(f"/admin/sessions/{sid}", status_code=303)


@router.get("/sessions/{sid}", response_class=HTMLResponse)
async def session_manage(request: Request, sid: int):
    """إدارة الجلسة: بوابة الحضور، فتح/إغلاق، ورابط المتابعة الآنية."""
    if (g := require_admin(request)):
        return g
    from sqlalchemy.orm import selectinload
    async with AsyncSessionLocal() as s:
        sess = (await s.execute(
            select(QuizSession).where(QuizSession.id == sid)
            .options(selectinload(QuizSession.quiz),
                     selectinload(QuizSession.participants).selectinload(SessionStudent.student)))).scalar_one_or_none()
    if sess is None:
        return HTMLResponse("الجلسة غير موجودة", status_code=404)
    parts = sorted(sess.participants, key=lambda p: p.student.full_name)
    return templates.TemplateResponse(
        "admin/session_manage.html", _ctx(request, sess=sess, parts=parts))


@router.post("/sessions/{sid}/attendance")
async def session_attendance(request: Request, sid: int):
    """يحفظ الحضور: المؤشَّرون حاضرون، والباقون غائبون (تُرفض رموزهم في الجلسة)."""
    if (g := require_admin(request)):
        return g
    form = await request.form()
    async with AsyncSessionLocal() as s:
        parts = (await s.execute(
            select(SessionStudent).where(SessionStudent.session_id == sid))).scalars().all()
        for p in parts:
            p.present = form.get(f"present_{p.id}") == "on"
        await s.commit()
    return RedirectResponse(f"/admin/sessions/{sid}", status_code=303)


@router.post("/sessions/{sid}/open")
async def session_open(request: Request, sid: int):
    if (g := require_admin(request)):
        return g
    from datetime import datetime
    async with AsyncSessionLocal() as s:
        sess = await s.get(QuizSession, sid)
        if sess:
            sess.status = "open"
            sess.opened_at = datetime.now()
            await s.commit()
    return RedirectResponse(f"/admin/sessions/{sid}/live", status_code=303)


@router.post("/sessions/{sid}/close")
async def session_close(request: Request, sid: int):
    if (g := require_admin(request)):
        return g
    from datetime import datetime
    from sqlalchemy.orm import selectinload
    async with AsyncSessionLocal() as s:
        sess = await s.get(QuizSession, sid)
        if sess:
            sess.status = "closed"
            sess.closed_at = datetime.now()
            # ح-١٢: تسليم آليّ لمسوّدات الحاضرين الذين لم يسلّموا — لئلّا تضيع أجوبتهم.
            # المغلقة تُصحَّح يقينياً وتُصادَق؛ المفتوحة تُسلَّم وتنتظر الأستاذ.
            quiz = (await s.execute(
                select(Quiz).where(Quiz.id == sess.quiz_id)
                .options(selectinload(Quiz.questions)))).scalar_one_or_none()
            q_by_id = {q.id: q for q in (quiz.questions if quiz else [])}
            parts = (await s.execute(select(SessionStudent).where(
                SessionStudent.session_id == sid,
                SessionStudent.present.is_(True),
                SessionStudent.submitted_at.is_(None)))).scalars().all()
            for part in parts:
                drafts = (await s.execute(select(Answer).where(
                    Answer.student_id == part.student_id,
                    Answer.quiz_question_id.in_(list(q_by_id)),
                    Answer.submitted.is_(False)))).scalars().all()
                for ans in drafts:
                    q = q_by_id.get(ans.quiz_question_id)
                    if q is None:
                        continue
                    graded = grade_answer(q, ans.raw)
                    ans.auto_score = graded["score"]
                    ans.teacher_confirmed = q.qtype in QUESTION_TYPES_CLOSED
                    ans.submitted = True
                if drafts:
                    part.submitted_at = datetime.now()
            await s.commit()
    # نسخة احتياطية تلقائية عند إغلاق الجلسة (أفضل جهد — لا تُفشِل الإغلاق).
    await asyncio.to_thread(try_backup_quiet)
    return RedirectResponse(f"/admin/sessions/{sid}", status_code=303)


@router.post("/sessions/{sid}/delete")
async def session_delete(request: Request, sid: int):
    """حذف جلسةٍ (تجريبيّة غير رسميّة مثلاً) مع **أثرها في التقارير**: تُحذف أجوبةُ
    المشاركين فيها لأسئلة هذا التقويم، فلا تبقى نقطُ التجربة في دفتر النقط. (لا يمسّ
    أجوبة تلاميذَ غير مشاركين، ولا أسئلة التقويم نفسه — يبقى قابلاً لإعادة التمرير.)"""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        sess = await s.get(QuizSession, sid)
        if sess is not None:
            part_ids = (await s.execute(select(SessionStudent.student_id)
                        .where(SessionStudent.session_id == sid))).scalars().all()
            qids = (await s.execute(select(QuizQuestion.id)
                    .where(QuizQuestion.quiz_id == sess.quiz_id))).scalars().all()
            if part_ids and qids:
                await s.execute(sa_delete(Answer).where(
                    Answer.student_id.in_(part_ids),
                    Answer.quiz_question_id.in_(qids)))
            await s.execute(sa_delete(QuizSession).where(QuizSession.id == sid))
            await s.commit()
    return RedirectResponse("/admin/sessions", status_code=303)


@router.get("/sessions/{sid}/live", response_class=HTMLResponse)
async def session_live(request: Request, sid: int):
    """شاشة المتابعة الآنية (تُحدَّث ذاتيّاً عبر HTMX)."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        sess = await s.get(QuizSession, sid)
    if sess is None:
        return HTMLResponse("الجلسة غير موجودة", status_code=404)
    return templates.TemplateResponse("admin/session_live.html", _ctx(request, sess=sess))


@router.get("/sessions/{sid}/live/status", response_class=HTMLResponse)
async def session_live_status(request: Request, sid: int):
    """جزء HTMX: شبكة حالات التلاميذ + العدّادات."""
    if (g := require_admin(request)):
        return g
    from sqlalchemy.orm import selectinload
    async with AsyncSessionLocal() as s:
        sess = (await s.execute(
            select(QuizSession).where(QuizSession.id == sid)
            .options(selectinload(QuizSession.participants).selectinload(SessionStudent.student)))).scalar_one_or_none()
        if sess is None:
            return HTMLResponse("—", status_code=404)
        # تقدّم كلّ تلميذ آنيّاً: كم سؤالاً أجاب وأين وقف (آخر سؤال) من مجموع الأسئلة
        # القابلة للإجابة (تُستثنى العناوين/النصوص). يُحسب من مسوّدات الأجوبة المحفوظة.
        qqs = (await s.execute(
            select(QuizQuestion.id, QuizQuestion.qtype)
            .where(QuizQuestion.quiz_id == sess.quiz_id)
            .order_by(QuizQuestion.position))).all()
        answerable = [qid for qid, qt in qqs if qt not in DISPLAY_TYPES]
        total_q = len(answerable)
        qnum = {qid: i + 1 for i, qid in enumerate(answerable)}   # ترقيم متسلسل كالتلميذ
        # لا تُنشَأ أسطر أجوبة فارغة (ح-١٣)، فوجود سطر = إجابة فعليّة على السؤال.
        answered: dict[int, set[int]] = {}
        if answerable:
            for stu_id, qq_id in (await s.execute(
                select(Answer.student_id, Answer.quiz_question_id)
                .where(Answer.quiz_question_id.in_(answerable)))).all():
                answered.setdefault(stu_id, set()).add(qq_id)
    parts = sorted(sess.participants, key=lambda p: p.student.full_name)
    rows = []
    for p in parts:
        done_ids = answered.get(p.student_id, set())
        done = len(done_ids)
        last = max((qnum[q] for q in done_ids), default=0)   # أبعد سؤال بلغه
        rows.append({"p": p, "name": p.student.full_name, "status": _live_status(p),
                     "done": done, "total": total_q, "last": last,
                     "pct": round(done * 100 / total_q) if total_q else 0})
    present = sum(1 for p in parts if p.present)
    submitted = sum(1 for p in parts if p.submitted_at)
    return templates.TemplateResponse(
        "admin/_session_live_status.html",
        _ctx(request, sid=sid, sess=sess, rows=rows, present=present,
             submitted=submitted, total_q=total_q))


@router.post("/sessions/{sid}/unlock/{part_id}")
async def session_unlock(request: Request, sid: int, part_id: int):
    """فكّ قفل جهاز تلميذ (هاتف نفد شحنه/خروج مفاجئ) ليدخل من جديد."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        p = await s.get(SessionStudent, part_id)
        if p and p.session_id == sid:
            p.device_token = None      # يسمح بمطالبة جديدة من أي جهاز
            await s.commit()
    return RedirectResponse(f"/admin/sessions/{sid}/live", status_code=303)

