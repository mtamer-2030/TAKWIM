"""خدمة التقاويم v2 — إنشاء التقاويم من JSON وتصحيحها يقينياً.

تُعاد محرّكات v1 النقيّة دون تغيير:
- ``app.validation.validate_assessment``: تحقّق صارم من صيغة JSON.
- ``app.scoring.score_answer`` و``answer_text``: تصحيح يقيني للمغلقة وجزئي للمفتوحة.

بذلك نضمن نفس السلوك المجرَّب في v1 داخل بنية v2 (SQLAlchemy async).
"""

from __future__ import annotations

from ..constants import COMPETENCY_TO_SKILL, QUESTION_TYPES_CLOSED, SKILLS
from ..models import Quiz, QuizQuestion
from ..scoring import answer_text, score_answer
from ..validation import validate_assessment


class QuizImportError(ValueError):
    """صيغة التقويم غير صحيحة — تُعرَض أخطاؤها للأستاذ."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(" | ".join(errors))


def normalize_quiz_json(data) -> dict:
    """يتحقّق من JSON التقويم ويعيد الصيغة المطبّعة، أو يرفع QuizImportError."""
    result = validate_assessment(data)
    if not result.ok:
        raise QuizImportError(result.errors)
    return result.normalized


import re as _re

# علامات خانات الاختيار: فارغة (خيار) ومملوءة (خيار صحيح مؤشَّر في المصدر).
# نشمل أشكالاً كثيرة تظهر في فروض Word: ☐ □ ▢ ⃞(محيط مربّع) ⬜ ○ …
_BOX_EMPTY = ("☐□▢◻◽⬜❑❒⧀○◯"
              "⭘⎔❏❎◼◾⁃⃞⬚▯▭"
              "⚀⃣")
_BOX_CHECKED = "☑☒■◾✅✔✓✘●◉✗"
_BOX_ANY = _re.compile("[" + _BOX_EMPTY + _BOX_CHECKED + "]")
_CHECKED_SET = set(_BOX_CHECKED)
# ترقيم السؤال: رقم يتبعه فاصل. نشمل الكشيدة «ـ» (U+0640) لأنّ فروضاً تكتب «1ـ 2ـ».
_LEAD = _re.compile(r"^\s*(?:(?:\d+|[٠-٩]+)\s*[).:\-–—؛ـ]|(?:السؤال|سؤال|س)\s*\d*\s*[).:\-–—ـ]?|[-*•])\s*")
# علامة الجواب الصحيح في خيارات بلا خانات: «… (صحيح)» في آخر السطر.
_CORRECT_MARK = _re.compile(r"\s*[\(（]\s*صحيحة?\s*[\)）]\s*$")
# سطر «عناصر الإجابة: …» يُسنَد لآخر سؤال مفتوح مؤشّراتِ تصحيحٍ (لا يصير سؤالاً).
_ELEMENTS_RE = _re.compile(r"^\s*عناصر\s+الإ?جابة\s*[:：]?\s*")

# سطر خيار: يبدأ بخانة، أو بعلامة حرفيّة بين قوسين «(أ) (ب) (a)», أو نقطة تعداد.
_OPT_MARK = _re.compile(
    r"^\s*(?:[" + _BOX_EMPTY + _BOX_CHECKED + r"]|\(\s*[أ-يa-zA-Z]\s*\)|[-*•▪◦])\s*")


def _is_option_line(line: str) -> bool:
    """هل السطر خيارُ اختيارٍ؟ (يبدأ بعلامة خيار أو يحوي خانة والسطر ليس طويلاً)."""
    if _OPT_MARK.match(line):
        return True
    return bool(_BOX_ANY.search(line)) and len(line) <= 220


def _mcq_from_option_lines(prompt: str, opt_lines: list[str]) -> dict | None:
    """يبني سؤال اختيار من نصّ السؤال وأسطر خياراته (العلامة قد تكون أوّل السطر أو آخره)."""
    options, correct = [], []
    for ol in opt_lines:
        checked = any(ch in _CHECKED_SET for ch in ol)
        text = _OPT_MARK.sub("", ol)                       # إزالة العلامة البادئة
        text = _BOX_ANY.sub("", text).strip(" .،؛:|-–—\t")  # إزالة أيّ خانة متبقّية
        if text:
            if checked:
                correct.append(len(options))
            options.append(text)
    if len(options) < 2:
        return None
    prompt = _LEAD.sub("", prompt).strip().rstrip(":：").strip() or "اختر"
    multi = any(w in prompt for w in ("كل ", "كلّ", "جميع", "جميعها", "كلها", "كلّها")) \
        or len(correct) > 1
    return {"type": "mcq_multi" if multi else "mcq_single", "competency": None,
            "prompt": prompt, "stimulus": None, "max_score": 2.0,
            "options": options, "correct": correct, "payload": {},
            "indicators": [], "penalties": [], "auto_scored": False}


def _is_plain_opt_candidate(line: str) -> bool:
    """سطر يصلح خياراً بلا خانة: قصير، بلا خانات، لا ترقيم، لا ينتهي باستفهام/نقطتين.
    لا نستبعد ما يبدأ بأداة سؤال (خياراتٌ تبدأ بـ«من/كيف/هل» شائعة). آمنٌ لأنّ المجموعة
    لا تُعتمَد اختياراً إلّا إن حوت «(صحيح)»، فلا يبتلع النصوص أو الأسئلة."""
    if not line or _BOX_ANY.search(line) or _NUM_START.match(line):
        return False
    if line.rstrip().endswith((":", "：", "؟")) or _is_header(line) or _is_section_title(line):
        return False
    return len(line) <= 90


def _mcq_from_correct_lines(prompt: str, opt_lines: list[str]) -> dict | None:
    """يبني اختياراً من خيارات بلا خانات، الصحيح فيها مؤشَّرٌ بـ«(صحيح)» في آخر السطر."""
    options, correct = [], []
    for ol in opt_lines:
        is_correct = bool(_CORRECT_MARK.search(ol))
        text = _CORRECT_MARK.sub("", ol).strip(" .،؛:|-–—\t")
        if text:
            if is_correct:
                correct.append(len(options))
            options.append(text)
    if len(options) < 2:
        return None
    p = _LEAD.sub("", prompt).strip()
    multi = "كل ما يصح" in p or "اختر كل" in p or "جميع" in p or len(correct) > 1
    p = _re.sub(r"\s*[\(（][^)）]*يصح[^)）]*[\)）]", "", p).strip().rstrip(":：؟").strip() or "اختر"
    return {"type": "mcq_multi" if multi else "mcq_single", "competency": None,
            "prompt": p, "stimulus": None, "max_score": 2.0,
            "options": options, "correct": correct, "payload": {},
            "indicators": [], "penalties": [], "auto_scored": False}


# كلمات ترويسة الورقة (مستوى/مادّة/مؤسّسة…) — أسطرها ليست أسئلة.
_HEADER_KW = ("المستوى", "المادة", "المادّة", "المدة", "المدّة", "المؤسسة", "المؤسّسة",
              "الأكاديمية", "الأكاديميّة", "المديرية", "المديريّة", "النيابة", "الثانوية",
              "الثانويّة", "الإعدادية", "التأهيلية", "الأسدس", "الدورة", "السنة الدراسية",
              "رقم الامتحان", "رقم التلميذ", "الاسم الكامل", "النقطة النهائية", "معامل")
_ID_HEADER = _re.compile(r"^\s*(?:الاسم|القسم|النسب|الرقم|رقم التلميذ|التوقيع)\s*[:：]")


def _is_header(line: str) -> bool:
    """سطر ترويسة إداريّة (مستوى/مادّة/مدّة/اسم…) لا يُعدّ سؤالاً."""
    if _ID_HEADER.match(line):
        return True
    hits = sum(1 for kw in _HEADER_KW if kw in line)
    return hits >= 2 or (hits >= 1 and "|" in line)


def _mcq_from_text(prompt_part: str, opts_part: str) -> dict | None:
    """يبني سؤال اختيار من نصّ فيه علامات خانات: النصّ قبل أوّل علامة سؤالٌ،
    وما بين العلامات خياراتٌ. العلامة المملوءة (☑) تُعلَّم إجابةً صحيحة."""
    marks = list(_BOX_ANY.finditer(opts_part))
    if len(marks) < 2:
        return None
    options, correct = [], []
    for k, m in enumerate(marks):
        start = m.end()
        end = marks[k + 1].start() if k + 1 < len(marks) else len(opts_part)
        opt = opts_part[start:end].strip(" .،؛|-–—\t")
        if not opt:
            continue
        if m.group() in _CHECKED_SET:
            correct.append(len(options))
        options.append(opt)
    if len(options) < 2:
        return None
    prompt = _LEAD.sub("", prompt_part).strip().rstrip(":：").strip() or "اختر"
    # «كلّ/جميع» تلمّح إلى تعدّد الصواب؛ وإلّا فاختيارٌ واحد. (لا نعدّ «التي» لكثرتها.)
    multi = any(w in prompt for w in ("كل ", "كلّ", "جميع", "جميعها", "كلها", "كلّها")) \
        or len(correct) > 1
    qtype = "mcq_multi" if multi else "mcq_single"
    return {"type": qtype, "competency": None, "prompt": prompt, "stimulus": None,
            "max_score": 2.0, "options": options, "correct": correct,
            "payload": {}, "indicators": [], "penalties": [], "auto_scored": False}


def _open_q(prompt: str) -> dict:
    return {"type": "long_text", "competency": None, "prompt": prompt, "stimulus": None,
            "max_score": 4.0, "options": [], "correct": [], "payload": {},
            "indicators": [], "penalties": [], "auto_scored": False}


def _as(prompt: str, qtype: str) -> dict:
    q = _open_q(prompt)
    q["type"] = qtype
    q["max_score"] = 0.0 if qtype in ("skip", "heading", "passage") else q["max_score"]
    return q


_SEP_RE = _re.compile(r"^[\s_\-–—=.·•*─-╿•]{4,}$")  # فاصل/سطر إجابة (نقاط/خطوط)
_NUM_START = _re.compile(r"^\s*(?:\d+|[٠-٩]+)\s*[).:\-–—ـ]")   # يبدأ بترقيم (سؤال/قسم)
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_POINTS_RE = _re.compile(r"\(\s*([\d.,٠-٩]+)\s*(?:نقاط|نقطة|نقط|ن)\s*\)")


def _extract_points(text: str):
    """يفصل «(3 نقاط)» عن نصّ السؤال، ويعيد (النصّ بلا النقطة، السقف أو None)."""
    m = _POINTS_RE.search(text)
    if not m:
        return text.strip(), None
    num = m.group(1).translate(_AR_DIGITS).replace(",", ".")
    try:
        val = float(num)
    except ValueError:
        return text.strip(), None
    clean = (text[:m.start()] + " " + text[m.end():]).strip(" :：.-").strip()
    return clean or text.strip(), val


# عناوين أقسام شائعة في الفروض المغربيّة (لا أسئلة).
_SECTION_RE = _re.compile(
    r"^\s*(?:أولا|ثانيا|ثالثا|رابعا|خامسا|الجزء|القسم|المحور|المجال|التمرين"
    r"|الوضعية|المطلوب|الموضوع|معارف|المهارات|القدرات|الكفايات|أسئلة|الأسئلة"
    r"|دراسة\s+نص|النص\s+الفلسفي|القولة|الإنشاء|الإنشاء\s+الفلسفي|تحليل\s+نص"
    r"|فرض|الفرض|تقويم|التقويم|امتحان|الامتحان|اختبار|رائز|الرائز)\b")
# أفعال/أدوات تبدأ بها الأسئلة الحقيقيّة (فلا تُعدّ عناوين).
_Q_VERB = _re.compile(
    r"^\s*(?:اشرح|حلّ?ل|عرّ?ف|بيّ?ن|ناقش|أجب|استخرج|قارن|علّ?ل|اذكر|صنّ?ف|رتّ?ب"
    r"|وضّ?ح|اكتب|أنشئ|استنتج|حدّ?د|أبرز|ضع|أكمل|املأ|صل|اربط|ما|هل|لماذا|كيف|متى|أين|من)\b")


def _is_section_title(line: str) -> bool:
    """سطر عنوانٍ (قسم/محور/معارف…) لا سؤال — بلا خانات ولا استفهام."""
    if _BOX_ANY.search(line) or line.endswith("؟"):
        return False
    core = _LEAD.sub("", line).strip()          # نتجاهل الترقيم البادئ (1. / 2-)
    if _Q_VERB.match(core):                       # يبدأ بفعل سؤال → سؤال لا عنوان
        return False
    if _SECTION_RE.match(core):
        return True
    # عنوانٌ ينتهي بنقاط بين قوسين ((4.5 نقط)) وليس سؤالاً.
    if _re.search(r"\(\s*[\d.,٠-٩]+\s*(?:نقط|نقطة|ن)\s*\)", core) and len(core) <= 80:
        return True
    return False


def _is_prose_line(line: str) -> bool:
    """سطر نثرٍ للقراءة (لا سؤال): بلا خانات، لا ترقيم سؤال، لا استفهام، لا فعل
    سؤال، وليس ترويسةً ولا عنوان قسم. تُجمَع أسطر النثر المتتالية في نصٍّ واحد."""
    if not line or _BOX_ANY.search(line) or line.endswith("؟") or _NUM_START.match(line):
        return False
    if _is_header(line) or _is_section_title(line) or _ELEMENTS_RE.match(line):
        return False
    return not _Q_VERB.match(_LEAD.sub("", line).strip())


# مؤشّرات نجاح المقال الفلسفيّ المغربيّ (شبكة التصحيح: فهم/تحليل/مناقشة/تركيب).
_ESSAY_INDICATORS = [
    "الفهم: التأطير وطرح الإشكال",
    "التحليل: الأطروحة والبنية المفاهيمية والحجاجية",
    "المناقشة: القيمة والحدود والانفتاح على المواقف",
    "التركيب: الخلاصة والاستنتاج والرأي الشخصيّ",
    "سلامة اللغة وتنظيم الجواب",
]


def essay_models_from_text(text: str) -> list[dict] | None:
    """يكشف بنية «القولة/المطلب» (فرض مقاليّ بنماذج، وقد يرافقه نموذج إجابة محلول)
    فيستخرج لكلّ نموذجٍ سؤالاً إنشائيّاً: القولة سندٌ معروض، المطلب نصّ السؤال،
    ومؤشّرات شبكة التصحيح الفلسفيّة عناصرَ إجابةٍ للتصحيح الآليّ. يعيد None إن لم تظهر."""
    if "القولة" not in text or "المطلب" not in text:
        return None
    parts = _re.split(r"(?=^\s*النموذج\b)", text, flags=_re.M) or [text]
    out: list[dict] = []
    for part in parts:
        p = part.strip()
        if "القولة" not in p or "المطلب" not in p:
            continue
        label = None
        m = _re.match(r"^\s*(النموذج[^\n:：]*)", p)
        if m:
            label = m.group(1).strip()
        qm = _re.search(r"القولة\s*[:：]?\s*(.*?)(?=المطلب)", p, _re.S)
        quote = qm.group(1).strip().strip("«»\"“”:،. \n\t") if qm else ""
        pm = _re.search(r"المطلب\s*[:：]?\s*(.+)", p)
        prompt = pm.group(1).strip().splitlines()[0].strip() if pm else ""
        # نحذف ذيل نقاط الإجابة إن وُجد في سطر المطلب.
        prompt = _re.sub(r"[.․‥…_ـ]{4,}.*$", "", prompt).strip()
        if len(quote) < 8 or len(prompt) < 8:
            continue
        if label:
            out.append(_as(label, "heading"))
        q = _open_q(prompt)
        q["stimulus"] = quote
        q["max_score"] = 20.0
        q["elements"] = list(_ESSAY_INDICATORS)
        out.append(q)
    return out or None


def heuristic_quiz_from_text(text: str, title_hint: str = "") -> dict:
    """محلّل متسامح يقينيّ: يحوّل نصّ فرض/تمرين عاديّاً إلى أسئلة مباشرةً.

    يتخطّى أسطر الترويسة الإداريّة، ويكتشف أسئلة الاختيار المتعدّد بعلامات الخانات
    (☐/☑) فيفصل نصّ السؤال عن خياراته (سواء في السطر نفسه أو في أسطر تالية)، ويجعل
    البقيّة أسئلة مفتوحة. لا يعرف الإجابة الصحيحة إن لم تكن مؤشَّرة في المصدر — يؤشّرها
    الأستاذ في شاشة المراجعة. بلا ذكاء اصطناعيّ وبلا اتصال؛ يعمل دائماً."""
    title = (title_hint or "").strip() or "تقويم مستورد"
    # بنية «القولة/المطلب» (فرض مقاليّ بنماذج) لها محلّلٌ متخصّص يحفظ القولة سنداً
    # والمطلب سؤالاً ومؤشّرات التصحيح الفلسفيّة — قبل التحليل العامّ (سطراً سطراً).
    essay = essay_models_from_text(text or "")
    if essay:
        return {"title": title, "kind": "exam", "level": None, "unit": None,
                "concept": None, "stimuli": [], "questions": essay}
    # نقصّ ذيل أسطر الإجابة (سلسلة نقاط/خطوط ≥4) من كلّ سطر، فتنظُف السقالات
    # («*اسم البنية: .......» → «*اسم البنية:») وتُحذف أسطر الإجابة الفارغة كلّيّاً.
    lines = []
    for ln in (text or "").splitlines():
        ln = _re.sub(r"[.․‥…_ـ]{4,}.*$", "", ln).strip(" *•-–—\t")
        if ln:
            lines.append(ln)

    questions: list[dict] = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        boxes = list(_BOX_ANY.finditer(ln))
        if not boxes:
            # سطر إجابة/فاصل (نقاط أو خطوط فقط) → يُحذف تماماً (ليس سؤالاً ولا يُعرَض).
            if _SEP_RE.match(ln):
                i += 1
                continue
            # «عناصر الإجابة: …» → تُسنَد مؤشّراتٍ لآخر سؤال مفتوح (لا تصير سؤالاً).
            if _ELEMENTS_RE.match(ln):
                body = _ELEMENTS_RE.sub("", ln).strip(" :：.-")
                elems = [e.strip(" .،؛-") for e in _re.split(r"[،؛]", body)
                         if len(e.strip()) >= 3]
                for q in reversed(questions):
                    if q["type"] in ("long_text", "short_text"):
                        if elems:
                            q["elements"] = elems
                        break
                i += 1
                continue
            if _is_header(ln) or _is_section_title(ln):
                clean, _ = _extract_points(_LEAD.sub("", ln).strip())
                questions.append(_as(clean, "heading"))
                i += 1
                continue
            # خيارات بلا خانات مؤشَّرٌ صوابها بـ«(صحيح)»: نصّ السؤال يتلوه أسطر الخيارات.
            # آمنٌ لأنّه لا يُفعَّل إلّا بوجود «(صحيح)» فلا يبتلع النصوص أو الأسئلة المفتوحة.
            j = i + 1
            cand = []
            while j < n and _is_plain_opt_candidate(lines[j]):
                cand.append(lines[j])
                j += 1
            if len(cand) >= 2 and any(_CORRECT_MARK.search(c) for c in cand):
                q = _mcq_from_correct_lines(ln, cand)
                if q:
                    questions.append(q)
                    i = j
                    continue
            if _is_prose_line(ln):
                # نجمع أسطر النثر المتتالية في نصٍّ واحد للقراءة (لا يضيع النصّ الفلسفيّ
                # لأنّ أسطره فُرادى قصيرة). نعتمده نصّاً فقط إن بلغ المجموع طولاً معتبراً.
                run = [ln]
                j = i + 1
                while j < n and _is_prose_line(lines[j]):
                    run.append(lines[j])
                    j += 1
                block = " ".join(run).strip()
                if len(block) >= 120:
                    questions.append(_as(block, "passage"))
                    i = j
                    continue
                # نثر قصير: لا يكفي نصّاً — يُعالَج السطر الحاليّ سؤالاً مفتوحاً أدناه.
        if len(boxes) >= 2:
            # خيارات مضمَّنة في السطر نفسه: ما قبل أوّل علامة سؤالٌ، والباقي خيارات.
            q = _mcq_from_text(ln[:boxes[0].start()], ln[boxes[0].start():])
            questions.append(q or _open_q(_LEAD.sub("", ln).strip()))
            i += 1
            continue
        # نصّ سؤال ربّما تلته خيارات في أسطر مستقلّة (خانة أوّل السطر أو «(أ)…⃞»).
        j = i + 1
        opt_lines = []
        while j < n and _is_option_line(lines[j]):
            opt_lines.append(lines[j])
            j += 1
        if len(opt_lines) >= 2:
            q = _mcq_from_option_lines(ln, opt_lines)
            questions.append(q or _open_q(_LEAD.sub("", ln).strip()))
            i = j
            continue
        prompt, pts = _extract_points(_LEAD.sub("", ln).strip())
        if prompt:
            oq = _open_q(prompt)
            if pts is not None:
                oq["max_score"] = pts            # «(3 نقاط)» → سقف السؤال
            questions.append(oq)
        i += 1

    if not questions and lines:
        questions.append(_open_q(" ".join(lines).strip()))
    return {"title": title, "kind": "exercise", "level": None, "unit": None,
            "concept": None, "stimuli": [], "questions": questions}


_ANSWERABLE_SKIP = ("heading", "passage", "skip")


def _split_answer_blocks(answers_text: str) -> dict[int, list[str]]:
    """يقسّم نصّ «عناصر الإجابة» إلى كتلٍ مرقّمة {رقم السؤال: [أسطر العناصر]}.
    يتسامح مع الترقيم العربيّ والغربيّ وبادئة «السؤال ٣». بلا ذكاء اصطناعيّ."""
    blocks: dict[int, list[str]] = {}
    cur: int | None = None
    for raw in (answers_text or "").splitlines():
        ln = _re.sub(r"[.․‥…_ـ]{4,}.*$", "", raw).strip(" *•-–—\t")
        if not ln:
            continue
        m = _NUM_START.match(ln)
        if m:
            digits = _re.match(r"\D*(\d+)", ln.translate(_AR_DIGITS))
            if digits:
                cur = int(digits.group(1))
                rest = ln[m.end():].strip(" .:،؛-–—")
                blocks[cur] = [rest] if rest else []
                continue
        if cur is not None:
            blocks[cur].append(ln)
    return blocks


def _elements_from_block(block: list[str]) -> list[str]:
    """يحوّل كتلة عناصر إجابةٍ إلى قائمة مؤشّرات: سطرٌ لكلّ عنصر، وإن كان سطراً
    واحداً فُصِل على «،/؛/-» فقط إن أعطى عناصر متعدّدة معقولة."""
    out: list[str] = []
    for ln in block:
        parts = [p.strip(" .،؛-–—") for p in _re.split(r"[،؛]|\s[-–—]\s", ln)]
        parts = [p for p in parts if len(p) >= 3]
        out.extend(parts if len(parts) >= 2 else ([ln] if len(ln) >= 3 else []))
    return out


def attach_answer_elements(questions: list[dict], answers_text: str) -> int:
    """يُسنِد عناصر الإجابة (من الملفّ الثاني) إلى الأسئلة المفتوحة يقينيّاً بالترقيم.

    يطابق الكتلة رقم k بالسؤال المفتوح ذي الترتيب k بين الأسئلة القابلة للإجابة
    (تُتخطّى العناوين/النصوص). يعيد عدد الأسئلة التي أُسنِدت لها عناصر. لا يخترع
    شيئاً؛ إن لم يتطابق الترقيم أعاد صفراً ويبقى النصّ للأستاذ يوزّعه يدويّاً."""
    blocks = _split_answer_blocks(answers_text)
    if not blocks:
        return 0
    matched = 0
    idx = 0
    for q in questions:
        if q.get("type") in _ANSWERABLE_SKIP:
            continue
        idx += 1
        blk = blocks.get(idx)
        if blk and q.get("type") in ("long_text", "short_text"):
            elems = _elements_from_block(blk)
            if elems:
                q["elements"] = elems
                matched += 1
    return matched


def resolve_skill_id(value: str | None, skill_ids: dict[str, int]) -> int | None:
    """يحلّ قيمة الكفاية/المهارة إلى skill_id من خريطة (اسم المهارة → id).

    يقبل مفتاح كفاية v1 (مثل ``argumentation``) أو اسم مهارة عربيّاً مباشرةً.
    يعيد None إن تعذّر التطابق (فيبقى السؤال بلا مهارة، لا ينهار الاستيراد).
    """
    if not value:
        return None
    name = value if value in SKILLS else COMPETENCY_TO_SKILL.get(value)
    return skill_ids.get(name) if name else None


def build_quiz(normalized: dict, *, level_id: int | None = None,
               group_name: str | None = None,
               skill_ids: dict[str, int] | None = None) -> Quiz:
    """يبني كائن Quiz (غير محفوظ) من الصيغة المطبّعة، مع حلّ نصوص الانطلاق.

    ``skill_ids``: خريطة (اسم المهارة العربيّ → id) من جدول skills المبذور،
    تُحلّ بها كفاية كل سؤال إلى skill_id؛ إن غابت بقيت الأسئلة بلا مهارة.
    """
    skill_ids = skill_ids or {}
    stim_by_id = {s["id"]: s["text"] for s in normalized.get("stimuli", [])}
    quiz = Quiz(
        title=normalized["title"],
        kind=normalized.get("kind") or "exercise",
        level_id=level_id,
        group_name=group_name or None,
        unit=normalized.get("unit"),
        concept=normalized.get("concept"),
    )
    for q in normalized["questions"]:
        quiz.questions.append(QuizQuestion(
            position=q.get("position", 0),
            qtype=q["type"],
            skill_id=resolve_skill_id(q.get("competency"), skill_ids),
            prompt=q["prompt"],
            stimulus=stim_by_id.get(q.get("stimulus")) if q.get("stimulus") else None,
            payload=q.get("payload") or {},
            indicators=q.get("indicators") or None,
            penalties=q.get("penalties") or None,
            max_score=float(q.get("max_score") or 0),
            auto_scored=bool(q.get("auto_scored")),
        ))
    return quiz


def grade_answer(question: QuizQuestion, raw: dict | None) -> dict:
    """يصحّح جواباً واحداً يقينياً.

    يعيد: {score, max_score, auto (هل صُحّح آلياً)، verdicts، feedback، answer_text}.
    - المغلقة: نقطة كاملة + تشخيصات (تغذية راجعة) من payload.diagnostics.
    - المفتوحة: نقطة جزئية من مؤشّرات لها check_rule؛ الباقي None (ينتظر الأستاذ).
    """
    res = score_answer(
        question.qtype, question.payload or {}, question.indicators or [],
        question.penalties or [], question.max_score, raw)
    out = {
        "score": res.get("auto_score"),
        "max_score": question.max_score,
        "auto": question.qtype in QUESTION_TYPES_CLOSED,
        "verdict": res.get("verdict"),
        "rule_verdicts": res.get("rule_verdicts"),
        "answer_text": answer_text(question.qtype, raw),
        "feedback": _feedback_for(question, raw, res),
    }
    return out


def readable_answer(question: QuizQuestion, raw: dict | None) -> str:
    """يحوّل جواب التلميذ الخام إلى نصّ مقروء للأستاذ (لشاشة التصحيح)."""
    raw = raw or {}
    payload = question.payload or {}
    opts = payload.get("options", [])
    if question.qtype == "mcq_single":
        c = raw.get("choice")
        return opts[c] if isinstance(c, int) and 0 <= c < len(opts) else "— بلا جواب —"
    if question.qtype == "mcq_multi":
        chosen = [opts[i] for i in raw.get("choices", []) if isinstance(i, int) and 0 <= i < len(opts)]
        return "، ".join(chosen) or "— بلا جواب —"
    if question.qtype == "classify":
        cats = payload.get("categories", [])
        items = payload.get("items", [])
        assigns = raw.get("assignments", [])
        parts = []
        for k, it in enumerate(items):
            a = assigns[k] if k < len(assigns) else None
            cat = cats[a] if isinstance(a, int) and 0 <= a < len(cats) else "—"
            parts.append(f"{it.get('text', '')} → {cat}")
        return " | ".join(parts) or "— بلا جواب —"
    if question.qtype == "order":
        items = payload.get("items", [])
        order = raw.get("order", [])
        seq = [items[i] for i in order if isinstance(i, int) and 0 <= i < len(items)]
        return " ثمّ ".join(seq) or "— بلا جواب —"
    if question.qtype in ("short_text", "long_text"):
        return (raw.get("text") or "").strip() or "— بلا جواب —"
    if question.qtype == "grid":
        cells = raw.get("cells", [])
        return " / ".join(" ، ".join(str(c) for c in row) for row in cells) or "— بلا جواب —"
    return "—"


def _feedback_for(question: QuizQuestion, raw: dict | None, res: dict) -> str | None:
    """تغذية راجعة نصّية للمتعلّم: صواب/خطأ للمغلقة + تشخيص البديل المختار إن وُجد."""
    if question.qtype not in QUESTION_TYPES_CLOSED:
        return None
    score = res.get("auto_score")
    full = question.max_score
    if score is not None and full and score >= full:
        return "إجابة صحيحة ✓"
    # للاختيار الأحادي: اعرض تشخيص البديل المختار إن كان مُعرّفاً في payload.
    diagnostics = (question.payload or {}).get("diagnostics") or {}
    if question.qtype == "mcq_single" and isinstance(raw, dict):
        choice = raw.get("choice")
        if choice is not None and str(choice) in diagnostics:
            return f"إجابة غير دقيقة — {diagnostics[str(choice)]}"
    return "إجابة غير صحيحة — راجع عناصر الجواب."
