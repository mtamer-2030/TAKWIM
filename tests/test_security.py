"""اختبارات الأمن (المرحلة ٣، معيار ق-٨): توقيع الكوكيز وكلمة السرّ.

يشمل خاصّةً: كوكي متعلّم مزوَّر (أو خام كالقديم) يُرفَض → لا هويّة (ومن ثمّ 401).
"""

import hashlib
from types import SimpleNamespace

from app import signing
from app.v2 import web


def _req(**cookies):
    return SimpleNamespace(cookies=cookies)


def test_sign_unsign_roundtrip_and_tamper():
    tok = signing.sign("42")
    assert signing.unsign(tok) == "42"
    # عبثٌ بالقيمة يُبطِل التوقيع.
    value, _, sig = tok.rpartition(".")
    assert signing.unsign(f"99.{sig}") is None
    # عبثٌ بالتوقيع يُبطِله.
    assert signing.unsign(f"{value}.deadbeef") is None
    assert signing.unsign(None) is None
    assert signing.unsign("no-signature") is None


def test_student_cookie_must_be_signed():
    # كوكي موقَّع صحيح → الهويّة تُقبَل.
    good = web.issue_student_cookie(7)
    assert web.current_student_id(_req(pt_student=good)) == 7
    # كوكي خام (كالقديم «7») غير موقَّع → يُرفَض (منع الانتحال، ح-١).
    assert web.current_student_id(_req(pt_student="7")) is None
    # كوكي مزوَّر لتلميذ آخر → يُرفَض.
    forged = "999." + good.rpartition(".")[2]
    assert web.current_student_id(_req(pt_student=forged)) is None
    # لا كوكي → لا هويّة.
    assert web.current_student_id(_req()) is None


def test_admin_cookie_must_be_signed():
    assert web.is_admin(_req(pt_admin=web.issue_admin_token())) is True
    assert web.is_admin(_req(pt_admin="admin")) is False          # خام غير موقَّع
    assert web.is_admin(_req(pt_admin="admin.deadbeef")) is False  # توقيع مزوَّر
    assert web.is_admin(_req()) is False


def test_password_hash_comparison(monkeypatch):
    monkeypatch.setattr(web.settings, "teacher_password_hash",
                        hashlib.sha256("سرّي".encode("utf-8")).hexdigest())
    assert web.check_admin_password("سرّي") is True
    assert web.check_admin_password("خطأ") is False
    assert web.check_admin_password("") is False


def test_login_backoff_grows_then_resets():
    web.record_login_result(True)             # صفّر أوّلاً
    assert web.next_login_delay() == 0.0
    web.record_login_result(False)
    web.record_login_result(False)
    assert web.next_login_delay() > 0         # تصاعد بعد الفشل
    web.record_login_result(True)             # نجاح يصفّر
    assert web.next_login_delay() == 0.0
