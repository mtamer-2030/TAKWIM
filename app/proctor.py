"""عدّاد مغادرات الشاشة أثناء التقويم (رادعُ غشّ) — في الذاكرة، بلا مخطّط.

يسجّل كم مرّةً غادر التلميذُ شاشةَ التقويم (تبديلُ تطبيق/تبويب، أو خروجٌ من ملء الشاشة)
عبر إشاراتٍ من واجهته. تعرضه شاشةُ المتابعة الآنية للأستاذ لحظيّاً — فيصير رادعاً
حقيقيّاً لا مجرّد تنبيهٍ للتلميذ. مفتاحه (معرّف الجلسة، معرّف التلميذ).

بلا قاعدة بيانات ولا تبعيّات: قاموسٌ محميٌّ بقفل. يُفرَّغ عند إعادة تشغيل الخادم أو
حذف الجلسة (الحصّة انتهت). يكفي لقسمٍ واحد (~٤٥ تلميذاً).
"""

from __future__ import annotations

import threading

# {(session_id, student_id): عدد المغادرات}
_LEAVES: dict[tuple[int, int], int] = {}
_LOCK = threading.Lock()


def record(session_id: int, student_id: int) -> int:
    """يزيد عدّاد مغادرات تلميذٍ في جلسة، ويعيد العدد الجديد."""
    with _LOCK:
        key = (session_id, student_id)
        _LEAVES[key] = _LEAVES.get(key, 0) + 1
        return _LEAVES[key]


def count(session_id: int, student_id: int) -> int:
    """عدد مغادرات تلميذٍ بعينه في جلسة (0 إن لا شيء)."""
    with _LOCK:
        return _LEAVES.get((session_id, student_id), 0)


def for_session(session_id: int) -> dict[int, int]:
    """خريطة {student_id: عدد المغادرات} لكلّ من غادر في هذه الجلسة (>0 فقط)."""
    with _LOCK:
        return {sid: n for (sess, sid), n in _LEAVES.items() if sess == session_id and n}


def reset_session(session_id: int) -> None:
    """يمسح عدّادات جلسةٍ (عند حذفها — انتهت الحصّة)."""
    with _LOCK:
        for k in [k for k in _LEAVES if k[0] == session_id]:
            _LEAVES.pop(k, None)


def reset() -> None:
    """تفريغٌ صريح (للاختبارات)."""
    with _LOCK:
        _LEAVES.clear()
