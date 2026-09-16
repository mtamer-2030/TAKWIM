"""مجال استيراد لائحة النقط الحقيقيّة (فرض/تمرين ورقيّ): رمز مسار + النقطة → تقويمٌ
بنقطةٍ مصادَقٍ عليها لكلّ تلميذ. مسارات تحت /admin (يضمّها admin.py)."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from ...database import AsyncSessionLocal
from ...importer import parse_marks_excel
from ...models import Answer, Quiz, QuizQuestion, Student
from ..web import _ctx, require_admin, templates

router = APIRouter()


@router.get("/marks", response_class=HTMLResponse)
async def marks_page(request: Request):
    """صفحة استيراد لائحة نقطٍ حقيقيّة (فرض ورقيّ) بأرقام مسار — تدخل دفتر النقط والرادار."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        groups = [r[0] for r in (await s.execute(
            select(Student.group_name).where(Student.group_name.is_not(None))
            .distinct().order_by(Student.group_name))).all()]
    return templates.TemplateResponse(
        "admin/marks.html",
        _ctx(request, groups=groups,
             ok=request.query_params.get("ok"),
             matched=request.query_params.get("matched"),
             skipped=request.query_params.get("skipped")))


@router.post("/marks/import", response_class=HTMLResponse)
async def marks_import(request: Request, title: str = Form(...),
                       max_score: float = Form(20.0), kind: str = Form("exam"),
                       group: str = Form(""), file: UploadFile = File(...)):
    """يستورد لائحة نقطٍ (رمز مسار + النقطة) لفرضٍ كامل: يُنشئ تقويماً بسؤالٍ واحدٍ
    بسلّمه، ويُسند نقطة كلّ تلميذٍ كنقطةٍ يدويّةٍ مصادَقٍ عليها. بلا أيّ تغييرٍ للمخطّط."""
    if (g := require_admin(request)):
        return g
    data = await file.read()
    parsed = parse_marks_excel(data, getattr(file, "filename", "") or "")
    async with AsyncSessionLocal() as s:
        groups = [r[0] for r in (await s.execute(
            select(Student.group_name).where(Student.group_name.is_not(None))
            .distinct().order_by(Student.group_name))).all()]
        if not parsed.ok:
            return templates.TemplateResponse(
                "admin/marks.html",
                _ctx(request, groups=groups, error="؛ ".join(parsed.errors)),
                status_code=400)
        maxs = float(max_score) if max_score and float(max_score) > 0 else 20.0
        quiz = Quiz(title=title.strip() or "فرض", kind=(kind or "exam"),
                    group_name=(group.strip() or None), published=False,
                    reveal_feedback=False)
        s.add(quiz)
        await s.flush()
        q = QuizQuestion(quiz_id=quiz.id, position=0, qtype="long_text",
                         prompt=title.strip() or "النقطة", payload={}, max_score=maxs)
        s.add(q)
        await s.flush()
        matched = 0
        skipped: list[str] = []
        for row in parsed.rows:
            st = await s.scalar(select(Student).where(
                Student.massar_code == row.massar_code))
            if st is None:
                skipped.append(row.massar_code)
                continue
            score = max(0.0, min(maxs, float(row.score)))
            s.add(Answer(quiz_question_id=q.id, student_id=st.id,
                         raw={"text": "نقطة مستورَدة"}, manual_score=score,
                         teacher_confirmed=True, submitted=True))
            matched += 1
        await s.commit()
    warn = ("&skipped=" + quote(str(len(skipped)))) if skipped else ""
    return RedirectResponse(
        f"/admin/marks?ok=1&matched={matched}{warn}", status_code=303)
