"""عطبٌ مكتشَف قبل العرض: استيراد اللائحة لم يكن يولّد login_code، فرموزٌ قصيرة مثل
TC1-10-JH لا تُقبَل (الدخول الوحيد كان رمز مسار الطويل). هذا يثبت أنّ الاستيراد يمنح
كلَّ تلميذٍ رمزاً قصيراً صالحاً، وأنّ الدخول به ينجح (بمسافات وحالة أحرفٍ متساهلة)."""

import asyncio
import json
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.codes import split_login_code
from app.models import Base, Level, Student
from app.v2.web import ADMIN_COOKIE, issue_admin_token


def _admin_req():
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, headers={},
                           client=SimpleNamespace(host="9.9.9.9"))


def _run(coro):
    return asyncio.run(coro)


def test_roster_import_generates_short_login_codes(monkeypatch):
    from app.v2.routers import rosters as admin_mod
    from app.v2 import student as st_mod

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(st_mod, "AsyncSessionLocal", S)

        async with S() as s:
            lv = Level(name="جذع مشترك", code="TC"); s.add(lv); await s.flush()
            lid = lv.id
            await s.commit()

        rows = json.dumps([
            {"full_name": "محمد أمين", "massar_code": "D100000001"},
            {"full_name": "سلمى العلوي", "massar_code": "D100000002"},
            {"full_name": "بلا مسار"},                      # يُقبَل بلا رمز مسار
        ])
        await admin_mod.import_save_roster(
            _admin_req(), level_id=lid, group_override="TC1", rows_json=rows)

        async with S() as s:
            studs = (await s.execute(select(Student).order_by(Student.id))).scalars().all()
            codes = [st.login_code for st in studs]
        # الدخول بالرمز المولَّد (بمسافاتٍ وحالةٍ صغيرة) يجد التلميذ.
        target = studs[0].login_code
        found = await st_mod._find_by_code("  " + target.lower().replace("-", "- ") + " ")
        # الدخول برمز مسار ما زال يعمل.
        by_massar = await st_mod._find_by_code("d100000001")
        await eng.dispose()
        return codes, target, (found.id if found else None), studs[0].id, \
            (by_massar.id if by_massar else None)

    codes, target, found_id, first_id, massar_id = _run(go())
    assert all(c for c in codes), "كلّ تلميذٍ حصل على رمز دخول"
    for c in codes:
        parsed = split_login_code(c)
        assert parsed is not None, f"رمزٌ غير صالح: {c}"
        assert parsed[0].startswith("TC1-")            # القسم الصحيح
    assert len(set(codes)) == len(codes)               # رموزٌ فريدة
    assert found_id == first_id                        # الدخول بالرمز المولَّد ينجح
    assert massar_id == first_id                       # وبرمز مسار أيضاً


def test_reimport_upgrades_old_students_missing_code(monkeypatch):
    """تلميذٌ قديمٌ بلا رمز (بيانات سابقة) يُمنَح رمزاً عند إعادة الاستيراد — بلا ترحيل."""
    from app.v2.routers import rosters as admin_mod

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            s.add(Student(full_name="قديم", level_id=lv.id, group_name="TC1",
                          massar_code="D999", login_code=None, active=True))
            await s.commit()
            lid = lv.id
        rows = json.dumps([{"full_name": "قديم", "massar_code": "D999"}])
        await admin_mod.import_save_roster(
            _admin_req(), level_id=lid, group_override="TC1", rows_json=rows)
        async with S() as s:
            st = (await s.execute(select(Student).where(Student.massar_code == "D999"))).scalar_one()
            code = st.login_code
        await eng.dispose()
        return code

    code = _run(go())
    assert code and split_login_code(code) is not None
