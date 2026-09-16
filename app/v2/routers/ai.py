"""مجال التدخّل العلاجي (الذكاء الاصطناعي): صفحة التدخّل واختبار المحرّك، توليد تقارير
التدخّل كمهمّة خلفية، وعرض/تصدير التقارير البيداغوجيّة (تعمل بلا Ollama).
مسارات تحت /admin (يضمّها admin.py). السلوك والمسارات لا تتغيّر بالتفكيك."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select

from ...ai_feedback import (AIUnavailable, generate_class_plan,
                            generate_student_plan, ollama_available, ping_generate)
from ...database import AsyncSessionLocal
from ...models import (Answer, ClassReport, QuizQuestion, QuizSession, Student,
                       StudentReport)
from ...services.analytics import (generate_class_report,
                                   generate_student_skill_profile)
from ...settings import settings
from ..web import _ctx, require_admin, templates

router = APIRouter()


@router.get("/ai", response_class=HTMLResponse)
async def ai_page(request: Request):
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        groups = [r[0] for r in (await s.execute(
            select(Student.group_name).where(Student.group_name.is_not(None))
            .distinct().order_by(Student.group_name))).all()]
        report_count = await s.scalar(select(func.count()).select_from(StudentReport))
    return templates.TemplateResponse(
        "admin/ai.html",
        _ctx(request, groups=groups, online=ollama_available(),
             model=settings.local_ai.model, report_count=report_count, result=None,
             test=request.query_params.get("test")))


@router.post("/ai/test")
async def ai_test(request: Request):
    """اختبار سريع للمحرّك المحلّي: توليد قصير يكشف الخطأ الحقيقي إن وُجد."""
    if (g := require_admin(request)):
        return g
    ok, msg = await asyncio.to_thread(ping_generate)
    prefix = "✓ المحرّك يعمل: " if ok else "✗ "
    return RedirectResponse(f"/admin/ai?test={prefix}{msg}", status_code=303)


# حالة مهامّ الذكاء الاصطناعي الجارية (في الذاكرة) — مفتاحها اسم الفوج.
_AI_JOBS: dict[str, dict] = {}


async def _run_ai_job(group_name: str, scope: str = "full") -> None:
    """مهمّة خلفية: تولّد تقارير التدخّل دون تجميد الواجهة.

    الترتيب: تقرير القسم **أوّلاً** (نداء واحد يظهر بسرعة)، ثمّ التقارير الفردية
    إن كان النطاق «full». نداءات Ollama الحاجبة تُنفَّذ في خيط (to_thread).
    scope="class" يكتفي بتقرير القسم (الأسرع).
    """
    do_class = scope in ("class", "full")
    do_students = scope in ("students", "full")
    job = _AI_JOBS[group_name]
    try:
        async with AsyncSessionLocal() as s:
            students = (await s.execute(
                select(Student).where(Student.group_name == group_name,
                                      Student.active.is_(True))
                .order_by(Student.full_name))).scalars().all()

            # (1) التقرير الجماعي أوّلاً — يظهر بسرعة (نداء واحد)
            if do_class:
                report = await generate_class_report(s, group_name)
                if report["has_data"]:
                    try:
                        plan = await asyncio.to_thread(generate_class_plan, report)
                        s.add(ClassReport(
                            level_id=(students[0].level_id if students else None),
                            group_name=group_name, skills_summary=report["skills"],
                            weakest_skill=report["dominant_deficit"],
                            ai_intervention_plan=plan, ai_model=settings.local_ai.model))
                        job["class_report"] = {"dominant": report["dominant_deficit"], "plan": plan}
                        await s.commit()
                    except AIUnavailable as exc:
                        job.update(status="error", offline=True,
                                   message=f"توقّفت المعالجة: {exc}")
                        return

            # (2) التقارير الفردية — تسلسليّاً
            if do_students:
                job["total"] = len(students)
                for st in students:
                    profile = await generate_student_skill_profile(s, st.id)
                    job["done"] += 1
                    if not profile["has_data"]:
                        continue
                    try:
                        plan = await asyncio.to_thread(generate_student_plan, profile)
                    except AIUnavailable as exc:
                        job.update(status="error", offline=True,
                                   message=f"توقّفت المعالجة (حُفظ ما تمّ): {exc}")
                        await s.commit()
                        return
                    s.add(StudentReport(
                        student_id=st.id, skill_profile=profile["skills"],
                        ai_intervention_plan=plan, ai_model=settings.local_ai.model))
                    job["processed"] += 1
                await s.commit()

        parts = []
        if do_class:
            parts.append("تقرير القسم" if job["class_report"] else "(لا بيانات للقسم)")
        if do_students:
            parts.append(f"{job['processed']} تقرير فردي")
        job.update(status="done", message="تمّ توليد " + " و".join(parts) + ".")
    except Exception as exc:  # noqa: BLE001
        job.update(status="error", message=f"خطأ أثناء المعالجة: {exc}")


@router.post("/ai/run", response_class=HTMLResponse)
async def ai_run(request: Request, group_name: str = Form(...),
                 do_class: str = Form(""), do_students: str = Form("")):
    """يطلق توليد التقارير كمهمّة خلفية ويعرض شريط تقدّم (لا تتجمّد الواجهة).

    يختار الأستاذ التقرير الجماعي و/أو الفردي بخانتَي اختيار مستقلّتين."""
    if (g := require_admin(request)):
        return g
    want_class = do_class == "on"
    want_students = do_students == "on"
    if want_class and want_students:
        scope = "full"
    elif want_students:
        scope = "students"
    else:
        scope = "class"          # الافتراض عند عدم الاختيار: الجماعي (الأسرع)

    async def render(**extra):
        async with AsyncSessionLocal() as s:
            groups = [r[0] for r in (await s.execute(
                select(Student.group_name).where(Student.group_name.is_not(None))
                .distinct().order_by(Student.group_name))).all()]
        return templates.TemplateResponse(
            "admin/ai.html",
            _ctx(request, groups=groups, online=ollama_available(),
                 model=settings.local_ai.model, report_count=None, **extra))

    # ق-٢ (فرض برمجيّ): لا يشتغل الذكاء الاصطناعي وأيّ جلسة صفّية مفتوحة — كل
    # معالجة ذكية بعد إغلاق الحصّة، لا أثناءها (حفاظاً على العتاد واليقين).
    async with AsyncSessionLocal() as s:
        open_sess = await s.scalar(
            select(func.count()).select_from(QuizSession)
            .where(QuizSession.status == "open"))
    if open_sess:
        return await render(result={
            "offline": False,
            "message": "توجد جلسة صفّية مفتوحة. أغلِق الجلسات أوّلاً — "
                       "المعالجة الذكية تكون بعد الحصّة لا أثناءها (ق-٢)."})

    if not ollama_available():
        return await render(result={"offline": True,
                                    "message": "محرك الذكاء الاصطناعي غير مشغل. "
                                               "يرجى تشغيل Ollama أولاً."})

    _AI_JOBS[group_name] = {"status": "running", "total": 0, "done": 0,
                            "processed": 0, "offline": False, "message": "",
                            "class_report": None,
                            "scope": scope}
    asyncio.create_task(_run_ai_job(group_name, scope))
    return await render(job_group=group_name)


async def _hardest_questions(s, group: str, limit: int = 6) -> list[dict]:
    """أصعب الأسئلة على القسم (الأخطاء الشائعة): نسبة نجاحٍ منخفضة عبر أجوبةٍ مصادَقة."""
    eff = func.coalesce(Answer.manual_score, Answer.auto_score)
    ratio = func.avg(eff / QuizQuestion.max_score)
    rows = (await s.execute(
        select(QuizQuestion.prompt, ratio, func.count(Answer.id))
        .join(Answer, Answer.quiz_question_id == QuizQuestion.id)
        .join(Student, Student.id == Answer.student_id)
        .where(Student.group_name == group, Answer.teacher_confirmed.is_(True),
               eff.is_not(None), QuizQuestion.max_score > 0)
        .group_by(QuizQuestion.id)
        .having(func.count(Answer.id) >= 2)
        .order_by(ratio.asc()).limit(limit))).all()
    return [{"prompt": (p or "").strip()[:100], "success": round((r or 0) * 100, 1),
             "count": n} for p, r, n in rows if (r or 0) < 0.7]


async def _ai_reports_data(s, group: str) -> dict:
    """يجمع تقارير التدخّل للقراءة/التصدير مع قراءةٍ بيداغوجيّة كاملة (بلا Ollama):
    خلاصةٌ سرديّة، تصنيف نوعيّ للمهارات، توزيع المستويات، القوّة/القصور، أصعب الأسئلة،
    وتوصيات دعمٍ عمليّة — جماعيّاً وفرديّاً؛ مع خطط الذكاء المخزّنة إن وُلِّدت."""
    from ...services.pedagogy import (class_narrative, class_pedagogy,
                                     student_narrative, student_pedagogy)
    class_rep = await generate_class_report(s, group)
    class_plan = await s.scalar(
        select(ClassReport.ai_intervention_plan)
        .where(ClassReport.group_name == group,
               ClassReport.ai_intervention_plan.is_not(None))
        .order_by(ClassReport.created_at.desc()).limit(1))
    students = (await s.execute(
        select(Student).where(Student.group_name == group, Student.active.is_(True))
        .order_by(Student.full_name))).scalars().all()
    stu = []
    overalls: list = []
    for st in students:
        prof = await generate_student_skill_profile(s, st.id)
        overalls.append(prof.get("overall"))
        plan = await s.scalar(
            select(StudentReport.ai_intervention_plan)
            .where(StudentReport.student_id == st.id,
                   StudentReport.ai_intervention_plan.is_not(None))
            .order_by(StudentReport.created_at.desc()).limit(1))
        sp = student_pedagogy(prof)
        stu.append({"student": st, "skills": prof.get("skills") or {},
                    "has_data": prof.get("has_data"), "plan": plan,
                    "peda": sp, "narrative": student_narrative(sp, st.full_name)})
    cp = class_pedagogy(class_rep, overalls)
    hardest = await _hardest_questions(s, group)
    return {
        "group": group,
        "class": {"skills": class_rep.get("skills") or {},
                  "weakest": class_rep.get("dominant_deficit"),
                  "plan": class_plan, "has_data": class_rep.get("has_data"),
                  "peda": cp, "narrative": class_narrative(cp, group),
                  "hardest": hardest},
        "students": stu,
    }


@router.get("/ai/reports", response_class=HTMLResponse)
async def ai_reports_view(request: Request):
    """يعرض تقارير التدخّل (جماعيّة + فرديّة) للقسم للقراءة على الشاشة."""
    if (g := require_admin(request)):
        return g
    group = request.query_params.get("group") or ""
    async with AsyncSessionLocal() as s:
        groups = [r[0] for r in (await s.execute(
            select(Student.group_name).where(Student.group_name.is_not(None))
            .distinct().order_by(Student.group_name))).all()]
        if not group and groups:
            # الافتراض: الفوج الأكثر بياناتٍ مصادَقة (لا فوجاً فارغاً) — تقريرٌ ذو معنى فوراً.
            eff = func.coalesce(Answer.manual_score, Answer.auto_score)
            top = (await s.execute(
                select(Student.group_name, func.count(Answer.id).label("n"))
                .join(Answer, Answer.student_id == Student.id)
                .where(Answer.teacher_confirmed.is_(True), eff.is_not(None),
                       Student.group_name.is_not(None))
                .group_by(Student.group_name).order_by(func.count(Answer.id).desc())
                .limit(1))).first()
            group = top[0] if top else groups[0]
        data = await _ai_reports_data(s, group) if group else None
    return templates.TemplateResponse(
        "admin/ai_reports.html",
        _ctx(request, groups=groups, group=group, data=data,
             has_plans=bool(data and (data["class"]["plan"] or
                            any(x["plan"] for x in data["students"]))) if data else False))


@router.get("/ai/reports/export")
async def ai_reports_export(request: Request):
    """تصدير تقارير التدخّل للقسم إلى Word (fmt=docx، الافتراض) أو نصّ (fmt=txt)."""
    if (g := require_admin(request)):
        return g
    group = request.query_params.get("group") or ""
    fmt = (request.query_params.get("fmt") or "docx").lower()
    if not group:
        return RedirectResponse("/admin/ai/reports", status_code=303)
    async with AsyncSessionLocal() as s:
        data = await _ai_reports_data(s, group)
    if fmt == "txt":
        from ...services.report_export import ai_reports_txt
        return Response(
            content=ai_reports_txt(data).encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="ai-{group}.txt"'})
    from ...services.report_export import ai_reports_docx
    return Response(
        content=ai_reports_docx(data),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="ai-{group}.docx"'})


@router.get("/ai/status", response_class=HTMLResponse)
async def ai_status(request: Request):
    """جزء HTMX: يعرض تقدّم مهمّة الذكاء الاصطناعي، ويتوقّف عن الاستطلاع عند الانتهاء."""
    if (g := require_admin(request)):
        return g
    group = request.query_params.get("group", "")
    job = _AI_JOBS.get(group)
    return templates.TemplateResponse(
        "admin/_ai_status.html", _ctx(request, group=group, job=job))
