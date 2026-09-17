"""مجال الجلسات الصفّية (اللائحة/الإنشاء/الحضور/الفتح/الإغلاق/الحذف والمتابعة الآنية).
مسارات تحت /admin (يضمّها admin.py). السلوك والمسارات لا تتغيّر بالتفكيك."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete as sa_delete, select

from ...backup import try_backup_quiet
from ...constants import DISPLAY_TYPES, QUESTION_TYPES_CLOSED, level_of_class_label
from ...database import AsyncSessionLocal
from ...models import (Answer, Level, Quiz, QuizQuestion, QuizSession,
                       SessionStudent, Student)
from ...services.quizzes import grade_answer
from ..web import _ctx, require_admin, templates

router = APIRouter()


def _live_status(p: SessionStudent) -> tuple[str, str]:
    """حالة التلميذ في الجلسة للمتابعة الآنية: (وصف، لون)."""
    if not p.present:
        return ("غائب", "#9e9e9e")
    if p.submitted_at is not None:
        return ("سلّم ✓", "#2e7d32")
    if p.device_token:
        return ("يجيب…", "#1565c0")
    return ("لم يدخل", "#c62828")


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_list(request: Request):
    """لائحة الجلسات الصفّية، وإنشاء جلسة جديدة لتقويم على فوج."""
    if (g := require_admin(request)):
        return g
    from sqlalchemy.orm import selectinload
    async with AsyncSessionLocal() as s:
        sessions = (await s.execute(
            select(QuizSession).options(selectinload(QuizSession.quiz),
                                        selectinload(QuizSession.participants))
            .order_by(QuizSession.created_at.desc()))).scalars().all()
        quizzes = (await s.execute(select(Quiz).order_by(Quiz.created_at.desc()))).scalars().all()
        groups = [r[0] for r in (await s.execute(
            select(Student.group_name).where(Student.group_name.is_not(None))
            .distinct().order_by(Student.group_name))).all()]
    # عائلاتٌ لها أكثر من جلسةٍ لنفس (التقويم + الفوج) → تُعرَض معها إمكانيّة الدمج.
    from collections import Counter
    fam = Counter((sess.quiz_id, sess.group_name) for sess in sessions)
    rows = []
    for sess in sessions:
        subs = sum(1 for p in sess.participants if p.submitted_at)
        present = sum(1 for p in sess.participants if p.present)
        rows.append({"s": sess, "subs": subs, "present": present,
                     "total": len(sess.participants),
                     "dup": fam[(sess.quiz_id, sess.group_name)] > 1})
    return templates.TemplateResponse(
        "admin/sessions.html", _ctx(request, rows=rows, quizzes=quizzes, groups=groups,
                                    error=request.query_params.get("error"),
                                    merged=request.query_params.get("merged")))


@router.post("/sessions/create")
async def session_create(request: Request, quiz_id: int = Form(...),
                         group_name: str = Form(...)):
    """ينشئ جلسة (مسودّة) ويضيف تلاميذ الفوج النشطين مشاركين حاضرين افتراضياً."""
    if (g := require_admin(request)):
        return g
    group_name = group_name.strip()
    async with AsyncSessionLocal() as s:
        # ح-١٥: منع صارم لفتح جلسة بتقويم لا يطابق مستوى الفوج (المستوى يُشتقّ من
        # بادئة رمز القسم). إن كان للتقويم مستوى وعُرف مستوى الفوج واختلفا → رفض.
        quiz = await s.get(Quiz, quiz_id)
        if quiz is None:
            return RedirectResponse("/admin/sessions?error=التقويم غير موجود.",
                                    status_code=303)
        lvl_code = level_of_class_label(group_name)
        if quiz.level_id is not None and lvl_code is not None:
            grp_level = await s.scalar(select(Level).where(Level.code == lvl_code))
            if grp_level is not None and grp_level.id != quiz.level_id:
                return RedirectResponse(
                    "/admin/sessions?error=مستوى التقويم لا يطابق مستوى الفوج — "
                    "اختر تقويماً من مستوى الفوج نفسه.", status_code=303)
        sess = QuizSession(quiz_id=quiz_id, group_name=group_name, status="draft")
        s.add(sess)
        await s.flush()
        students = (await s.execute(
            select(Student).where(Student.group_name == group_name,
                                  Student.active.is_(True)))).scalars().all()
        for st in students:
            s.add(SessionStudent(session_id=sess.id, student_id=st.id, present=True))
        await s.commit()
        sid = sess.id
    return RedirectResponse(f"/admin/sessions/{sid}", status_code=303)


@router.get("/sessions/{sid}", response_class=HTMLResponse)
async def session_manage(request: Request, sid: int):
    """إدارة الجلسة: بوابة الحضور، فتح/إغلاق، ورابط المتابعة الآنية."""
    if (g := require_admin(request)):
        return g
    from sqlalchemy.orm import selectinload
    async with AsyncSessionLocal() as s:
        sess = (await s.execute(
            select(QuizSession).where(QuizSession.id == sid)
            .options(selectinload(QuizSession.quiz),
                     selectinload(QuizSession.participants).selectinload(SessionStudent.student)))).scalar_one_or_none()
    if sess is None:
        return HTMLResponse("الجلسة غير موجودة", status_code=404)
    parts = sorted(sess.participants, key=lambda p: p.student.full_name)
    return templates.TemplateResponse(
        "admin/session_manage.html", _ctx(request, sess=sess, parts=parts))


@router.post("/sessions/{sid}/attendance")
async def session_attendance(request: Request, sid: int):
    """يحفظ الحضور: المؤشَّرون حاضرون، والباقون غائبون (تُرفض رموزهم في الجلسة)."""
    if (g := require_admin(request)):
        return g
    form = await request.form()
    async with AsyncSessionLocal() as s:
        parts = (await s.execute(
            select(SessionStudent).where(SessionStudent.session_id == sid))).scalars().all()
        for p in parts:
            p.present = form.get(f"present_{p.id}") == "on"
        await s.commit()
    return RedirectResponse(f"/admin/sessions/{sid}", status_code=303)


@router.post("/sessions/{sid}/open")
async def session_open(request: Request, sid: int):
    if (g := require_admin(request)):
        return g
    from datetime import datetime
    async with AsyncSessionLocal() as s:
        sess = await s.get(QuizSession, sid)
        if sess:
            sess.status = "open"
            sess.opened_at = datetime.now()
            await s.commit()
    return RedirectResponse(f"/admin/sessions/{sid}/live", status_code=303)


@router.post("/sessions/{sid}/close")
async def session_close(request: Request, sid: int):
    if (g := require_admin(request)):
        return g
    from datetime import datetime
    from sqlalchemy.orm import selectinload
    async with AsyncSessionLocal() as s:
        sess = await s.get(QuizSession, sid)
        if sess:
            sess.status = "closed"
            sess.closed_at = datetime.now()
            # ح-١٢: تسليم آليّ لمسوّدات الحاضرين الذين لم يسلّموا — لئلّا تضيع أجوبتهم.
            # المغلقة تُصحَّح يقينياً وتُصادَق؛ المفتوحة تُسلَّم وتنتظر الأستاذ.
            quiz = (await s.execute(
                select(Quiz).where(Quiz.id == sess.quiz_id)
                .options(selectinload(Quiz.questions)))).scalar_one_or_none()
            q_by_id = {q.id: q for q in (quiz.questions if quiz else [])}
            parts = (await s.execute(select(SessionStudent).where(
                SessionStudent.session_id == sid,
                SessionStudent.present.is_(True),
                SessionStudent.submitted_at.is_(None)))).scalars().all()
            for part in parts:
                drafts = (await s.execute(select(Answer).where(
                    Answer.student_id == part.student_id,
                    Answer.quiz_question_id.in_(list(q_by_id)),
                    Answer.submitted.is_(False)))).scalars().all()
                for ans in drafts:
                    q = q_by_id.get(ans.quiz_question_id)
                    if q is None:
                        continue
                    graded = grade_answer(q, ans.raw)
                    ans.auto_score = graded["score"]
                    ans.teacher_confirmed = q.qtype in QUESTION_TYPES_CLOSED
                    ans.submitted = True
                if drafts:
                    part.submitted_at = datetime.now()
            await s.commit()
    # نسخة احتياطية تلقائية عند إغلاق الجلسة (أفضل جهد — لا تُفشِل الإغلاق).
    await asyncio.to_thread(try_backup_quiet)
    return RedirectResponse(f"/admin/sessions/{sid}", status_code=303)


@router.post("/sessions/{sid}/delete")
async def session_delete(request: Request, sid: int):
    """حذف جلسةٍ (تجريبيّة غير رسميّة مثلاً) مع **أثرها في التقارير**: تُحذف أجوبةُ
    المشاركين فيها لأسئلة هذا التقويم، فلا تبقى نقطُ التجربة في دفتر النقط. (لا يمسّ
    أجوبة تلاميذَ غير مشاركين، ولا أسئلة التقويم نفسه — يبقى قابلاً لإعادة التمرير.)"""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        sess = await s.get(QuizSession, sid)
        if sess is not None:
            part_ids = (await s.execute(select(SessionStudent.student_id)
                        .where(SessionStudent.session_id == sid))).scalars().all()
            qids = (await s.execute(select(QuizQuestion.id)
                    .where(QuizQuestion.quiz_id == sess.quiz_id))).scalars().all()
            if part_ids and qids:
                await s.execute(sa_delete(Answer).where(
                    Answer.student_id.in_(part_ids),
                    Answer.quiz_question_id.in_(qids)))
            await s.execute(sa_delete(QuizSession).where(QuizSession.id == sid))
            await s.commit()
    return RedirectResponse("/admin/sessions", status_code=303)


@router.post("/sessions/{sid}/merge")
async def session_merge(request: Request, sid: int):
    """يدمج كلّ جلسات نفس (التقويم + الفوج) في جلسةٍ واحدة (الأقدم) فتظهر كجلسةٍ واحدة.

    الأجوبة أصلاً مخزّنةٌ بمفتاح (تلميذ + سؤال) لا بالجلسة — فالتقارير مدموجةٌ سلفاً؛
    هذا الدمج يوحّد **سجلّات الجلسة** نفسها: يُنقَل المشاركون إلى الأقدم بلا تكرار
    (حاضرٌ إن حضر في أيّ جلسة، ويُحفَظ أبكر دخولٍ/تسليم)، وتُحذف الجلسات الأخرى.
    لا يمسّ أيّ جواب — لا يضيع شيء."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        target = await s.get(QuizSession, sid)
        if target is None:
            return RedirectResponse(
                "/admin/sessions?error=الجلسة غير موجودة.", status_code=303)
        fam = (await s.execute(select(QuizSession).where(
            QuizSession.quiz_id == target.quiz_id,
            QuizSession.group_name == target.group_name))).scalars().all()
        if len(fam) < 2:
            return RedirectResponse(
                "/admin/sessions?error=لا توجد جلسةٌ أخرى لنفس التقويم والفوج للدمج.",
                status_code=303)
        # الوجهة = الأقدم إنشاءً (حفظاً للتسلسل الزمنيّ)؛ الباقي يُدمَج فيها.
        fam.sort(key=lambda x: (x.created_at is None, x.created_at, x.id))
        dest, others = fam[0], fam[1:]
        dest_parts = (await s.execute(select(SessionStudent)
                      .where(SessionStudent.session_id == dest.id))).scalars().all()
        by_student = {p.student_id: p for p in dest_parts}
        statuses = {x.status for x in fam}
        for other in others:
            oparts = (await s.execute(select(SessionStudent)
                      .where(SessionStudent.session_id == other.id))).scalars().all()
            for p in oparts:
                keep = by_student.get(p.student_id)
                if keep is None:
                    p.session_id = dest.id                 # نقلُ مشاركٍ جديدٍ للوجهة
                    by_student[p.student_id] = p
                else:
                    # دمجُ مشاركٍ مكرّر: حاضرٌ إن حضر في أيّ جلسة؛ أبكر دخول/تسليم؛ رمز جهازٍ إن وُجد
                    keep.present = keep.present or p.present
                    if p.joined_at and (keep.joined_at is None or p.joined_at < keep.joined_at):
                        keep.joined_at = p.joined_at
                    if p.submitted_at and (keep.submitted_at is None or p.submitted_at < keep.submitted_at):
                        keep.submitted_at = p.submitted_at
                    if not keep.device_token and p.device_token:
                        keep.device_token = p.device_token
                    await s.delete(p)                       # حُذف المكرّر (قيد التفرّد)
            # توحيدُ توقيتات الوجهة: أبكر فتحٍ وأحدث إغلاق.
            if other.opened_at and (dest.opened_at is None or other.opened_at < dest.opened_at):
                dest.opened_at = other.opened_at
            if other.closed_at and (dest.closed_at is None or other.closed_at > dest.closed_at):
                dest.closed_at = other.closed_at
            await s.flush()                                 # ثبِّت نقل المشاركين قبل حذف الجلسة
            await s.delete(other)
        # حالةُ الوجهة: مفتوحةٌ إن كان أيٌّ منها مفتوحاً، وإلّا مغلقةٌ إن أُغلق أيّها.
        if "open" in statuses:
            dest.status = "open"
        elif "closed" in statuses:
            dest.status = "closed"
        await s.commit()
        merged = len(others)
    return RedirectResponse(f"/admin/sessions?merged={merged}", status_code=303)


