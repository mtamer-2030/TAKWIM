"""مجال التقاويم بالأسئلة وتصحيحها: الاستيراد (JSON/Word/PDF + سحابيّ/محلّي) والمراجعة
والحفظ والتحرير والحذف والإسناد والنشر، وقسم التصحيح (مصادقة + تنقيط المفتوحة + معايير).
مسارات تحت /admin (يضمّها admin.py). السلوك والمسارات لا تتغيّر بالتفكيك."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import case, delete as sa_delete, func, select

from ...ai_feedback import (AIUnavailable, extract_quiz_json, ollama_available,
                            suggest_open_score, warm_up)
from ...cloud_ai import CloudAIUnavailable, cloud_available, extract_quiz_cloud
from ...constants import (COMPETENCIES, COMPETENCY_TO_SKILL, DISPLAY_TYPES, KINDS,
                          QUESTION_TYPES_CLOSED, QUESTION_TYPES_OPEN)
from ...database import AsyncSessionLocal
from ...docx_import import parse_docx, parse_lines
from ...docx_template import build_template_docx
from ...importer import ImporterError, extract_text
from ...models import (Answer, Level, Quiz, QuizQuestion, QuizSession,
                       SessionStudent, Skill, Student)
from ...services.quizzes import (QuizImportError, attach_answer_elements, build_quiz,
                                 heuristic_quiz_from_text, normalize_quiz_json,
                                 raw_is_empty, readable_answer, resolve_skill_id)
from ..web import _ctx, read_form, require_admin, skill_id_map, templates

router = APIRouter()


def _normalize_quiz_upload(filename: str, data: bytes) -> tuple[dict | None, list[str]]:
    """يحوّل ملفّاً مرفوعاً إلى صيغة تقويم مطبّعة. يدعم JSON وWord وPDF.

    يعيد (normalized أو None، قائمة أخطاء). التحويل يقينيّ بالكامل — لا ذكاء اصطناعي.
    """
    name = (filename or "").lower()
    try:
        if name.endswith(".json"):
            payload = json.loads(data.decode("utf-8"))
            return normalize_quiz_json(payload), []
        if name.endswith(".docx"):
            r = parse_docx(data)                       # قالب Word اليقيني
            return (r.normalized, []) if r.ok else (None, r.errors)
        # PDF أو أي نصّ: نستخرج النصّ ثمّ نحلّله بمنطق القالب نفسه.
        text = extract_text(filename, data)
        r = parse_lines(text.splitlines())
        return (r.normalized, []) if r.ok else (None, r.errors)
    except QuizImportError as exc:
        return None, exc.errors
    except json.JSONDecodeError as exc:
        return None, [f"ملفّ JSON غير صالح: {exc}"]
    except ImporterError as exc:
        return None, [str(exc)]
    except Exception as exc:  # noqa: BLE001
        return None, [f"تعذّر تحويل الملفّ: {exc}"]


@router.get("/quizzes", response_class=HTMLResponse)
async def quizzes(request: Request):
    """لائحة التقاويم بالأسئلة، مع عدد الأسئلة، وإسناد الفوج/المستوى، والنشر، والحذف."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(Quiz, func.count(QuizQuestion.id))
            .join(QuizQuestion, QuizQuestion.quiz_id == Quiz.id, isouter=True)
            .group_by(Quiz.id).order_by(Quiz.created_at.desc()))).all()
        levels = (await s.execute(select(Level).order_by(Level.position))).scalars().all()
        groups = [r[0] for r in (await s.execute(
            select(Student.group_name).where(Student.group_name.is_not(None))
            .distinct().order_by(Student.group_name))).all()]
    return templates.TemplateResponse(
        "admin/quizzes.html",
        _ctx(request, rows=rows, levels=levels, groups=groups,
             error=request.query_params.get("error"),
             saved=request.query_params.get("saved")))


async def _save_normalized_quiz(normalized: dict, lid: int, group_name: str) -> tuple[str, int]:
    """يبني التقويم من صيغة مطبّعة ويحفظه؛ يعيد (العنوان، عدد الأسئلة)."""
    skills = await skill_id_map()
    async with AsyncSessionLocal() as s:
        quiz = build_quiz(normalized, level_id=lid,
                          group_name=group_name.strip() or None, skill_ids=skills)
        s.add(quiz)
        await s.commit()
        return normalized["title"], len(quiz.questions)


def _review_page(request: Request, *, title: str, kind: str, questions: list[dict],
                 level_id: str, group_name: str, note: str = "",
                 errors: list[str] | None = None, raw_text: str = "",
                 answers_text: str = "", ai_online: bool | None = None):
    """صفحة مراجعة الاستيراد: عنوان + نوع + أسئلة قابلة للتحرير (بلا JSON)."""
    if ai_online is None:
        ai_online = ollama_available()
    return templates.TemplateResponse(
        "admin/quiz_import_review.html",
        _ctx(request, r_title=title, r_kind=kind, questions=questions,
             level_id=level_id, group_name=group_name, note=note,
             errors=errors or [], raw_text=raw_text, answers_text=answers_text,
             ai_online=ai_online, cloud_online=cloud_available(),
             competencies=list(COMPETENCIES.items())))


_AI_TYPE_MAP = {"heading": "heading", "passage": "passage",
                "mcq_single": "mcq_single", "mcq_multi": "mcq_multi",
                "long_text": "long_text", "short_text": "short_text",
                "essay": "long_text", "mcq": "mcq_single", "text": "passage",
                "title": "heading"}


