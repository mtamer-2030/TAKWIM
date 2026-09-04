"""كشف عنوان الحاسوب على الشبكة المحلّية — لتيسير الاختبار قبل ضبط IP ثابت."""

from __future__ import annotations

import socket


def lan_ip() -> str | None:
    """يعيد عنوان IP للواجهة الأساسية (يعمل حتى بلا إنترنت على شبكة معزولة).

    لا يُرسل أي حزمة فعلية؛ connect على UDP يختار الواجهة فقط.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 80))  # بوابة الراوتر النموذجية
        return s.getsockname()[0]
    except OSError:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return None
    finally:
        s.close()


def lan_url(port: int) -> str | None:
    ip = lan_ip()
    return f"http://{ip}:{port}" if ip else None
