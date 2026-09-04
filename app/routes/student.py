"""مسار التلميذ (CLAUDE.md §7، §13، §15).

لا حساب ولا كلمة سرّ. الدخول برمز محلّي (07-K7)، مع بوابة حضور وقفل جهاز.
لا نموذج ذكاء اصطناعي يعمل هنا؛ كل تصحيح يقيني. التلميذ لا يرى نقطة أبداً.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..codes import build_login_code
from ..constants import QUESTION_TYPES_CLOSED
from ..db import get_conn
from ..queries import (
    error_code_labels,
    get_session_full,
    open_sessions,
    question_dict,
    questions_of,
    record_identity_event,
    sanitize_for_student,
    student_by_login,
)
from ..scoring import score_answer
from ..web import templates

router = APIRouter()


# ————————————————————— صفحة الدخول —————————————————————


@router.get("/")
def student_home(request: Request):
    with get_conn() as conn:
        sessions = open_sessions(conn)
    payload = [
        {"id": s["id"], "class_label": s["class_label"], "level": s["level"],
         "title": s["title"], "kind": s["kind"]}
        for s in sessions
    ]
    return templates.TemplateResponse(
        "student/home.html", {"request": request, "sessions": payload}
    )


# ————————————————————— منطق مشترك للتحقّق —————————————————————


class Reject(Exception):
    def __init__(self, reason: str, message: str):
        self.reason = reason
        self.message = message


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _resolve(conn: sqlite3.Connection, request: Request, session_id: int,
             typed: str, device_token: str):
    """يتحقّق من الرمز والحضور وقفل الجهاز. يرمي Reject عند الرفض مع تسجيل الحدث."""
    session = get_session_full(conn, session_id)
    if session is None or session["status"] != "open":
        raise Reject("closed", "الجلسة غير مفتوحة الآن.")

    login_code = build_login_code(session["class_label"], typed)
    if not login_code:
        record_identity_event(conn, session_id, typed, "rejected_unknown",
                              device_token, _client_ip(request))
        raise Reject("unknown", "الرمز غير صالح. تأكّد من كتابته: مثل 07-K7.")

    student = student_by_login(conn, login_code)
    if student is None or student["class_id"] != session["class_id"]:
        record_identity_event(conn, session_id, login_code, "rejected_unknown",
                              device_token, _client_ip(request))
        raise Reject("unknown", "لا يوجد تلميذ بهذا الرمز في هذا القسم.")

    att = conn.execute(
        "SELECT present FROM attendance WHERE session_id = ? AND student_id = ?",
        (session_id, student["id"]),
    ).fetchone()
    if att is None or att["present"] != 1:
        record_identity_event(conn, session_id, login_code, "rejected_absent",
                              device_token, _client_ip(request))
        raise Reject("absent", "رمزك غير مُدرَج ضمن الحاضرين لهذه الجلسة. "
                               "نبّه الأستاذ.")

    attempt = conn.execute(
        "SELECT * FROM attempts WHERE session_id = ? AND student_id = ? "
        "ORDER BY attempt_no DESC LIMIT 1",
        (session_id, student["id"]),
    ).fetchone()

    if attempt is not None:
        if attempt["device_token"] and attempt["device_token"] != device_token:
            record_identity_event(conn, session_id, login_code, "rejected_locked",
                                  device_token, _client_ip(request))
            raise Reject("locked", "رمزك مُستعمَل على جهاز آخر. نبّه الأستاذ ليفكّ القفل.")
        if attempt["status"] == "submitted" and not session["allow_retry"]:
            raise Reject("submitted", "لقد سلّمتَ محاولتك. شكراً.")

    return session, student, attempt


# ————————————————————— كشف الاسم قبل التأكيد —————————————————————


@router.post("/api/student/lookup")
async def lookup(request: Request):
    body = await request.json()
    session_id = int(body.get("session_id") or 0)
    typed = str(body.get("typed") or "")
    device_token = str(body.get("device_token") or "")
    with get_conn() as conn:
        try:
            session, student, attempt = _resolve(conn, request, session_id, typed, device_token)
        except Reject as rj:
            return JSONResponse({"ok": False, "reason": rj.reason, "error": rj.message})
    return {
        "ok": True,
        "full_name": student["full_name"],
        "roster_id": student["roster_id"],
        "resuming": bool(attempt and attempt["status"] == "in_progress"),
    }


# ————————————————————— بدء/استئناف المحاولة —————————————————————


@router.post("/api/student/start")
async def start(request: Request):
    body = await request.json()
    session_id = int(body.get("session_id") or 0)
    typed = str(body.get("typed") or "")
    device_token = str(body.get("device_token") or "")
    if not device_token:
        return JSONResponse({"ok": False, "error": "جهاز غير معرّف."}, status_code=400)

    with get_conn() as conn:
        try:
            session, student, attempt = _resolve(conn, request, session_id, typed, device_token)
        except Reject as rj:
            return JSONResponse({"ok": False, "reason": rj.reason, "error": rj.message})

        if attempt is None or (attempt["status"] == "submitted" and session["allow_retry"]):
            next_no = (attempt["attempt_no"] + 1) if attempt else 1
            cur = conn.execute(
                "INSERT INTO attempts (session_id, student_id, attempt_no, status, "
                "device_token) VALUES (?, ?, ?, 'in_progress', ?)",
                (session_id, student["id"], next_no, device_token),
            )
            attempt_id = cur.lastrowid
            record_identity_event(conn, session_id, student["login_code"], "claimed",
                                  device_token, _client_ip(request))
        else:
            attempt_id = attempt["id"]
            # تثبيت الجهاز إن كان أوّل استعمال فعلي.
            if not attempt["device_token"]:
                conn.execute("UPDATE attempts SET device_token = ? WHERE id = ?",
                             (device_token, attempt_id))

        qrows = questions_of(conn, session["assessment_id"])
        # نصوص الانطلاق المشتركة
        stimuli = {s["id"]: s["text"] for s in
                   json.loads(get_session_stimuli(conn, session["assessment_id"]))}
        questions = []
        for row in qrows:
            q = sanitize_for_student(question_dict(row))
            q["stimulus_text"] = stimuli.get(q["stimulus"]) if q["stimulus"] else None
            questions.append(q)

        saved = {
            r["question_id"]: json.loads(r["raw_json"]) if r["raw_json"] else None
            for r in conn.execute(
                "SELECT question_id, raw_json FROM answers WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchall()
        }

    return {
        "ok": True,
        "attempt_id": attempt_id,
        "full_name": student["full_name"],
        "reveal_mode": session["reveal_mode"],
        "kind": session["kind"],
        "questions": questions,
        "answers": saved,
    }


def get_session_stimuli(conn: sqlite3.Connection, assessment_id: int) -> str:
    row = conn.execute("SELECT stimuli_json FROM assessments WHERE id = ?",
                       (assessment_id,)).fetchone()
    return row["stimuli_json"] if row else "[]"


# ————————————————————— حفظ فوري لكل جواب —————————————————————


@router.post("/api/student/answer")
async def save_answer(request: Request):
    body = await request.json()
    attempt_id = int(body.get("attempt_id") or 0)
    device_token = str(body.get("device_token") or "")
    question_id = int(body.get("question_id") or 0)
    raw = body.get("raw")
    ms_spent = int(body.get("ms_spent") or 0)
    revision = int(body.get("revision_count") or 0)

    with get_conn() as conn:
        attempt = conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
        if attempt is None or attempt["device_token"] != device_token:
            return JSONResponse({"ok": False, "error": "محاولة غير معروفة أو جهاز مختلف."},
                                status_code=403)
        if attempt["status"] != "in_progress":
            return JSONResponse({"ok": False, "error": "المحاولة غير نشطة."}, status_code=409)

        qrow = conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
        if qrow is None:
            return JSONResponse({"ok": False, "error": "سؤال غير معروف."}, status_code=404)
        q = question_dict(qrow)
        is_closed = q["type"] in QUESTION_TYPES_CLOSED

        # تصحيح يقيني داخل الجلسة (مقفلة كاملة، ومفتوحة لِما له check_rule فقط).
        result = score_answer(q["type"], q["payload"], q["indicators"], q["penalties"],
                              q["max_score"], raw)
        # المقفلة يقينية ← تُصادَق آلياً فتدخل التقارير؛ المفتوحة تنتظر الأستاذ (§6).
        confirmed_closed = 1 if is_closed else None

        existing = conn.execute(
            "SELECT id, first_seen_at FROM answers WHERE attempt_id = ? AND question_id = ?",
            (attempt_id, question_id),
        ).fetchone()
        raw_json = json.dumps(raw, ensure_ascii=False)
        verdicts_json = json.dumps(result.get("rule_verdicts"), ensure_ascii=False)
        error_tags = json.dumps(result.get("error_tags") or [], ensure_ascii=False)

        if existing:
            conn.execute(
                "UPDATE answers SET raw_json = ?, auto_score = ?, rule_verdicts_json = ?, "
                "error_tags = ?, teacher_confirmed = COALESCE(?, teacher_confirmed), "
                "answered_at = datetime('now'), ms_spent = ?, revision_count = ? "
                "WHERE id = ?",
                (raw_json, result.get("auto_score"), verdicts_json, error_tags,
                 confirmed_closed, ms_spent, revision, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO answers (attempt_id, question_id, raw_json, auto_score, "
                "rule_verdicts_json, error_tags, teacher_confirmed, first_seen_at, "
                "answered_at, ms_spent, revision_count) VALUES (?, ?, ?, ?, ?, ?, ?, "
                "datetime('now'), datetime('now'), ?, ?)",
                (attempt_id, question_id, raw_json, result.get("auto_score"),
                 verdicts_json, error_tags, 1 if is_closed else 0, ms_spent, revision),
            )
    return {"ok": True}


# ————————————————————— تسليم المحاولة —————————————————————


@router.post("/api/student/submit")
async def submit(request: Request):
    body = await request.json()
    attempt_id = int(body.get("attempt_id") or 0)
    device_token = str(body.get("device_token") or "")
    total_ms = int(body.get("total_ms") or 0)

    with get_conn() as conn:
        attempt = conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
        if attempt is None or attempt["device_token"] != device_token:
            return JSONResponse({"ok": False, "error": "محاولة غير معروفة."}, status_code=403)
        session = get_session_full(conn, attempt["session_id"])

        if attempt["status"] == "in_progress":
            conn.execute(
                "UPDATE attempts SET status = 'submitted', submitted_at = datetime('now'), "
                "total_ms = ? WHERE id = ?",
                (total_ms, attempt_id),
            )

        feedback = []
        if session["reveal_mode"] == "immediate" and session["kind"] != "exam":
            feedback = _build_feedback(conn, attempt_id, session["assessment_id"])

    return {"ok": True, "reveal_mode": session["reveal_mode"], "feedback": feedback}


def _build_feedback(conn: sqlite3.Connection, attempt_id: int, assessment_id: int) -> list[dict]:
    """يبني التغذية الراجعة (§13): صواب/خطأ ومؤشّرات، بلا نقطة ولا كشف للجواب."""
    labels = error_code_labels(conn)
    qrows = questions_of(conn, assessment_id)
    answers = {
        r["question_id"]: r for r in conn.execute(
            "SELECT * FROM answers WHERE attempt_id = ?", (attempt_id,)
        ).fetchall()
    }
    out = []
    for row in qrows:
        q = question_dict(row)
        ans = answers.get(q["id"])
        item = {"position": q["position"], "is_closed": q["type"] in
                ("mcq_single", "mcq_multi", "classify", "order"),
                "answered": ans is not None}
        if ans is None:
            item["verdict"] = "blank"
            out.append(item)
            continue
        verdicts = json.loads(ans["rule_verdicts_json"] or "{}")
        tags = json.loads(ans["error_tags"] or "[]")
        if item["is_closed"]:
            item["verdict"] = "correct" if verdicts.get("is_correct") \
                or (ans["auto_score"] == q["max_score"]) else (
                    "partial" if (ans["auto_score"] or 0) > 0 else "incorrect")
            # وصف الخطأ لا كشف الجواب
            item["error_labels"] = [labels.get(t, t) for t in tags]
        else:
            met, pending = [], 0
            ind_texts = {i["id"]: i["text"] for i in q["indicators"]}
            for iid, v in verdicts.items():
                if iid.startswith("penalty"):
                    continue
                if v.get("met") is True:
                    met.append(ind_texts.get(iid, iid))
                elif v.get("met") is None:
                    pending += 1
            item["verdict"] = "open"
            item["met_indicators"] = met
            item["pending_count"] = pending
        out.append(item)
    return out
