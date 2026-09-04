"""حفظ التقاويم وتصديرها واستنساخها — رحلة ذهاب وإياب كاملة (CLAUDE.md §2، §9)."""

from __future__ import annotations

import json
import sqlite3


def insert_assessment(conn: sqlite3.Connection, norm: dict) -> int:
    """يحفظ تقويماً مطبّعاً (مخرَج validate_assessment) داخل معاملة، ويعيد معرّفه."""
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            "INSERT INTO assessments (teacher_id, title, kind, level, unit, concept, "
            "stimuli_json) VALUES (1, ?, ?, ?, ?, ?, ?)",
            (norm["title"], norm["kind"], norm.get("level"), norm.get("unit"),
             norm.get("concept"), json.dumps(norm.get("stimuli", []), ensure_ascii=False)),
        )
        aid = cur.lastrowid
        for q in norm["questions"]:
            _insert_question(conn, aid, q)
        conn.execute("COMMIT")
        return aid
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _insert_question(conn: sqlite3.Connection, aid: int, q: dict) -> int:
    indicators_blob = {
        "indicators": q.get("indicators", []),
        "penalties": q.get("penalties", []),
    }
    cur = conn.execute(
        "INSERT INTO questions (assessment_id, position, type, prompt, stimulus, "
        "payload_json, indicators_json, max_score, competency, auto_scored) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (aid, q["position"], q["type"], q["prompt"], q.get("stimulus"),
         json.dumps(q.get("payload", {}), ensure_ascii=False),
         json.dumps(indicators_blob, ensure_ascii=False),
         q["max_score"], q["competency"], q.get("auto_scored", 0)),
    )
    return cur.lastrowid


def export_assessment(conn: sqlite3.Connection, aid: int) -> dict:
    """يعيد بنية JSON مطابقة لصيغة الاستيراد (§9) — للتصدير والنسخ الاحتياطي."""
    a = conn.execute("SELECT * FROM assessments WHERE id = ?", (aid,)).fetchone()
    if a is None:
        raise KeyError(aid)
    qrows = conn.execute(
        "SELECT * FROM questions WHERE assessment_id = ? ORDER BY position", (aid,)
    ).fetchall()
    questions = []
    for row in qrows:
        blob = json.loads(row["indicators_json"] or "{}")
        q = {
            "type": row["type"],
            "competency": row["competency"],
            "prompt": row["prompt"],
            "max_score": row["max_score"],
            "payload": json.loads(row["payload_json"] or "{}"),
        }
        if row["stimulus"]:
            q["stimulus"] = row["stimulus"]
        if blob.get("indicators"):
            q["indicators"] = blob["indicators"]
        if blob.get("penalties"):
            q["penalties"] = blob["penalties"]
        questions.append(q)
    out = {
        "title": a["title"],
        "kind": a["kind"],
        "level": a["level"],
        "unit": a["unit"],
        "concept": a["concept"],
        "stimuli": json.loads(a["stimuli_json"] or "[]"),
        "questions": questions,
    }
    return out


def question_to_bank(conn: sqlite3.Connection, qid: int) -> int:
    """ينسخ سؤالاً إلى بنك الأسئلة، موسوماً بالمستوى/المجزوءة/المفهوم/الكفاية (§16)."""
    q = conn.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone()
    if q is None:
        raise KeyError(qid)
    a = conn.execute("SELECT * FROM assessments WHERE id = ?",
                     (q["assessment_id"],)).fetchone()
    # حلّ نصّ الانطلاق إلى نصّه الحرفيّ حتى يكون السؤال محمولاً بين التقاويم.
    stim_text = None
    if q["stimulus"]:
        for s in json.loads(a["stimuli_json"] or "[]"):
            if s.get("id") == q["stimulus"]:
                stim_text = s.get("text")
                break
    cur = conn.execute(
        "INSERT INTO question_bank (teacher_id, level, unit, concept, competency, type, "
        "prompt, stimulus_text, payload_json, indicators_json, max_score) "
        "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (a["level"], a["unit"], a["concept"], q["competency"], q["type"], q["prompt"],
         stim_text, q["payload_json"], q["indicators_json"], q["max_score"]),
    )
    return cur.lastrowid


def bank_to_assessment(conn: sqlite3.Connection, bid: int, aid: int) -> int:
    """يُدرج سؤالاً من البنك في تقويم، مُنشئاً نصّ انطلاق جديداً إن لزم."""
    b = conn.execute("SELECT * FROM question_bank WHERE id = ?", (bid,)).fetchone()
    a = conn.execute("SELECT * FROM assessments WHERE id = ?", (aid,)).fetchone()
    if b is None or a is None:
        raise KeyError((bid, aid))
    conn.execute("BEGIN")
    try:
        stimulus_id = None
        if b["stimulus_text"]:
            stimuli = json.loads(a["stimuli_json"] or "[]")
            stimulus_id = f"s{len(stimuli) + 1}"
            stimuli.append({"id": stimulus_id, "text": b["stimulus_text"]})
            conn.execute("UPDATE assessments SET stimuli_json = ? WHERE id = ?",
                         (json.dumps(stimuli, ensure_ascii=False), aid))
        pos = conn.execute(
            "SELECT COALESCE(MAX(position),0)+1 p FROM questions WHERE assessment_id=?",
            (aid,)).fetchone()["p"]
        blob = json.loads(b["indicators_json"] or "{}")
        auto = 1 if b["type"] in ("mcq_single", "mcq_multi", "classify", "order") else (
            1 if blob.get("indicators") and all(i.get("check_rule") for i in blob["indicators"])
            else 0)
        cur = conn.execute(
            "INSERT INTO questions (assessment_id, position, type, prompt, stimulus, "
            "payload_json, indicators_json, max_score, competency, auto_scored) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, pos, b["type"], b["prompt"], stimulus_id, b["payload_json"],
             b["indicators_json"], b["max_score"], b["competency"], auto),
        )
        conn.execute("UPDATE question_bank SET times_used = times_used + 1 WHERE id = ?", (bid,))
        conn.execute("COMMIT")
        return cur.lastrowid
    except Exception:
        conn.execute("ROLLBACK")
        raise


def clone_assessment(conn: sqlite3.Connection, aid: int) -> int:
    """استنساخ تقويم بنقرة (§16) مع تسجيل source_assessment_id."""
    a = conn.execute("SELECT * FROM assessments WHERE id = ?", (aid,)).fetchone()
    if a is None:
        raise KeyError(aid)
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            "INSERT INTO assessments (teacher_id, title, kind, level, unit, concept, "
            "stimuli_json, source_assessment_id) VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
            (a["title"] + " (نسخة)", a["kind"], a["level"], a["unit"], a["concept"],
             a["stimuli_json"], aid),
        )
        new_id = cur.lastrowid
        for row in conn.execute(
            "SELECT * FROM questions WHERE assessment_id = ? ORDER BY position", (aid,)
        ).fetchall():
            conn.execute(
                "INSERT INTO questions (assessment_id, position, type, prompt, stimulus, "
                "payload_json, indicators_json, max_score, competency, auto_scored) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id, row["position"], row["type"], row["prompt"], row["stimulus"],
                 row["payload_json"], row["indicators_json"], row["max_score"],
                 row["competency"], row["auto_scored"]),
            )
        conn.execute("COMMIT")
        return new_id
    except Exception:
        conn.execute("ROLLBACK")
        raise
