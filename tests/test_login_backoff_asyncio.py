"""ح-١٤: asyncio مستورد على رأس admin.py، فمحاولتا دخول فاشلتان متتاليتان
تُرجعان 401 لا 500 (كان asyncio.sleep في login يرفع NameError على المحاولة الثانية).
"""

import asyncio
from types import SimpleNamespace


def test_two_failed_logins_return_401_not_500(monkeypatch):
    from app.v2 import admin as admin_mod
    from app.v2 import web

    # asyncio مستورد فعلاً على رأس الوحدة (جوهر ح-١٤).
    assert hasattr(admin_mod, "asyncio")

    # سرّ معروف حتى تفشل أيّ كلمة خاطئة، وتصفير عدّاد المحاولات.
    monkeypatch.setattr(web.settings, "teacher_password_hash", "deadbeef")
    web.record_login_result(True)
    # عزل تصيير القالب عن منطق الدخول (لسنا نختبر القالب).
    monkeypatch.setattr(admin_mod.templates, "TemplateResponse",
                        lambda *a, **k: SimpleNamespace(status_code=k.get("status_code", 200)))
    # تأخير صغير موجب يمرّ فعلاً على await asyncio.sleep (يثبت أنّ asyncio معرّف).
    monkeypatch.setattr(admin_mod, "next_login_delay", lambda: 0.001)

    async def run():
        req = SimpleNamespace()
        r1 = await admin_mod.login(req, password="wrong")
        r2 = await admin_mod.login(req, password="wrong")   # هنا كان يقع 500 سابقاً
        return r1, r2

    r1, r2 = asyncio.run(run())
    assert r1.status_code == 401
    assert r2.status_code == 401
