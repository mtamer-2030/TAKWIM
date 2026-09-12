"""PHILO-TECH v2 — نقطة تشغيل FastAPI الأساسية والوحيدة.

بعد توحيد النظامين وترحيل بيانات v1 (عبر ``scripts/migrate_v1_to_v2.py``)، صار
هذا هو التطبيق الوحيد للتشغيل: لوحة الأستاذ على ``/admin`` وواجهة التلميذ على
``/student``، مع قاعدة v2 (``data/philotech.db``) والذكاء الاصطناعي المحلّي (Ollama).

الأداء: نستعمل حلقة الأحداث الأسرع المتاحة. uvloop لا يعمل على ويندوز، لذا
نضبط uvicorn على loop="auto" (يختار الأفضل)؛ وعلى ويندوز يمكن تثبيت winloop
للحصول على أداء مماثل. لا يُفرض uvloop حتى لا ينهار الخادم على ويندوز.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from fastapi.responses import RedirectResponse

from app.database import DB_PATH, engine
from app.db_bootstrap import ensure_head
from app.netinfo import lan_url
from app.settings import settings
from app.v2 import admin as v2_admin
from app.v2 import student as v2_student
from app.v2.web import seed_levels, seed_skills

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = STATIC_DIR / "uploads"


def _print_access_banner() -> None:
    """يطبع العناوين الصحيحة (مكتشَفة تلقائياً) عند الإقلاع — بلا ضبط يدوي."""
    port = settings.port
    lan = lan_url(port)
    line = "═" * 56
    print("\n" + line)
    print("   PHILO-TECH v2 — النظام جاهز")
    print(line)
    print(f"   لوحة الأستاذ (هذا الحاسوب):  http://localhost:{port}/admin")
    if lan:
        print(f"   دخول التلاميذ (الهواتف):     {lan}/student")
        print(f"   رمز QR للطباعة:              http://localhost:{port}/admin/qr")
        print("   (العنوان مكتشَف تلقائياً من شبكتك — لا حاجة لضبط يدوي.)")
    else:
        print("   تعذّر كشف عنوان الشبكة المحلّية — تحقّق من اتصال الشبكة.")
    print(line + "\n")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # ح-٨: يُرفَض الإقلاع بكلمة السرّ الافتراضية (أو الفارغة) — لئلّا تُفتح لوحة
    # الأستاذ على الشبكة بلا حماية. رسالة عربية واضحة تدلّ على الإصلاح.
    if settings.password_is_default:
        msg = (
            "\n" + "═" * 56 +
            "\n   ⛔ تعذّر الإقلاع: كلمة سرّ الأستاذ لم تُضبَط بعد.\n"
            "   افتح config.ini وغيّر في القسم [teacher] السطر:\n"
            "       password = change-me-please\n"
            "   إلى كلمة سرّ خاصّة بك، ثمّ أعد التشغيل.\n"
            "   (إن لم يوجد config.ini فانسخه من config.ini.example.)\n"
            + "═" * 56 + "\n")
        print(msg)
        raise RuntimeError("كلمة سرّ الأستاذ الافتراضية — اضبطها في config.ini قبل التشغيل.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # Alembic مصدرُ الحقيقة الوحيد للمخطّط (البند ٢-ب): يبني قاعدة جديدة من الصفر،
    # ويُهاجر القائمة إلى head، ويتبنّى قاعدة قديمة بُنيت بـ create_all بوسمها ثمّ
    # ترقيتها — بلا انحراف مخطّط ولا create_all. يُشغَّل في خيط لأنّ Alembic متزامن.
    await asyncio.to_thread(ensure_head)
    await seed_levels()          # يبذر المستويات الثلاثة إن غابت
    await seed_skills()          # يبذر المهارات الستّ الموحّدة إن غابت
    _print_access_banner()       # يطبع العناوين الصحيحة تلقائياً في نافذة التشغيل
    yield
    await engine.dispose()


app = FastAPI(title="PHILO-TECH", version="2.0.0", lifespan=lifespan)

# رفع/تخزين الوسائط والملفّات الثابتة
STATIC_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(v2_admin.router)
app.include_router(v2_student.router)


@app.get("/")
async def root():
    return RedirectResponse("/student")


@app.get("/health")
async def health() -> dict:
    return {"app": "PHILO-TECH", "version": "2.0.0", "db": str(DB_PATH), "ok": True}


if __name__ == "__main__":
    import uvicorn
    # loop="auto": uvloop على لينكس/ماك، وasyncio/winloop على ويندوز.
    uvicorn.run("main:app", host="0.0.0.0", port=8000, loop="auto", workers=1)
