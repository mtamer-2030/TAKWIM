"""مجال الشبكة والوصول: رمز QR (كلّ العناوين)، عدّاد الهواتف المتّصلة، بطاقات الدخول
بالمسح، والنسخة الاحتياطيّة. مسارات تحت /admin (يضمّها admin.py)."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from ... import presence
from ...backup import backup_bytes
from ...database import AsyncSessionLocal
from ...models import Student
from ...netinfo import lan_ips, lan_url
from ...qrcodes import qr_png
from ...settings import settings
from ..web import _ctx, require_admin, templates

router = APIRouter()


def _student_url() -> str:
    """عنوان دخول التلميذ للـ QR: IP الشبكة المحلّية إن اكتُشف، وإلّا public_url."""
    return lan_url(settings.port) or settings.public_url


def _all_student_urls() -> list[str]:
    """كلّ عناوين الحاسوب المكتشَفة كروابط دخولٍ للتلميذ. حين يحمل الحاسوب أكثر من
    بطاقة/شبكة لا نعرف مسبقاً أيُّها يطابق شبكةَ الهواتف، فنعرضها كلَّها ليجرّب الأستاذ
    الصحيح — الحلّ الجذريّ لـ«يعرض نسخة offline» (عنوانٌ لا يصله الهاتف)."""
    port = settings.port
    urls = [f"http://{ip}:{port}" for ip in lan_ips()]
    return urls or [_student_url()]


def _join_url(st: Student) -> str:
    """رابط الدخول بلا كتابة لتلميذٍ بعينه: {عنوان الخادم}/student/join?code=رمزه.
    نفضّل login_code (بلاحقةٍ عشوائيّة أأمن)، وإلّا massar_code."""
    code = (st.login_code or st.massar_code or "").strip().upper()
    return f"{_student_url()}/student/join?code={quote(code, safe='')}"


@router.get("/qr", response_class=HTMLResponse)
async def qr_page(request: Request):
    """صفحة رمز QR: تعرض كلَّ عناوين الحاسوب المكتشَفة (لا عنواناً واحداً) — فإن كان
    الهاتف على شبكةٍ فرعيّة مختلفة يختار الأستاذ العنوان المطابق بدل تخمين."""
    if (g := require_admin(request)):
        return g
    urls = _all_student_urls()
    return templates.TemplateResponse(
        "admin/qr.html", _ctx(request, url=urls[0], urls=urls))


@router.get("/qr.png")
async def qr_png_route(request: Request):
    """صورة رمز QR (PNG) تُولَّد محلّياً. يقبل ?ip= لعنوانٍ بعينه (مُتحقَّقٌ منه ضمن
    العناوين المكتشَفة فقط) ليكون لكلّ عنوانٍ رمزُه الخاصّ."""
    if (g := require_admin(request)):
        return g
    ip = (request.query_params.get("ip") or "").strip()
    port = settings.port
    url = f"http://{ip}:{port}" if ip and f"http://{ip}:{port}" in _all_student_urls() \
        else _student_url()
    return Response(content=qr_png(url), media_type="image/png")


@router.get("/connected")
async def connected_count(request: Request):
    """عدد الهواتف التي زارت مسار التلميذ خلال آخر دقيقةٍ ونصف — عدّاد القاعة الحيّ."""
    if (g := require_admin(request)):
        return g
    return {"count": presence.count()}


@router.get("/cards", response_class=HTMLResponse)
async def student_cards(request: Request, group: str | None = None):
    """بطاقاتٌ قابلة للطباعة: بطاقةٌ لكلّ تلميذ (اسمه + رمز QR خاصّ) — مسحُها = دخولٌ
    بلا كتابةٍ ولا رمز."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        stmt = select(Student).where(Student.active.is_(True))
        if group:
            stmt = stmt.where(Student.group_name == group)
        stmt = stmt.order_by(Student.group_name, Student.full_name)
        students = (await s.execute(stmt)).scalars().all()
        groups = [g for (g,) in (await s.execute(
            select(Student.group_name).where(Student.group_name.is_not(None))
            .distinct().order_by(Student.group_name))).all()]
    return templates.TemplateResponse(
        "admin/cards.html",
        _ctx(request, students=students, groups=groups, group=group,
             base=_student_url()))


@router.get("/cards/qr/{student_id}.png")
async def student_card_qr(request: Request, student_id: int):
    """رمز QR (PNG) لبطاقة تلميذٍ بعينه — يحمل رابط الدخول بلا كتابة."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        st = await s.get(Student, student_id)
    if st is None:
        return Response(status_code=404)
    return Response(content=qr_png(_join_url(st)), media_type="image/png")


@router.post("/backup")
async def backup_now(request: Request):
    """«نسخة احتياطية الآن»: نسخة متّسقة (تُفحَص سلامتها) تُنزَّل على المتصفّح."""
    if (g := require_admin(request)):
        return g
    try:
        name, data = await asyncio.to_thread(backup_bytes)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/admin?backup_error={exc}", status_code=303)
    return Response(
        content=data, media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})
