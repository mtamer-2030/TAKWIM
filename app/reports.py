"""التقارير والرسوم (CLAUDE.md §16، §8/م٨).

الرسوم SVG مكتوبة يدوياً — لا مكتبة ولا إنترنت. لا نقطة تُعرض للتلميذ؛
هذه للأستاذ فقط. لا يدخل تقريراً إلّا ما teacher_confirmed = 1 (§6).
"""

from __future__ import annotations

import html
import json
import sqlite3

from .constants import COMPETENCIES

# الترتيب الثابت للكفايات الخمس في العرض.
COMP_ORDER = list(COMPETENCIES.keys())

_CONFIRMED = "an.teacher_confirmed = 1"


def student_competency_summary(conn: sqlite3.Connection, student_id: int) -> dict[str, dict]:
    rows = conn.execute(
        f"""
        SELECT q.competency AS comp,
               SUM(COALESCE(an.manual_score, an.auto_score, 0)) AS got,
               SUM(q.max_score) AS maxsum, COUNT(*) AS n
        FROM answers an
        JOIN attempts at ON at.id = an.attempt_id
        JOIN questions q ON q.id = an.question_id
        WHERE at.student_id = ? AND {_CONFIRMED}
        GROUP BY q.competency
        """,
        (student_id,),
    ).fetchall()
    by_comp = {r["comp"]: r for r in rows}
    out: dict[str, dict] = {}
    for comp in COMP_ORDER:
        r = by_comp.get(comp)
        if r and r["maxsum"]:
            out[comp] = {"ratio": r["got"] / r["maxsum"], "n": r["n"],
                         "got": r["got"], "max": r["maxsum"]}
        else:
            out[comp] = {"ratio": None, "n": 0, "got": 0, "max": 0}
    return out


def student_curve(conn: sqlite3.Connection, student_id: int) -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT a.title AS title, se.opened_at AS opened_at,
               SUM(COALESCE(an.manual_score, an.auto_score, 0)) AS got,
               SUM(q.max_score) AS maxsum
        FROM answers an
        JOIN attempts at ON at.id = an.attempt_id
        JOIN sessions se ON se.id = at.session_id
        JOIN assessments a ON a.id = se.assessment_id
        JOIN questions q ON q.id = an.question_id
        WHERE at.student_id = ? AND {_CONFIRMED}
        GROUP BY se.id
        ORDER BY se.opened_at
        """,
        (student_id,),
    ).fetchall()
    return [
        {"title": r["title"], "at": r["opened_at"],
         "ratio": (r["got"] / r["maxsum"]) if r["maxsum"] else 0.0}
        for r in rows
    ]


def class_competency_summary(conn: sqlite3.Connection, class_id: int) -> dict[str, dict]:
    rows = conn.execute(
        f"""
        SELECT q.competency AS comp,
               SUM(COALESCE(an.manual_score, an.auto_score, 0)) AS got,
               SUM(q.max_score) AS maxsum
        FROM answers an
        JOIN attempts at ON at.id = an.attempt_id
        JOIN students st ON st.id = at.student_id
        JOIN questions q ON q.id = an.question_id
        WHERE st.class_id = ? AND {_CONFIRMED}
        GROUP BY q.competency
        """,
        (class_id,),
    ).fetchall()
    by_comp = {r["comp"]: r for r in rows}
    out: dict[str, dict] = {}
    for comp in COMP_ORDER:
        r = by_comp.get(comp)
        out[comp] = {"ratio": (r["got"] / r["maxsum"]) if r and r["maxsum"] else None}
    return out


def class_common_errors(conn: sqlite3.Connection, class_id: int) -> list[dict]:
    """«أشيع الأخطاء» (§11): عدد التلاميذ الذين وقعوا في كلّ خطأ مشخِّص."""
    rows = conn.execute(
        """
        SELECT an.error_tags AS tags, at.student_id AS sid
        FROM answers an
        JOIN attempts at ON at.id = an.attempt_id
        JOIN students st ON st.id = at.student_id
        WHERE st.class_id = ? AND an.error_tags IS NOT NULL
        """,
        (class_id,),
    ).fetchall()
    labels = {r["code"]: r["label"] for r in conn.execute("SELECT code, label FROM error_codes")}
    students_by_code: dict[str, set] = {}
    count_by_code: dict[str, int] = {}
    for r in rows:
        try:
            tags = json.loads(r["tags"] or "[]")
        except (ValueError, TypeError):
            continue
        for t in tags:
            students_by_code.setdefault(t, set()).add(r["sid"])
            count_by_code[t] = count_by_code.get(t, 0) + 1
    out = [
        {"code": code, "label": labels.get(code, code),
         "students": len(sids), "count": count_by_code.get(code, 0)}
        for code, sids in students_by_code.items()
    ]
    out.sort(key=lambda x: (-x["students"], -x["count"]))
    return out


def class_hardest_questions(conn: sqlite3.Connection, class_id: int, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT q.id AS qid, q.prompt AS prompt, a.title AS title,
               AVG(COALESCE(an.manual_score, an.auto_score, 0) /
                   CASE WHEN q.max_score > 0 THEN q.max_score ELSE 1 END) AS ratio,
               COUNT(*) AS n
        FROM answers an
        JOIN attempts at ON at.id = an.attempt_id
        JOIN students st ON st.id = at.student_id
        JOIN questions q ON q.id = an.question_id
        JOIN assessments a ON a.id = q.assessment_id
        WHERE st.class_id = ? AND {_CONFIRMED} AND q.max_score > 0
        GROUP BY q.id
        HAVING n >= 1
        ORDER BY ratio ASC
        LIMIT ?
        """,
        (class_id, limit),
    ).fetchall()
    return [{"qid": r["qid"], "prompt": r["prompt"], "title": r["title"],
             "ratio": r["ratio"], "n": r["n"]} for r in rows]


