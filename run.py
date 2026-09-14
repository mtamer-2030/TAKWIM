"""نقطة تشغيل PHILO-TECH v2 — النظام الأساسي والوحيد بعد توحيد v1→v2.

يشغّل تطبيق v2 (``main:app``) على 0.0.0.0 حتى تصل الهواتف عبر الشبكة المحلّية
إلى IP الثابت للحاسوب. loop="auto" يختار حلقة الأحداث الأسرع المتاحة
(uvloop على لينكس/ماك، asyncio/winloop على ويندوز).

ملاحظة: نظام v1 (مِحَكّ · app.main:app) لم يعد نقطة التشغيل. بياناته تُرحَّل مرّة
واحدة عبر ``scripts/migrate_v1_to_v2.py``، وتبقى ملفّاته في المستودع للأرشيف فقط.
"""

from __future__ import annotations

import uvicorn

from app.netinfo import lan_ip, lan_ips
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


def _startup_banner() -> None:
    """يطبع عنوان دخول التلاميذ وتعليمة القاعة الحاسمة قبل إقلاع الخادم.

    التعليمة الأهمّ لضمان قراءة كلّ الهواتف: «وضع الطيران ثمّ تشغيل WiFi» — فبلا
    بيانات جوّال لا يُسقِط الهاتفُ شبكةَ الراوتر المعزولة (بلا إنترنت) ويصل للنظام.
    """
    ip = lan_ip()
    ips = lan_ips()
    port = settings.port
    line = "═" * 60
    print(f"\n{line}")
    print("  PHILO-TECH — جاهز")
    if ip:
        print(f"  📱 عنوان دخول التلاميذ:  http://{ip}:{port}/student")
        print(f"  🖥️  لوحة الأستاذ:         http://{ip}:{port}/admin   (رمز QR: /admin/qr)")
        # إن تعدّدت الشبكات: اعرضها كلَّها ليجرّب الأستاذ العنوان المطابق لشبكة الهواتف
        # (سببُ «يعرض نسخة offline»: الهاتف على شبكةٍ فرعيّة لا تصل لهذا العنوان).
        others = [x for x in ips if x != ip]
        if others:
            print("  ── حاسوبك على أكثر من شبكة؛ إن لم يصل الهاتف جرّب:")
            for x in others:
                print(f"       http://{x}:{port}/student")
            print("     (الصحيح يشارك الهاتفَ أوّلَ ثلاثة أرقام، مثل 192.168.0.__)")
    else:
        print("  ⚠️  تعذّر كشف عنوان الشبكة — تأكّد من وصل الحاسوب بالراوتر.")
    print("  ────────────────────────────────────────────────")
    print("  ✈️  ليقرأ النظامَ كلُّ الهواتف على شبكةٍ بلا إنترنت:")
    print("      على كلّ هاتف: «وضع الطيران» ثمّ شغّل WiFi وتّصل بشبكة الراوتر.")
    print("      (بلا بيانات جوّال لا يُغادر الهاتف شبكة الراوتر.)")
    print(f"{line}\n")


if __name__ == "__main__":
    _enable_fast_loop()
    _startup_banner()
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port,
                loop="auto", workers=1)
