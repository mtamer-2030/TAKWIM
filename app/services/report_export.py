"""تصدير تقارير الأستاذ إلى Word (.docx) ونصّ (.txt) — جماعيّ (فوج) وفرديّ (تلميذ).

تقارير للأستاذ وحده (نقط صريحة، ق-٤). تُبنى من مخرجات services.gradebook دون تغيير
مخطّط. يُضبط الاتجاه RTL في الفقرات والجداول لتظهر العربية سليمةً في Word.
"""

from __future__ import annotations

import io

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.shared import Pt


def _rtl_para(p):
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    pPr = p._p.get_or_add_pPr()
    pPr.append(OxmlElement("w:bidi"))


def _rtl_table(table):
    tblPr = table._tbl.tblPr
    tblPr.append(OxmlElement("w:bidiVisual"))


def _pct(v) -> str:
    return f"{v}٪" if v is not None else "—"


def _new_doc(title: str) -> Document:
    doc = Document()
    doc.styles["Normal"].font.name = "Arial"
    doc.styles["Normal"].font.size = Pt(11)
    h = doc.add_heading(title, level=1)
    _rtl_para(h)
    return doc


# ═══════════════ تقرير التلميذ ═══════════════


def student_report_docx(data: dict) -> bytes:
    st = data["student"]
    doc = _new_doc(f"التقرير الفرديّ — {st.full_name}")
    meta = doc.add_paragraph()
    _rtl_para(meta)
    trend = data.get("trend")
    trend_txt = (("+" if trend >= 0 else "") + f"{trend} نقطة") if trend is not None else "—"
    meta.add_run(
        f"الفوج: {st.group_name or '—'}  |  المعدّل العامّ: {_pct(data.get('overall'))}"
        f"  |  التطوّر (أوّل→آخر): {trend_txt}")

    # جدول التقويمات
    ph = doc.add_paragraph(); _rtl_para(ph); ph.add_run("النسب عبر التقويمات:").bold = True
    evals = data.get("evaluations") or []
    if evals:
        t = doc.add_table(rows=1, cols=4); t.style = "Table Grid"; _rtl_table(t)
        for i, head in enumerate(["التاريخ", "التقويم", "النوع", "النسبة"]):
            t.rows[0].cells[i].paragraphs[0].add_run(head).bold = True
            _rtl_para(t.rows[0].cells[i].paragraphs[0])
        for e in evals:
            c = t.add_row().cells
            for i, val in enumerate([e.get("date", "—"), e.get("title", ""),
                                     e.get("kind", ""), _pct(e.get("pct"))]):
                c[i].paragraphs[0].add_run(str(val)); _rtl_para(c[i].paragraphs[0])
    else:
        p = doc.add_paragraph("لا تقويمات مصادَق عليها بعد."); _rtl_para(p)

    # جدول المهارات
    skills = data.get("skills") or {}
    if skills:
        ps = doc.add_paragraph(); _rtl_para(ps); ps.add_run("ملمح المهارات:").bold = True
        t = doc.add_table(rows=1, cols=2); t.style = "Table Grid"; _rtl_table(t)
        for i, head in enumerate(["المهارة", "التمكّن ٪"]):
            t.rows[0].cells[i].paragraphs[0].add_run(head).bold = True
            _rtl_para(t.rows[0].cells[i].paragraphs[0])
        for name, sk in skills.items():
            avg = sk.get("avg")
            c = t.add_row().cells
            c[0].paragraphs[0].add_run(str(name)); _rtl_para(c[0].paragraphs[0])
            c[1].paragraphs[0].add_run(_pct(round(avg * 100, 1) if avg is not None else None))
            _rtl_para(c[1].paragraphs[0])

    buf = io.BytesIO(); doc.save(buf); return buf.getvalue()


def student_report_txt(data: dict) -> str:
    st = data["student"]
    lines = [f"التقرير الفرديّ — {st.full_name}",
             f"الفوج: {st.group_name or '—'}",
             f"المعدّل العامّ: {_pct(data.get('overall'))}",
             f"التطوّر: {data.get('trend') if data.get('trend') is not None else '—'}",
             "", "النسب عبر التقويمات:"]
    for e in (data.get("evaluations") or []):
        lines.append(f"  - {e.get('date','—')} | {e.get('title','')} | {e.get('kind','')} | {_pct(e.get('pct'))}")
    if not (data.get("evaluations")):
        lines.append("  (لا تقويمات مصادَق عليها بعد)")
    lines += ["", "ملمح المهارات:"]
    for name, sk in (data.get("skills") or {}).items():
        avg = sk.get("avg")
        lines.append(f"  - {name}: {_pct(round(avg*100,1) if avg is not None else None)}")
    return "\n".join(lines)


# ═══════════════ تقرير الفوج ═══════════════


