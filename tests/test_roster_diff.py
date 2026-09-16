"""أداة «تقرير التغييرات»: تقارن نسخةً احتياطيّة (.db) بالحالة الحاليّة فتُظهر مَن نُقِل
(تغيّر فوجه) ومَن أُضيف جديداً — للمراجعة بعد استيراد لائحة."""

import asyncio
import io
import sqlite3
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Level, Student
from app.v2.web import ADMIN_COOKIE, issue_admin_token


class _UploadStub:
    def __init__(self, data: bytes):
        self.filename = "backup.db"
        self._data = data

    async def read(self):
        return self._data


def _admin_req():
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, headers={},
                           client=SimpleNamespace(host="9.9.9.9"), query_params={})


def _backup_db_bytes(rows):
    """يبني ملفّ SQLite بسيطاً فيه جدول students بالحقول المطلوبة (يحاكي نسخةً احتياطيّة)."""
    import os
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE students (full_name TEXT, massar_code TEXT, group_name TEXT)")
    con.executemany("INSERT INTO students VALUES (?,?,?)", rows)
    con.commit(); con.close()
    with open(path, "rb") as f:
        data = f.read()
    os.unlink(path)
    return data


def test_roster_diff_detects_moved_and_added(monkeypatch):
    from app.v2.routers import rosters as admin_mod
    cap = {}

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(
            admin_mod.templates, "TemplateResponse",
            lambda name, ctx, status_code=200: cap.update(name=name, status=status_code, **ctx)
            or SimpleNamespace(status_code=status_code))
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            # الحالة الحاليّة: ثابتٌ في TC1، منقولٌ صار TC3، وجديد
            s.add_all([
                Student(full_name="ثابت", massar_code="D1", group_name="TC1", level_id=lv.id, active=True),
                Student(full_name="منقول", massar_code="D2", group_name="TC3", level_id=lv.id, active=True),
                Student(full_name="جديد", massar_code="D3", group_name="TC3", level_id=lv.id, active=True),
            ])
            await s.commit()
        # النسخة السابقة: ثابت TC1، منقول كان TC1، (جديد غير موجود)
        backup = _backup_db_bytes([("ثابت", "D1", "TC1"), ("منقول", "D2", "TC1")])
        await admin_mod.roster_diff_run(_admin_req(), _UploadStub(backup))
        await eng.dispose()

    asyncio.run(go())
    assert cap["name"] == "admin/roster_diff.html" and cap["done"] is True
    moved = {m["name"]: (m["old"], m["new"]) for m in cap["moved"]}
    added = {a["name"] for a in cap["added"]}
    assert moved == {"منقول": ("TC1", "TC3")}          # نُقِل من TC1 إلى TC3
    assert added == {"جديد"}                            # جديدٌ فقط
    assert "ثابت" not in moved and "ثابت" not in added  # لم يتغيّر


def test_roster_diff_bad_file_reports_error(monkeypatch):
    from app.v2.routers import rosters as admin_mod
    cap = {}

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(
            admin_mod.templates, "TemplateResponse",
            lambda name, ctx, status_code=200: cap.update(name=name, status=status_code, **ctx)
            or SimpleNamespace(status_code=status_code))
        await admin_mod.roster_diff_run(_admin_req(), _UploadStub(b"not a database"))
        await eng.dispose()

    asyncio.run(go())
    assert cap["status"] == 400 and cap.get("error")
