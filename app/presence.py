"""تتبّعٌ خفيفٌ لحضور الهواتف — ليرى الأستاذ أنّ الاتصال ناجحٌ فوراً.

المشكلة التي يحلّها: فشل وصول الهواتف كان «غير مرئيّ» — لا يدري الأستاذ أنجح الربط
أم لا حتى يشتكي تلميذ. هنا نسجّل كلّ زيارةٍ لمسار ‎/student‎ (IP + آخر لحظة)، فتعرض
لوحةُ الأستاذ عدّاداً حيّاً: «📱 هواتف متّصلة الآن: N». إن ظهر الرقم يرتفع، فالشبكة
سليمة؛ إن بقي صفراً رغم محاولة التلاميذ، فالعلّة شبكيّة (لا في الواجهة) — تشخيصٌ فوريّ.

بلا قاعدة بيانات ولا تبعيّات: قاموسٌ في الذاكرة يُنظَّف تلقائياً. يكفي لقسمٍ (~٤٥ هاتفاً).
"""

from __future__ import annotations

import threading
import time

# {ip: آخر لحظة ظهور (epoch)} — محميّ بقفلٍ لأنّ uvicorn قد يخدم عدّة طلباتٍ معاً.
_SEEN: dict[str, float] = {}
_LOCK = threading.Lock()
_WINDOW = 90.0          # يُعدّ الهاتف «متّصلاً» إن ظهر خلال هذه المدّة (ثوانٍ)


def touch(ip: str | None) -> None:
    """يسجّل ظهور هاتفٍ (IP). يُستدعى من middleware عند كلّ زيارةٍ لمسار التلميذ."""
    if not ip:
        return
    now = time.time()
    with _LOCK:
        _SEEN[ip] = now
        # تنظيفٌ كسولٌ: احذف ما تجاوز نافذتين حتى لا ينتفخ القاموس عبر الحصص.
        if len(_SEEN) > 8:
            cutoff = now - 2 * _WINDOW
            for k in [k for k, t in _SEEN.items() if t < cutoff]:
                _SEEN.pop(k, None)


def count(window: float | None = None) -> int:
    """عدد الهواتف المميَّزة التي ظهرت خلال النافذة (افتراضها ٩٠ ثانية)."""
    w = _WINDOW if window is None else window
    cutoff = time.time() - w
    with _LOCK:
        return sum(1 for t in _SEEN.values() if t >= cutoff)


def reset() -> None:
    """تفريغٌ صريح (للاختبارات، أو زرّ «إعادة العدّ» في القاعة)."""
    with _LOCK:
        _SEEN.clear()
