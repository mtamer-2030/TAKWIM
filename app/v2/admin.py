"""لوحة الأستاذ v2 (/admin) — Tabler + Chart.js + سير عمل الاستيراد الثنائي.

سير الاستيراد: رفع الملفّ ← استخراج النصّ وعرضه في حقل قابل للتعديل (مراجعة
الأستاذ وإصلاح أخطاء OCR) ← الحفظ النهائي في القاعدة (نماذج SQLAlchemy).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import delete as sa_delete, func, select

from ..ai_feedback import (
    AIUnavailable,
    generate_class_plan,
    generate_student_plan,
    ollama_available,
)
from ..database import AsyncSessionLocal
from ..docx_import import parse_docx, parse_lines
from ..importer import ImporterError, extract_text, parse_students_excel
from ..models import (
    Axis,
    ClassReport,
    Concept,
    EssayExercise,
    EvaluationEvent,
    Level,
    Module,
    PhilosophicalText,
    Quiz,
    QuizAnswer,
    QuizQuestion,
    Student,
    StudentReport,
    Submission,
    TextType,
)
from ..netinfo import lan_url
from ..qrcodes import qr_png
from ..services.analytics import generate_class_report, generate_student_skill_profile
from ..constants import QUESTION_TYPES_CLOSED
from ..services.quizzes import (
    QuizImportError,
    build_quiz,
    normalize_quiz_json,
    readable_answer,
)
from ..settings import settings
from .web import (
    ADMIN_COOKIE,
    check_admin_password,
    issue_admin_token,
    require_admin,
    revoke_admin_token,
    templates,
)

router = APIRouter(prefix="/admin")


def _ctx(request: Request, **extra):
    return {"request": request, **extra}


def _student_url() -> str:
    """عنوان دخول التلميذ للـ QR: IP الشبكة المحلّية إن اكتُشف، وإلّا public_url."""
    return lan_url(settings.port) or settings.public_url


# ═══════════════ الاستيثاق ═══════════════


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("admin/login.html", _ctx(request))


@router.post("/login")
def login(request: Request, password: str = Form(...)):
    if not check_admin_password(password):
        return templates.TemplateResponse(
            "admin/login.html", _ctx(request, error="كلمة السرّ غير صحيحة."),
            status_code=401)
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
    async with AsyncSessionLocal() as s:
        counts = {
            "students": await s.scalar(select(func.count()).select_from(Student)),
            "texts": await s.scalar(select(func.count()).select_from(PhilosophicalText)),
            "essays": await s.scalar(select(func.count()).select_from(EssayExercise)),
            "submissions": await s.scalar(select(func.count()).select_from(Submission)),
        }
        # توزيع التلاميذ حسب المستوى (لرسم Chart.js تجريبي)
        rows = (await s.execute(
            select(Level.name, func.count(Student.id))
            .join(Student, Student.level_id == Level.id, isouter=True)
            .group_by(Level.id))).all()
    chart = {"labels": [r[0] for r in rows], "data": [r[1] for r in rows]}
    base = _student_url()                       # عنوان الشبكة المحلّية المكتشَف تلقائياً
    return templates.TemplateResponse(
        "admin/dashboard.html",
        _ctx(request, counts=counts, chart=json.dumps(chart),
             lan_base=base, student_url=f"{base}/student", auto=bool(lan_url(settings.port))))


# ═══════════════ رمز QR لربط هواتف التلاميذ ═══════════════


@router.get("/qr", response_class=HTMLResponse)
async def qr_page(request: Request):
    """صفحة رمز QR ثابت: يعرضه الأستاذ على السبّورة أو يطبعه ليمسحه التلاميذ."""
    if (g := require_admin(request)):
        return g
    return templates.TemplateResponse("admin/qr.html", _ctx(request, url=_student_url()))


@router.get("/qr.png")
async def qr_png_route(request: Request):
    """صورة رمز QR (PNG) تُولَّد محلّياً — لا إنترنت ولا CDN."""
    if (g := require_admin(request)):
        return g
    return Response(content=qr_png(_student_url()), media_type="image/png")


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


@router.post("/import/save/roster")
async def import_save_roster(request: Request, level_id: int = Form(...),
                             group_override: str = Form(""), rows_json: str = Form(...)):
    """الحفظ النهائي للائحة: تراكمي (مطابقة برمز مسار)، بلا حذف."""
    if (g := require_admin(request)):
        return g
    rows = json.loads(rows_json)
    added = updated = 0
    async with AsyncSessionLocal() as s:
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
                updated += 1
            else:
                s.add(Student(full_name=name, level_id=level_id, group_name=group,
                              massar_code=massar))
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
        levels = {lv.id: lv.name for lv in
                  (await s.execute(select(Level))).scalars().all()}
    return templates.TemplateResponse(
        "admin/rosters.html",
        _ctx(request, students=students, levels=levels,
             added=request.query_params.get("added"),
             updated=request.query_params.get("updated")))


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
             model=settings.local_ai.model, report_count=report_count, result=None))


@router.post("/ai/run", response_class=HTMLResponse)
async def ai_run(request: Request, group_name: str = Form(...)):
    """معالجة تسلسلية (واحدة تلو الأخرى) لتفادي اختناق المعالج/بطاقة الرسوم.

    تُحسب التحاليل ثمّ يُنادى Ollama لكلّ تلميذ على حدة، وتُحفظ في StudentReport،
    ثمّ تقرير القسم في ClassReport. تدهور لطيف: إن كان المحرك مغلقاً لا 500.
    """
    if (g := require_admin(request)):
        return g

    async def render(**extra):
        async with AsyncSessionLocal() as s:
            groups = [r[0] for r in (await s.execute(
                select(Student.group_name).where(Student.group_name.is_not(None))
                .distinct().order_by(Student.group_name))).all()]
        return templates.TemplateResponse(
            "admin/ai.html",
            _ctx(request, groups=groups, online=ollama_available(),
                 model=settings.local_ai.model, report_count=None, **extra))

    if not ollama_available():
        return await render(result={"offline": True,
                                    "message": "محرك الذكاء الاصطناعي غير مشغل. "
                                               "يرجى تشغيل Ollama أولاً."})

    processed = failed = 0
    stopped_offline = False
    async with AsyncSessionLocal() as s:
        students = (await s.execute(
            select(Student).where(Student.group_name == group_name,
                                  Student.active.is_(True))
            .order_by(Student.full_name))).scalars().all()

        # (1) تقارير فردية — تسلسلياً
        for st in students:
            profile = await generate_student_skill_profile(s, st.id)
            if not profile["has_data"]:
                continue
            try:
                plan = generate_student_plan(profile)   # نداء Ollama (متزامن، واحد تلو الآخر)
            except AIUnavailable:
                stopped_offline = True
                break
            s.add(StudentReport(
                student_id=st.id, skill_profile=profile["skills"],
                ai_intervention_plan=plan, ai_model=settings.local_ai.model))
            processed += 1

        # (2) تقرير القسم — بعد الأفراد
        class_report = None
        if not stopped_offline:
            report = await generate_class_report(s, group_name)
            if report["has_data"]:
                try:
                    plan = generate_class_plan(report)
                    level_id = students[0].level_id if students else None
                    s.add(ClassReport(
                        level_id=level_id, group_name=group_name,
                        skills_summary=report["skills"],
                        weakest_skill=report["dominant_deficit"],
                        ai_intervention_plan=plan, ai_model=settings.local_ai.model))
                    class_report = {"dominant": report["dominant_deficit"], "plan": plan}
                except AIUnavailable:
                    stopped_offline = True
        await s.commit()

    return await render(result={
        "offline": stopped_offline, "processed": processed, "failed": failed,
        "group_name": group_name, "class_report": class_report,
        "message": ("انقطع المحرك أثناء المعالجة؛ حُفظ ما تمّ." if stopped_offline
                    else f"تمّ توليد {processed} تقرير تدخّل فردي وتقرير القسم.")})


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


# ═══════════════ التقاويم (سياقات التقويم) ═══════════════


@router.get("/events", response_class=HTMLResponse)
async def events(request: Request):
    """لائحة التقاويم مع عدد الإنجازات المرتبطة بكلّ تقويم، وإمكان الحذف."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(EvaluationEvent, func.count(Submission.id))
            .join(Submission, Submission.event_id == EvaluationEvent.id, isouter=True)
            .group_by(EvaluationEvent.id)
            .order_by(EvaluationEvent.created_at.desc()))).all()
    return templates.TemplateResponse("admin/events.html", _ctx(request, rows=rows))