def level_comparison(conn: sqlite3.Connection, level: str) -> list[dict]:
    classes = conn.execute(
        "SELECT id, label FROM classes WHERE level = ? AND active = 1 ORDER BY label",
        (level,),
    ).fetchall()
    return [
        {"label": c["label"], "summary": class_competency_summary(conn, c["id"])}
        for c in classes
    ]


# ————————————————————— رسوم SVG مكتوبة يدوياً —————————————————————


def _esc(s: str) -> str:
    return html.escape(str(s))


def svg_hbar(items: list[tuple[str, float | None]], width: int = 420,
             bar_h: int = 26, gap: int = 12, label_w: int = 120) -> str:
    """رسم أعمدة أفقية (مناسب لعناوين عربية RTL). القيمة نسبة 0..1 أو None."""
    n = len(items)
    height = n * (bar_h + gap) + gap
    track_w = width - label_w - 60
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'role="img" xmlns="http://www.w3.org/2000/svg" font-family="system-ui">'
    ]
    for i, (label, val) in enumerate(items):
        y = gap + i * (bar_h + gap)
        cy = y + bar_h / 2
        # الخلفية (المسار)
        parts.append(
            f'<rect x="60" y="{y}" width="{track_w}" height="{bar_h}" rx="6" '
            f'fill="#eee"/>'
        )
        if val is not None:
            w = max(2, track_w * max(0.0, min(1.0, val)))
            col = "#2e7d32" if val >= 0.6 else ("#f9a825" if val >= 0.4 else "#c62828")
            parts.append(
                f'<rect x="60" y="{y}" width="{w:.1f}" height="{bar_h}" rx="6" fill="{col}"/>'
            )
            parts.append(
                f'<text x="{60 + track_w + 6}" y="{cy + 4:.0f}" font-size="13" '
                f'text-anchor="start">{val * 100:.0f}٪</text>'
            )
        else:
            parts.append(
                f'<text x="{60 + track_w + 6}" y="{cy + 4:.0f}" font-size="12" '
                f'fill="#999" text-anchor="start">—</text>'
            )
        # التسمية على اليمين (RTL)
        parts.append(
            f'<text x="{width - 4}" y="{cy + 4:.0f}" font-size="14" '
            f'text-anchor="end">{_esc(label)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def svg_line(points: list[float], width: int = 460, height: int = 160) -> str:
    """منحنى تطوّر بسيط. القيم نسب 0..1."""
    if not points:
        return '<svg viewBox="0 0 10 10"></svg>'
    pad = 24
    n = len(points)
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    step = inner_w / max(1, n - 1) if n > 1 else 0
    coords = []
    for i, v in enumerate(points):
        x = pad + i * step
        y = pad + inner_h * (1 - max(0.0, min(1.0, v)))
        coords.append((x, y))
    path = " ".join(
        ("M" if i == 0 else "L") + f"{x:.1f} {y:.1f}" for i, (x, y) in enumerate(coords)
    )
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'xmlns="http://www.w3.org/2000/svg" font-family="system-ui">',
        f'<rect x="{pad}" y="{pad}" width="{inner_w}" height="{inner_h}" '
        f'fill="none" stroke="#ddd"/>',
        # خطوط شبكة عند 50٪
        f'<line x1="{pad}" y1="{pad + inner_h / 2}" x2="{pad + inner_w}" '
        f'y2="{pad + inner_h / 2}" stroke="#eee"/>',
        f'<path d="{path}" fill="none" stroke="#1565c0" stroke-width="2.5"/>',
    ]
    for x, y in coords:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#1565c0"/>')
    parts.append("</svg>")
    return "".join(parts)
