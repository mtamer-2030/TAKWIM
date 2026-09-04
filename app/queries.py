"""مساعدات الوصول إلى القاعدة — تُبقي الوجهات نظيفة."""

from __future__ import annotations

import json
import sqlite3

from .constants import QUESTION_TYPES_CLOSED


# ————————————————————— أقسام وتلاميذ —————————————————————


def list_classes(conn: sqlite3.Connection, active_only: bool = True) -> list[sqlite3.Row]:
    q = "SELECT * FROM classes"
    if active_only:
        q += " WHERE active = 1"
    q += " ORDER BY level, label"
    return conn.execute(q).fetchall()


def get_class(conn: sqlite3.Connection, class_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()


def get_class_by_label(conn: sqlite3.Connection, label: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM classes WHERE label = ?", (label,)).fetchone()


def students_of_class(conn: sqlite3.Connection, class_id: int,
                      active_only: bool = True) -> list[sqlite3.Row]:
    q = "SELECT * FROM students WHERE class_id = ?"
    if active_only:
        q += " AND active = 1"
    q += " ORDER BY roster_id"
    return conn.execute(q, (class_id,)).fetchall()


def existing_roster_ids(conn: sqlite3.Connection) -> set[str]:
    return {r["roster_id"] for r in conn.execute("SELECT roster_id FROM students")}


def student_by_login(conn: sqlite3.Connection, login_code: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM students WHERE login_code = ?", (login_code,)
    ).fetchone()


# ————————————————————— تقاويم وأسئلة —————————————————————


def list_assessments(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM assessments ORDER BY created_at DESC, id DESC"
    ).fetchall()


def get_assessment(conn: sqlite3.Connection, aid: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM assessments WHERE id = ?", (aid,)).fetchone()


def questions_of(conn: sqlite3.Connection, aid: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM questions WHERE assessment_id = ? ORDER BY position", (aid,)
    ).fetchall()


def question_dict(row: sqlite3.Row) -> dict:
    """يفكّ حقول JSON لسؤال إلى بنية جاهزة للتصحيح والعرض."""
    indicators_blob = json.loads(row["indicators_json"] or "{}")
    return {
        "id": row["id"],
        "position": row["position"],
        "type": row["type"],
        "prompt": row["prompt"],
        "stimulus": row["stimulus"],
        "payload": json.loads(row["payload_json"] or "{}"),
        "indicators": indicators_blob.get("indicators", []),
        "penalties": indicators_blob.get("penalties", []),
        "max_score": row["max_score"],
        "competency": row["competency"],
        "auto_scored": row["auto_scored"],
    }


def sanitize_for_student(q: dict) -> dict:
    """ينزع كل ما يكشف الجواب قبل إرساله إلى الهاتف.

    لا correct، لا diagnostics، لا correct_order، لا تصنيف العناصر، لا قواعد فحص.
    """
    payload = dict(q.get("payload") or {})
    qtype = q["type"]
    safe_payload: dict = {}

    if qtype in ("mcq_single", "mcq_multi"):
        safe_payload = {"options": payload.get("options", [])}
    elif qtype == "classify":
        safe_payload = {
            "categories": payload.get("categories", []),
            "items": [{"text": it.get("text", "")} for it in payload.get("items", [])],
        }
    elif qtype == "order":
        # ترتيب أوّلي مبعثر (لا نكشف correct_order)؛ الترتيب المعروض هو ترتيب المصفوفة.
        safe_payload = {"items": payload.get("items", [])}
    elif qtype in ("short_text", "long_text"):
        safe_payload = {k: payload[k] for k in ("max_chars", "scaffold") if k in payload}
    elif qtype == "grid":
        safe_payload = {k: payload[k] for k in ("columns", "rows", "max_chars_per_cell")
                        if k in payload}

    # عناصر الجواب تظهر للتلميذ بنصّها فقط (بلا قواعد الفحص ولا النقاط الحاسمة).
    safe_indicators = [{"id": i.get("id"), "text": i.get("text", "")}
                       for i in q.get("indicators", [])]

    return {
        "id": q["id"],
        "position": q["position"],
        "type": qtype,
        "prompt": q["prompt"],
        "stimulus": q["stimulus"],
        "payload": safe_payload,
        "indicators": safe_indicators,
        "is_closed": qtype in QUESTION_TYPES_CLOSED,
    }


# ————————————————————— جلسات —————————————————————


def open_sessions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT s.*, c.label AS class_label, c.level AS level, a.title AS title, "
        "a.kind AS kind FROM sessions s "
        "JOIN classes c ON c.id = s.class_id "
        "JOIN assessments a ON a.id = s.assessment_id "
        "WHERE s.status = 'open' ORDER BY c.label"
    ).fetchall()


def get_session_full(conn: sqlite3.Connection, sid: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT s.*, c.label AS class_label, c.level AS level, "
        "a.title AS title, a.kind AS kind, a.id AS assessment_id "
        "FROM sessions s JOIN classes c ON c.id = s.class_id "
        "JOIN assessments a ON a.id = s.assessment_id WHERE s.id = ?", (sid,)
    ).fetchone()


def list_sessions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT s.*, c.label AS class_label, a.title AS title, a.kind AS kind "
        "FROM sessions s JOIN classes c ON c.id = s.class_id "
        "JOIN assessments a ON a.id = s.assessment_id "
        "ORDER BY s.id DESC"
    ).fetchall()


def record_identity_event(conn: sqlite3.Connection, session_id: int | None,
                          login_code: str | None, event: str,
                          device_token: str | None = None, ip: str | None = None) -> None:
    conn.execute(
        "INSERT INTO identity_events (session_id, login_code, event, device_token, ip) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, login_code, event, device_token, ip),
    )


def error_code_labels(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["code"]: r["label"] for r in conn.execute("SELECT code, label FROM error_codes")}