def _ai_json_to_questions(raw_json: str) -> tuple[str, list[dict]]:
    """يحوّل مخرَج المحرّك (JSON) إلى (عنوان، أسئلة للمراجعة). متسامح مع نقص المخطّط."""
    data = json.loads(raw_json)
    if isinstance(data, list):
        data = {"questions": data}
    title = str(data.get("title") or "").strip() or "تقويم مستورد"
    out: list[dict] = []
    for q in (data.get("questions") or []):
        if not isinstance(q, dict):
            continue
        prompt = str(q.get("prompt") or q.get("text") or q.get("question") or "").strip()
        if not prompt:
            continue
        qtype = _AI_TYPE_MAP.get(str(q.get("type") or "").strip().lower(), "long_text")
        try:
            ms = max(0.0, float(q.get("max_score") or (0 if qtype in DISPLAY_TYPES else 4)))
        except (TypeError, ValueError):
            ms = 0.0 if qtype in DISPLAY_TYPES else 4.0
        comp = str(q.get("competency") or "").strip()
        elems = [str(e).strip() for e in (q.get("elements") or []) if str(e).strip()]
        item = {"type": qtype, "prompt": prompt, "max_score": ms,
                "options": [], "correct": [],
                "competency": comp if comp in COMPETENCIES else None, "elements": elems}
        if qtype in ("mcq_single", "mcq_multi"):
            opts = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
            raw_correct = q.get("correct")
            if isinstance(raw_correct, int):
                raw_correct = [raw_correct]
            correct = [c for c in (raw_correct or [])
                       if isinstance(c, int) and 0 <= c < len(opts)]
            if len(opts) < 2:                    # اختيار بلا خيارات كافية → مفتوح
                item["type"] = "long_text"
            else:
                item["options"] = opts
                item["correct"] = correct[:1] if qtype == "mcq_single" else correct
        out.append(item)
    return title, out


def _extract_upload_text(filename: str, data: bytes) -> str:
    try:
        return extract_text(filename, data).text
    except Exception:  # noqa: BLE001 — txt أو أيّ ملفّ قابل للفكّ نصّاً
        return data.decode("utf-8", "replace")


@router.post("/quizzes/import")
async def quizzes_import(request: Request, file: UploadFile = File(...),
                         answers_file: UploadFile | None = File(None),
                         level_id: str = Form(""), group_name: str = Form("")):
    """رفع تقويم (JSON/Word/PDF/صورة) + ملفّ عناصر إجابة اختياريّ → أسئلة.

    (١) يقينيّ — JSON/قالب Word يُحلَّل حرفيّاً فيُحفَظ مباشرةً.
    (٢) ذكيّ سحابيّ — إن هُيّئ مفتاح Claude: يحوّل **أيّ بنية** فرضٍ إلى أسئلة مع
        الأجوبة الصحيحة ومؤشّرات النجاح (وقت التحضير، يحتاج إنترنت)، للمراجعة.
    (٣) متسامح — وإلّا: تحليل قاعديّ يقينيّ (بلا اتصال) + إسناد عناصر الإجابة بالترقيم."""
    if (g := require_admin(request)):
        return g
    data = await file.read()
    lid = int(level_id) if level_id.strip().isdigit() else None
    if lid is None:
        # ح-٢: لا يُحفَظ تقويم بلا مستوى (لئلّا يُنشَر لاحقاً فيتسرّب لكلّ المستويات).
        return RedirectResponse(
            "/admin/quizzes?error=اختر المستوى قبل الاستيراد.", status_code=303)

    # (١) المسار اليقينيّ: JSON صالح أو قالب Word/PDF يُحلَّل حرفيّاً → حفظ مباشر.
    normalized, errors = _normalize_quiz_upload(file.filename, data)
    if normalized is not None:
        title, n = await _save_normalized_quiz(normalized, lid, group_name)
        return RedirectResponse(
            f"/admin/quizzes?saved=تمّ استيراد «{title}» بـ{n} سؤالاً.", status_code=303)

    import os
    raw = _extract_upload_text(file.filename, data)
    hint = os.path.splitext(os.path.basename(file.filename or ""))[0]
    # ملفّ عناصر الإجابة (اختياريّ) — نصّه يُمرَّر للمحرّك لبناء مؤشّرات التصحيح.
    answers_text = ""
    afn = getattr(answers_file, "filename", None)   # تجاهُل قيمة File(None) الافتراضيّة
    if afn:
        answers_text = _extract_upload_text(afn, await answers_file.read())

    # (٢) الاستيراد الذكيّ السحابيّ (وقت التحضير، يحتاج إنترنت): يعالج **أيّ بنية** فرضٍ
    #     — عناوين ونصوص وأسئلة وأجوبة صحيحة ومؤشّرات — عبر نموذج Claude قويّ. يُشغَّل
    #     تلقائيّاً حين يكون المفتاح مهيّأً. إن تعذّر، نمرّ للتحليل القاعديّ (لا يضيع العمل).
    cloud_note = ""
    if cloud_available():
        try:
            title, questions = _ai_json_to_questions(
                await asyncio.to_thread(extract_quiz_cloud, raw, answers_text))
            if not questions:
                raise CloudAIUnavailable("لم يُرجِع النموذج أسئلة صالحة.")
            note = ("هيكلة ذكيّة لأيّ بنية فرضٍ: أسئلة مفتوحة/مغلقة مع الأجوبة الصحيحة "
                    "ومؤشّرات النجاح. راجِعها وصادِق عليها قبل الحفظ.")
            return _review_page(request, title=title, kind="exercise", questions=questions,
                                level_id=level_id, group_name=group_name, note=note,
                                raw_text=raw, answers_text=answers_text)
        except (CloudAIUnavailable, ValueError, json.JSONDecodeError) as exc:
            cloud_note = f"تعذّر الاستيراد الذكيّ السحابيّ: {exc}"

    # (٣) المسار اليقينيّ (فوريّ، بلا اتصال): تحليل قاعديّ يحفظ النصّ الفلسفيّ والعناوين
    # ويكشف الاختيار. إن رُفِع ملفّ عناصر الإجابة أُسنِدت عناصره للأسئلة المفتوحة بالترقيم.
    parsed = heuristic_quiz_from_text(raw, title_hint=hint)
    if not parsed["questions"]:
        return RedirectResponse(
            "/admin/quizzes?error=تعذّر إيجاد نصّ أسئلة في الملفّ. جرّب ملفّاً آخر أو القالب.",
            status_code=303)
    errs = [cloud_note] if cloud_note else None
    if answers_text.strip():
        matched = attach_answer_elements(parsed["questions"], answers_text)
        if matched:
            note = (f"استُخرج الفرض ونصوصه، وأُسنِدت عناصر الإجابة لـ{matched} سؤالاً مفتوحاً "
                    "مؤشّراتٍ للتصحيح الآليّ. راجِعها، أشّر الصواب في الاختيار، واختر "
                    "الكفاية، ثمّ احفظ.")
        else:
            note = ("استُخرج الفرض ونصوصه. تعذّر مطابقة ترقيم عناصر الإجابة بالأسئلة "
                    "آليّاً — انسخ العناصر في خانة «عناصر الإجابة» لكلّ سؤال مفتوح ثمّ احفظ.")
        return _review_page(request, title=parsed["title"], kind=parsed["kind"],
                            questions=parsed["questions"], level_id=level_id,
                            group_name=group_name, note=note, errors=errs,
                            raw_text=raw, answers_text=answers_text)

    note = ("استُخرج الملفّ فوريّاً: خانات الاختيار، العناوين والنصوص الفلسفيّة، حذف "
            "أسطر الإجابة، واستخراج النقط. في الاختيار **أشّر الصواب**؛ وفي المفتوحة "
            "اكتب **عناصر الإجابة** واختر **الكفاية**. (للهيكلة الذكيّة لأيّ بنية: "
            "هيّئ مفتاح Claude في config.ini ثمّ اضغط «استخراج ذكيّ».)")
    return _review_page(request, title=parsed["title"], kind=parsed["kind"],
                        questions=parsed["questions"], level_id=level_id,
                        group_name=group_name, note=note, errors=errs, raw_text=raw)


