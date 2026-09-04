"""لوحة الأستاذ (CLAUDE.md §16) — تجمع مِحَكّ كلّه.

كل ما هنا خلف كلمة سرّ من config.ini. لا نموذج يعمل هنا أثناء أي جلسة؛
المساعدة المسائية وحدها تنادي مزوّداً، وعلى جلسة مغلقة فقط.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)

from ..assessments import (
    bank_to_assessment,
    clone_assessment,
    export_assessment,
    insert_assessment,
    question_to_bank,
)
from ..constants import COMPETENCIES, KINDS
from ..db import get_conn
from ..docx_import import parse_docx
from ..docx_template import build_template_docx
from ..evening import OfflineError, agreement_stats, run_evening
from ..netinfo import lan_url
from ..qrcodes import qr_png
from ..queries import (
    error_code_labels,
    get_assessment,
    get_class,
    get_session_full,
    list_assessments,
    list_classes,
    list_sessions,
    question_dict,
    questions_of,
    students_of_class,
)
from ..queries import existing_massar_ids as q_existing_massar_ids
from ..queries import existing_roster_ids as q_existing_roster_ids
from ..reports import (
    class_common_errors,
    class_competency_summary,
    class_hardest_questions,
    level_comparison,
    student_competency_summary,
    student_curve,
    svg_hbar,
    svg_line,
)
from ..rosters import analyze_csv, apply_import
from ..settings import DB_PATH, BACKUP_DIR, settings
from ..validation import validate_assessment
from ..web import (
    TEACHER_COOKIE,
    check_teacher_password,
    issue_teacher_token,
    require_teacher,
    revoke_teacher_token,
    templates,
)

router = APIRouter(prefix="/teacher")


def _ctx(request: Request, **extra) -> dict:
    base = {"request": request}
    base.update(extra)
    return base


# ═══════════════════════════ الاستيثاق ═══════════════════════════


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("teacher/login.html", _ctx(request))


@router.post("/login")
def login(request: Request, password: str = Form(...)):
    if not check_teacher_password(password):
        return templates.TemplateResponse(
            "teacher/login.html", _ctx(request, error="كلمة السرّ غير صحيحة."),
            status_code=401,
        )
    token = issue_teacher_token()
    resp = RedirectResponse(url="/teacher", status_code=303)
    resp.set_cookie(TEACHER_COOKIE, token, httponly=True, samesite="lax")
    return resp


@router.get("/logout")
def logout(request: Request):
    revoke_teacher_token(request.cookies.get(TEACHER_COOKIE))
    resp = RedirectResponse(url="/teacher/login", status_code=303)
    resp.delete_cookie(TEACHER_COOKIE)
    return resp


# ═══════════════════════════ اللوحة ═══════════════════════════


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        classes = list_classes(conn)
        assessments = list_assessments(conn)
        sessions = list_sessions(conn)
        counts = {c["id"]: conn.execute(
            "SELECT COUNT(*) n FROM students WHERE class_id = ? AND active = 1",
            (c["id"],)).fetchone()["n"] for c in classes}
    return templates.TemplateResponse(
        "teacher/dashboard.html",
        _ctx(request, classes=classes, assessments=assessments, sessions=sessions,
             counts=counts),
    )


# ═══════════════════════════ رمز QR ═══════════════════════════


@router.get("/qr", response_class=HTMLResponse)
def qr_page(request: Request):
    guard = require_teacher(request)
    if guard:
        return guard
    detected = lan_url(settings.port)
    matches = (detected == settings.public_url)
    return templates.TemplateResponse(
        "teacher/qr.html",
        _ctx(request, detected_url=detected, matches=matches))


@router.get("/qr.png")
def qr_image(request: Request):
    guard = require_teacher(request)
    if guard:
        return guard
    return Response(content=qr_png(settings.public_url), media_type="image/png")


@router.get("/qr-detected.png")
def qr_image_detected(request: Request):
    guard = require_teacher(request)
    if guard:
        return guard
    url = lan_url(settings.port)
    if not url:
        return Response(status_code=404)
    return Response(content=qr_png(url), media_type="image/png")


# ═══════════════════════════ اللوائح (م١) ═══════════════════════════


@router.get("/rosters", response_class=HTMLResponse)
def rosters_page(request: Request):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        classes = list_classes(conn, active_only=False)
        by_class = {c["id"]: students_of_class(conn, c["id"], active_only=False)
                    for c in classes}
    return templates.TemplateResponse(
        "teacher/rosters.html", _ctx(request, classes=classes, by_class=by_class,
                                     preview=None, result=None))


@router.post("/rosters", response_class=HTMLResponse)
async def rosters_upload(request: Request, file: UploadFile = File(...)):
    guard = require_teacher(request)
    if guard:
        return guard
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    with get_conn() as conn:
        preview = analyze_csv(raw, q_existing_roster_ids(conn),
                              q_existing_massar_ids(conn))
        classes = list_classes(conn, active_only=False)
        by_class = {c["id"]: students_of_class(conn, c["id"], active_only=False)
                    for c in classes}
    return templates.TemplateResponse(
        "teacher/rosters.html",
        _ctx(request, classes=classes, by_class=by_class, preview=preview,
             raw_text=raw, result=None))


@router.post("/rosters/apply", response_class=HTMLResponse)
def rosters_apply(request: Request, raw_text: str = Form(...)):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        preview = analyze_csv(raw_text, q_existing_roster_ids(conn),
                              q_existing_massar_ids(conn))
        result = None
        if preview.ok:
            result = apply_import(conn, preview.rows)
        classes = list_classes(conn, active_only=False)
        by_class = {c["id"]: students_of_class(conn, c["id"], active_only=False)
                    for c in classes}
    return templates.TemplateResponse(
        "teacher/rosters.html",
        _ctx(request, classes=classes, by_class=by_class,
             preview=None if result else preview, result=result))


@router.post("/student/{student_id}/toggle")
def toggle_student(request: Request, student_id: int):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        # المغادر ← active = 0، بلا حذف (يحتفظ بملفّه التطوّري).
        conn.execute("UPDATE students SET active = 1 - active WHERE id = ?", (student_id,))
    return RedirectResponse(url="/teacher/rosters", status_code=303)


@router.get("/class/{class_id}/cards", response_class=HTMLResponse)
def class_cards(request: Request, class_id: int):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        cls = get_class(conn, class_id)
        students = students_of_class(conn, class_id, active_only=True)
    if cls is None:
        return HTMLResponse("قسم غير موجود", status_code=404)
    return templates.TemplateResponse(
        "teacher/cards.html", _ctx(request, cls=cls, students=students))


# ═══════════════════════════ التقاويم: استيراد/تأليف (م٢، م٥) ═══════════════════════════


@router.get("/assessments", response_class=HTMLResponse)
def assessments_page(request: Request):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        assessments = list_assessments(conn)
        qcounts = {a["id"]: conn.execute(
            "SELECT COUNT(*) n FROM questions WHERE assessment_id = ?", (a["id"],)
        ).fetchone()["n"] for a in assessments}
    return templates.TemplateResponse(
        "teacher/assessments.html",
        _ctx(request, assessments=assessments, qcounts=qcounts))


@router.get("/assessments/import", response_class=HTMLResponse)
def assessment_import_page(request: Request):
    guard = require_teacher(request)
    if guard:
        return guard
    return templates.TemplateResponse(
        "teacher/assessment_import.html", _ctx(request, result=None, errors=None))


@router.post("/assessments/import", response_class=HTMLResponse)
async def assessment_import(request: Request, file: UploadFile = File(...)):
    guard = require_teacher(request)
    if guard:
        return guard
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return templates.TemplateResponse(
            "teacher/assessment_import.html",
            _ctx(request, errors=[f"JSON غير صالح: {exc}"], result=None))
    r = validate_assessment(data)
    if not r.ok:
        return templates.TemplateResponse(
            "teacher/assessment_import.html", _ctx(request, errors=r.errors, result=None))
    with get_conn() as conn:
        aid = insert_assessment(conn, r.normalized)
    return RedirectResponse(url=f"/teacher/assessments/{aid}", status_code=303)


@router.get("/assessments/template.docx")
def assessment_template(request: Request):
    guard = require_teacher(request)
    if guard:
        return guard
    return Response(
        content=build_template_docx(),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="mihakk-template.docx"'})


@router.post("/assessments/import-docx", response_class=HTMLResponse)
async def assessment_import_docx(request: Request, file: UploadFile = File(...)):
    """استيراد تمرين من ملفّ Word — تحويل يقيني ثمّ تحقّق صارم، بلا أي ذكاء اصطناعي."""
    guard = require_teacher(request)
    if guard:
        return guard
    raw = await file.read()
    r = parse_docx(raw)
    if not r.ok:
        return templates.TemplateResponse(
            "teacher/assessment_import.html", _ctx(request, errors=r.errors, result=None))
    with get_conn() as conn:
        aid = insert_assessment(conn, r.normalized)
    return RedirectResponse(url=f"/teacher/assessments/{aid}", status_code=303)


@router.post("/assessments/new")
def assessment_new(request: Request, title: str = Form(...), kind: str = Form(...),
                   level: str = Form(""), unit: str = Form(""), concept: str = Form("")):
    guard = require_teacher(request)
    if guard:
        return guard
    if kind not in KINDS:
        return HTMLResponse("نوع غير صالح", status_code=400)
    norm = {"title": title.strip(), "kind": kind, "level": level.strip() or None,
            "unit": unit.strip() or None, "concept": concept.strip() or None,
            "stimuli": [], "questions": []}
    with get_conn() as conn:
        aid = insert_assessment(conn, norm)
    return RedirectResponse(url=f"/teacher/assessments/{aid}", status_code=303)


@router.get("/assessments/{aid}", response_class=HTMLResponse)
def assessment_edit(request: Request, aid: int):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        a = get_assessment(conn, aid)
        if a is None:
            return HTMLResponse("تقويم غير موجود", status_code=404)
        qs = [question_dict(r) for r in questions_of(conn, aid)]
        stimuli = json.loads(a["stimuli_json"] or "[]")
        error_codes = error_code_labels(conn)
    return templates.TemplateResponse(
        "teacher/assessment_edit.html",
        _ctx(request, a=a, questions=qs, stimuli=stimuli, error_codes=error_codes))


@router.post("/assessments/{aid}/question")
async def assessment_add_question(request: Request, aid: int):
    """يستقبل سؤالاً واحداً (من شاشة التأليف) كـ JSON، يتحقّق منه ثم يحفظه/يحدّثه."""
    guard = require_teacher(request)
    if guard:
        return guard
    body = await request.json()
    # نلفّه في مغلّف تقويم صغير لإعادة استعمال نفس التحقّق الصارم.
    with get_conn() as conn:
        a = get_assessment(conn, aid)
        if a is None:
            return JSONResponse({"ok": False, "errors": ["تقويم غير موجود"]}, status_code=404)
        stimuli = json.loads(a["stimuli_json"] or "[]")
        envelope = {"title": a["title"], "kind": a["kind"], "stimuli": stimuli,
                    "questions": [body]}
        r = validate_assessment(envelope)
        if not r.ok:
            return JSONResponse({"ok": False, "errors": r.errors}, status_code=400)
        q = r.normalized["questions"][0]
        indicators_blob = {"indicators": q.get("indicators", []),
                           "penalties": q.get("penalties", [])}
        qid = body.get("id")
        if qid:  # تحديث
            conn.execute(
                "UPDATE questions SET type=?, prompt=?, stimulus=?, payload_json=?, "
                "indicators_json=?, max_score=?, competency=?, auto_scored=? WHERE id=? "
                "AND assessment_id=?",
                (q["type"], q["prompt"], q.get("stimulus"),
                 json.dumps(q.get("payload", {}), ensure_ascii=False),
                 json.dumps(indicators_blob, ensure_ascii=False), q["max_score"],
                 q["competency"], q.get("auto_scored", 0), qid, aid),
            )
        else:  # إضافة في آخر ترتيب
            pos = conn.execute(
                "SELECT COALESCE(MAX(position),0)+1 p FROM questions WHERE assessment_id=?",
                (aid,)).fetchone()["p"]
            conn.execute(
                "INSERT INTO questions (assessment_id, position, type, prompt, stimulus, "
                "payload_json, indicators_json, max_score, competency, auto_scored) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (aid, pos, q["type"], q["prompt"], q.get("stimulus"),
                 json.dumps(q.get("payload", {}), ensure_ascii=False),
                 json.dumps(indicators_blob, ensure_ascii=False), q["max_score"],
                 q["competency"], q.get("auto_scored", 0)),
            )
    return {"ok": True}


@router.post("/assessments/{aid}/stimuli")
async def assessment_set_stimuli(request: Request, aid: int):
    guard = require_teacher(request)
    if guard:
        return guard
    body = await request.json()
    stimuli = body.get("stimuli", [])
    with get_conn() as conn:
        conn.execute("UPDATE assessments SET stimuli_json=? WHERE id=?",
                     (json.dumps(stimuli, ensure_ascii=False), aid))
    return {"ok": True}


@router.post("/questions/{qid}/delete")
def delete_question(request: Request, qid: int, aid: int = Form(...)):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        conn.execute("DELETE FROM questions WHERE id=? AND assessment_id=?", (qid, aid))
    return RedirectResponse(url=f"/teacher/assessments/{aid}", status_code=303)


# ═══════════════════════════ بنك الأسئلة (م٥) ═══════════════════════════


@router.get("/bank", response_class=HTMLResponse)
def bank_page(request: Request):
    guard = require_teacher(request)
    if guard:
        return guard
    f_level = request.query_params.get("level") or ""
    f_comp = request.query_params.get("competency") or ""
    f_type = request.query_params.get("type") or ""
    where, params = ["1=1"], []
    if f_level:
        where.append("level = ?"); params.append(f_level)
    if f_comp:
        where.append("competency = ?"); params.append(f_comp)
    if f_type:
        where.append("type = ?"); params.append(f_type)
    with get_conn() as conn:
        items = conn.execute(
            "SELECT * FROM question_bank WHERE " + " AND ".join(where) +
            " ORDER BY id DESC", params).fetchall()
        assessments = list_assessments(conn)
    return templates.TemplateResponse(
        "teacher/bank.html",
        _ctx(request, items=items, assessments=assessments,
             f_level=f_level, f_comp=f_comp, f_type=f_type))


@router.post("/questions/{qid}/to-bank")
def question_bank_add(request: Request, qid: int, aid: int = Form(...)):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        question_to_bank(conn, qid)
    return RedirectResponse(url=f"/teacher/assessments/{aid}", status_code=303)


@router.post("/bank/{bid}/insert")
def bank_insert(request: Request, bid: int, aid: int = Form(...)):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        bank_to_assessment(conn, bid, aid)
    return RedirectResponse(url=f"/teacher/assessments/{aid}", status_code=303)


@router.post("/assessments/{aid}/clone")
def assessment_clone(request: Request, aid: int):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        new_id = clone_assessment(conn, aid)
    return RedirectResponse(url=f"/teacher/assessments/{new_id}", status_code=303)


@router.get("/assessments/{aid}/export.json")
def assessment_export(request: Request, aid: int):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        try:
            data = export_assessment(conn, aid)
        except KeyError:
            return HTMLResponse("تقويم غير موجود", status_code=404)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    return Response(
        content=payload, media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="assessment-{aid}.json"'})


# ═══════════════════════════ الجلسات + بوابة الحضور (م٣) ═══════════════════════════


@router.post("/sessions/new")
def session_new(request: Request, assessment_id: int = Form(...), class_id: int = Form(...)):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        a = get_assessment(conn, assessment_id)
        if a is None:
            return HTMLResponse("تقويم غير موجود", status_code=404)
        # 'none' إجباري لكل kind='exam' (§4/7، §6).
        reveal = "none" if a["kind"] == "exam" else "immediate"
        cur = conn.execute(
            "INSERT INTO sessions (assessment_id, class_id, status, reveal_mode) "
            "VALUES (?, ?, 'draft', ?)", (assessment_id, class_id, reveal))
        sid = cur.lastrowid
    return RedirectResponse(url=f"/teacher/sessions/{sid}", status_code=303)


@router.get("/sessions/{sid}", response_class=HTMLResponse)
def session_page(request: Request, sid: int):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        s = get_session_full(conn, sid)
        if s is None:
            return HTMLResponse("جلسة غير موجودة", status_code=404)
        students = students_of_class(conn, s["class_id"], active_only=True)
        present = {r["student_id"] for r in conn.execute(
            "SELECT student_id FROM attendance WHERE session_id=? AND present=1", (sid,))}
    return templates.TemplateResponse(
        "teacher/session.html",
        _ctx(request, s=s, students=students, present=present))


@router.post("/sessions/{sid}/attendance")
async def session_attendance(request: Request, sid: int):
    guard = require_teacher(request)
    if guard:
        return guard
    form = await request.form()
    present_ids = {int(v) for v in form.getlist("present")}
    with get_conn() as conn:
        s = get_session_full(conn, sid)
        if s is None:
            return HTMLResponse("جلسة غير موجودة", status_code=404)
        students = students_of_class(conn, s["class_id"], active_only=True)
        conn.execute("BEGIN")
        try:
            for st in students:
                conn.execute(
                    "INSERT INTO attendance (session_id, student_id, present) VALUES (?,?,?) "
                    "ON CONFLICT(session_id, student_id) DO UPDATE SET present=excluded.present",
                    (sid, st["id"], 1 if st["id"] in present_ids else 0))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return RedirectResponse(url=f"/teacher/sessions/{sid}", status_code=303)


@router.post("/sessions/{sid}/open")
def session_open(request: Request, sid: int):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        s = get_session_full(conn, sid)
        if s is None:
            return HTMLResponse("جلسة غير موجودة", status_code=404)
        reveal = "none" if s["kind"] == "exam" else s["reveal_mode"]
        # الحضور بوابة إلزامية: إن لم تُضبط بعد، عُدّ كلّ الحاضرين حاضرين افتراضاً
        # يتطلّب مرور الأستاذ على البوابة أولاً — نمنع الفتح بلا سجلّ حضور.
        has_attendance = conn.execute(
            "SELECT COUNT(*) n FROM attendance WHERE session_id=?", (sid,)).fetchone()["n"]
        if not has_attendance:
            return RedirectResponse(url=f"/teacher/sessions/{sid}?need_attendance=1",
                                    status_code=303)
        conn.execute(
            "UPDATE sessions SET status='open', reveal_mode=?, opened_at=datetime('now') "
            "WHERE id=?", (reveal, sid))
    return RedirectResponse(url=f"/teacher/sessions/{sid}/live", status_code=303)


@router.post("/sessions/{sid}/close")
def session_close(request: Request, sid: int):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET status='closed', closed_at=datetime('now') WHERE id=?",
            (sid,))
    return RedirectResponse(url=f"/teacher/sessions/{sid}", status_code=303)


# ═══════════════════════════ المتابعة المباشرة (م٤) ═══════════════════════════


@router.get("/sessions/{sid}/live", response_class=HTMLResponse)
def live_page(request: Request, sid: int):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        s = get_session_full(conn, sid)
        if s is None:
            return HTMLResponse("جلسة غير موجودة", status_code=404)
    return templates.TemplateResponse("teacher/live.html", _ctx(request, s=s))


@router.get("/sessions/{sid}/live.json")
def live_data(request: Request, sid: int):
    if not require_teacher(request) is None:
        return JSONResponse({"ok": False}, status_code=403)
    with get_conn() as conn:
        s = get_session_full(conn, sid)
        if s is None:
            return JSONResponse({"ok": False}, status_code=404)
        total_q = conn.execute(
            "SELECT COUNT(*) n FROM questions WHERE assessment_id=?",
            (s["assessment_id"],)).fetchone()["n"]
        students = students_of_class(conn, s["class_id"], active_only=True)
        present = {r["student_id"] for r in conn.execute(
            "SELECT student_id FROM attendance WHERE session_id=? AND present=1", (sid,))}
        # استعلامان مجمّعان فقط (بدل استعلامين لكلّ تلميذ) — يخفّ الحمل مع ٤٥ هاتفاً.
        latest_attempt = {}
        for att in conn.execute(
            "SELECT * FROM attempts WHERE session_id=? ORDER BY student_id, attempt_no",
            (sid,)):
            latest_attempt[att["student_id"]] = att  # الأحدث يبقى (ترتيب تصاعدي)
        answered_by = {r["aid"]: r["n"] for r in conn.execute(
            "SELECT an.attempt_id AS aid, COUNT(*) AS n FROM answers an "
            "JOIN attempts at ON at.id = an.attempt_id WHERE at.session_id=? "
            "GROUP BY an.attempt_id", (sid,))}
        grid = []
        submitted = 0
        for st in students:
            att = latest_attempt.get(st["id"])
            if att is None:
                status = "present" if st["id"] in present else "absent"
                answered = 0
                locked = False
            else:
                answered = answered_by.get(att["id"], 0)
                status = att["status"]
                locked = bool(att["device_token"])
                if att["status"] == "submitted":
                    submitted += 1
            grid.append({"student_id": st["id"], "roster_id": st["roster_id"],
                         "name": st["full_name"], "status": status,
                         "answered": answered, "present": st["id"] in present,
                         "locked": locked})
        # إشعارات الرفض منذ فتح الجلسة (حمراء)
        rejections = [dict(r) for r in conn.execute(
            "SELECT event, login_code, at FROM identity_events WHERE session_id=? "
            "AND event LIKE 'rejected_%' ORDER BY at DESC LIMIT 20", (sid,)).fetchall()]
    return {"ok": True, "status": s["status"], "total_q": total_q,
            "submitted": submitted, "grid": grid, "rejections": rejections}


@router.post("/sessions/{sid}/unlock")
async def session_unlock(request: Request, sid: int):
    guard = require_teacher(request)
    if guard:
        return guard
    body = await request.json()
    student_id = int(body.get("student_id") or 0)
    with get_conn() as conn:
        att = conn.execute(
            "SELECT * FROM attempts WHERE session_id=? AND student_id=? "
            "ORDER BY attempt_no DESC LIMIT 1", (sid, student_id)).fetchone()
        student = conn.execute("SELECT login_code FROM students WHERE id=?",
                               (student_id,)).fetchone()
        if att:
            conn.execute("UPDATE attempts SET device_token=NULL WHERE id=?", (att["id"],))
        conn.execute(
            "INSERT INTO identity_events (session_id, login_code, event) VALUES (?,?, "
            "'teacher_unlocked')", (sid, student["login_code"] if student else None))
    return {"ok": True}


# ═══════════════════════════ التصحيح اليدوي (م٦) ═══════════════════════════


@router.get("/sessions/{sid}/grade", response_class=HTMLResponse)
def grade_index(request: Request, sid: int):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        s = get_session_full(conn, sid)
        if s is None:
            return HTMLResponse("جلسة غير موجودة", status_code=404)
        qs = [question_dict(r) for r in questions_of(conn, s["assessment_id"])]
    return templates.TemplateResponse("teacher/grade_index.html", _ctx(request, s=s, questions=qs))


@router.get("/sessions/{sid}/grade/{qid}", response_class=HTMLResponse)
def grade_question(request: Request, sid: int, qid: int):
    """سؤال واحد وأجوبة كلّ تلاميذ القسم عليه مصفوفة تحته (§16)."""
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        s = get_session_full(conn, sid)
        qrow = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        if s is None or qrow is None:
            return HTMLResponse("غير موجود", status_code=404)
        q = question_dict(qrow)
        rows = conn.execute(
            "SELECT an.*, st.roster_id AS roster_id, st.full_name AS full_name "
            "FROM answers an JOIN attempts at ON at.id=an.attempt_id "
            "JOIN students st ON st.id=at.student_id "
            "WHERE at.session_id=? AND an.question_id=? ORDER BY st.roster_id", (sid, qid))
        answers = []
        for r in rows.fetchall():
            answers.append({
                "id": r["id"], "roster_id": r["roster_id"], "full_name": r["full_name"],
                "raw": json.loads(r["raw_json"]) if r["raw_json"] else None,
                "auto_score": r["auto_score"],
                "rule_verdicts": json.loads(r["rule_verdicts_json"] or "{}"),
                "ai_score": r["ai_score"],
                "ai_verdicts": json.loads(r["ai_verdicts_json"] or "{}") if r["ai_verdicts_json"] else {},
                "manual_score": r["manual_score"], "teacher_note": r["teacher_note"],
                "teacher_confirmed": r["teacher_confirmed"],
            })
        error_codes = error_code_labels(conn)
    return templates.TemplateResponse(
        "teacher/grade_question.html",
        _ctx(request, s=s, q=q, answers=answers, error_codes=error_codes))


@router.post("/answers/{answer_id}/grade")
async def grade_save(request: Request, answer_id: int):
    guard = require_teacher(request)
    if guard:
        return guard
    body = await request.json()
    manual_score = body.get("manual_score")
    note = body.get("teacher_note")
    confirmed = 1 if body.get("teacher_confirmed") else 0
    error_tags = body.get("error_tags")
    with get_conn() as conn:
        conn.execute(
            "UPDATE answers SET manual_score=?, teacher_note=?, teacher_confirmed=?, "
            "error_tags=COALESCE(?, error_tags) WHERE id=?",
            (manual_score, note, confirmed,
             json.dumps(error_tags, ensure_ascii=False) if error_tags is not None else None,
             answer_id))
    return {"ok": True}


# ═══════════════════════════ رموز الأخطاء (§11) ═══════════════════════════


@router.get("/error-codes", response_class=HTMLResponse)
def error_codes_page(request: Request):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        codes = conn.execute("SELECT * FROM error_codes ORDER BY code").fetchall()
    return templates.TemplateResponse("teacher/error_codes.html", _ctx(request, codes=codes))


@router.post("/error-codes")
def error_codes_add(request: Request, code: str = Form(...), label: str = Form(...)):
    guard = require_teacher(request)
    if guard:
        return guard
    code = code.strip()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO error_codes (code, label) VALUES (?,?) "
            "ON CONFLICT(code) DO UPDATE SET label=excluded.label", (code, label.strip()))
    return RedirectResponse(url="/teacher/error-codes", status_code=303)


# ═══════════════════════════ المساعدة المسائية (م٧) ═══════════════════════════


@router.get("/sessions/{sid}/evening", response_class=HTMLResponse)
def evening_page(request: Request, sid: int):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        s = get_session_full(conn, sid)
        if s is None:
            return HTMLResponse("جلسة غير موجودة", status_code=404)
        agreement = agreement_stats(conn, sid)
    return templates.TemplateResponse(
        "teacher/evening.html",
        _ctx(request, s=s, usable=settings.evening.usable, agreement=agreement))


@router.post("/sessions/{sid}/evening/run")
def evening_run(request: Request, sid: int):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        try:
            result = run_evening(conn, settings.evening, sid,
                                 limit=settings.evening.batch_size)
        except OfflineError as exc:
            return JSONResponse({"ok": False, "offline": True, "message": str(exc)})
        except RuntimeError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return {"ok": True, **result}


# ═══════════════════════════ التقارير (م٨) ═══════════════════════════


@router.get("/reports", response_class=HTMLResponse)
def reports_hub(request: Request):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        classes = list_classes(conn)
    return templates.TemplateResponse("teacher/reports.html", _ctx(request, classes=classes))


@router.get("/reports/class/{class_id}", response_class=HTMLResponse)
def report_class(request: Request, class_id: int):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        cls = get_class(conn, class_id)
        if cls is None:
            return HTMLResponse("قسم غير موجود", status_code=404)
        summary = class_competency_summary(conn, class_id)
        errors = class_common_errors(conn, class_id)
        hardest = class_hardest_questions(conn, class_id)
        students = students_of_class(conn, class_id)
    items = [(COMPETENCIES[c], summary[c]["ratio"]) for c in summary]
    chart = svg_hbar(items)
    return templates.TemplateResponse(
        "teacher/report_class.html",
        _ctx(request, cls=cls, summary=summary, errors=errors, hardest=hardest,
             students=students, chart=chart))


@router.get("/reports/student/{student_id}", response_class=HTMLResponse)
def report_student(request: Request, student_id: int):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        st = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
        if st is None:
            return HTMLResponse("تلميذ غير موجود", status_code=404)
        summary = student_competency_summary(conn, student_id)
        curve = student_curve(conn, student_id)
    items = [(COMPETENCIES[c], summary[c]["ratio"]) for c in summary]
    chart = svg_hbar(items)
    line = svg_line([p["ratio"] for p in curve]) if curve else None
    return templates.TemplateResponse(
        "teacher/report_student.html",
        _ctx(request, st=st, summary=summary, curve=curve, chart=chart, line=line))


@router.get("/reports/level/{level}", response_class=HTMLResponse)
def report_level(request: Request, level: str):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        comparison = level_comparison(conn, level)
    return templates.TemplateResponse(
        "teacher/report_level.html",
        _ctx(request, level=level, comparison=comparison))


# ═══════════════════════════ التصدير (م٨) ═══════════════════════════


def _csv_response(rows: list[list], filename: str) -> Response:
    """CSV بترميز utf-8-sig حتى تفتح العربية سليمة في Excel على ويندوز (§16)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow(row)
    data = buf.getvalue().encode("utf-8-sig")
    return Response(content=data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _student_rows(conn: sqlite3.Connection, where: str, params: tuple) -> list[list]:
    header = ["roster_id", "الاسم", "القسم"] + [COMPETENCIES[c] for c in COMPETENCIES]
    rows = [header]
    students = conn.execute(
        f"SELECT st.*, c.label AS class_label FROM students st "
        f"JOIN classes c ON c.id=st.class_id WHERE {where} ORDER BY st.roster_id", params
    ).fetchall()
    for st in students:
        summary = student_competency_summary(conn, st["id"])
        vals = []
        for c in COMPETENCIES:
            r = summary[c]["ratio"]
            vals.append(f"{r * 100:.0f}%" if r is not None else "")
        rows.append([st["roster_id"], st["full_name"], st["class_label"]] + vals)
    return rows


@router.get("/export/class/{class_id}.csv")
def export_class_csv(request: Request, class_id: int):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        rows = _student_rows(conn, "st.class_id=?", (class_id,))
    return _csv_response(rows, f"class-{class_id}.csv")


@router.get("/export/level/{level}.csv")
def export_level_csv(request: Request, level: str):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        rows = _student_rows(conn, "c.level=?", (level,))
    return _csv_response(rows, f"level-{level}.csv")


@router.get("/export/students.csv")
def export_students_csv(request: Request):
    guard = require_teacher(request)
    if guard:
        return guard
    with get_conn() as conn:
        rows = _student_rows(conn, "1=1", ())
    return _csv_response(rows, "students.csv")


@router.get("/backup")
def backup_db(request: Request):
    guard = require_teacher(request)
    if guard:
        return guard
    # نسخة متّسقة عبر واجهة النسخ الاحتياطي في SQLite (تراعي WAL).
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"mihakk-{stamp}.db"
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(target)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    data = target.read_bytes()
    return Response(
        content=data, media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="mihakk-{stamp}.db"'})
