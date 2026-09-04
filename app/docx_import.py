"""استيراد التمارين/التقاويم من Word (.docx) — يقيني، بلا أي ذكاء اصطناعي.

يقرأ ملفّ Word مكتوباً بعلامات بسيطة (انظر القالب) ويحوّله إلى بنية التقويم
نفسها التي يقبلها التحقّق الصارم (§9)، ثمّ يمرّرها عليه. لا نموذج يُستدعى هنا.

قواعد العلامات (فقرات عادية، لا جداول):
  العنوان: ...            النوع: تمرين|تشخيص|فرض
  المستوى: TC|1BAC|2BAC   المجزوءة: ...     المفهوم: ...
  نص s1: نصّ الانطلاق...            (اختياري، يُشار إليه بـ نص=s1)
  س) اختيار | الكفاية=المفهمة | النقطة=2 | نص=s1
  ما الأطروحة؟
  - بديل خاطئ | خطأ=copy_verbatim
  * البديل الصحيح
  ...
"""

from __future__ import annotations

import io
import re

from .validation import ValidationResult, validate_assessment

# ——— قواميس التعريب ———
TYPE_AR = {
    "اختيار": "mcq_single", "اختيار_واحد": "mcq_single", "اختياري": "mcq_single",
    "متعدد": "mcq_multi", "اختيار_متعدد": "mcq_multi",
    "تصنيف": "classify", "ترتيب": "order",
    "قصير": "short_text", "جواب_قصير": "short_text",
    "مقال": "long_text", "إنشائي": "long_text", "انشائي": "long_text", "فقرة": "long_text",
    "جدول": "grid",
}
COMP_AR = {
    "الأشكلة": "problematization", "الاشكلة": "problematization",
    "المفهمة": "conceptualization",
    "الحجاج": "argumentation",
    "التركيب": "synthesis",
    "الاستحضار": "knowledge",
}
KIND_AR = {
    "تمرين": "exercise", "تشخيص": "diagnostic", "تشخيصي": "diagnostic", "فرض": "exam",
}
RULE_AR = {
    "يحتوي": "any_of", "يحتوي_الكل": "all_of", "لا_يحتوي": "none_of",
    "حد_أدنى": "min_chars", "حد_ادنى": "min_chars",
    "حد_أقصى": "max_chars", "حد_اقصى": "max_chars", "نمط": "regex",
}
_SPLIT = re.compile(r"[؛،|]")           # فواصل القوائم داخل السطر
_QSTART = re.compile(r"^\s*(?:سؤال|س)\s*[)\-.:：]")  # بداية سؤال


def _norm_key(k: str) -> str:
    return k.strip().lower().replace("ـ", "")


def _is_comment(line: str) -> bool:
    """أسطر إرشاد/زينة تُتجاهَل: تبدأ بـ # أو // أو (( أو كلّها فواصل."""
    if not line:
        return True
    if line.startswith("#") or line.startswith("//") or line.startswith("(("):
        return True
    return set(line) <= set("—–-═=_ .•*")


def _read_lines(data: bytes) -> list[str]:
    """يقرأ فقرات الملفّ سطراً سطراً (بلا جداول)."""
    from docx import Document
    doc = Document(io.BytesIO(data))
    lines = []
    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if t:
            lines.append(t)
    return lines


def _split_meta(segment: str) -> tuple[str, str] | None:
    for sep in ("=", ":", "："):
        if sep in segment:
            k, v = segment.split(sep, 1)
            return _norm_key(k), v.strip()
    return None


def _after_colon(line: str) -> str | None:
    """يعيد ما بعد أوّل نقطتين (:) — لأسطر «مؤشر/عنصر/فئات...» حيث قد يحمل = معنى داخلياً."""
    for sep in (":", "："):
        if sep in line:
            return line.split(sep, 1)[1].strip()
    return None


def _parse_check_rule(spec: str, errors: list, where: str) -> dict | None:
    """يحوّل «قاعدة=يحتوي: ؟ ؛ هل» إلى check_rule."""
    m = _split_meta(spec)
    if not m:
        return None
    name, val = m
    rtype = RULE_AR.get(name.replace(" ", "_"))
    if rtype is None:
        errors.append(f"{where}: قاعدة فحص غير معروفة «{name}».")
        return None
    if rtype in ("any_of", "all_of", "none_of"):
        pats = [p.strip() for p in _SPLIT.split(val) if p.strip()]
        return {"type": rtype, "patterns": pats}
    if rtype in ("min_chars", "max_chars"):
        try:
            return {"type": rtype, "value": int(re.search(r"\d+", val).group())}
        except (AttributeError, ValueError):
            errors.append(f"{where}: قاعدة {name} تحتاج عدداً.")
            return None
    if rtype == "regex":
        return {"type": rtype, "pattern": val}
    return None


class _Q:
    def __init__(self):
        self.type = None
        self.competency = None
        self.max_score = None
        self.stimulus = None
        self.partial = False
        self.max_chars = None
        self.prompt_lines: list[str] = []
        self.body: list[str] = []


