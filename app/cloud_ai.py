"""استيراد ذكيّ سحابيّ: يحوّل أيّ بنية فرضٍ إلى أسئلة مهيكلة عبر نموذج Claude قويّ.

يُستعمَل **وقت التحضير فقط** (يحتاج إنترنت ومفتاح API)؛ الجلسة في الفصل تبقى محلّية.
غيابُ المفتاح أو الإنترنت لا يعطّل النظام — يرجع المستدعي للمحلّل القاعديّ اليقينيّ.

المفتاح والنموذج من ``settings.cloud_ai`` (config.ini [cloud_ai] أو ANTHROPIC_API_KEY).
تُعاد النتيجة JSON نصّاً بنفس شكل ``_ai_json_to_questions`` (عنوان + أسئلة)، فيُراجعها
الأستاذ ويصادق عليها قبل الحفظ.
"""

from __future__ import annotations

import json
import re

from .settings import settings


class CloudAIUnavailable(RuntimeError):
    """تعذّر الاستخراج السحابيّ (لا مفتاح/لا إنترنت/خطأ من الخدمة) — رسالة للأستاذ."""


# مخطّط الإخراج + قواعد صارمة. النموذج قويّ فيملأ الأجوبة الصحيحة ويصنّف الكفايات
# ويحفظ العناوين والنصوص أيّاً كانت بنية الفرض.
SYSTEM_EXTRACT_CLOUD = (
    "أنت خبير ديداكتيك الفلسفة في التعليم الثانويّ المغربيّ. مهمّتك تحويل نصّ فرضٍ أو "
    "تمرينٍ أو تقويم — أيّاً كانت بنيته أو تنسيقه — إلى JSON مهيكل يمثّل عناصره "
    "بالترتيب، جاهزاً لنظام تقويم رقميّ. أعِد JSON فقط، بلا أيّ شرح أو أسوار Markdown.\n\n"
    "المخطّط:\n"
    '{"title":"عنوان الفرض","questions":[\n'
    '  {"type":"...","prompt":"...","max_score":3,"competency":"...",\n'
    '   "options":["..."],"correct":[0],"elements":["..."]}\n]}\n\n'
    "أنواع type:\n"
    "- heading: عنوان قسم/محور يُعرَض للتلميذ ولا يُجاب عنه.\n"
    "- passage: نصّ فلسفيّ/قولة للقراءة يُعرَض كاملاً (انسخه حرفيّاً بلا اختصار).\n"
    "- mcq_single: اختيار جواب واحد صحيح. mcq_multi: اختيار عدّة صحيحة.\n"
    "- short_text: جواب قصير. long_text: مقال/تحليل مطوّل.\n\n"
    "قواعد صارمة:\n"
    "1. احفظ كلّ عنوان قسمٍ كـheading، وكلّ نصٍّ فلسفيّ كـpassage — لا تحذفهما.\n"
    "2. احذف الترويسة الإداريّة (مؤسّسة/مدّة/اسم) والتذييل (مثل «وبالله التوفيق») وأسطر "
    "الإجابة الفارغة (نقاط/خطوط).\n"
    "3. لأسئلة الاختيار: استخرج options كاملة، وحدّد correct (قائمة مؤشّرات الصواب "
    "من 0) بمعرفتك حتّى لو لم تُؤشَّر في المصدر. mcq_single → مؤشّر واحد.\n"
    "4. max_score: من «(3 نقط)» إن وُجدت، وإلّا قدّرها بما يناسب (مقال 4–8، قصير 2–3، "
    "اختيار 0.25–2). heading/passage → 0.\n"
    "5. competency لكلّ سؤالٍ يُجاب عنه، من هذه المفاتيح حصراً: "
    "problematization (صياغة الإشكال) · conceptualization (البنية المفاهيمية) · "
    "argumentation (البنية الحجاجية) · synthesis (التركيب) · knowledge (المعرفة/الأطروحة). "
    "أسئلة المعارف العامّة → knowledge.\n"
    "6. elements: للأسئلة المفتوحة فقط — قائمة عناصر الإجابة النموذجيّة (مؤشّرات نجاح "
    "يصحّح بها لاحقاً). استخرجها من «عناصر الإجابة» المرفقة إن وُجدت وطابِقها بالسؤال؛ "
    "وإلّا اقترح 2–4 عناصر وجيهة.\n"
    "7. لا تخترع أسئلة غير واردة. حافظ على ترتيب الفرض. هذا اقتراحٌ يراجعه الأستاذ."
)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def cloud_available() -> bool:
    """هل الاستيراد السحابيّ مُهيّأ؟ (مفتاح API موجود)."""
    return bool((settings.cloud_ai.api_key or "").strip())


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = _FENCE.sub("", t).strip()
    return t


