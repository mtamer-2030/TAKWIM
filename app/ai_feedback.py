"""طبقة الذكاء الاصطناعي المحلّي (المرحلة 5) — Ollama على طاولة الأستاذ.

لا سحابة ولا إنترنت: يتّصل بمحرك محلّي (Ollama) على http://localhost:11434.
تدهور لطيف صارم: إن كان المحرك مغلقاً لا ينهار النظام، بل يرفع AIUnavailable
برسالة واضحة للواجهة. الذكاء الاصطناعي هنا مقترحٌ للتقارير وخطط التدخّل فقط،
خارج القاعة، لا حكم آنيّ (حفاظاً على اليقين البيداغوجي وعلى ذاكرة بطاقة الرسوم).
"""

from __future__ import annotations

from .constants import SKILLS
from .settings import settings

OFFLINE_MESSAGE = "محرك الذكاء الاصطناعي غير مشغل. يرجى تشغيل Ollama أولاً."


class AIUnavailable(RuntimeError):
    """المحرك المحلّي غير متاح (مغلق/مهلة/معطّل) — تُعرَض رسالته للواجهة."""


# ——————————————————— هندسة الأمر (System Prompts) ———————————————————

SYSTEM_STUDENT = (
    "أنت مفتّش بيداغوجي خبير في ديداكتيك الفلسفة بالمنهاج المغربي. "
    "حلّل بيانات هذا التلميذ في المهارات الستّ الموحّدة (صياغة الإشكال، البنية "
    "المفاهيمية، الأطروحة، البنية الحجاجية، المناقشة، التركيب)، وقدّم خطة تدخّل "
    "علاجي **قصيرة وعملية ومباشرة في ٣ نقاط** لمعالجة القصور المنهجي المرصود. "
    "تكلّم إلى الأستاذ. اذكر إجراءات صفّية ملموسة قابلة للتطبيق، لا كلاماً عامّاً. "
    "لا تخترع أرقاماً غير معطاة."
)

SYSTEM_CLASS = (
    "أنت مفتّش بيداغوجي خبير في ديداكتيك الفلسفة بالمنهاج المغربي. "
    "حلّل بيانات هذا القسم في المهارات الستّ الموحّدة، وحدّد القصور المنهجي المهيمن، "
    "وقدّم خطة دعم فصلي **قصيرة وعملية ومباشرة في ٣ نقاط** موجّهة إلى الأستاذ لمعالجة "
    "هذا القصور لدى أغلبية التلاميذ. إجراءات صفّية ملموسة لا عموميات. لا تخترع أرقاماً."
)

# أسماء المهارات كما تظهر في البيانات (عربية) — SKILLS من الثوابت (جدول skills).


def _pct(v: float | None) -> str:
    return f"{round(v * 100)}٪" if v is not None else "لا معطى"


def build_student_prompt(profile: dict) -> str:
    lines = [f"بيانات التلميذ: {profile.get('student_name') or profile.get('student_id')}",
             "متوسّط كل مهارة:"]
    for skill in SKILLS:
        d = profile.get("skills", {}).get(skill, {})
        lines.append(f"- {skill}: {_pct(d.get('avg'))} (من {d.get('count', 0)} إنجاز)")
    lines.append(f"نقطة القوة: {profile.get('strength') or '—'}")
    lines.append(f"القصور الحرج (أضعف مهارة): {profile.get('critical_deficit') or '—'}")
    lines.append(f"المتوسّط العامّ: {_pct(profile.get('overall'))}")
    lines.append("\nاكتب خطة التدخّل العلاجي في ٣ نقاط.")
    return "\n".join(lines)


def build_class_prompt(report: dict) -> str:
    lines = [f"بيانات القسم: {report.get('group_name')}",
             f"عدد التلاميذ: {report.get('student_count')}",
             "متوسّط القسم في كل مهارة:"]
    for skill in SKILLS:
        d = report.get("skills", {}).get(skill, {})
        lines.append(f"- {skill}: {_pct(d.get('avg'))}")
    lines.append(f"القصور المنهجي المهيمن: {report.get('dominant_deficit') or '—'} "
                 f"(أضعف مهارة لدى {_pct(report.get('dominant_share'))} من التلاميذ)")
    lines.append(f"المتوسّط العامّ للقسم: {_pct(report.get('overall'))}")
    lines.append("\nاكتب خطة الدعم الفصلي في ٣ نقاط.")
    return "\n".join(lines)


# ——————————————————— الاتصال بالمحرك المحلّي ———————————————————