def parse_docx(data: bytes) -> ValidationResult:
    """يحلّل ملفّ Word ويعيد ValidationResult (normalized جاهز للحفظ أو errors)."""
    r = ValidationResult()
    try:
        lines = _read_lines(data)
    except Exception as exc:  # ملفّ تالف أو ليس docx
        r.fail(f"تعذّرت قراءة ملفّ Word: {exc}")
        return r

    header = {"title": None, "kind": None, "level": None, "unit": None, "concept": None}
    stimuli: list[dict] = []
    questions: list[_Q] = []
    cur: _Q | None = None

    HKEYS = {"العنوان": "title", "النوع": "kind", "المستوى": "level",
             "المجزوءة": "unit", "المفهوم": "concept"}

    for raw in lines:
        line = raw.strip()
        if _is_comment(line):
            continue

        # نصّ الانطلاق: «نص s1: ...»
        mstim = re.match(r"^نص\s+([A-Za-z0-9_]+)\s*[:：]\s*(.+)$", line)
        if mstim and cur is None:
            stimuli.append({"id": mstim.group(1), "text": mstim.group(2).strip()})
            continue

        # ترويسة التقويم
        if cur is None:
            hm = re.match(r"^(العنوان|النوع|المستوى|المجزوءة|المفهوم)\s*[:：=]\s*(.+)$", line)
            if hm:
                header[HKEYS[hm.group(1)]] = hm.group(2).strip()
                continue

        # بداية سؤال
        if _QSTART.match(line):
            cur = _Q()
            questions.append(cur)
            rest = re.sub(r"^(?:سؤال|س)\s*[)\-.:：]\s*", "", line)
            segs = [s.strip() for s in rest.split("|") if s.strip()]
            if segs:
                cur.type = TYPE_AR.get(segs[0].replace(" ", "_"), segs[0])
                for seg in segs[1:]:
                    kv = _split_meta(seg)
                    if not kv:
                        continue
                    k, v = kv
                    if k in ("الكفاية", "كفاية", "competency"):
                        cur.competency = COMP_AR.get(v.strip(), v.strip())
                    elif k in ("النقطة", "نقطة", "points", "score"):
                        try:
                            cur.max_score = float(re.search(r"[\d.]+", v).group())
                        except (AttributeError, ValueError):
                            pass
                    elif k in ("نص", "stimulus"):
                        cur.stimulus = v.strip()
                    elif k in ("جزئي", "partial"):
                        cur.partial = v.strip() in ("نعم", "yes", "true", "1")
                    elif k in ("حد", "max_chars", "حد_محارف"):
                        try:
                            cur.max_chars = int(re.search(r"\d+", v).group())
                        except (AttributeError, ValueError):
                            pass
            continue

        if cur is None:
            continue  # سطر قبل أوّل سؤال وليس ترويسة معروفة ← يُتجاهَل

        cur.body.append(line)

    if not any(header.values()) and not questions:
        r.fail("لم يُعثر على أي محتوى معروف. استعمل القالب.")
        return r

    # بناء بنية JSON من الأسئلة
    norm_questions = []
    for i, q in enumerate(questions, start=1):
        built = _build_question(i, q, r)
        if built is not None:
            norm_questions.append(built)

    data_dict = {
        "title": header["title"] or "تقويم من Word",
        "kind": KIND_AR.get((header["kind"] or "").strip(), header["kind"] or "exercise"),
        "level": header["level"], "unit": header["unit"], "concept": header["concept"],
        "stimuli": stimuli, "questions": norm_questions,
    }
    if not r.ok:
        return r
    # التحقّق الصارم النهائي (نفس بوّابة JSON).
    return validate_assessment(data_dict)