@router.post("/events/{event_id}/delete")
async def delete_event(request: Request, event_id: int):
    """حذف تقويم وكلّ إنجازاته بالتتالي (ON DELETE CASCADE)."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        await s.execute(sa_delete(EvaluationEvent).where(EvaluationEvent.id == event_id))
        await s.commit()
    return RedirectResponse("/admin/events", status_code=303)


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


@router.post("/quizzes/import")
async def quizzes_import(request: Request, file: UploadFile = File(...),
                         level_id: str = Form(""), group_name: str = Form("")):
    """رفع تقويم (JSON/Word/PDF) → أسئلة. تصحيح صارم؛ عند الخطأ تُعرَض الأسباب."""
    if (g := require_admin(request)):
        return g
    data = await file.read()
    normalized, errors = _normalize_quiz_upload(file.filename, data)
    if normalized is None:
        msg = " | ".join(errors) or "تعذّر تحويل الملفّ."
        return RedirectResponse(f"/admin/quizzes?error={msg}", status_code=303)
    lid = int(level_id) if level_id.strip().isdigit() else None
    async with AsyncSessionLocal() as s:
        quiz = build_quiz(normalized, level_id=lid, group_name=group_name.strip() or None)
        s.add(quiz)
        await s.commit()
        n = len(quiz.questions)
    return RedirectResponse(
        f"/admin/quizzes?saved=تمّ استيراد «{normalized['title']}» بـ{n} سؤالاً.",
        status_code=303)


@router.post("/quizzes/{quiz_id}/assign")
async def quiz_assign(request: Request, quiz_id: int, level_id: str = Form(""),
                      group_name: str = Form(""), published: str = Form("")):
    """إسناد التقويم إلى مستوى/فوج وضبط نشره (رؤية التلاميذ له)."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        quiz = await s.get(Quiz, quiz_id)
        if quiz:
            quiz.level_id = int(level_id) if level_id.strip().isdigit() else None
            quiz.group_name = group_name.strip() or None
            quiz.published = (published == "on")
            await s.commit()
    return RedirectResponse("/admin/quizzes", status_code=303)


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