@router.post("/quizzes/import/ai")
async def quizzes_import_ai(request: Request):
    """استخراج ذكيّ يعيد هيكلة نصّ الفرض لأيّ بنية ويعرضها للمراجعة.

    يفضّل المحرّك السحابيّ القويّ (Claude) إن كان المفتاح مهيّأً — يعالج أيّ بنية؛
    وإلّا يجرّب المحرّك المحلّي. تدهور لطيف: عند التعذّر يبقى التحليل القاعديّ ويُعرَض السبب."""
    if (g := require_admin(request)):
        return g
    form = await read_form(request)
    raw = form.get("raw_text") or ""
    answers_text = form.get("answers_text") or ""
    level_id = (form.get("level_id") or "").strip()
    group_name = form.get("group_name") or ""

    # (١) السحابيّ أوّلاً إن هُيّئ — يعالج أيّ بنية بدقّة وسرعة.
    if cloud_available():
        try:
            title, questions = _ai_json_to_questions(
                await asyncio.to_thread(extract_quiz_cloud, raw, answers_text))
            if not questions:
                raise CloudAIUnavailable("لم يُرجِع النموذج أسئلة صالحة.")
            note = ("هيكلة ذكيّة لأيّ بنية — أسئلة مع الأجوبة الصحيحة ومؤشّرات النجاح. "
                    "راجِعها وصادِق عليها قبل الحفظ.")
            return _review_page(request, title=title, kind="exercise", questions=questions,
                                level_id=level_id, group_name=group_name, note=note,
                                raw_text=raw, answers_text=answers_text)
        except (CloudAIUnavailable, json.JSONDecodeError, ValueError) as exc:
            cloud_err = f"تعذّر الاستيراد الذكيّ السحابيّ: {exc}"
    else:
        cloud_err = ""

    # (٢) المحلّي (Ollama) إن توفّر — أبطأ وأضعف، لكن بلا إنترنت.
    try:
        title, questions = _ai_json_to_questions(
            await asyncio.to_thread(extract_quiz_json, raw, answers_text))
        if not questions:
            raise AIUnavailable("لم يُرجِع المحرّك أسئلة صالحة.")
        note = "هيكلة المحرّك المحلّي — راجِعها وصحّحها وأشّر الصواب قبل الحفظ."
        return _review_page(request, title=title, kind="exercise", questions=questions,
                            level_id=level_id, group_name=group_name, note=note,
                            raw_text=raw, answers_text=answers_text)
    except (AIUnavailable, json.JSONDecodeError, ValueError) as exc:
        # (٣) نرجع للتحليل القاعديّ مع رسالة واضحة (لا نفقد عمل الأستاذ).
        parsed = heuristic_quiz_from_text(raw, title_hint="")
        errs = [e for e in (cloud_err, f"تعذّر الاستخراج المحلّي: {exc}") if e]
        return _review_page(
            request, title=parsed["title"], kind="exercise",
            questions=parsed["questions"], level_id=level_id, group_name=group_name,
            raw_text=raw, answers_text=answers_text, errors=errs,
            note="عُرِض التحليل القاعديّ. هيّئ مفتاح Claude للهيكلة الذكيّة لأيّ بنية.")


_REVIEW_OPEN = ("long_text", "short_text")
_REVIEW_MCQ = ("mcq_single", "mcq_multi")


