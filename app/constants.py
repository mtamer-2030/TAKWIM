"""ثوابت المشروع — الترميز والمجموعات المغلقة.

كل ما هنا بنية لا محتوى: رموز المستويات وأنواع الأسئلة والكفايات ثابتة،
أمّا الأسئلة واللوائح فمتغيّرة وتأتي من الاستيراد أو التأليف (CLAUDE.md §2).
"""

from __future__ import annotations

# ——— المستويات وبادئات الأقسام (CLAUDE.md §3) ———
# رمز المستوى يُشتقّ من بادئة رمز القسم.
LEVELS: dict[str, str] = {
    "TC": "الجذع المشترك",
    "1BAC": "الأولى باكالوريا",
    "2BAC": "الثانية باكالوريا",
}
# مرتّبة من الأطول إلى الأقصر حتى تُطابق "1BAC" قبل احتمال أقصر.
LEVEL_PREFIXES: tuple[str, ...] = ("1BAC", "2BAC", "TC")

# ——— أبجدية لاحقة رمز الدخول (CLAUDE.md §3) ———
# بلا حروف ملتبسة: I O S Z 0 1 5.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRTUVWXY2346789"
# طول اللاحقة: محرفان، فيصير إدخال الهاتف خمسة محارف "07-K7".
CODE_SUFFIX_LEN = 2

# ——— الكيانات المغلقة ———
KINDS = ("diagnostic", "exercise", "exam")

COMPETENCIES: dict[str, str] = {
    "problematization": "الأشكلة",
    "conceptualization": "المفهمة",
    "argumentation": "الحجاج",
    "synthesis": "التركيب",
    "knowledge": "الاستحضار",
}

# ——— المهارات الستّ الموحّدة عبر المستويات الثلاثة (قرار الأستاذ، شتنبر 2026) ———
# مصدر واحد للحقيقة؛ تُبذَر في جدول skills عند الإقلاع (ROADMAP §٢-١، ١-ج).
SKILLS: list[str] = [
    "صياغة الإشكال",
    "البنية المفاهيمية",
    "الأطروحة",
    "البنية الحجاجية",
    "المناقشة",
    "التركيب",
]

# توافق مع الملفّات القديمة: مفاتيح كفايات v1 (competency) ← اسم المهارة الجديد.
# «المناقشة» مهارة جديدة بلا مفتاح v1 مقابل — تُدخَل عبر الاسم مباشرةً.
COMPETENCY_TO_SKILL: dict[str, str] = {
    "problematization": "صياغة الإشكال",
    "conceptualization": "البنية المفاهيمية",
    "argumentation": "البنية الحجاجية",
    "synthesis": "التركيب",
    "knowledge": "الأطروحة",
}

QUESTION_TYPES_CLOSED = ("mcq_single", "mcq_multi", "classify", "order")
QUESTION_TYPES_OPEN = ("short_text", "grid", "long_text")
QUESTION_TYPES = QUESTION_TYPES_CLOSED + QUESTION_TYPES_OPEN

SESSION_STATUS = ("draft", "open", "closed")
REVEAL_MODES = ("immediate", "none")

ATTEMPT_STATUS = ("in_progress", "submitted", "abandoned")

IDENTITY_EVENTS = (
    "claimed",
    "rejected_locked",
    "rejected_absent",
    "rejected_unknown",
    "teacher_unlocked",
)

CHECK_RULE_TYPES = ("any_of", "all_of", "none_of", "min_chars", "max_chars", "regex")


def level_of_class_label(label: str) -> str | None:
    """يشتقّ رمز المستوى من رمز القسم: TC1→TC، 1BAC2→1BAC، 2BAC1→2BAC."""
    label = (label or "").strip().upper()
    for prefix in LEVEL_PREFIXES:
        if label.startswith(prefix) and label[len(prefix):].isdigit():
            return prefix
    return None
