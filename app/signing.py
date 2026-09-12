"""توقيع الكوكيز بمكتبة بايثون القياسية (HMAC-SHA256) — بلا تبعية جديدة، بلا إنترنت.

يعالج ح-١ (انتحال هويّة المتعلّم بتزوير الكوكي) وح-٩ (رموز الأستاذ في الذاكرة):
القيمة تُوقَّع بسرّ الخادم، فلا يقبلها النظام إلّا إن طابق توقيعُها السرَّ — ولا
يُحفظ شيء في الذاكرة، فتصمد الجلسة عبر إعادة التشغيل ولا ينمو مجمّع رموز.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from .settings import settings


def _key() -> bytes:
    return settings.secret_key.encode("utf-8")


def sign(value: str) -> str:
    """يعيد «value.signature» حيث التوقيع HMAC-SHA256 للقيمة بسرّ الخادم."""
    mac = hmac.new(_key(), value.encode("utf-8"), hashlib.sha256).digest()
    sig = base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")
    return f"{value}.{sig}"


def unsign(token: str | None) -> str | None:
    """يتحقّق من التوقيع ويعيد القيمة الأصليّة، أو None إن غاب/فسد التوقيع."""
    if not token or "." not in token:
        return None
    value, _, sig = token.rpartition(".")
    expected = sign(value).rpartition(".")[2]
    # مقارنة ثابتة الزمن تمنع تسريب التوقيع عبر توقيت المقارنة.
    return value if hmac.compare_digest(sig, expected) else None