def _parse_review_form(form) -> tuple[str, str, list[dict], list[str]]:
    """يقرأ نموذج المراجعة → (العنوان، النوع، أسئلة للعرض، أخطاء).

    كلّ سؤال في القائمة يحمل type/prompt/max_score وoptions/correct (للاختيار)، فتُعاد
    الصفحة بالتحرير نفسه إن وُجد خطأ. الأخطاء تخصّ الاختيار (خيارات ناقصة/بلا صحيح)."""
    title = (form.get("r_title") or "").strip() or "تقويم مستورد"
    kind = form.get("r_kind") or "exercise"
    if kind not in KINDS:
        kind = "exercise"
    try:
        count = int(form.get("count") or 0)
    except ValueError:
        count = 0
    questions: list[dict] = []
    errors: list[str] = []
    for i in range(count):
        prompt = (form.get(f"q_prompt_{i}") or "").strip()
        if not prompt:                          # صفّ حُذف نصّه → يُتجاهَل
            continue
        qtype = form.get(f"q_type_{i}") or "long_text"
        if qtype == "skip":                     # سطر يُتجاهَل — لا يُحفَظ إطلاقاً
            continue
        if qtype in DISPLAY_TYPES:              # عنوان/نصّ للقراءة — يُعرَض بلا تصحيح
            questions.append({"type": qtype, "prompt": prompt, "max_score": 0.0,
                              "options": [], "correct": [], "competency": None,
                              "elements": []})
            continue
        if qtype not in _REVIEW_OPEN and qtype not in _REVIEW_MCQ:
            qtype = "long_text"
        try:
            ms = max(0.0, float(form.get(f"q_score_{i}") or 4))
        except ValueError:
            ms = 4.0
        comp = form.get(f"q_comp_{i}") or ""
        elems = [ln.strip() for ln in (form.get(f"q_elem_{i}") or "").splitlines()
                 if ln.strip()]
        q = {"type": qtype, "prompt": prompt, "max_score": ms,
             "options": [], "correct": [],
             "competency": comp if comp in COMPETENCIES else None,
             "elements": elems}
        if qtype in _REVIEW_MCQ:
            try:
                oc = int(form.get(f"q_optcount_{i}") or 0)
            except ValueError:
                oc = 0
            correct_raw = {int(v) for v in form.getlist(f"q_correct_{i}") if v.isdigit()}
            for k in range(oc):
                opt = (form.get(f"q_opt_{i}_{k}") or "").strip()
                if not opt:
                    continue
                if k in correct_raw:
                    q["correct"].append(len(q["options"]))
                q["options"].append(opt)
            where = f"السؤال {len(questions) + 1}"
            if len(q["options"]) < 2:
                errors.append(f"{where}: سؤال الاختيار يحتاج خيارين على الأقلّ.")
            elif not q["correct"]:
                errors.append(f"{where}: أشّر الإجابة الصحيحة.")
            elif qtype == "mcq_single":
                q["correct"] = [q["correct"][0]]   # واحدة فقط للاختيار الأحاديّ
        questions.append(q)
    return title, kind, questions, errors


def _to_normalized_question(q: dict) -> dict:
    """يحوّل سؤال المراجعة إلى صيغة build_quiz (payload الاختيار + عناصر إجابة المفتوحة)."""
    qtype = q["type"]
    payload: dict = {}
    if qtype == "mcq_single":
        payload = {"options": q["options"], "correct": q["correct"][0]}
    elif qtype == "mcq_multi":
        payload = {"options": q["options"], "correct": q["correct"]}
    # عناصر الإجابة (للأسئلة المفتوحة) → مؤشّرات يصحّح بها الذكاء الاصطناعيّ لاحقاً.
    ms = float(q["max_score"])
    elems = q.get("elements") or []
    per = round(ms / len(elems), 2) if elems else 0
    indicators = [{"text": t, "points": per} for t in elems]
    return {"type": qtype, "competency": q.get("competency"), "prompt": q["prompt"],
            "stimulus": None, "max_score": ms, "payload": payload,
            "indicators": indicators, "penalties": [],
            "auto_scored": qtype in QUESTION_TYPES_CLOSED}


@router.post("/quizzes/import/save")
async def quizzes_import_save(request: Request):
    """يحفظ التقويم بعد مراجعة الأستاذ لأسئلته المستخرَجة وتحريرها (وتأشير الصواب)."""
    if (g := require_admin(request)):
        return g
    form = await read_form(request)
    level_id = (form.get("level_id") or "").strip()
    group_name = form.get("group_name") or ""
    lid = int(level_id) if level_id.isdigit() else None
    if lid is None:
        return RedirectResponse(
            "/admin/quizzes?error=اختر المستوى قبل الحفظ.", status_code=303)

    title, kind, questions, errors = _parse_review_form(form)
    if not questions:
        errors.append("أضِف نصّ سؤال واحد على الأقلّ قبل الحفظ.")
    if errors:
        if not questions:
            questions = [{"type": "long_text", "prompt": "", "max_score": 4.0,
                          "options": [], "correct": []}]
        return _review_page(request, title=title, kind=kind, questions=questions,
                            level_id=level_id, group_name=group_name, errors=errors,
                            raw_text=form.get("raw_text") or "")

    normalized = {"title": title, "kind": kind, "level": None, "unit": None,
                  "concept": None, "stimuli": [],
                  "questions": [_to_normalized_question(q) for q in questions]}
    saved_title, n = await _save_normalized_quiz(normalized, lid, group_name)
    return RedirectResponse(
        f"/admin/quizzes?saved=تمّ استيراد «{saved_title}» بـ{n} سؤالاً.", status_code=303)


