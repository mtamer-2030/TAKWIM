"""كشف عنوان الحاسوب على الشبكة المحلّية — ليصل التلاميذ عبر الراوتر بلا ضبط يدويّ.

متين عبر مختلف الراوترات (192.168.0.x لـTP-Link، 192.168.1.x، 10.x…) وعلى شبكة
معزولة بلا إنترنت: لا نكتفي ببوّابة واحدة، بل نجرّب عدّة بوّابات شائعة ثمّ نستعلم عن
عناوين الجهاز نفسه، ونختار عنواناً خاصّاً (private) غير محلّي (loopback).
"""

from __future__ import annotations

import ipaddress
import socket

# بوّابات شائعة نستعملها لاختيار الواجهة (UDP connect لا يُرسل حزمة فعلية).
# نُدرج بوّابة TP-Link الافتراضية (192.168.0.1) إلى جانب 192.168.1.1 و10.x.
_PROBES = ("192.168.1.1", "192.168.0.1", "10.0.0.1", "172.16.0.1", "8.8.8.8")


def _is_lan(ip: str) -> bool:
    """هل العنوان IPv4 خاصّ (شبكة محلّية) وصالحٌ لوصول الهواتف؟"""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return (a.version == 4 and a.is_private
            and not a.is_loopback and not a.is_link_local)


def _pick(cands: list[str]) -> str | None:
    """يختار عنواناً من المرشّحين: يفضّل 192.168.* (المألوف للتلاميذ)، وإلّا الأوّل."""
    for ip in cands:
        if ip.startswith("192.168."):
            return ip
    return cands[0] if cands else None


def lan_ip() -> str | None:
    """يعيد عنوان الحاسوب على الشبكة المحلّية، أو None إن تعذّر.

    يعمل مع أيّ راوتر وبلا إنترنت: يجمع مرشّحين من اختيار الواجهة (عبر عدّة بوّابات)
    ومن استعلام اسم الجهاز، ثمّ يفضّل عناوين 192.168.* المألوفة لدى التلاميذ.
    """
    cands: list[str] = []

    for target in _PROBES:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 80))          # يختار الواجهة فقط، بلا إرسال
            ip = s.getsockname()[0]
            if _is_lan(ip) and ip not in cands:
                cands.append(ip)
        except OSError:
            pass
        finally:
            s.close()

    try:                                     # عناوين الجهاز نفسه (يعمل بلا إنترنت)
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _is_lan(ip) and ip not in cands:
                cands.append(ip)
    except OSError:
        pass

    return _pick(cands)                      # نفضّل 192.168.* المألوفة للتلاميذ


def lan_ips() -> list[str]:
    """كلّ عناوين الشبكة المحلّية المكتشَفة (للتشخيص عند تعدّد البطاقات/الواجهات)."""
    ips: list[str] = []
    for target in _PROBES:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 80))
            ip = s.getsockname()[0]
            if _is_lan(ip) and ip not in ips:
                ips.append(ip)
        except OSError:
            pass
        finally:
            s.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _is_lan(ip) and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


def lan_url(port: int) -> str | None:
    ip = lan_ip()
    return f"http://{ip}:{port}" if ip else None
