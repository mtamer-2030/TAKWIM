"""ترميز التلاميذ: roster_id للعرض، login_code للدخول (CLAUDE.md §3، §7).

roster_id  = TC1-07      ← للعرض والتقارير والتصدير
login_code = TC1-07-K7   ← للدخول فقط، بلاحقة عشوائية

اللاحقة تُخمَّن بكلفة صفر من اللائحة، فتجعل انتحال رمز زميل مستحيلاً.
"""

from __future__ import annotations

import re
import secrets

from .constants import CODE_ALPHABET, CODE_SUFFIX_LEN, level_of_class_label

# roster_id = <رمز القسم>-<رقم>، ورمز القسم = بادئة مستوى + رقم.
_ROSTER_RE = re.compile(r"^(?P<label>(?:TC|1BAC|2BAC)\d+)-(?P<num>\d+)$")
# login_code = roster_id-<لاحقة>
_LOGIN_RE = re.compile(
    r"^(?P<roster>(?:TC|1BAC|2BAC)\d+-\d+)-(?P<suffix>[A-Z0-9]+)$"
)
# الجزء الذي يكتبه التلميذ على الهاتف: <رقم>-<لاحقة> مثل 07-K7
_LOCAL_RE = re.compile(r"^(?P<num>\d+)-(?P<suffix>[A-Za-z0-9]+)$")


def parse_roster_id(roster_id: str) -> tuple[str, str] | None:
    """يفصل roster_id إلى (رمز القسم، الرقم). يعيد None إن كان غير صالح."""
    m = _ROSTER_RE.match((roster_id or "").strip().upper())
    if not m:
        return None
    label = m.group("label")
    if level_of_class_label(label) is None:
        return None
    return label, m.group("num")


def is_valid_roster_id(roster_id: str) -> bool:
    return parse_roster_id(roster_id) is not None


def random_suffix() -> str:
    """لاحقة عشوائية من الأبجدية غير الملتبسة."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_SUFFIX_LEN))


def make_login_code(roster_id: str, exists: callable) -> str:
    """يولّد login_code فريداً لـ roster_id معطى.

    `exists(code) -> bool` تُستشار لضمان عدم التصادم مع رمز موجود.
    """
    roster_id = roster_id.strip().upper()
    for _ in range(1000):
        code = f"{roster_id}-{random_suffix()}"
        if not exists(code):
            return code
    raise RuntimeError("تعذّر توليد رمز دخول فريد — راجع أبجدية الرموز")


def split_login_code(login_code: str) -> tuple[str, str] | None:
    """يفصل login_code إلى (roster_id، اللاحقة)."""
    m = _LOGIN_RE.match((login_code or "").strip().upper())
    if not m:
        return None
    return m.group("roster"), m.group("suffix")


def local_part(login_code: str) -> str | None:
    """الجزء الذي يُدخله التلميذ على الهاتف: من TC1-07-K7 يعيد 07-K7."""
    split = split_login_code(login_code)
    if not split:
        return None
    roster_id, suffix = split
    parsed = parse_roster_id(roster_id)
    if not parsed:
        return None
    _label, num = parsed
    return f"{num}-{suffix}"


def build_login_code(class_label: str, typed: str) -> str | None:
    """يعيد بناء login_code كاملاً من رمز القسم وما كتبه التلميذ (07-K7).

    الجلسة مرتبطة بقسم واحد، فالتلميذ يُدخل الجزء المحلّي فقط (CLAUDE.md §3).
    يتساهل مع المسافات وحالة الأحرف؛ يعيد None إن كان الشكل غير صالح.
    """
    class_label = (class_label or "").strip().upper()
    typed = (typed or "").strip().upper().replace(" ", "")
    if not typed:
        return None
    # يقبل أيضاً أن يلصق التلميذ الرمز الكامل بالخطأ.
    if typed.startswith(class_label + "-"):
        candidate = typed
    else:
        m = _LOCAL_RE.match(typed)
        if not m:
            return None
        candidate = f"{class_label}-{m.group('num')}-{m.group('suffix')}"
    return candidate if split_login_code(candidate) else None
