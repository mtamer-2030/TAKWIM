"""كشف عنوان الشبكة المحلّية: متانة عبر مختلف الراوترات (TP-Link 192.168.0.x…)."""

from app.netinfo import _is_lan, _pick


def test_is_lan_accepts_private_rejects_public_and_loopback():
    assert _is_lan("192.168.0.23")      # شبكة TP-Link الافتراضية
    assert _is_lan("192.168.1.50")
    assert _is_lan("10.0.0.5")
    assert _is_lan("172.16.4.4")
    assert not _is_lan("127.0.0.1")     # loopback
    assert not _is_lan("169.254.1.1")   # link-local (بلا DHCP)
    assert not _is_lan("8.8.8.8")       # عامّ
    assert not _is_lan("ليس عنواناً")


def test_pick_prefers_192_168_then_first():
    # يفضّل 192.168.* حتّى لو جاء بعد عناوين أخرى (شبكة القاعة المألوفة).
    assert _pick(["10.0.0.5", "192.168.0.23"]) == "192.168.0.23"
    # بلا 192.168 → أوّل مرشّح.
    assert _pick(["10.0.0.5", "172.16.4.4"]) == "10.0.0.5"
    assert _pick([]) is None