@router.get("/quizzes/{quiz_id}/grade", response_class=HTMLResponse)
async def quiz_grade_page(request: Request, quiz_id: int):
    """شاشة تصحيح: لكلّ سؤال أجوبة التلاميذ. المغلقة مصحّحة آليّاً (تُصادَق)،
    والمفتوحة يُدخل الأستاذ نقطتها ويصادق. تغذّي التقارير والذكاء الاصطناعي."""
    if (g := require_admin(request)):
        return g
    from sqlalchemy.orm import selectinload
    async with AsyncSessionLocal() as s:
        quiz = (await s.execute(
            select(Quiz).where(Quiz.id == quiz_id)
            .options(selectinload(Quiz.questions)))).scalar_one_or_none()
        if quiz is None:
            return HTMLResponse("التقويم غير موجود", status_code=404)
        rows = (await s.execute(
            select(QuizAnswer, Student)
            .join(Student, Student.id == QuizAnswer.student_id)
            .join(QuizQuestion, QuizQuestion.id == QuizAnswer.question_id)
            .where(QuizQuestion.quiz_id == quiz_id)
            .order_by(QuizQuestion.position, Student.full_name))).all()
    # تجميع الأجوبة حسب السؤال، مع نصّ مقروء وحالة الإغلاق.
    by_q: dict[int, list] = {}
    for ans, student in rows:
        q = next((qq for qq in quiz.questions if qq.id == ans.question_id), None)
        if q is None:
            continue
        by_q.setdefault(q.id, []).append({
            "ans": ans, "student": student,
            "readable": readable_answer(q, ans.raw),
            "effective": ans.manual_score if ans.manual_score is not None else ans.auto_score,
        })
    questions = [{"q": q, "closed": q.qtype in QUESTION_TYPES_CLOSED,
                  "answers": by_q.get(q.id, [])} for q in quiz.questions]
    return templates.TemplateResponse(
        "admin/quiz_grade.html", _ctx(request, quiz=quiz, questions=questions))


@router.post("/quizzes/{quiz_id}/grade")
async def quiz_grade_save(request: Request, quiz_id: int):
    """يحفظ نقط الأستاذ للمفتوحة ويصادق على الأجوبة المؤشَّرة."""
    if (g := require_admin(request)):
        return g
    form = await request.form()
    async with AsyncSessionLocal() as s:
        answers = (await s.execute(
            select(QuizAnswer)
            .join(QuizQuestion, QuizQuestion.id == QuizAnswer.question_id)
            .where(QuizQuestion.quiz_id == quiz_id))).scalars().all()
        for ans in answers:
            raw_score = form.get(f"score_{ans.id}")
            if raw_score not in (None, ""):
                try:
                    ans.manual_score = float(raw_score)
                except ValueError:
                    pass
            ans.teacher_confirmed = form.get(f"confirm_{ans.id}") == "on"
        await s.commit()
    return RedirectResponse(f"/admin/quizzes/{quiz_id}/grade", status_code=303)
