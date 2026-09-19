"""مجال اللوائح والاستيراد: سير عمل الاستيراد (نصّ فلسفيّ أو لائحة تلاميذ + توليد رمز
الدخول)، وعرض اللوائح، وإضافة تلميذ، وأداة مقارنة نسختين (مَن نُقِل/أُضيف).
مسارات تحت /admin (يضمّها admin.py). السلوك والمسارات لا تتغيّر بالتفكيك."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete as sa_delete, func, select

from ...codes import make_login_code
from ...constants import level_of_class_label
from ...database import AsyncSessionLocal
from ...importer import ImporterError, extract_text, parse_students_excel
from ...models import (Answer, Axis, Level, PhilosophicalText, SessionStudent,
                       Student, StudentReport, TextType)
from ..web import _ctx, require_admin, templates

router = APIRouter()


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    if (g := require_admin(request)):
        return g
    return templates.TemplateResponse("admin/import.html", _ctx(request))


@router.post("/import/extract", response_class=HTMLResponse)
async def import_extract(request: Request, kind: str = Form(...),
                         file: UploadFile = File(...)):
    """الخطوة 2: يستخرج ويعرض للمراجعة (نصّ في حقل قابل للتعديل، أو جدول لائحة)."""
    if (g := require_admin(request)):
        return g
    data = await file.read()
    if kind == "roster":
        r = parse_students_excel(data)
        async with AsyncSessionLocal() as s:
            levels = (await s.execute(select(Level).order_by(Level.position))).scalars().all()
        return templates.TemplateResponse(
            "admin/import_roster.html",
            _ctx(request, preview=r, filename=file.filename, levels=levels,
                 rows_json=json.dumps([r_.__dict__ for r_ in r.rows], ensure_ascii=False)))
    # نصّ (docx/pptx/pdf/صورة)
    try:
        extracted = extract_text(file.filename, data)
    except ImporterError as exc:
        return templates.TemplateResponse(
            "admin/import.html", _ctx(request, error=str(exc)))
    async with AsyncSessionLocal() as s:
        axes = (await s.execute(select(Axis))).scalars().all()
    return templates.TemplateResponse(
        "admin/import_review.html",
        _ctx(request, extracted=extracted, axes=axes, text_types=list(TextType)))


@router.post("/import/save/text")
async def import_save_text(request: Request, axis_id: int = Form(...),
                           title: str = Form(...), author: str = Form(""),
                           text_type: str = Form("أساسي"), content: str = Form(...)):
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        s.add(PhilosophicalText(
            axis_id=axis_id, title=title.strip(), author=author.strip() or None,
            content=content, text_type=TextType(text_type)))
        await s.commit()
    return RedirectResponse("/admin/texts", status_code=303)


def _class_label(group: str | None, level: Level | None) -> str:
    """رمز قسمٍ صالحٌ للترميز (TC1، 1BAC2…): القسم إن كان صالحاً، وإلّا من رمز المستوى
    (TC → TC1) — فيبقى توليد رمز الدخول ممكناً حتى لو كان اسم القسم عربيّاً أو فارغاً."""
    g = (group or "").strip().upper()
    if level_of_class_label(g):
        return g
    base = ((level.code if level and level.code else "TC") or "TC").strip().upper()
    return f"{base}1"


@router.post("/import/save/roster")
async def import_save_roster(request: Request, level_id: int = Form(...),
                             group_override: str = Form(""), rows_json: str = Form(...)):
    """الحفظ النهائي للائحة: تراكمي (مطابقة برمز مسار)، بلا حذف.

    يولّد **رمز دخولٍ قصيراً** لكلّ تلميذٍ جديد (مثل TC1-10-JH) — كان ناقصاً فلم
    تُقبَل الرموز القصيرة أصلاً؛ يظهر في اللائحة والبطاقات فيدخل التلميذ بالمسح أو
    بكتابته. القدامى بلا رمزٍ يُمنَحون واحداً أيضاً (ترقية لطيفة، بلا ترحيل)."""
    if (g := require_admin(request)):
        return g
    rows = json.loads(rows_json)
    added = updated = 0
    async with AsyncSessionLocal() as s:
        level = await s.get(Level, level_id)
        # مجموعة الرموز الموجودة لضمان التفرّد (make_login_code تستشير دالّةً متزامنة).
        existing_codes = set((await s.execute(
            select(Student.login_code).where(Student.login_code.is_not(None)))).scalars().all())
        # عدّاد تسلسليّ لكلّ قسمٍ يبدأ بعد الموجودين (لترقيمٍ نظيف: 01، 02…).
        seq: dict[str, int] = dict((grp or "", n) for grp, n in (await s.execute(
            select(Student.group_name, func.count()).group_by(Student.group_name))).all())

        def _new_code(group: str | None) -> str:
            label = _class_label(group, level)
            seq[group or ""] = seq.get(group or "", 0) + 1
            code = make_login_code(f"{label}-{seq[group or '']:02d}", existing_codes.__contains__)
            existing_codes.add(code)
            return code

        for row in rows:
            name = (row.get("full_name") or "").strip()
            if not name:
                continue
            massar = (row.get("massar_code") or None)
            group = group_override.strip() or (row.get("group_name") or None)
            existing = None
            if massar:
                existing = await s.scalar(select(Student).where(Student.massar_code == massar))
            if existing:
                existing.full_name = name
                if group:
                    existing.group_name = group
                if not existing.login_code:          # ترقية القدامى بلا رمز
                    existing.login_code = _new_code(existing.group_name or group)
                updated += 1
            else:
                s.add(Student(full_name=name, level_id=level_id, group_name=group,
                              massar_code=massar, login_code=_new_code(group)))
                added += 1
        await s.commit()
    return RedirectResponse(f"/admin/rosters?added={added}&updated={updated}", status_code=303)


# ═══════════════ عرض ما حُفظ ═══════════════


@router.get("/rosters", response_class=HTMLResponse)
async def rosters(request: Request):
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        students = (await s.execute(
            select(Student).order_by(Student.level_id, Student.group_name,
                                     Student.full_name))).scalars().all()
        level_objs = (await s.execute(select(Level).order_by(Level.position))).scalars().all()
        levels = {lv.id: lv.name for lv in level_objs}
        groups = [r[0] for r in (await s.execute(
            select(Student.group_name).where(Student.group_name.is_not(None))
            .distinct().order_by(Student.group_name))).all()]
    return templates.TemplateResponse(
        "admin/rosters.html",
        _ctx(request, students=students, levels=levels, level_objs=level_objs,
             groups=groups,
             added=request.query_params.get("added"),
             updated=request.query_params.get("updated"),
             new_code=request.query_params.get("new_code"),
             new_name=request.query_params.get("new_name"),
             add_error=request.query_params.get("add_error"),
             deleted=request.query_params.get("deleted"),
             merged_student=request.query_params.get("merged_student"),
             merged_from=request.query_params.get("from"),
             merged_moved=request.query_params.get("moved"),
             merge_error=request.query_params.get("merge_error")))


@router.post("/students/add")
async def student_add(request: Request, full_name: str = Form(...),
                      level_id: str = Form(...), group_name: str = Form(""),
                      massar_code: str = Form("")):
    """إضافة تلميذٍ جديد غير مسجَّل (مثلاً حُوِّل من الإدارة) مع توليد رمز دخوله القصير
    تلقائيّاً — فيلج برمز مسار أو برمزه المختصر مثل بقيّة المتعلّمين."""
    if (g := require_admin(request)):
        return g
    name = full_name.strip()
    group = group_name.strip() or None
    massar = massar_code.strip().upper() or None
    if not name or not str(level_id).strip().isdigit():
        return RedirectResponse(
            "/admin/rosters?add_error=" + quote("الاسم والمستوى مطلوبان."), status_code=303)
    async with AsyncSessionLocal() as s:
        level = await s.get(Level, int(level_id))
        if level is None:
            return RedirectResponse(
                "/admin/rosters?add_error=" + quote("المستوى غير موجود."), status_code=303)
        if massar and await s.scalar(select(Student).where(Student.massar_code == massar)):
            return RedirectResponse(
                "/admin/rosters?add_error=" + quote(f"رمز مسار «{massar}» مسجَّلٌ سلفاً."),
                status_code=303)
        existing_codes = set((await s.execute(
            select(Student.login_code).where(Student.login_code.is_not(None)))).scalars().all())
        n = (await s.scalar(select(func.count()).select_from(Student)
                            .where(Student.group_name == group))) or 0
        label = _class_label(group, level)
        code = make_login_code(f"{label}-{n + 1:02d}", existing_codes.__contains__)
        s.add(Student(full_name=name, level_id=level.id, group_name=group,
                      massar_code=massar, login_code=code, active=True))
        await s.commit()
    return RedirectResponse(
        f"/admin/rosters?new_code={quote(code)}&new_name={quote(name)}", status_code=303)


@router.post("/students/{student_id}/delete")
async def student_delete(request: Request, student_id: int):
    """حذف تلميذٍ نهائيّاً (لإزالة مكرَّرٍ مثلاً) وكلّ أثره: أجوبته ونقطه وتقاريره
    وسجلّات حضوره. نحذف الأبناء صراحةً ثمّ التلميذ (لا نعتمد على تفعيل المفاتيح
    الأجنبيّة). لا رجعة."""
    if (g := require_admin(request)):
        return g
    name = ""
    async with AsyncSessionLocal() as s:
        st = await s.get(Student, student_id)
        if st is not None:
            name = st.full_name
            await s.execute(sa_delete(Answer).where(Answer.student_id == student_id))
            await s.execute(sa_delete(SessionStudent).where(SessionStudent.student_id == student_id))
            await s.execute(sa_delete(StudentReport).where(StudentReport.student_id == student_id))
            await s.execute(sa_delete(Student).where(Student.id == student_id))
            await s.commit()
    return RedirectResponse("/admin/rosters?deleted=" + quote(name), status_code=303)


@router.post("/students/merge")
async def students_merge(request: Request, source_id: int = Form(...),
                         target_id: int = Form(...)):
    """يدمج سجلَّي تلميذٍ مكرَّرَين بلا فقدِ إنجازات: يَنقل كلّ أجوبة/حضور/تقارير
    **المصدر** إلى **الهدف** (بلا تكرارٍ يخالف قيود التفرّد)، ثمّ يحذف المصدر الفارغ.

    الحالة النموذجيّة: تلميذٌ أُضيف يدويّاً بلا رقم مسار وأنجز تقاويم، ثمّ استُوردت
    اللائحة فأُنشئ له سجلٌّ رسميٌّ برقم مسار (فارغ). ادمج القديم (المصدر) في الرسميّ
    (الهدف) فتنتقل إنجازاته ويبقى رقم مساره. لا رجعة في حذف المصدر بعد النقل."""
    if (g := require_admin(request)):
        return g
    if source_id == target_id:
        return RedirectResponse(
            "/admin/rosters?merge_error=" + quote("اختر تلميذَين مختلفَين للدمج."),
            status_code=303)
    moved = 0
    src_name = tgt_name = ""
    async with AsyncSessionLocal() as s:
        src = await s.get(Student, source_id)
        dst = await s.get(Student, target_id)
        if src is None or dst is None:
            return RedirectResponse(
                "/admin/rosters?merge_error=" + quote("أحد التلميذَين غير موجود."),
                status_code=303)
        src_name, tgt_name = src.full_name, dst.full_name

        # (١) الأجوبة — مفتاح السؤال (تقويم/تحليل/مقال) فريدٌ لكلّ تلميذ.
        def _akey(a):
            return (a.quiz_question_id, a.analysis_question_id, a.essay_id)
        dst_ans = {_akey(a): a for a in (await s.execute(
            select(Answer).where(Answer.student_id == target_id))).scalars()}
        for a in (await s.execute(
                select(Answer).where(Answer.student_id == source_id))).scalars().all():
            keep = dst_ans.get(_akey(a))
            if keep is None:
                a.student_id = target_id
                dst_ans[_akey(a)] = a
                moved += 1
            else:
                # عند تعارضٍ نادر: نُبقي الأنفع (المُسلَّم/ذو النقطة) ونحذف الآخر.
                s_eff = a.manual_score if a.manual_score is not None else a.auto_score
                t_eff = keep.manual_score if keep.manual_score is not None else keep.auto_score
                src_better = (a.submitted and not keep.submitted) or (t_eff is None and s_eff is not None)
                if src_better:
                    await s.delete(keep)
                    await s.flush()               # ثبِّت الحذف قبل إعادة الإسناد (قيد التفرّد)
                    a.student_id = target_id
                    dst_ans[_akey(a)] = a
                    moved += 1
                else:
                    await s.delete(a)
                    await s.flush()

        # (٢) الحضور في الجلسات — فريدٌ بـ(الجلسة، التلميذ).
        dst_ss = {p.session_id: p for p in (await s.execute(
            select(SessionStudent).where(SessionStudent.student_id == target_id))).scalars()}
        for p in (await s.execute(
                select(SessionStudent).where(SessionStudent.student_id == source_id))).scalars().all():
            keep = dst_ss.get(p.session_id)
            if keep is None:
                p.student_id = target_id
                dst_ss[p.session_id] = p
            else:
                keep.present = keep.present or p.present
                if p.submitted_at and (keep.submitted_at is None or p.submitted_at < keep.submitted_at):
                    keep.submitted_at = p.submitted_at
                if not keep.device_token and p.device_token:
                    keep.device_token = p.device_token
                await s.delete(p)
                await s.flush()

        # (٣) التقارير — لا قيد تفرّد؛ تُعاد إسناداً كلّها.
        for rep in (await s.execute(
                select(StudentReport).where(StudentReport.student_id == source_id))).scalars().all():
            rep.student_id = target_id

        # الهدف يرث ما ينقص هويّته من المصدر (رمز مسار/دخول) إن كان أحدهما فارغاً.
        if not dst.massar_code and src.massar_code:
            dst.massar_code = src.massar_code
        if not dst.login_code and src.login_code:
            dst.login_code = src.login_code

        await s.flush()
        await s.delete(src)                       # المصدر صار فارغاً — يُحذف بأمان
        await s.commit()
    return RedirectResponse(
        f"/admin/rosters?merged_student={quote(tgt_name)}&from={quote(src_name)}&moved={moved}",
        status_code=303)


def _read_backup_students(data: bytes):
    """يقرأ (الاسم، رمز مسار، الفوج) لكلّ تلميذ من ملفّ نسخةٍ احتياطيّة (.db) — للقراءة
    فقط، في ملفٍّ مؤقّت، دون أن يمسّ قاعدة النظام الحيّة."""
    import os
    import sqlite3
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        con = sqlite3.connect(path)
        try:
            return con.execute(
                "SELECT full_name, massar_code, group_name FROM students").fetchall()
        finally:
            con.close()
    finally:
        os.unlink(path)


def _student_key(name: str | None, massar: str | None) -> str:
    """مفتاح مطابقةٍ للتلميذ: رمز مسار إن وُجد، وإلّا الاسم — لمقارنة نسختين."""
    if massar and str(massar).strip():
        return "m:" + str(massar).strip().upper()
    return "n:" + (name or "").strip()


@router.get("/roster-diff", response_class=HTMLResponse)
async def roster_diff_page(request: Request):
    """أداة «تقرير التغييرات»: قارن نسخةً احتياطيّة سابقة بالحالة الحاليّة لترى مَن نُقِل
    (تغيّر فوجه) ومَن أُضيف جديداً — بنظرة، بعد استيراد لائحة."""
    if (g := require_admin(request)):
        return g
    return templates.TemplateResponse("admin/roster_diff.html", _ctx(request))


@router.post("/roster-diff", response_class=HTMLResponse)
async def roster_diff_run(request: Request, backup: UploadFile = File(...)):
    if (g := require_admin(request)):
        return g
    data = await backup.read()
    try:
        old_rows = await asyncio.to_thread(_read_backup_students, data)
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(
            "admin/roster_diff.html",
            _ctx(request, error=f"تعذّرت قراءة الملفّ كنسخة قاعدة بيانات: {exc}"),
            status_code=400)
    old = {}
    for name, massar, group in old_rows:
        old[_student_key(name, massar)] = (name, massar, group)
    async with AsyncSessionLocal() as s:
        cur_rows = (await s.execute(
            select(Student.full_name, Student.massar_code, Student.group_name)
            .order_by(Student.group_name, Student.full_name))).all()
    moved, added = [], []
    for name, massar, group in cur_rows:
        k = _student_key(name, massar)
        if k in old:
            old_group = old[k][2]
            if (old_group or "") != (group or ""):
                moved.append({"name": name, "massar": massar or "—",
                              "old": old_group or "—", "new": group or "—"})
        else:
            added.append({"name": name, "massar": massar or "—", "group": group or "—"})
    moved.sort(key=lambda r: (r["new"], r["name"]))
    added.sort(key=lambda r: (r["group"], r["name"]))
    return templates.TemplateResponse(
        "admin/roster_diff.html",
        _ctx(request, done=True, moved=moved, added=added,
             old_count=len(old), cur_count=len(cur_rows)))
