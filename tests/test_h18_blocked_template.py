"""ح-١٨: صفحة رفض التلميذ تُصيَّر عبر قالب الشِلّ لا بـ f-string خام."""

from app.v2.student import _blocked


def test_blocked_uses_shell_template():
    html = _blocked("أنت غائب")
    # القالب يمدّد الشِلّ: يحمل هيكل الصفحة وهوية الموقع والرسالة.
    assert "<!DOCTYPE html>" in html
    assert 'dir="rtl"' in html
    assert "PHILO-TECH" in html
    assert "أنت غائب" in html
    assert "/student/home" in html


def test_blocked_is_not_raw_div():
    # لم يعُد div خاماً بلا شِلّ (السلوك القديم قبل ح-١٨).
    html = _blocked("رسالة")
    assert 'font-family:sans-serif;direction:rtl' not in html
