"""مجال التقارير التراكميّة (دفتر النقط): تقرير الفوج وتقرير التلميذ، وتصديرهما
(Excel/CSV/Word/نصّ). مسارات تحت /admin (يضمّها admin.py). للأستاذ وحده."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from ...database import AsyncSessionLocal
from ...models import Student
from ...services.gradebook import class_gradebook, student_gradebook
from ..web import _ctx, require_admin, templates

router = APIRouter()


@router.get("/gradebook", response_class=HTMLResponse)
async def gradebook(request: Request):
    """تقرير الفوج التراكميّ: مصفوفة (تلاميذ × تقاويم) بالنِّسب + معدّل القسم زمنيّاً."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        groups = [r[0] for r in (await s.execute(
            select(Student.group_name).where(Student.group_name.is_not(None))
            .distinct().order_by(Student.group_name))).all()]
        selected = request.query_params.get("group") or (groups[0] if groups else None)
        data = await class_gradebook(s, selected) if selected else None
    return templates.TemplateResponse(
        "admin/gradebook.html",
        _ctx(request, groups=groups, selected=selected, data=data))


@router.get("/gradebook/export")
async def gradebook_export(request: Request):
    """تصدير تقرير الفوج (نقط صريحة للأستاذ) إلى Excel/CSV/Word/نصّ. fmt=xlsx (الافتراض)."""
    if (g := require_admin(request)):
        return g
    group = request.query_params.get("group") or ""
    fmt = (request.query_params.get("fmt") or "xlsx").lower()
    if not group:
        return RedirectResponse("/admin/gradebook", status_code=303)
    async with AsyncSessionLocal() as s:
        data = await class_gradebook(s, group)
    if not data or not data.get("has_data"):
        return RedirectResponse(
            f"/admin/gradebook?group={group}&error=لا بيانات للتصدير.", status_code=303)

    if fmt == "docx":
        from ...services.report_export import class_report_docx
        return Response(
            content=class_report_docx(data),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="report-{group}.docx"'})
    if fmt == "txt":
        from ...services.report_export import class_report_txt
        return Response(
            content=class_report_txt(data).encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="report-{group}.txt"'})

    import io
    import pandas as pd

    evals = data["evaluations"]
    cols = [f"{e['title']} ({e['date']})" for e in evals]
    rows = []
    for srow in data["students"]:
        rec = {"التلميذ": srow["student"].full_name}
        for e, col in zip(evals, cols):
            v = srow["per"].get(e["quiz_id"])
            rec[col] = v if v is not None else ""
        rec["المعدّل العامّ ٪"] = srow["overall"] if srow["overall"] is not None else ""
        rows.append(rec)
    avg_rec = {"التلميذ": "معدّل القسم"}
    for e, col in zip(evals, cols):
        avg_rec[col] = e.get("class_avg") if e.get("class_avg") is not None else ""
    avg_rec["المعدّل العامّ ٪"] = data.get("class_overall") if data.get("class_overall") is not None else ""
    rows.append(avg_rec)

    df = pd.DataFrame(rows, columns=["التلميذ", *cols, "المعدّل العامّ ٪"])

    if fmt == "csv":
        body = df.to_csv(index=False).encode("utf-8-sig")
        return Response(
            content=body, media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="gradebook-{group}.csv"'})

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        df.to_excel(xl, index=False, sheet_name=group[:31] or "التقرير")
        xl.sheets[list(xl.sheets)[0]].sheet_view.rightToLeft = True
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="gradebook-{group}.xlsx"'})


@router.get("/gradebook/student/{student_id}", response_class=HTMLResponse)
async def gradebook_student(request: Request, student_id: int):
    """تقرير تلميذ تراكميّ: منحنى نسبته عبر التقاويم + رادار المهارات + جدول قابل للطباعة."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        data = await student_gradebook(s, student_id)
    if data["student"] is None:
        return HTMLResponse("التلميذ غير موجود", status_code=404)
    import json as _json
    vals = [e["pct"] for e in data["evaluations"]]
    # المعدّل التراكميّ: متوسّطٌ جارٍ حتى كلّ تقويم (يُظهر مسار التقدّم لا التذبذب اللحظيّ).
    _run: list[float] = []
    cumulative = []
    for v in vals:
        if v is not None:
            _run.append(v)
        cumulative.append(round(sum(_run) / len(_run), 1) if _run else None)
    chart = {"labels": [e["date"] + " · " + e["title"][:18] for e in data["evaluations"]],
             "values": vals, "cumulative": cumulative}
    skills = data["skills"]
    radar = {"labels": list(skills.keys()),
             "values": [round((skills[k]["avg"] or 0) * 100, 1) for k in skills]}
    return templates.TemplateResponse(
        "admin/gradebook_student.html",
        _ctx(request, data=data, chart=_json.dumps(chart), radar=_json.dumps(radar)))


@router.get("/gradebook/student/{student_id}/export")
async def gradebook_student_export(request: Request, student_id: int):
    """تصدير التقرير الفرديّ للتلميذ إلى Word (fmt=docx، الافتراض) أو نصّ (fmt=txt)."""
    if (g := require_admin(request)):
        return g
    fmt = (request.query_params.get("fmt") or "docx").lower()
    async with AsyncSessionLocal() as s:
        data = await student_gradebook(s, student_id)
    if data["student"] is None:
        return HTMLResponse("التلميذ غير موجود", status_code=404)
    if fmt == "txt":
        from ...services.report_export import student_report_txt
        return Response(
            content=student_report_txt(data).encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="student-{student_id}.txt"'})
    from ...services.report_export import student_report_docx
    return Response(
        content=student_report_docx(data),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="student-{student_id}.docx"'})
