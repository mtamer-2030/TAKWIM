"""مجال النصوص الفلسفيّة (عرض/حذف). مسارات تحت /admin (يضمّها admin.py).
السلوك والمسارات لا تتغيّر بالتفكيك."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete as sa_delete, select

from ...database import AsyncSessionLocal
from ...models import Axis, PhilosophicalText
from ..web import _ctx, require_admin, templates

router = APIRouter()


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