@router.get("/grading", response_class=HTMLResponse)
async def grading_center(request: Request):
    """قسم التصحيح: كلّ تقويم فيه أجوبة مُسلَّمة، مع عدد المُسلَّم والمنتظِر للمصادقة،
    ورابطٌ مباشر لشاشة تصحيحه. مركزٌ واضح بدل أزرار متفرّقة."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(Quiz.id, Quiz.title, Quiz.group_name,
                   func.count(Answer.id),
                   func.sum(case((Answer.teacher_confirmed.is_(False), 1), else_=0)))
            .join(QuizQuestion, QuizQuestion.quiz_id == Quiz.id)
            .join(Answer, Answer.quiz_question_id == QuizQuestion.id)
            .where(Answer.submitted.is_(True))
            .group_by(Quiz.id).order_by(Quiz.created_at.desc()))).all()
    items = [{"id": r[0], "title": r[1], "group": r[2], "submitted": r[3],
              "pending": int(r[4] or 0)} for r in rows]
    # ما يحتاج تصحيحاً فعلاً (منتظِرٌ للمصادقة) منفصلٌ عمّا اكتمل — فلا يظهر المصحَّح
    # ثانيةً في طابور العمل. المكتمل يبقى للمراجعة فقط (نتائجه محفوظةٌ في التقارير).
    pending_items = [it for it in items if it["pending"] > 0]
    done_items = [it for it in items if it["pending"] == 0]
    return templates.TemplateResponse(
        "admin/grading.html",
        _ctx(request, items=pending_items, done_items=done_items,
             cleared=request.query_params.get("cleared")))


@router.post("/grading/{quiz_id}/clear")
async def grading_clear_answers(request: Request, quiz_id: int):
    """يحذف **كلّ أجوبة** هذا التقويم (أيّاً كان مصدرها) — لإزالة أجوبةٍ عالقةٍ ناتجةٍ
    عن جلساتٍ مشوّهة أو غير مكتملة أو محذوفة. لا يمسّ أسئلة التقويم نفسه، فيبقى قابلاً
    لإعادة التمرير من جديد. لا رجعة في حذف الأجوبة."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        qids = select(QuizQuestion.id).where(QuizQuestion.quiz_id == quiz_id)
        res = await s.execute(sa_delete(Answer).where(Answer.quiz_question_id.in_(qids)))
        await s.commit()
    n = res.rowcount if res.rowcount is not None else 0
    return RedirectResponse(f"/admin/grading?cleared={n}", status_code=303)


@router.get("/quizzes/template")
async def quiz_template(request: Request):
    """تنزيل قالب Word لتأليف تقويم يدوياً (ميزة أُعيدت بعد أن ضاعت في الانتقال، ٥-د)."""
    if (g := require_admin(request)):
        return g
    data = await asyncio.to_thread(build_template_docx)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="philotech-quiz-template.docx"'})


@router.post("/quizzes/{quiz_id}/assign")
async def quiz_assign(request: Request, quiz_id: int, level_id: str = Form(""),
                      group_name: str = Form(""), published: str = Form("")):
    """إسناد التقويم إلى مستوى/فوج وضبط نشره (رؤية التلاميذ له)."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        quiz = await s.get(Quiz, quiz_id)
        if quiz:
            lid = int(level_id) if level_id.strip().isdigit() else None
            quiz.level_id = lid
            quiz.group_name = group_name.strip() or None
            # ح-٢: لا نشر لتقويم بلا مستوى (يمنع التسريب لكلّ المستويات).
            quiz.published = (published == "on") and lid is not None
            await s.commit()
            if published == "on" and lid is None:
                return RedirectResponse(
                    "/admin/quizzes?error=لا يمكن نشر تقويم بلا مستوى — اختر المستوى أوّلاً.",
                    status_code=303)
    return RedirectResponse("/admin/quizzes", status_code=303)


def _correct_set(value) -> set:
    """يطبّع فهارس الأجوبة الصحيحة إلى مجموعة، مهما كان تخزينها: قائمة، رقمٌ مفرد
    (بعض التقويمات تخزّن correct=2 لا [2])، أو غياب."""
    if isinstance(value, bool):
        return set()
    if isinstance(value, int):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return {v for v in value if isinstance(v, int) and not isinstance(v, bool)}
    return set()


def _opts_text(q: QuizQuestion) -> str:
    """يبني نصّ الخيارات للمحرّر: سطرٌ لكلّ خيار، والصحيح مسبوقٌ بنجمة (*).
    متسامحٌ مع بنياتٍ مختلفة (خيارات رقميّة، correct رقمٌ مفرد، payload غير قاموسيّ)."""
    payload = q.payload if isinstance(q.payload, dict) else {}
    options = payload.get("options")
    if not isinstance(options, (list, tuple)):
        options = []
    correct = _correct_set(payload.get("correct"))
    lines = []
    for i, o in enumerate(options):
        text = str(o)
        lines.append(("* " + text) if i in correct else text)
    return "\n".join(lines)


@router.get("/quizzes/{quiz_id}/edit", response_class=HTMLResponse)
async def quiz_edit_page(request: Request, quiz_id: int):
    """محرّر التقويم المحفوظ: تعديل العنوان ونصوص الأسئلة والنصّ المرافق والسلّم
    وخيارات الاختيار، وحذف سؤال — لإصلاح أخطاءٍ بعد الحفظ بلا إعادة استيراد."""
    if (g := require_admin(request)):
        return g
    from sqlalchemy.orm import selectinload
    try:
        async with AsyncSessionLocal() as s:
            quiz = (await s.execute(
                select(Quiz).where(Quiz.id == quiz_id)
                .options(selectinload(Quiz.questions)))).scalar_one_or_none()
            if quiz is None:
                return HTMLResponse("التقويم غير موجود", status_code=404)
            qs = sorted(quiz.questions, key=lambda q: (q.position, q.id))
            items = [{
                "q": q,
                "is_mcq": q.qtype in ("mcq_single", "mcq_multi"),
                "is_display": q.qtype in DISPLAY_TYPES,
                "opts_text": _opts_text(q),
            } for q in qs]
        return templates.TemplateResponse(
            "admin/quiz_edit.html",
            _ctx(request, quiz=quiz, items=items,
                 saved=request.query_params.get("saved")))
    except Exception as exc:  # noqa: BLE001 — بدل «Internal Server Error» الغامض: أظهر السبب
        import html as _html
        import traceback as _tb
        tb = _html.escape(_tb.format_exc())
        return HTMLResponse(
            "<div dir='rtl' style='font-family:sans-serif;padding:1.5rem;max-width:60rem;margin:auto'>"
            "<h2 style='color:#b71c1c'>تعذّر فتح محرّر هذا التقويم</h2>"
            f"<p><b>السبب:</b> {_html.escape(type(exc).__name__)}: {_html.escape(str(exc))}</p>"
            "<p>أرسِل هذا النصّ للمطوّر لإصلاحه فوراً، أو استعمل «حذف» لهذا التقويم "
            "وأعِد استيراده. بقيّة التقويمات غير متأثّرة.</p>"
            f"<pre style='background:#f6f8fa;padding:1rem;overflow:auto;font-size:.8rem'>{tb}</pre>"
            "<a href='/admin/quizzes'>→ رجوع إلى التقويمات</a></div>",
            status_code=200)


@router.post("/quizzes/{quiz_id}/edit")
async def quiz_edit_save(request: Request, quiz_id: int):
    """يطبّق تعديلات المحرّر على التقويم المحفوظ."""
    if (g := require_admin(request)):
        return g
    from sqlalchemy.orm import selectinload
    form = await read_form(request)
    async with AsyncSessionLocal() as s:
        quiz = (await s.execute(
            select(Quiz).where(Quiz.id == quiz_id)
            .options(selectinload(Quiz.questions)))).scalar_one_or_none()
        if quiz is None:
            return HTMLResponse("التقويم غير موجود", status_code=404)
        title = (form.get("title") or "").strip()
        if title:
            quiz.title = title
        by_id = {q.id: q for q in quiz.questions}
        for qid_str in (form.get("qids") or "").split(","):
            if not qid_str.strip().isdigit():
                continue
            q = by_id.get(int(qid_str))
            if q is None:
                continue
            if form.get(f"q_{q.id}_delete") == "on":
                await s.delete(q)
                continue
            prompt = (form.get(f"q_{q.id}_prompt") or "").strip()
            if prompt:
                q.prompt = prompt
            stim = (form.get(f"q_{q.id}_stimulus") or "").strip()
            q.stimulus = stim or None
            ms = form.get(f"q_{q.id}_max")
            if ms not in (None, ""):
                try:
                    q.max_score = max(0.0, float(str(ms).replace(",", ".")))
                except ValueError:
                    pass
            if q.qtype in ("mcq_single", "mcq_multi"):
                options, correct = [], []
                for line in (form.get(f"q_{q.id}_options") or "").splitlines():
                    t = line.strip()
                    if not t:
                        continue
                    is_c = t.startswith("*")
                    t = t[1:].strip() if is_c else t
                    if not t:
                        continue
                    if is_c:
                        correct.append(len(options))
                    options.append(t)
                if len(options) >= 2 and correct:
                    payload = dict(q.payload or {})
                    payload["options"] = options
                    payload["correct"] = correct
                    q.payload = payload            # إسناد قاموسٍ جديد يُعلِّم العمود متغيّراً
        await s.commit()
    return RedirectResponse(f"/admin/quizzes/{quiz_id}/edit?saved=1", status_code=303)


@router.post("/quizzes/{quiz_id}/delete")
async def quiz_delete(request: Request, quiz_id: int):
    """حذف تقويم وكلّ أسئلته وأجوبته بالتتالي."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        await s.execute(sa_delete(Quiz).where(Quiz.id == quiz_id))
        await s.commit()
    return RedirectResponse("/admin/quizzes", status_code=303)


