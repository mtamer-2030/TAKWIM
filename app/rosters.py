"""استيراد اللوائح من CSV (CLAUDE.md §8) — مع دعم أرقام مسار الرسمية.

عمود الرمز يقبل صيغتين:
- رقم مسار حقيقي (مثل D161055238): يُخزَّن كهوية ثابتة، ويُولَّد له roster_id
  تسلسلي للعرض والدخول (TC1-01، TC1-02…) فيبقى إدخال الهاتف قصيراً.
- الصيغة القديمة (TC1-07): تُستعمل كما هي roster_id مباشرةً.

تراكمي: تلميذ موجود ← تحديث الاسم فقط؛ جديد ← إضافة. لا حذف ولا استبدال.
معاينة قبل التنفيذ، ورفض الملفّ كلّه عند التكرار مع تحديد السطر.
التحليل دالّة نقيّة قابلة للاختبار؛ التطبيق منفصل ويكتب في القاعدة.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass, field

from .codes import make_login_code, parse_roster_id
from .constants import level_of_class_label
from .db import login_code_exists

# أعمدة قد تحمل رمز التلميذ (يُقبل أوّل موجود منها).
CODE_COLUMNS = ("massar_id", "roster_id", "code", "cne", "cin", "massar")
REQUIRED_BASE = ("class_label", "full_name")


@dataclass
class RosterRow:
    line: int
    class_label: str
    level: str
    full_name: str
    massar_id: str | None = None      # رقم مسار (هوية ثابتة) إن وُجد
    roster_id: str | None = None      # roster_id صريح (النمط القديم) إن وُجد


@dataclass
class RosterPreview:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    rows: list[RosterRow] = field(default_factory=list)
    to_add: int = 0
    to_update: int = 0
    new_classes: list[str] = field(default_factory=list)


def _find_code_column(headers: list[str]) -> str | None:
    for c in CODE_COLUMNS:
        if c in headers:
            return c
    return None


def analyze_csv(text: str, existing_roster_ids: set[str],
                existing_massar_ids: set[str] | None = None) -> RosterPreview:
    """يحلّل نصّ CSV ويعيد معاينة. لا يكتب شيئاً."""
    existing_massar_ids = existing_massar_ids or set()
    preview = RosterPreview()

    if text.startswith("﻿"):
        text = text[1:]

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        preview.ok = False
        preview.errors.append("الملفّ فارغ أو بلا ترويسة.")
        return preview

    headers = [h.strip() for h in reader.fieldnames]
    missing = [h for h in REQUIRED_BASE if h not in headers]
    if missing:
        preview.ok = False
        preview.errors.append("أعمدة ناقصة في الترويسة: " + "، ".join(missing))
        return preview

    code_col = _find_code_column(headers)
    if code_col is None:
        preview.ok = False
        preview.errors.append(
            "لا عمود لرمز التلميذ. المتوقّع أحد: " + "، ".join(CODE_COLUMNS)
            + " (رقم مسار أو roster_id بصيغة TC1-01)."
        )
        return preview

    seen: dict[str, int] = {}
    new_classes: set[str] = set()

    for offset, raw in enumerate(reader, start=2):  # السطر 1 ترويسة
        class_label = (raw.get("class_label") or "").strip().upper()
        code = (raw.get(code_col) or "").strip().upper()
        full_name = (raw.get("full_name") or "").strip()

        # سطر فارغ أو ذيل بلا رمز ولا اسم ← يُتجاهَل بهدوء.
        if not code and not full_name:
            continue

        if not full_name:
            preview.ok = False
            preview.errors.append(f"السطر {offset}: الاسم الكامل مفقود.")
            continue

        level = level_of_class_label(class_label)
        if level is None:
            preview.ok = False
            preview.errors.append(
                f"السطر {offset}: رمز قسم غير معروف «{class_label}» "
                "(المتوقّع بادئة TC أو 1BAC أو 2BAC)."
            )
            continue

        if not code:
            preview.ok = False
            preview.errors.append(f"السطر {offset}: رمز التلميذ مفقود.")
            continue

        # هل الرمز بصيغة roster_id النظاميّة (TC1-07)؟
        parsed = parse_roster_id(code)
        row = RosterRow(offset, class_label, level, full_name)
        if parsed is not None:
            # يبدو roster_id نظامياً ← يجب أن يتبع قسمه، وإلّا فهو خطأ إدخال.
            if parsed[0] != class_label:
                preview.ok = False
                preview.errors.append(
                    f"السطر {offset}: roster_id «{code}» لا يتبع القسم «{class_label}»."
                )
                continue
            row.roster_id = code
            already = code in existing_roster_ids
        else:
            # رقم مسار (هوية ثابتة)، ويُولَّد roster_id تسلسلي تلقائياً لاحقاً.
            row.massar_id = code
            already = code in existing_massar_ids
        key = code

        if key in seen:
            preview.ok = False
            preview.errors.append(
                f"السطر {offset}: الرمز «{code}» مكرّر "
                f"(ظهر أوّل مرّة في السطر {seen[key]}). يُرفض الملفّ كلّه."
            )
            continue
        seen[key] = offset

        preview.rows.append(row)
        new_classes.add(class_label)
        if already:
            preview.to_update += 1
        else:
            preview.to_add += 1

    preview.new_classes = sorted(new_classes)
    return preview


def _next_seq(conn: sqlite3.Connection, class_label: str, cache: dict[str, int]) -> int:
    """أعلى رقم تسلسلي مستعمل في القسم +1 (لتوليد roster_id: TC1-NN)."""
    if class_label not in cache:
        rows = conn.execute(
            "SELECT roster_id FROM students s JOIN classes c ON c.id = s.class_id "
            "WHERE c.label = ?", (class_label,)
        ).fetchall()
        mx = 0
        for r in rows:
            parsed = parse_roster_id(r["roster_id"])
            if parsed and parsed[0] == class_label:
                try:
                    mx = max(mx, int(parsed[1]))
                except ValueError:
                    pass
        cache[class_label] = mx
    cache[class_label] += 1
    return cache[class_label]


def apply_import(conn: sqlite3.Connection, rows: list[RosterRow]) -> dict:
    """يطبّق الاستيراد التراكمي داخل معاملة. لا حذف.

    - قسم جديد ← يُنشأ.
    - رقم مسار موجود ← تحديث الاسم، وإبقاء roster_id وlogin_code.
    - roster_id صريح موجود ← تحديث الاسم فقط.
    - جديد ← إضافة مع توليد roster_id (إن لزم) وlogin_code فريد لا يتغيّر بعدها.
    """
    added = updated = 0
    class_ids: dict[str, int] = {}
    seq_cache: dict[str, int] = {}

    conn.execute("BEGIN")
    try:
        for row in rows:
            # القسم
            cid = class_ids.get(row.class_label)
            if cid is None:
                existing = conn.execute(
                    "SELECT id FROM classes WHERE label = ?", (row.class_label,)
                ).fetchone()
                if existing:
                    cid = existing["id"]
                else:
                    cur = conn.execute(
                        "INSERT INTO classes (teacher_id, label, level, active) "
                        "VALUES (1, ?, ?, 1)", (row.class_label, row.level))
                    cid = cur.lastrowid
                class_ids[row.class_label] = cid

            # إيجاد تلميذ موجود: بمسار أوّلاً، وإلّا بـ roster_id الصريح.
            student = None
            if row.massar_id:
                student = conn.execute(
                    "SELECT id FROM students WHERE massar_id = ?", (row.massar_id,)
                ).fetchone()
            elif row.roster_id:
                student = conn.execute(
                    "SELECT id FROM students WHERE roster_id = ?", (row.roster_id,)
                ).fetchone()

            if student:
                conn.execute("UPDATE students SET full_name = ? WHERE id = ?",
                             (row.full_name, student["id"]))
                updated += 1
                continue

            # تلميذ جديد ← حدّد roster_id (صريح أو مُولَّد)، ثمّ ولّد login_code.
            if row.roster_id:
                roster_id = row.roster_id
            else:
                seq = _next_seq(conn, row.class_label, seq_cache)
                roster_id = f"{row.class_label}-{seq:02d}"
            code = make_login_code(roster_id, lambda c: login_code_exists(conn, c))
            conn.execute(
                "INSERT INTO students (class_id, roster_id, massar_id, login_code, "
                "full_name, active) VALUES (?, ?, ?, ?, ?, 1)",
                (cid, roster_id, row.massar_id, code, row.full_name))
            added += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {"added": added, "updated": updated}