def ollama_available() -> bool:
    """فحص سريع: هل محرك Ollama مشغّل؟ (بلا رمي استثناء)."""
    cfg = settings.local_ai
    if not cfg.enabled:
        return False
    try:
        import httpx
        r = httpx.get(f"{cfg.base_url}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _generate(system_prompt: str, user_prompt: str, fmt: str | None = None) -> str:
    """نداء Ollama /api/generate. يرفع AIUnavailable عند أي تعذّر اتصال/مهلة.

    fmt="json" يقيّد المخرَج بمخطّط JSON (بدل تفكيكه بـ regex) — للتصحيح المُعان.
    """
    cfg = settings.local_ai
    if not cfg.enabled:
        raise AIUnavailable(OFFLINE_MESSAGE)
    try:
        import httpx
    except ImportError as e:  # pragma: no cover
        raise AIUnavailable(OFFLINE_MESSAGE) from e
    payload = {"model": cfg.model, "system": system_prompt,
               "prompt": user_prompt, "stream": False,
               # يُبقي النموذج محمّلاً في الذاكرة بين التلاميذ (تسريع كبير)،
               # ويحدّ طول المخرَج فتقلّ مدّة التوليد على المعالج.
               "keep_alive": "30m",
               "options": {"num_predict": 350, "temperature": 0.3}}
    if fmt:
        payload["format"] = fmt   # "json" → مخرَج JSON صالح مضمون من Ollama
    try:
        resp = httpx.post(f"{cfg.base_url}/api/generate", json=payload, timeout=cfg.timeout)
        resp.raise_for_status()
        data = resp.json()
        text = (data.get("response") or "").strip()
        if not text:
            raise AIUnavailable("لم يُرجع المحرك المحلّي أيّ نصّ.")
        return text
    except AIUnavailable:
        raise
    except httpx.TimeoutException as e:
        # مهلة: المحرك مشغّل لكنّه بطيء (تحميل النموذج/معالج بلا بطاقة رسوم).
        raise AIUnavailable(
            "المحرّك المحلّي بطيء (قد يُحمّل النموذج لأوّل مرّة). أعد المحاولة، "
            "أو زد المهلة في config.ini، أو استعمل نموذجاً أخفّ.") from e
    except httpx.HTTPStatusError as e:
        # المحرك يعمل لكنّه ردّ بخطأ — غالباً اسم النموذج غير مطابق (404).
        body = ""
        try:
            body = e.response.text[:200]
        except Exception:  # noqa: BLE001
            pass
        if e.response.status_code == 404:
            raise AIUnavailable(
                f"النموذج «{cfg.model}» غير موجود في Ollama. "
                f"شغّل: ollama pull {cfg.model} — أو صحّح الاسم في config.ini "
                f"(تحقّق بـ ollama list).") from e
        raise AIUnavailable(f"ردّ المحرّك المحلّي بخطأ {e.response.status_code}: {body}") from e
    except Exception as e:
        # ConnectionRefused / ConnectError / أي خطأ اتصال ← تدهور لطيف
        raise AIUnavailable(f"تعذّر الاتصال بالمحرّك المحلّي: {type(e).__name__}") from e


def ping_generate() -> tuple[bool, str]:
    """اختبار سريع: يطلب توليداً قصيراً جداً ويعيد (نجاح، رسالة/نصّ) للتشخيص."""
    try:
        out = _generate("أجب بكلمة واحدة.", "قل: جاهز")
        return True, out[:120]
    except AIUnavailable as e:
        return False, str(e)


def generate_student_plan(profile: dict) -> str:
    """خطة تدخّل علاجي لتلميذ (نصّ). يرفع AIUnavailable إن كان المحرك مغلقاً."""
    return _generate(SYSTEM_STUDENT, build_student_prompt(profile))


def generate_class_plan(report: dict) -> str:
    """خطة دعم فصلي لقسم (نصّ). يرفع AIUnavailable إن كان المحرك مغلقاً."""
    return _generate(SYSTEM_CLASS, build_class_prompt(report))


# ——————————————————— التصحيح المسائي المُعان (اقتراح لا حكم) ———————————————————

SYSTEM_CORRECTION = (
    "أنت أستاذ فلسفة مصحّح خبير بالمنهاج المغربي. اقترح نقطة لجواب التلميذ على "
    "سؤال مفتوح، استناداً إلى عناصر الإجابة المعطاة، مع تعليل من جملة قصيرة. "
    "أعِد JSON فقط بهذا الشكل: {\"score\": <عدد بين 0 والسقف>, \"note\": \"<جملة قصيرة>\"}. "
    "لا تتجاوز السقف، ولا تخترع عناصر غير واردة. هذا اقتراحٌ يراجعه الأستاذ."
)


def _build_correction_prompt(question_prompt: str, guidance: str,
                             answer_text: str, max_score: float) -> str:
    lines = [f"السؤال: {question_prompt}",
             f"السقف: {max_score}",
             f"عناصر الإجابة المنتظَرة:\n{guidance or '—'}",
             f"جواب التلميذ:\n{answer_text or '(بلا جواب)'}",
             "\nاقترح النقطة والتعليل بالصيغة المطلوبة."]
    return "\n".join(lines)


def suggest_open_score(question_prompt: str, guidance: str, answer_text: str,
                       max_score: float) -> tuple[float, str]:
    """يقترح (نقطة، تعليل) لجواب مفتوح عبر المحرك المحلّي. يرفع AIUnavailable إن أُغلق.

    النقطة تُقصَر على [0, السقف]. التعليل سطرٌ واحد. اقتراحٌ لا حكم — يصادق الأستاذ.
    المخرَج مقيَّد بمخطّط JSON (لا تفكيك regex هشّ يخلط سقفاً بنقطة).
    """
    import json
    raw = _generate(SYSTEM_CORRECTION,
                    _build_correction_prompt(question_prompt, guidance, answer_text, max_score),
                    fmt="json")
    score, note = 0.0, ""
    try:
        data = json.loads(raw)
        raw_score = data.get("score")
        score = float(raw_score) if raw_score is not None else 0.0
        note = str(data.get("note") or "").strip()
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        # مخرَج غير متوقّع رغم طلب JSON: رجوعٌ آمن إلى أوّل رقم في النصّ.
        import re
        m = re.search(r"[-+]?\d+(?:[.,]\d+)?", raw or "")
        if m:
            try:
                score = float(m.group().replace(",", "."))
            except ValueError:
                score = 0.0
        note = (raw or "").strip()[:200]
    score = max(0.0, min(float(max_score or 0), score))
    return round(score, 2), (note or (raw or "").strip()[:200])
