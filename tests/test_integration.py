"""اختبارات تكامل عبر TestClient — تغطّي سيناريوهات §19 الحيّة.

تشمل: قفل الجهاز، رفض الغائب، الفرض بلا تغذية راجعة، رحلة JSON ذهاباً وإياباً،
CSV بترميز utf-8-sig، والتصحيح اليقيني للمقفلة.
"""

import json
import re

import pytest
from fastapi.testclient import TestClient

import app.settings as S
from app.main import app

S.settings.teacher_password = "pw"


def teacher():
    c = TestClient(app)
    c.post("/teacher/login", data={"password": "pw"})
    return c


def db():
    import sqlite3
    conn = sqlite3.connect(S.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def import_roster(c, csv):
    return c.post("/teacher/rosters/apply", data={"raw_text": csv})


def import_assessment(c, data):
    r = c.post("/teacher/assessments/import",
               files={"file": ("a.json", json.dumps(data), "application/json")},
               follow_redirects=False)
    return int(r.headers["location"].rsplit("/", 1)[-1])


def class_id_of(label):
    return db().execute("SELECT id FROM classes WHERE label=?", (label,)).fetchone()[0]


def make_open_session(c, kind="exercise", class_label="TC1"):
    import_roster(c, f"class_label,roster_id,full_name\n{class_label},{class_label}-01,محمد\n"
                     f"{class_label},{class_label}-02,سلمى\n")
    aid = import_assessment(c, {
        "title": "ت", "kind": kind,
        "questions": [{"type": "mcq_single", "competency": "knowledge", "prompt": "س",
                       "max_score": 1, "payload": {"options": ["أ", "ب"], "correct": 0,
                                                   "diagnostics": {"1": "concept_confusion"}}}],
    })
    cid = class_id_of(class_label)
    r = c.post("/teacher/sessions/new", data={"assessment_id": aid, "class_id": cid},
               follow_redirects=False)
    sid = int(r.headers["location"].rsplit("/", 1)[-1])
    r = c.get(f"/teacher/sessions/{sid}")
    ids = [int(x) for x in re.findall(r'name="present" value="(\d+)"', r.text)]
    c.post(f"/teacher/sessions/{sid}/attendance", data={"present": [str(i) for i in ids]})
    c.post(f"/teacher/sessions/{sid}/open")
    return sid, ids


def typed_for(roster_id):
    conn = db()
    code = conn.execute("SELECT login_code FROM students WHERE roster_id=?",
                        (roster_id,)).fetchone()[0]
    return "-".join(code.split("-")[1:])


def test_health():
    c = TestClient(app)
    assert c.get("/health").json()["ok"] is True


def test_device_lock_second_device_rejected():  # §19/3
    c = teacher()
    sid, _ = make_open_session(c, class_label="TC2")
    sc = TestClient(app)
    t = typed_for("TC2-01")
    r = sc.post("/api/student/start", json={"session_id": sid, "typed": t, "device_token": "A"})
    assert r.json()["ok"]
    r2 = sc.post("/api/student/lookup", json={"session_id": sid, "typed": t, "device_token": "B"})
    assert r2.json()["ok"] is False and r2.json()["reason"] == "locked"
    # حدث الرفض مُسجَّل
    ev = db().execute("SELECT event FROM identity_events WHERE session_id=? AND "
                      "event='rejected_locked'", (sid,)).fetchone()
    assert ev is not None


def test_absent_student_rejected():  # §19/4
    c = teacher()
    import_roster(c, "class_label,roster_id,full_name\nTC3,TC3-01,حاضر\nTC3,TC3-02,غائب\n")
    aid = import_assessment(c, {"title": "ت", "kind": "exercise", "questions": [
        {"type": "mcq_single", "competency": "knowledge", "prompt": "س", "max_score": 1,
         "payload": {"options": ["أ", "ب"], "correct": 0}}]})
    cid = class_id_of("TC3")
    r = c.post("/teacher/sessions/new", data={"assessment_id": aid, "class_id": cid},
               follow_redirects=False)
    sid = int(r.headers["location"].rsplit("/", 1)[-1])
    r = c.get(f"/teacher/sessions/{sid}")
    ids = [int(x) for x in re.findall(r'name="present" value="(\d+)"', r.text)]
    # علِّم الأوّل حاضراً فقط
    c.post(f"/teacher/sessions/{sid}/attendance", data={"present": [str(ids[0])]})
    c.post(f"/teacher/sessions/{sid}/open")
    sc = TestClient(app)
    r = sc.post("/api/student/lookup",
                json={"session_id": sid, "typed": typed_for("TC3-02"), "device_token": "X"})
    assert r.json()["ok"] is False and r.json()["reason"] == "absent"


def test_exam_shows_no_feedback():  # §19/8
    c = teacher()
    sid, _ = make_open_session(c, kind="exam", class_label="2BAC1")
    sc = TestClient(app)
    t = typed_for("2BAC1-01")
    js = sc.post("/api/student/start",
                 json={"session_id": sid, "typed": t, "device_token": "D"}).json()
    assert js["reveal_mode"] == "none"
    qid = js["questions"][0]["id"]
    sc.post("/api/student/answer", json={"attempt_id": js["attempt_id"], "device_token": "D",
            "question_id": qid, "raw": {"choice": 0}, "ms_spent": 1, "revision_count": 0})
    r = sc.post("/api/student/submit",
                json={"attempt_id": js["attempt_id"], "device_token": "D", "total_ms": 10})
    fb = r.json()
    assert fb["reveal_mode"] == "none" and fb["feedback"] == []


def test_json_roundtrip():  # §19/13
    c = teacher()
    original = {
        "title": "رحلة", "kind": "exercise", "level": "1BAC", "unit": "u", "concept": "cc",
        "stimuli": [{"id": "s1", "text": "نصّ"}],
        "questions": [
            {"type": "mcq_single", "competency": "conceptualization", "stimulus": "s1",
             "prompt": "س", "max_score": 2,
             "payload": {"options": ["أ", "ب", "ج"], "correct": 2,
                         "diagnostics": {"0": "copy_verbatim"}}},
            {"type": "short_text", "competency": "problematization", "prompt": "ص",
             "max_score": 1, "payload": {"max_chars": 200},
             "indicators": [{"id": "i1", "text": "استفهام", "points": 1,
                             "check_rule": {"type": "any_of", "patterns": ["؟"]}}]},
        ],
    }
    aid = import_assessment(c, original)
    exported = c.get(f"/teacher/assessments/{aid}/export.json").json()
    # نفس عدد الأسئلة والحقول الجوهرية
    assert exported["title"] == original["title"]
    assert len(exported["questions"]) == 2
    assert exported["questions"][0]["payload"]["correct"] == 2
    assert exported["questions"][1]["indicators"][0]["check_rule"]["type"] == "any_of"
    # إعادة الاستيراد تنجح
    aid2 = import_assessment(c, exported)
    assert aid2 != aid


def test_csv_export_bom():  # §19/12
    c = teacher()
    make_open_session(c, class_label="1BAC2")
    r = c.get("/teacher/export/students.csv")
    assert r.content[:3] == b"\xef\xbb\xbf"  # utf-8-sig


def test_import_rejects_bad_json_field():  # §19/7
    c = teacher()
    bad = {"title": "x", "kind": "exercise", "questions": [
        {"type": "mcq_single", "competency": "knowledge", "prompt": "س", "max_score": 1,
         "payload": {"options": ["أ", "ب"], "correct": 9}}]}
    r = c.post("/teacher/assessments/import",
               files={"file": ("a.json", json.dumps(bad), "application/json")})
    assert "correct" in r.text and "السؤال 1" in r.text
