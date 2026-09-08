"""نقطة تجميع تطبيق FastAPI لنظام PHILO-TECH."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import APP_NAME, APP_VERSION
from .db import init_db
from .routes import student, teacher

BASE_DIR = Path(__file__).resolve().parent.parent

# يُنشأ المخطّط عند تحميل الوحدة، فتوجد الجداول حتماً قبل أوّل طلب.
init_db()

app = FastAPI(title="PHILO-TECH (mihakk)", version=APP_VERSION)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

app.include_router(student.router)
app.include_router(teacher.router)


@app.get("/health")
def health() -> dict:
    return {"app": APP_NAME, "version": APP_VERSION, "ok": True}