# ═══════════════ تصحيح أجوبة التلاميذ (مصادقة + تنقيط المفتوحة) ═══════════════


async def _grade_view(s, quiz_id: int):
    """يبني بيانات شاشة التصحيح (quiz + questions) — مصدر واحد يشترك فيه العرض
    والحفظ الجزئيّ (HTMX)، فلا يفترق ما يُعرَض عمّا يُعاد بعد الحفظ."""
    from sqlalchemy.orm import selectinload
    quiz = (await s.execute(
        select(Quiz).where(Quiz.id == quiz_id)
        .options(selectinload(Quiz.questions)))).scalar_one_or_none()
    if quiz is None:
        return None, None
    rows = (await s.execute(
        select(Answer, Student)
        .join(Student, Student.id == Answer.student_id)
        .join(QuizQuestion, QuizQuestion.id == Answer.quiz_question_id)
        .where(QuizQuestion.quiz_id == quiz_id)
        .order_by(QuizQuestion.position, Student.full_name))).all()
    # قائمة المتوقَّع منهم الإجابة (ح-١٢: لتمييز «لم يجب»): مشاركو جلسات هذا
    # التقويم الحاضرون + تلاميذ فوجه إن كان منزليّاً.
    expected: dict[int, str] = {}
    for sid_, name in (await s.execute(
            select(Student.id, Student.full_name)
            .join(SessionStudent, SessionStudent.student_id == Student.id)
            .join(QuizSession, QuizSession.id == SessionStudent.session_id)
            .where(QuizSession.quiz_id == quiz_id,
                   SessionStudent.present.is_(True)))).all():
        expected[sid_] = name
    if quiz.group_name:
        for sid_, name in (await s.execute(
                select(Student.id, Student.full_name).where(
                    Student.group_name == quiz.group_name,
                    Student.active.is_(True)))).all():
            expected[sid_] = name
    # تجميع الأجوبة حسب السؤال، مع نصّ مقروء وحالة الإغلاق والتسليم (سلّم/مسوّدة).
    by_q: dict[int, list] = {}
    answered: dict[int, set] = {}
    for ans, student in rows:
        q = next((qq for qq in quiz.questions if qq.id == ans.quiz_question_id), None)
        if q is None:
            continue
        by_q.setdefault(q.id, []).append({
            "ans": ans, "student": student,
            "readable": readable_answer(q, ans.raw),
            "effective": ans.manual_score if ans.manual_score is not None else ans.auto_score,
            "submitted": ans.submitted,          # سلّم؟ أم مسوّدة لم تُسلَّم؟
            "blank": raw_is_empty(ans.raw),      # «لا جواب» → يُعرَض بنقطة 0 جاهزة
        })
        answered.setdefault(q.id, set()).add(student.id)
    # لكلّ سؤال: من لم يجب (متوقَّع بلا أيّ جواب) — الحالة الثالثة.
    # عناصر العرض (عنوان/نصّ) لا تُصحَّح فلا تظهر في شاشة التصحيح.
    questions = []
    for q in quiz.questions:
        if q.qtype in DISPLAY_TYPES:
            continue
        missing = [name for sid_, name in expected.items()
                   if sid_ not in answered.get(q.id, set())]
        questions.append({"q": q, "closed": q.qtype in QUESTION_TYPES_CLOSED,
                          "answers": by_q.get(q.id, []), "missing": missing})
    return quiz, questions