def _build_question(i: int, q: _Q, r: ValidationResult) -> dict | None:
    where = f"السؤال {i}"
    if q.type is None:
        r.fail(f"{where}: بلا نوع.")
        return None

    prompt_lines, options, correct_flags, diagnostics = [], [], [], {}
    categories, items, order_items = [], [], []
    indicators, penalties, scaffold = [], [], []
    columns, rows, cell_chars = [], None, None

    for line in q.body:
        # خيارات
        mopt = re.match(r"^([*+\-–—•])\s*(.+)$", line)
        if mopt and q.type in ("mcq_single", "mcq_multi"):
            mark, text = mopt.group(1), mopt.group(2).strip()
            code = None
            mm = re.search(r"[|]\s*خطأ\s*[=:：]\s*([A-Za-z0-9_]+)", text)
            if mm:
                code = mm.group(1)
                text = text[:mm.start()].strip()
            options.append(text)
            correct_flags.append(mark in ("*", "+"))
            if code:
                diagnostics[str(len(options) - 1)] = code
            continue
        # تصنيف
        if line.startswith("فئات"):
            rest = _after_colon(line)
            categories = [c.strip() for c in _SPLIT.split(rest) if c.strip()] if rest else []
            continue
        if line.startswith("عنصر"):
            rest = _after_colon(line)
            if rest and "=>" in rest:
                txt, cat = rest.split("=>", 1)
                items.append((txt.strip(), cat.strip()))
            continue
        # ترتيب
        mord = re.match(r"^\d+\s*[).\-]\s*(.+)$", line)
        if mord and q.type == "order":
            order_items.append(mord.group(1).strip())
            continue
        # مؤشرات وخصوم
        if line.startswith("مؤشر"):
            _add_indicator(line, indicators, r, where)
            continue
        if line.startswith("خصم"):
            _add_penalty(line, penalties, r, where)
            continue
        if line.startswith("سقالة"):
            rest = _after_colon(line)
            scaffold = [s.strip() for s in _SPLIT.split(rest) if s.strip()] if rest else []
            continue
        if line.startswith("أعمدة") or line.startswith("اعمدة"):
            rest = _after_colon(line)
            columns = [c.strip() for c in _SPLIT.split(rest) if c.strip()] if rest else []
            continue
        if line.startswith("صفوف"):
            rest = _after_colon(line)
            m2 = re.search(r"\d+", rest or "")
            rows = int(m2.group()) if m2 else None
            continue
        if line.startswith("حد_خلية") or line.startswith("حد الخلية"):
            rest = _after_colon(line)
            m2 = re.search(r"\d+", rest or "")
            cell_chars = int(m2.group()) if m2 else None
            continue
        # وإلّا: جزء من نصّ السؤال
        prompt_lines.append(line)

    prompt = " ".join(prompt_lines).strip()
    out = {"type": q.type, "competency": q.competency, "prompt": prompt,
           "max_score": q.max_score if q.max_score is not None else 0}
    if q.stimulus:
        out["stimulus"] = q.stimulus

    payload: dict = {}
    if q.type == "mcq_single":
        payload["options"] = options
        corr = [k for k, f in enumerate(correct_flags) if f]
        if len(corr) != 1:
            r.fail(f"{where}: علّم جواباً صحيحاً واحداً بنجمة (*).")
        else:
            payload["correct"] = corr[0]
        if diagnostics:
            payload["diagnostics"] = diagnostics
    elif q.type == "mcq_multi":
        payload["options"] = options
        payload["correct"] = [k for k, f in enumerate(correct_flags) if f]
        if not payload["correct"]:
            r.fail(f"{where}: علّم الأجوبة الصحيحة بنجمة (*).")
        if diagnostics:
            payload["diagnostics"] = diagnostics
    elif q.type == "classify":
        payload["categories"] = categories
        cat_index = {c.strip(): idx for idx, c in enumerate(categories)}
        built_items = []
        for txt, cat in items:
            if cat.isdigit():
                ci = int(cat)
            else:
                ci = cat_index.get(cat.strip())
            if ci is None:
                r.fail(f"{where}: العنصر «{txt}» يشير إلى فئة غير معرّفة «{cat}».")
                ci = 0
            built_items.append({"text": txt, "category": ci})
        payload["items"] = built_items
    elif q.type == "order":
        payload["items"] = order_items
        payload["correct_order"] = list(range(len(order_items)))
        payload["partial_credit"] = q.partial
    elif q.type in ("short_text", "long_text"):
        if q.max_chars:
            payload["max_chars"] = q.max_chars
        if q.type == "long_text" and scaffold:
            payload["scaffold"] = scaffold
        if indicators:
            out["indicators"] = indicators
        if penalties:
            out["penalties"] = penalties
    elif q.type == "grid":
        payload["columns"] = columns
        payload["rows"] = rows or 1
        if cell_chars:
            payload["max_chars_per_cell"] = cell_chars
        if indicators:
            out["indicators"] = indicators
        if penalties:
            out["penalties"] = penalties

    out["payload"] = payload
    return out


def _add_indicator(line: str, indicators: list, r: ValidationResult, where: str) -> None:
    rest = _after_colon(line)  # "النص | النقاط | قاعدة=..."
    if not rest:
        return
    parts = [p.strip() for p in rest.split("|")]
    text = parts[0]
    points = 0.0
    rule = None
    if len(parts) >= 2:
        try:
            points = float(re.search(r"[\d.]+", parts[1]).group())
        except (AttributeError, ValueError):
            points = 0.0
    for extra in parts[2:]:
        e = extra.strip()
        if e.startswith("قاعدة"):
            spec = e[len("قاعدة"):].lstrip("=:： ").strip()  # «يحتوي: ؟ ؛ هل»
            rule = _parse_check_rule(spec, r, where)
    ind = {"id": f"i{len(indicators) + 1}", "text": text, "points": points}
    if rule:
        ind["check_rule"] = rule
    indicators.append(ind)


def _add_penalty(line: str, penalties: list, r: ValidationResult, where: str) -> None:
    rest = _after_colon(line)
    if not rest:
        return
    parts = [p.strip() for p in rest.split("|")]
    text = parts[0]
    points = 0.0
    if len(parts) >= 2:
        try:
            points = -abs(float(re.search(r"[\d.]+", parts[1]).group()))
        except (AttributeError, ValueError):
            points = 0.0
    penalties.append({"text": text, "points": points})
