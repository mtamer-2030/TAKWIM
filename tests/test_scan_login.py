"""الحلّ الجذريّ لعقبة الدخول على الهاتف: مسح بطاقة التلميذ = دخولٌ بلا كتابة.

- ‎GET /student/join?code=رمز‎ يعرض شاشة التأكيد (اسم التلميذ) بنقرةٍ واحدة، لا لوحة مفاتيح.
- رمزٌ مجهول → يعود لصفحة الدخول برسالةٍ واضحة (٤٠٤).
- عدّاد الحضور (presence) يعدّ الهواتف المتّصلة فيرى الأستاذ نجاح الربط فوراً.
- بطاقات الأستاذ + رمز QR لكلّ تلميذ يُولَّدان.
"""

import asyncio
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import presence
from app.models import Base, Level, Student
from app.v2.web import ADMIN_COOKIE, issue_admin_token


def _req(**kw):
    return SimpleNamespace(cookies={}, headers={}, client=SimpleNamespace(host="1.2.3.4"), **kw)


def _admin_req(**kw):
    return SimpleNamespace(cookies={ADMIN_COOKIE: issue_admin_token()}, headers={},
                           client=SimpleNamespace(host="9.9.9.9"), **kw)


# ═══════════════ عدّاد الحضور ═══════════════


def test_presence_counts_distinct_phones_in_window():
    presence.reset()
    assert presence.count() == 0
    presence.touch("192.168.0.21")
    presence.touch("192.168.0.22")
    presence.touch("192.168.0.21")          # نفس الهاتف ثانيةً — لا يُضاعَف
    assert presence.count() == 2
    presence.touch(None)                     # لا IP → يُتجاهَل
    assert presence.count() == 2
    assert presence.count(window=0) == 0     # نافذةٌ صفريّة → لا شيء «حديث»
    presence.reset()
    assert presence.count() == 0


# ═══════════════ الدخول بالمسح ═══════════════


def _setup_student(monkeypatch, login_code="TC1-07-K7"):
    from app.v2 import student as st_mod
    cap = {}

    async def prepare():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(st_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(
            st_mod.templates, "TemplateResponse",
            lambda name, ctx, status_code=200: cap.update(name=name, status=status_code, **ctx)
            or SimpleNamespace(status_code=status_code))
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            s.add(Student(full_name="أمين العلوي", level_id=lv.id, group_name="TC1",
                          login_code=login_code, massar_code="D161055238", active=True))
            await s.commit()
        return eng

    return st_mod, cap, prepare


def test_scan_known_code_shows_confirm_no_typing(monkeypatch):
    st_mod, cap, prepare = _setup_student(monkeypatch)

    async def run():
        eng = await prepare()
        # يُقبَل رمز الدخول بأيّ حالةٍ حرفيّة (يُطبَّع إلى الأحرف الكبيرة).
        await st_mod.join(_req(), code="tc1-07-k7")
        await eng.dispose()

    asyncio.run(run())
    assert cap["name"] == "student/confirm.html"          # شاشة التأكيد لا الكتابة
    assert cap["student"].full_name == "أمين العلوي"
    assert cap["code"] == "TC1-07-K7"


def test_scan_by_massar_code_also_works(monkeypatch):
    st_mod, cap, prepare = _setup_student(monkeypatch)

    async def run():
        eng = await prepare()
        await st_mod.join(_req(), code="d161055238")
        await eng.dispose()

    asyncio.run(run())
    assert cap["name"] == "student/confirm.html"
    assert cap["student"].massar_code == "D161055238"


def test_scan_unknown_code_returns_login_error(monkeypatch):
    st_mod, cap, prepare = _setup_student(monkeypatch)

    async def run():
        eng = await prepare()
        await st_mod.join(_req(), code="ZZZ-NOPE")
        await eng.dispose()

    asyncio.run(run())
    assert cap["name"] == "student/login.html"
    assert cap["status"] == 404
    assert "error" in cap and cap["error"]


# ═══════════════ بطاقات الأستاذ + رمز كلّ تلميذ ═══════════════


def test_admin_cards_and_per_student_qr(monkeypatch):
    from app.v2 import admin as admin_mod
    cap = {}

    async def run():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(admin_mod, "AsyncSessionLocal", S)
        monkeypatch.setattr(
            admin_mod.templates, "TemplateResponse",
            lambda name, ctx: cap.update(name=name, **ctx) or SimpleNamespace(status_code=200))
        async with S() as s:
            lv = Level(name="ج", code="TC"); s.add(lv); await s.flush()
            s.add(Student(full_name="سارة", level_id=lv.id, group_name="TC1",
                          login_code="TC1-01-A2", active=True))
            await s.commit()
            sid = (await s.execute(__import__("sqlalchemy").select(Student.id))).scalar_one()
        await admin_mod.student_cards(_admin_req(), group=None)
        png = await admin_mod.student_card_qr(_admin_req(), sid)
        await eng.dispose()
        return sid, png

    sid, png = asyncio.run(run())
    assert cap["name"] == "admin/cards.html"
    assert len(cap["students"]) == 1 and cap["students"][0].full_name == "سارة"
    # الرابط في البطاقة يحمل رمز الدخول ويؤدّي لمسار المسح.
    assert "/student/join?code=" in admin_mod._join_url(cap["students"][0])
    assert "TC1-01-A2" in admin_mod._join_url(cap["students"][0])
    assert png.media_type == "image/png" and png.body[:4] == b"\x89PNG"


def test_qr_page_lists_all_detected_networks(monkeypatch):
    """صفحة QR تعرض كلَّ عناوين الحاسوب المكتشَفة (لا واحداً) — ليختار الأستاذ العنوان
    المطابق لشبكة الهواتف حين يحمل الحاسوب أكثر من بطاقة/شبكة."""
    from app.v2 import admin as admin_mod
    cap = {}
    monkeypatch.setattr(admin_mod, "lan_ips", lambda: ["192.168.0.92", "192.168.11.104"])
    monkeypatch.setattr(admin_mod.settings, "port", 8000)
    monkeypatch.setattr(
        admin_mod.templates, "TemplateResponse",
        lambda name, ctx: cap.update(name=name, **ctx) or SimpleNamespace(status_code=200))

    async def run():
        return await admin_mod.qr_page(_admin_req())

    asyncio.run(run())
    assert cap["name"] == "admin/qr.html"
    assert cap["urls"] == ["http://192.168.0.92:8000", "http://192.168.11.104:8000"]
    # رمز QR لعنوانٍ بعينه يُقبَل فقط إن كان ضمن المكتشَف (لا حقن عناوين).
    good = admin_mod._all_student_urls()
    assert "http://192.168.0.92:8000" in good and "http://192.168.11.104:8000" in good


def test_connected_endpoint_reports_count(monkeypatch):
    from app.v2 import admin as admin_mod
    presence.reset()
    presence.touch("192.168.0.5")

    async def run():
        return await admin_mod.connected_count(_admin_req())

    out = asyncio.run(run())
    assert out == {"count": 1}
    presence.reset()