@router.get("/quizzes/{quiz_id}/grade", response_class=HTMLResponse)
async def quiz_grade_page(request: Request, quiz_id: int):
    """شاشة تصحيح: لكلّ سؤال أجوبة التلاميذ. المغلقة مصحّحة آليّاً (تُصادَق)،
    والمفتوحة يُدخل الأستاذ نقطتها ويصادق. تغذّي التقارير والذكاء الاصطناعي."""
    if (g := require_admin(request)):
        return g
    async with AsyncSessionLocal() as s:
        quiz, questions = await _grade_view(s, quiz_id)
        if quiz is None:
            return HTMLResponse("التقويم غير موجود", status_code=404)
        # محرّر المعايير للأسئلة المفتوحة فقط (الكفاية + مؤشّرات النجاح للتصحيح الآليّ).
        rubric = [await _rubric_item(s, it["q"]) for it in questions if not it["closed"]]
    online = ollama_available()
    if online and any(not it["closed"] for it in questions):
        # تحميلٌ مسبقٌ للنموذج في الخلفيّة (لا يوقف عرض الصفحة) فيكون أوّلُ اقتراحٍ سريعاً.
        asyncio.create_task(asyncio.to_thread(warm_up))
    return templates.TemplateResponse(
        "admin/quiz_grade.html",
        _ctx(request, quiz=quiz, questions=questions, rubric=rubric,
             competencies=list(COMPETENCIES.items()),
             online=online,
             ai_msg=request.query_params.get("ai"),
             warn=request.query_params.get("warn"),
             saved=request.query_params.get("saved")))


@router.post("/quizzes/{quiz_id}/grade/ai")
async def quiz_grade_ai(request: Request, quiz_id: int):
    """التصحيح المسائي المُعان: يقترح المحرّك المحلّي (Ollama) نقط الأسئلة المفتوحة.

    اقتراحٌ لا حكم — يُملأ في حقول النقط ليراجعها الأستاذ ويصادق. تسلسليّاً،
    وتدهور لطيف إن أُغلق المحرّك. المغلقة لا تُمسّ (مصحّحة يقينيّاً)."""
    if (g := require_admin(request)):
        return g
    if not ollama_available():
        return RedirectResponse(
            f"/admin/quizzes/{quiz_id}/grade?ai=المحرّك المحلّي غير مشغّل — شغّل Ollama.",
            status_code=303)
    from sqlalchemy.orm import selectinload
    suggested = 0
    skipped = 0
    async with AsyncSessionLocal() as s:
        quiz = (await s.execute(
            select(Quiz).where(Quiz.id == quiz_id)
            .options(selectinload(Quiz.questions)))).scalar_one_or_none()
        if quiz is None:
            return HTMLResponse("التقويم غير موجود", status_code=404)
        open_qs = {q.id: q for q in quiz.questions
                   if q.qtype not in QUESTION_TYPES_CLOSED
                   and q.qtype not in DISPLAY_TYPES}
        if open_qs:
            answers = (await s.execute(
                select(Answer).where(Answer.quiz_question_id.in_(list(open_qs))))).scalars().all()
            for ans in answers:
                # ح-١٠: لا يُكتب اقتراحٌ على جوابٍ نقّطه الأستاذ يدوياً أو صادق عليه —
                # الاقتراح مساعدةٌ للأجوبة غير المصحّحة فقط، لا يمحو عمل الأستاذ.
                if ans.manual_score is not None or ans.teacher_confirmed:
                    skipped += 1
                    continue
                q = open_qs[ans.quiz_question_id]
                answer_text = (ans.raw or {}).get("text", "") if isinstance(ans.raw, dict) else ""
                guidance = "\n".join(
                    f"- {i.get('text', '')} ({i.get('points', 0)} ن)"
                    for i in (q.indicators or []))
                try:
                    score, _note = suggest_open_score(q.prompt, guidance, answer_text, q.max_score)
                except AIUnavailable:
                    break
                ans.manual_score = score      # اقتراح؛ يبقى غير مصادَق حتى يراجعه الأستاذ
                suggested += 1
        await s.commit()
    msg = f"اقتُرحت {suggested} نقطة للأسئلة المفتوحة — راجِعها وصادِق."
    if skipped:
        msg += f" وتُركت {skipped} نقطة مصحّحة يدوياً كما هي."
    return RedirectResponse(f"/admin/quizzes/{quiz_id}/grade?ai={msg}", status_code=303)