def class_report_docx(data: dict) -> bytes:
    group = data.get("group_name", "")
    doc = _new_doc(f"التقرير الجماعيّ — الفوج {group}")
    meta = doc.add_paragraph(); _rtl_para(meta)
    meta.add_run(f"معدّل القسم العامّ: {_pct(data.get('class_overall'))}").bold = True

    evals = data.get("evaluations") or []
    students = data.get("students") or []
    cols = ["التلميذ"] + [f"{e['title']} ({e['date']})" for e in evals] + ["المعدّل ٪"]
    t = doc.add_table(rows=1, cols=len(cols)); t.style = "Table Grid"; _rtl_table(t)
    for i, head in enumerate(cols):
        t.rows[0].cells[i].paragraphs[0].add_run(head).bold = True
        _rtl_para(t.rows[0].cells[i].paragraphs[0])
    for srow in students:
        c = t.add_row().cells
        c[0].paragraphs[0].add_run(srow["student"].full_name); _rtl_para(c[0].paragraphs[0])
        for j, e in enumerate(evals):
            v = srow["per"].get(e["quiz_id"])
            c[j + 1].paragraphs[0].add_run(_pct(v)); _rtl_para(c[j + 1].paragraphs[0])
        c[-1].paragraphs[0].add_run(_pct(srow.get("overall"))); _rtl_para(c[-1].paragraphs[0])
    # صفّ معدّل القسم
    c = t.add_row().cells
    c[0].paragraphs[0].add_run("معدّل القسم").bold = True; _rtl_para(c[0].paragraphs[0])
    for j, e in enumerate(evals):
        c[j + 1].paragraphs[0].add_run(_pct(e.get("class_avg"))); _rtl_para(c[j + 1].paragraphs[0])
    c[-1].paragraphs[0].add_run(_pct(data.get("class_overall"))); _rtl_para(c[-1].paragraphs[0])

    buf = io.BytesIO(); doc.save(buf); return buf.getvalue()


def ai_reports_docx(data: dict) -> bytes:
    """تقارير التدخّل (قسم «التدخل AI») في Word: تقرير القسم (المهارات + أضعفها + خطّة
    الدعم إن وُلِّدت) ثمّ تقريرٌ فرديّ لكلّ تلميذ (ملمح مهاراته + خطّته)."""
    group = data.get("group", "")
    doc = _new_doc(f"تقارير التدخّل العلاجيّ — الفوج {group}")
    cr = data.get("class") or {}

    h = doc.add_paragraph(); _rtl_para(h); h.add_run("التقرير الجماعيّ للقسم").bold = True
    w = doc.add_paragraph(); _rtl_para(w)
    w.add_run(f"أضعف مهارةٍ للقسم: {cr.get('weakest') or '—'}")
    for name, sk in (cr.get("skills") or {}).items():
        avg = sk.get("avg") if isinstance(sk, dict) else sk
        p = doc.add_paragraph(f"• {name}: {_pct(round(avg*100,1) if avg is not None else None)}")
        _rtl_para(p)
    if cr.get("plan"):
        pp = doc.add_paragraph(); _rtl_para(pp); pp.add_run("خطّة الدعم:").bold = True
        body = doc.add_paragraph(str(cr["plan"])); _rtl_para(body)

    for stu in (data.get("students") or []):
        doc.add_paragraph()
        h = doc.add_heading(stu["student"].full_name, level=2); _rtl_para(h)
        for name, sk in (stu.get("skills") or {}).items():
            avg = sk.get("avg") if isinstance(sk, dict) else sk
            p = doc.add_paragraph(f"• {name}: {_pct(round(avg*100,1) if avg is not None else None)}")
            _rtl_para(p)
        if stu.get("plan"):
            pp = doc.add_paragraph(); _rtl_para(pp); pp.add_run("خطّة التدخّل:").bold = True
            body = doc.add_paragraph(str(stu["plan"])); _rtl_para(body)
        elif not stu.get("has_data"):
            p = doc.add_paragraph("لا بيانات مصادَقٌ عليها بعد لهذا التلميذ."); _rtl_para(p)

    buf = io.BytesIO(); doc.save(buf); return buf.getvalue()


def ai_reports_txt(data: dict) -> str:
    group = data.get("group", "")
    cr = data.get("class") or {}
    lines = [f"تقارير التدخّل العلاجيّ — الفوج {group}", "",
             "== التقرير الجماعيّ ==",
             f"أضعف مهارة: {cr.get('weakest') or '—'}"]
    for name, sk in (cr.get("skills") or {}).items():
        avg = sk.get("avg") if isinstance(sk, dict) else sk
        lines.append(f"  • {name}: {_pct(round(avg*100,1) if avg is not None else None)}")
    if cr.get("plan"):
        lines += ["خطّة الدعم:", str(cr["plan"])]
    for stu in (data.get("students") or []):
        lines += ["", f"== {stu['student'].full_name} =="]
        for name, sk in (stu.get("skills") or {}).items():
            avg = sk.get("avg") if isinstance(sk, dict) else sk
            lines.append(f"  • {name}: {_pct(round(avg*100,1) if avg is not None else None)}")
        if stu.get("plan"):
            lines += ["خطّة التدخّل:", str(stu["plan"])]
    return "\n".join(lines)


def class_report_txt(data: dict) -> str:
    group = data.get("group_name", "")
    evals = data.get("evaluations") or []
    lines = [f"التقرير الجماعيّ — الفوج {group}",
             f"معدّل القسم العامّ: {_pct(data.get('class_overall'))}", ""]
    heads = ["التلميذ"] + [f"{e['title']} ({e['date']})" for e in evals] + ["المعدّل"]
    lines.append(" | ".join(heads))
    for srow in (data.get("students") or []):
        row = [srow["student"].full_name]
        row += [_pct(srow["per"].get(e["quiz_id"])) for e in evals]
        row.append(_pct(srow.get("overall")))
        lines.append(" | ".join(row))
    avg = ["معدّل القسم"] + [_pct(e.get("class_avg")) for e in evals] + [_pct(data.get("class_overall"))]
    lines.append(" | ".join(avg))
    return "\n".join(lines)
