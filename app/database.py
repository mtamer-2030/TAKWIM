"""PHILO-TECH v2 — محرك قاعدة بيانات لامتزامن (aiosqlite + WAL).

منفصل عن قاعدة v1 (mihakk.db) في ملفّ خاصّ (philotech.db) حتى يبقى النظام
الشغّال سليماً أثناء إعادة الهندسة. لا قاعدة بيانات خارجية (SQLite حصراً).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "philotech.db"

DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False, future=True)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
    """يُفعّل WAL والمفاتيح الأجنبية عند كلّ اتصال — ضروري للتزامن (٤٥–٥٠ هاتفاً)."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=15000")
    cur.close()


AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


async def get_session() -> AsyncSession:
    """تبعية FastAPI: جلسة لكلّ طلب تُغلق حتماً."""
    async with AsyncSessionLocal() as session:
        yield session