def extract_quiz_cloud(raw_text: str, answers_text: str = "") -> str:
    """يستخرج فرضاً مهيكلاً (JSON نصّاً) من نصّه الخام عبر نموذج Claude قويّ.

    answers_text: نصّ «عناصر الإجابة» (ملفّ ثانٍ اختياريّ) يُدمَج لبناء مؤشّرات التصحيح.
    يرفع CloudAIUnavailable عند غياب المفتاح/المكتبة أو خطأ الشبكة/الخدمة.
    """
    cfg = settings.cloud_ai
    key = (cfg.api_key or "").strip()
    if not key:
        raise CloudAIUnavailable(
            "لا مفتاح API سحابيّ. ضَع مفتاح Claude في config.ini قسم [cloud_ai] "
            "(api_key=...) أو في متغيّر البيئة ANTHROPIC_API_KEY.")
    text = (raw_text or "").strip()
    if not text:
        raise CloudAIUnavailable("النصّ فارغ — لا محتوى لاستخراجه.")

    try:
        import anthropic  # استيراد كسول: النظام يعمل بلا المكتبة حتّى تُستعمَل هذه الميزة.
    except ImportError as e:
        raise CloudAIUnavailable(
            "مكتبة anthropic غير مثبّتة. شغّل: pip install anthropic") from e

    user = "نصّ الفرض:\n" + text[:24000]
    if (answers_text or "").strip():
        user += "\n\n=== عناصر الإجابة (لمؤشّرات التصحيح) ===\n" + answers_text.strip()[:12000]

    try:
        client = anthropic.Anthropic(api_key=key, timeout=float(cfg.timeout))
        resp = client.messages.create(
            model=cfg.model,
            max_tokens=8000,
            system=SYSTEM_EXTRACT_CLOUD,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.APIConnectionError as e:      # لا إنترنت
        raise CloudAIUnavailable(
            "تعذّر الاتصال بالخدمة السحابيّة — تحقّق من الإنترنت (الاستيراد الذكيّ يحتاج "
            "إنترنت وقت التحضير فقط).") from e
    except anthropic.AuthenticationError as e:
        raise CloudAIUnavailable("مفتاح API غير صالح — راجِع config.ini [cloud_ai].") from e
    except anthropic.APIStatusError as e:
        raise CloudAIUnavailable(f"خطأ من الخدمة السحابيّة ({e.status_code}).") from e
    except Exception as e:  # noqa: BLE001 — أيّ خطأ آخر يُعرَض للأستاذ بلطف
        raise CloudAIUnavailable(f"تعذّر الاستخراج السحابيّ: {e}") from e

    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    out = _strip_fences("".join(parts))
    if not out:
        raise CloudAIUnavailable("لم يُرجِع النموذج نصّاً.")
    # نتحقّق أنّه JSON صالح مبكّراً (المستدعي سيحوّله عبر _ai_json_to_questions).
    try:
        json.loads(out)
    except json.JSONDecodeError as e:
        raise CloudAIUnavailable("أرجع النموذج نصّاً غير صالح JSON — أعِد المحاولة.") from e
    return out