@router.get("/sessions/{sid}/live", response_class=HTMLResponse)
async def session_live(request: Request, sid: int):
    """شاشة المتابعة الآنية (تُحدَّث ذاتيّاً عبر HTMX)."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        sess = await s.get(QuizSession, sid)
    if sess is None:
        return HTMLResponse("الجلسة غير موجودة", status_code=404)
    return templates.TemplateResponse("admin/session_live.html", _ctx(request, sess=sess))


@router.get("/sessions/{sid}/live/status", response_class=HTMLResponse)
async def session_live_status(request: Request, sid: int):
    """جزء HTMX: شبكة حالات التلاميذ + العدّادات."""
    if (g := require_admin(request)):
        return g
    from sqlalchemy.orm import selectinload
    async with AsyncSessionLocal() as s:
        sess = (await s.execute(
            select(QuizSession).where(QuizSession.id == sid)
            .options(selectinload(QuizSession.participants).selectinload(SessionStudent.student)))).scalar_one_or_none()
        if sess is None:
            return HTMLResponse("—", status_code=404)
        # تقدّم كلّ تلميذ آنيّاً: كم سؤالاً أجاب وأين وقف (آخر سؤال) من مجموع الأسئلة
        # القابلة للإجابة (تُستثنى العناوين/النصوص). يُحسب من مسوّدات الأجوبة المحفوظة.
        qqs = (await s.execute(
            select(QuizQuestion.id, QuizQuestion.qtype)
            .where(QuizQuestion.quiz_id == sess.quiz_id)
            .order_by(QuizQuestion.position))).all()
        answerable = [qid for qid, qt in qqs if qt not in DISPLAY_TYPES]
        total_q = len(answerable)
        qnum = {qid: i + 1 for i, qid in enumerate(answerable)}   # ترقيم متسلسل كالتلميذ
        # لا تُنشَأ أسطر أجوبة فارغة (ح-١٣)، فوجود سطر = إجابة فعليّة على السؤال.
        answered: dict[int, set[int]] = {}
        if answerable:
            for stu_id, qq_id in (await s.execute(
                select(Answer.student_id, Answer.quiz_question_id)
                .where(Answer.quiz_question_id.in_(answerable)))).all():
                answered.setdefault(stu_id, set()).add(qq_id)
    parts = sorted(sess.participants, key=lambda p: p.student.full_name)
    rows = []
    for p in parts:
        done_ids = answered.get(p.student_id, set())
        done = len(done_ids)
        last = max((qnum[q] for q in done_ids), default=0)   # أبعد سؤال بلغه
        rows.append({"p": p, "name": p.student.full_name, "status": _live_status(p),
                     "done": done, "total": total_q, "last": last,
                     "pct": round(done * 100 / total_q) if total_q else 0})
    present = sum(1 for p in parts if p.present)
    submitted = sum(1 for p in parts if p.submitted_at)
    return templates.TemplateResponse(
        "admin/_session_live_status.html",
        _ctx(request, sid=sid, sess=sess, rows=rows, present=present,
             submitted=submitted, total_q=total_q))


@router.post("/sessions/{sid}/unlock/{part_id}")
async def session_unlock(request: Request, sid: int, part_id: int):
    """فكّ قفل جهاز تلميذ (هاتف نفد شحنه/خروج مفاجئ) ليدخل من جديد."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        p = await s.get(SessionStudent, part_id)
        if p and p.session_id == sid:
            p.device_token = None      # يسمح بمطالبة جديدة من أي جهاز
            await s.commit()
    return RedirectResponse(f"/admin/sessions/{sid}/live", status_code=303)
