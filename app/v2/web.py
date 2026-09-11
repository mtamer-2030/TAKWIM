"""بنية ويب v2 المشتركة: القوالب، استيثاق الأستاذ، جلسة التلميذ، بذر المستويات."""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from ..constants import LEVELS, SKILLS  # {code: arabic_name} + المهارات الستّ الموحّدة
from ..database import AsyncSessionLocal
from ..models import Level, Skill
from ..settings import settings

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class _Templates(Jinja2Templates):
    """يقبل النمطين القديم (name, ctx) والحديث (request, name, ctx)."""

    def TemplateResponse(self, *args, **kwargs):  # noqa: N802
        if args and isinstance(args[0], str):
            name = args[0]
            context = args[1] if len(args) > 1 else kwargs.pop("context", {}) or {}
            request = context.get("request")
            return super().TemplateResponse(request, name, context, *args[2:], **kwargs)
        return super().TemplateResponse(*args, **kwargs)


templates = _Templates(directory=str(BASE_DIR / "templates" / "v2"))
templates.env.globals.update({"LEVELS": LEVELS})

# ——— استيثاق الأستاذ (كلمة سرّ config.ini، رمز في الذاكرة) ———
ADMIN_COOKIE = "pt_admin"
STUDENT_COOKIE = "pt_student"
_admin_tokens: set[str] = set()


def check_admin_password(password: str) -> bool:
    return secrets.compare_digest(password or "", settings.teacher_password)


def issue_admin_token() -> str:
    t = secrets.token_urlsafe(24)
    _admin_tokens.add(t)
    return t


def revoke_admin_token(token: str | None) -> None:
    if token:
        _admin_tokens.discard(token)


def is_admin(request: Request) -> bool:
    t = request.cookies.get(ADMIN_COOKIE)
    return bool(t and t in _admin_tokens)


def require_admin(request: Request):
    """None إن مستوثقاً، وإلّا تحويل إلى صفحة الدخول."""
    return None if is_admin(request) else RedirectResponse("/admin/login", status_code=303)


def current_student_id(request: Request) -> int | None:
    raw = request.cookies.get(STUDENT_COOKIE)
    if raw and raw.isdigit():
        return int(raw)
    return None


async def seed_levels() -> None:
    """يبذر المستويات الثلاثة (جذع/أولى/ثانية) إن لم تكن موجودة."""
    async with AsyncSessionLocal() as s:
        existing = {r for (r,) in (await s.execute(select(Level.code))).all()}
        pos = 0
        for code, name in LEVELS.items():
            if code not in existing:
                s.add(Level(code=code, name=name, position=pos))
            pos += 1
        await s.commit()


async def seed_skills() -> None:
    """يبذر المهارات الستّ الموحّدة في جدول skills إن لم تكن موجودة."""
    async with AsyncSessionLocal() as s:
        existing = {r for (r,) in (await s.execute(select(Skill.name))).all()}
        for pos, name in enumerate(SKILLS):
            if name not in existing:
                s.add(Skill(name=name, position=pos))
        await s.commit()


async def skill_id_map() -> dict[str, int]:
    """خريطة (اسم المهارة → id) من جدول skills المبذور — لحلّ skill_id عند الاستيراد."""
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(Skill.name, Skill.id))).all()
    return {name: sid for name, sid in rows}
