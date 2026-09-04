"""استيراد اللوائح من CSV (CLAUDE.md §8).

تراكمي: رمز موجود ← تحديث الاسم فقط؛ جديد ← إضافة. لا حذف ولا استبدال.
معاينة قبل التنفيذ، ورفض الملفّ كلّه عند تكرار roster_id مع تحديد السطر.
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

REQUIRED_HEADERS = ("class_label", "roster_id", "full_name")


@dataclass
class RosterRow:
    line: int
    class_label: str
    roster_id: str
    full_name: str
    level: str


@dataclass
class RosterPreview:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    rows: list[RosterRow] = field(default_factory=list)
    to_add: int = 0
    to_update: int = 0
    new_classes: list[str] = field(default_factory=list)


def analyze_csv(text: str, existing_roster_ids: set[str]) -> RosterPreview:
    """يحلّل نصّ CSV ويعيد معاينة. لا يكتب شيئاً.

    `existing_roster_ids`: مجموعة roster_id الموجودة حالياً في القاعدة،
    لتمييز الإضافة من التحديث في المعاينة.
    """
    preview = RosterPreview()

    # تجاوز BOM إن وُجد (ملفّات Excel).
    if text.startswith("﻿"):
        text = text[1:]

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        preview.ok = False
        preview.errors.append("الملفّ فارغ أو بلا ترويسة.")
        return preview

    headers = [h.strip() for h in reader.fieldnames]
    missing = [h for h in REQUIRED_HEADERS if h not in headers]
    if missing:
        preview.ok = False
        preview.errors.append(
            "أعمدة ناقصة في الترويسة: " + "، ".join(missing)
            + f" (المتوقّع: {', '.join(REQUIRED_HEADERS)})"
        )
        return preview

    seen_in_file: dict[str, int] = {}
    new_classes: set[str] = set()

    for offset, raw in enumerate(reader, start=2):  # السطر 1 ترويسة
        class_label = (raw.get("class_label") or "").strip().upper()
        roster_id = (raw.get("roster_id") or "").strip().upper()
        full_name = (raw.get("full_name") or "").strip()

        if not class_label and not roster_id and not full_name:
            continue  # سطر فارغ

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

        parsed = parse_roster_id(roster_id)
        if parsed is None:
            preview.ok = False
            preview.errors.append(
                f"السطر {offset}: roster_id غير صالح «{roster_id}»."
            )
            continue

        label_from_roster, _num = parsed
        if label_from_roster != class_label:
            preview.ok = False
            preview.errors.append(
                f"السطر {offset}: roster_id «{roster_id}» لا يتبع القسم "
                f"«{class_label}»."
            )
            continue

        if roster_id in seen_in_file:
            preview.ok = False
            preview.errors.append(
                f"السطر {offset}: roster_id «{roster_id}» مكرّر "
                f"(ظهر أول مرة في السطر {seen_in_file[roster_id]}). "
                "يُرفض الملفّ كلّه."
            )
            continue
        seen_in_file[roster_id] = offset

        row = RosterRow(offset, class_label, roster_id, full_name, level)
        preview.rows.append(row)
        new_classes.add(class_label)

        if roster_id in existing_roster_ids:
            preview.to_update += 1
        else:
            preview.to_add += 1

    preview.new_classes = sorted(new_classes)
    return preview


def apply_import(conn: sqlite3.Connection, rows: list[RosterRow]) -> dict:
    """يطبّق الاستيراد التراكمي داخل معاملة. لا حذف.

    - قسم جديد ← يُنشأ.
    - roster_id موجود ← تحديث الاسم فقط، وإبقاء login_code كما هو.
    - roster_id جديد ← إضافة مع توليد login_code فريد لا يتغيّر بعدها.
    """
    added = updated = 0
    class_ids: dict[str, int] = {}

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
                        "VALUES (1, ?, ?, 1)",
                        (row.class_label, row.level),
                    )
                    cid = cur.lastrowid
                class_ids[row.class_label] = cid

            # التلميذ
            student = conn.execute(
                "SELECT id FROM students WHERE roster_id = ?", (row.roster_id,)
            ).fetchone()
            if student:
                conn.execute(
                    "UPDATE students SET full_name = ? WHERE id = ?",
                    (row.full_name, student["id"]),
                )
                updated += 1
            else:
                code = make_login_code(
                    row.roster_id, lambda c: login_code_exists(conn, c)
                )
                conn.execute(
                    "INSERT INTO students "
                    "(class_id, roster_id, login_code, full_name, active) "
                    "VALUES (?, ?, ?, ?, 1)",
                    (cid, row.roster_id, code, row.full_name),
                )
                added += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return {"added": added, "updated": updated}
