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
from ..signing import sign, unsign

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

# ——— استيثاق الأستاذ والتلميذ عبر كوكيز موقَّعة (ح-١، ح-٩) ———
# لا رموز في الذاكرة ولا معرّف تلميذ خام قابل للتزوير: القيمة تُوقَّع بسرّ الخادم،
# فتصمد عبر إعادة التشغيل ويُرفَض أيّ كوكي مزوَّر.
ADMIN_COOKIE = "pt_admin"
STUDENT_COOKIE = "pt_student"
_ADMIN_MARKER = "admin"


def check_admin_password(password: str) -> bool:
    """مقارنة تجزئة كلمة السرّ (لا نصّاً) مقارنةً ثابتة الزمن (ح-٨)."""
    import hashlib
    given = hashlib.sha256((password or "").encode("utf-8")).hexdigest()
    return secrets.compare_digest(given, settings.teacher_password_hash)


# تأخير تصاعدي على محاولات الدخول الفاشلة (ح-٨): يبطّئ التخمين على الشبكة المحلّية.
_failed_admin_attempts = 0


def next_login_delay() -> float:
    """ثوانٍ انتظار قبل الردّ على محاولة دخول، تتصاعد مع الفشل المتتالي (بحدّ أقصى)."""
    return min(2.0 ** _failed_admin_attempts - 1, 8.0) if _failed_admin_attempts else 0.0


def record_login_result(ok: bool) -> None:
    global _failed_admin_attempts
    _failed_admin_attempts = 0 if ok else _failed_admin_attempts + 1


def issue_admin_token() -> str:
    """قيمة كوكي أستاذ موقَّعة (بلا حالة على الخادم) — تُوضَع في كوكي pt_admin."""
    return sign(_ADMIN_MARKER)


def revoke_admin_token(token: str | None) -> None:
    """لا حالة تُلغى (الكوكي موقَّع بلا خادم)؛ الخروج يُمحى بحذف الكوكي."""
    return None


def is_admin(request: Request) -> bool:
    return unsign(request.cookies.get(ADMIN_COOKIE)) == _ADMIN_MARKER


def require_admin(request: Request):
    """None إن مستوثقاً، وإلّا تحويل إلى صفحة الدخول."""
    return None if is_admin(request) else RedirectResponse("/admin/login", status_code=303)


def issue_student_cookie(student_id: int) -> str:
    """قيمة كوكي تلميذ موقَّعة تحمل معرّفه — تُوضَع في كوكي pt_student."""
    return sign(str(student_id))


def current_student_id(request: Request) -> int | None:
    """معرّف التلميذ من كوكي موقَّع؛ يُرفَض أيّ كوكي غير موقَّع (منع الانتحال)."""
    raw = unsign(request.cookies.get(STUDENT_COOKIE))
    return int(raw) if raw and raw.isdigit() else None


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
