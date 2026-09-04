"""بنية الويب المشتركة: القوالب والاستيثاق (لوحة الأستاذ)."""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from .constants import COMPETENCIES, KIND_LABELS, KINDS, LEVELS
from .settings import settings

BASE_DIR = Path(__file__).resolve().parent.parent


class _Templates(Jinja2Templates):
    """يقبل النمط القديم (name, context) والحديث (request, name, context)."""

    def TemplateResponse(self, *args, **kwargs):  # noqa: N802
        if args and isinstance(args[0], str):
            name = args[0]
            context = args[1] if len(args) > 1 else kwargs.pop("context", {}) or {}
            request = context.get("request")
            return super().TemplateResponse(request, name, context, *args[2:], **kwargs)
        return super().TemplateResponse(*args, **kwargs)


templates = _Templates(directory=str(BASE_DIR / "templates"))

# متغيّرات متاحة في كل قالب
templates.env.globals.update({
    "COMPETENCIES": COMPETENCIES,
    "LEVELS": LEVELS,
    "KINDS": KINDS,
    "KIND_LABELS": KIND_LABELS,
    "public_url": settings.public_url,
})

TEACHER_COOKIE = "mihakk_teacher"
_valid_tokens: set[str] = set()


def check_teacher_password(password: str) -> bool:
    # مقارنة ثابتة الزمن.
    return secrets.compare_digest(password or "", settings.teacher_password)


def issue_teacher_token() -> str:
    token = secrets.token_urlsafe(24)
    _valid_tokens.add(token)
    return token


def revoke_teacher_token(token: str | None) -> None:
    if token:
        _valid_tokens.discard(token)


def is_teacher(request: Request) -> bool:
    token = request.cookies.get(TEACHER_COOKIE)
    return bool(token and token in _valid_tokens)


def require_teacher(request: Request):
    """يعيد None إن كان مستوثقاً، وإلّا استجابة تحويل إلى صفحة الدخول."""
    if is_teacher(request):
        return None
    return RedirectResponse(url="/teacher/login", status_code=303)
