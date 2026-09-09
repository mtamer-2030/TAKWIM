"""نقطة تشغيل PHILO-TECH v2 — النظام الأساسي والوحيد بعد توحيد v1→v2.

يشغّل تطبيق v2 (``main:app``) على 0.0.0.0 حتى تصل الهواتف عبر الشبكة المحلّية
إلى IP الثابت للحاسوب. loop="auto" يختار حلقة الأحداث الأسرع المتاحة
(uvloop على لينكس/ماك، asyncio/winloop على ويندوز).

ملاحظة: نظام v1 (مِحَكّ · app.main:app) لم يعد نقطة التشغيل. بياناته تُرحَّل مرّة
واحدة عبر ``scripts/migrate_v1_to_v2.py``، وتبقى ملفّاته في المستودع للأرشيف فقط.
"""

from __future__ import annotations

import uvicorn

from app.settings import settings


def _enable_fast_loop() -> None:
    """يُفعّل winloop تلقائياً إن كان مثبّتاً (ويندوز) لأداء أعلى — بلا إلزام.

    غيابه ليس خطأ: يبقى asyncio الافتراضي. (uvloop يُستعمل تلقائياً على لينكس/ماك.)
    """
    try:
        import winloop
        winloop.install()
    except Exception:  # noqa: BLE001 — غير مثبّت أو غير مدعوم: تجاهل بلا ضجّة
        pass


if __name__ == "__main__":
    _enable_fast_loop()
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port,
                loop="auto", workers=1)
