"""PHILO-TECH v2 — نقطة تشغيل FastAPI الأساسية والوحيدة.

بعد توحيد النظامين وترحيل بيانات v1 (عبر ``scripts/migrate_v1_to_v2.py``)، صار
هذا هو التطبيق الوحيد للتشغيل: لوحة الأستاذ على ``/admin`` وواجهة التلميذ على
``/student``، مع قاعدة v2 (``data/philotech.db``) والذكاء الاصطناعي المحلّي (Ollama).

الأداء: نستعمل حلقة الأحداث الأسرع المتاحة. uvloop لا يعمل على ويندوز، لذا
نضبط uvicorn على loop="auto" (يختار الأفضل)؛ وعلى ويندوز يمكن تثبيت winloop
للحصول على أداء مماثل. لا يُفرض uvloop حتى لا ينهار الخادم على ويندوز.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from fastapi.responses import RedirectResponse

from app.database import DB_PATH, engine
from app.models import Base
from app.v2 import admin as v2_admin
from app.v2 import student as v2_student
from app.v2.web import seed_levels

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = STATIC_DIR / "uploads"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # في التطوير فقط: أنشئ الجداول إن لم تُطبَّق هجرات Alembic بعد.
    if os.environ.get("PHILOTECH_AUTOCREATE") == "1":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    await seed_levels()          # يبذر المستويات الثلاثة إن غابت
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
