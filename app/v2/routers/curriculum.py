"""مجال المنهاج (مستويات/مجزوءات/مفاهيم/محاور): عرضٌ وإضافةٌ وحذفٌ بالتتالي — ليكون
للنصوص هدفٌ في الشجرة. مسارات تحت /admin (يضمّها admin.py). السلوك والمسارات لا تتغيّر."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete as sa_delete, select

from ...database import AsyncSessionLocal
from ...models import Axis, Concept, Level, Module
from ..web import _ctx, require_admin, templates

router = APIRouter()


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
