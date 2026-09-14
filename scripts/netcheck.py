"""تشخيص شبكة القاعة: يؤكّد أنّ النظام يرى الراوتر ويعطي عنوان دخول التلاميذ.

يشغّله الأستاذ على حاسوبه (لا في السحابة) بعد وصله بالراوتر:

    .\.venv\Scripts\python.exe scripts\netcheck.py

يطبع: عناوين الشبكة المكتشَفة، عنوان دخول التلاميذ، وحالة المنفذ (هل الخادم يستمع؟)،
مع تلميحات لجدار حماية ويندوز إن لزم. لا يتّصل بالإنترنت.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.netinfo import lan_ip, lan_ips  # noqa: E402
from app.settings import settings  # noqa: E402


def _port_listening(host: str, port: int, timeout: float = 0.6) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def main() -> int:
    port = settings.port
    ips = lan_ips()
    primary = lan_ip()

    print("=" * 56)
    print("  تشخيص شبكة القاعة — PHILO-TECH")
    print("=" * 56)
    print(f"اسم الحاسوب        : {socket.gethostname()}")
    print(f"المنفذ (port)      : {port}")
    print(f"العناوين المكتشَفة  : {', '.join(ips) if ips else '— لا شيء —'}")
    print()

    if not primary:
        print("❌ لم يُكتشَف عنوان شبكة محلّية.")
        print("   • تأكّد أنّ الحاسوب موصولٌ بالراوتر (Archer AX55) عبر Wi-Fi أو سلك.")
        print("   • تحقّق من أنّ Wi-Fi مفعّل وأنّك على شبكة الراوتر لا شبكة أخرى.")
        return 1

    url = f"http://{primary}:{port}/student"
    print(f"✅ عنوان دخول التلاميذ (اكتبه أو امسح رمز QR):")
    print(f"     {url}")
    print()

    listening = _port_listening(primary, port)
    if listening:
        print(f"✅ الخادم يستمع على {primary}:{port} — التلاميذ على نفس الراوتر يصلون.")
    else:
        print(f"⚠️  الخادم لا يستمع على {primary}:{port} بعد.")
        print("   • شغّل النظام في نافذة أخرى:  python run.py")
        print("   • ثمّ أعِد تشغيل هذا الفحص.")
        print("   • إن بقي: افتح المنفذ في جدار حماية ويندوز (أمر PowerShell كأدمن):")
        print(f'       New-NetFirewallRule -DisplayName "PHILO-TECH {port}" '
              f'-Direction Inbound -Action Allow -Protocol TCP -LocalPort {port}')

    if len(ips) > 1:
        print()
        print("ℹ️  عُثر على أكثر من عنوان (بطاقات/واجهات متعدّدة).")
        print(f"   يستعمل النظام: {primary}. إن لم يصل التلاميذ، جرّب عنواناً آخر من القائمة")
        print("   وثبّته في config.ini قسم [server] public_url.")

    print()
    print("خطوات القاعة: (١) وصّل هواتف التلاميذ بشبكة نفس الراوتر (SSID)  "
          "(٢) شغّل python run.py  (٣) وزّع رمز QR من /admin/qr")
    return 0 if primary else 1


if __name__ == "__main__":
    raise SystemExit(main())