@router.post("/quizzes/{quiz_id}/grade")
async def quiz_grade_save(request: Request, quiz_id: int):
    """يحفظ نقط الأستاذ للمفتوحة ويصادق على الأجوبة المؤشَّرة.

    ٤-ب: لا يمسّ إلّا الأجوبة المعروضة فعلاً في الصفحة (حقل shown) — فجوابٌ سلّمه
    تلميذ متأخّراً بعد فتح الشاشة لا يُلغى تصديقه سهواً (سباق التسليم المتأخّر).
    ولا يُصادَق على جوابٍ مفتوح بلا نقطة (تفادي صفر صامت)."""
    if (g := require_admin(request)):
        return g
    # قسمٌ كامل × أسئلة × (shown+score+confirm) قد يتجاوز حدّ Starlette الافتراضيّ (1000
    # حقل) فيُرفَض التصحيح كلّه بـ«Too many fields». نرفع الحدّ ليتّسع لأكبر قسم.
    form = await read_form(request)
    shown: set[int] = set()
    for v in form.getlist("shown"):
        try:
            shown.add(int(v))
        except (TypeError, ValueError):
            pass
    confirmed = 0
    refused = 0                 # مصادقةٌ طُلبت لكن رُفضت (مفتوحٌ بلا نقطة) — نُبلِّغ عنها
    async with AsyncSessionLocal() as s:
        if shown:
            answers = (await s.execute(
                select(Answer)
                .join(QuizQuestion, QuizQuestion.id == Answer.quiz_question_id)
                .where(QuizQuestion.quiz_id == quiz_id,
                       Answer.id.in_(shown)))).scalars().all()
            qmap = {q.id: q for q in (await s.execute(
                select(QuizQuestion).where(QuizQuestion.quiz_id == quiz_id))).scalars().all()}
            for ans in answers:
                q = qmap.get(ans.quiz_question_id)
                is_closed = q is not None and q.qtype in QUESTION_TYPES_CLOSED
                is_open = q is not None and q.qtype in QUESTION_TYPES_OPEN
                maxs = float(q.max_score) if (q is not None and q.max_score) else None
                raw_score = form.get(f"score_{ans.id}")
                if raw_score not in (None, ""):
                    try:
                        # متسامحٌ مع الفاصلة (3,5 أو 3.5)، رقمان بعدها، ومحصورٌ في [0..السلّم].
                        val = float(str(raw_score).replace(",", ".").strip())
                        if maxs is not None:
                            val = max(0.0, min(maxs, val))
                        ans.manual_score = round(val, 2)
                    except ValueError:
                        pass
                # «لا جواب» في سؤالٍ مفتوحٍ بلا نقطةٍ مُدخَلة → 0 تلقائيّاً (مباشرةً).
                auto_zero = is_open and ans.manual_score is None and raw_is_empty(ans.raw)
                if auto_zero:
                    ans.manual_score = 0.0
                want = form.get(f"confirm_{ans.id}") == "on"
                eff = ans.manual_score if ans.manual_score is not None else ans.auto_score
                # مصادقةٌ إن كان مغلقاً (نقطته آليّة) أو كانت له نقطة فعليّة (يدويّة أو 0 لِـ«لا جواب»).
                ok = is_closed or eff is not None
                ans.teacher_confirmed = (want or auto_zero) and ok
                if ans.teacher_confirmed:
                    confirmed += 1
                elif want and not ok:
                    refused += 1        # طُلبت المصادقة لكن لا نقطة للمفتوح — سنُخبر الأستاذ
            await s.commit()
    if request.headers.get("HX-Request"):
        async with AsyncSessionLocal() as s:
            quiz, questions = await _grade_view(s, quiz_id)
        return templates.TemplateResponse(
            "admin/_quiz_grade_rows.html",
            _ctx(request, quiz=quiz, questions=questions,
                 saved_confirmed=confirmed, saved_refused=refused))
    dest = f"/admin/quizzes/{quiz_id}/grade?saved={confirmed}"
    if refused:
        dest += f"&warn={refused}"
    return RedirectResponse(dest, status_code=303)


def _skill_to_competency() -> dict:
    """عكس COMPETENCY_TO_SKILL (اسم المهارة → مفتاح الكفاية) لاختيار الكفاية مسبقاً."""
    return {v: k for k, v in COMPETENCY_TO_SKILL.items()}


async def _rubric_item(s, q) -> dict:
    """يبني بيانات محرّر المعايير لسؤالٍ مفتوح: الكفاية الحاليّة + مؤشّرات النجاح."""
    comp = ""
    if q.skill_id is not None:
        sk = await s.get(Skill, q.skill_id)
        if sk is not None:
            comp = _skill_to_competency().get(sk.name, "")
    return {"q": q, "competency": comp, "indicators": q.indicators or []}


@router.post("/quizzes/{quiz_id}/grade/rubric")
async def quiz_grade_rubric(request: Request, quiz_id: int):
    """يحفظ معايير تصحيح سؤالٍ مفتوح: الكفاية + مؤشّرات النجاح (نصّ + نقطة لكلّ مؤشّر).

    تُخزَّن في السؤال نفسه فيستعملها التصحيح المُعان (Ollama) لاقتراح النقط، ثمّ
    يصادق الأستاذ. عناصر الإجابة = مؤشّرات النجاح (تخزينٌ موحَّد)."""
    if (g := require_admin(request)):
        return g
    form = await read_form(request)
    try:
        qid = int(form.get("question_id") or 0)
    except (TypeError, ValueError):
        qid = 0
    competency = (form.get("competency") or "").strip()
    texts = form.getlist("ind_text")
    points = form.getlist("ind_points")
    skills = await skill_id_map()
    async with AsyncSessionLocal() as s:
        q = await s.get(QuizQuestion, qid)
        if q is None or q.quiz_id != quiz_id:
            return HTMLResponse("السؤال غير موجود", status_code=404)
        q.skill_id = resolve_skill_id(competency, skills)
        inds = []
        for t, p in zip(texts, points):
            t = (t or "").strip()
            if not t:
                continue
            try:
                pts = round(float(p), 2)
            except (TypeError, ValueError):
                pts = 0.0
            inds.append({"id": f"i{len(inds) + 1}", "text": t, "points": pts})
        q.indicators = inds or None
        await s.commit()
        item = await _rubric_item(s, q)
        return templates.TemplateResponse(
            "admin/_quiz_grade_rubric.html",
            _ctx(request, quiz_id=quiz_id, item=item,
                 competencies=list(COMPETENCIES.items()), saved=True))

