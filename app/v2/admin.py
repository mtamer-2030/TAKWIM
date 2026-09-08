"""لوحة الأستاذ v2 (/admin) — Tabler + Chart.js + سير عمل الاستيراد الثنائي.

سير الاستيراد: رفع الملفّ ← استخراج النصّ وعرضه في حقل قابل للتعديل (مراجعة
الأستاذ وإصلاح أخطاء OCR) ← الحفظ النهائي في القاعدة (نماذج SQLAlchemy).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from ..database import AsyncSessionLocal
from ..importer import ImporterError, extract_text, parse_students_excel
from ..models import (
    Axis,
    Concept,
    EssayExercise,
    Level,
    Module,
    PhilosophicalText,
    Student,
    Submission,
    TextType,
)
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
    return templates.TemplateResponse(
        "admin/dashboard.html", _ctx(request, counts=counts, chart=json.dumps(chart)))


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
