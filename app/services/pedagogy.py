"""إثراءٌ بيداغوجيٌّ حتميّ للتقرير الجماعيّ (بلا Ollama): تصنيف نوعيّ للمهارات، توزيع
مستويات القسم، تحديد نقاط القوّة والقصور، وتوصيات دعمٍ عمليّة لكلّ كفاية فلسفيّة.

المهارات الستّ (constants.SKILLS): صياغة الإشكال، البنية المفاهيمية، الأطروحة،
البنية الحجاجية، المناقشة، التركيب.
"""

from __future__ import annotations


# نطاقات التمكّن (متوسّط 0..1) → وصفٌ نوعيّ ولون.
_BANDS = [
    (0.80, "متمكّن", "#2e7d32"),
    (0.60, "جيّد", "#558b2f"),
    (0.40, "في طور النموّ", "#f9a825"),
    (0.0, "يحتاج دعماً مكثّفاً", "#c62828"),
]


def band(avg: float | None) -> tuple[str, str]:
    """يحوّل متوسّط المهارة (0..1) إلى (وصفٍ نوعيّ، لون)."""
    if avg is None:
        return ("لم يُقيَّم بعد", "#9e9e9e")
    for threshold, label, color in _BANDS:
        if avg >= threshold:
            return (label, color)
    return ("يحتاج دعماً مكثّفاً", "#c62828")


# توصيات دعمٍ عمليّة لكلّ كفاية — لغةٌ بيداغوجيّة فلسفيّة (المنهاج المغربيّ).
SKILL_REMEDIATION: dict[str, str] = {
    "صياغة الإشكال":
        "درّب المتعلّمين على تحويل الموضوع إلى سؤالٍ إشكاليّ يُبرز التوتّر بين أطروحتين "
        "متقابلتين. أنشطة: صياغة إشكالٍ انطلاقاً من قولة، كشف المفارقة الكامنة في موقفٍ "
        "شائع، التمييز بين السؤال العاديّ والسؤال الإشكاليّ، وتحويل رأيٍ إلى مشكلة فلسفيّة.",
    "البنية المفاهيمية":
        "اشتغل على تحديد المفاهيم وضبط دلالاتها وعلاقاتها (تقابل/تضمّن/تدرّج). أنشطة: "
        "تعريف مفهومٍ محوريّ، التمييز بين مفهومين متقاربين (الحقّ/العدالة)، بناء شبكةٍ "
        "مفاهيميّة للدرس، وتوظيف المفهوم في سياقٍ حجاجيّ.",
    "الأطروحة":
        "درّب على استخراج أطروحة نصٍّ وصياغتها بدقّة، والتمييز بينها وبين حججها الداعمة. "
        "أنشطة: تلخيص أطروحةٍ في جملةٍ واحدة، مقابلة أطروحتين متعارضتين، ونسبة الأطروحة "
        "إلى صاحبها وسياقها الفلسفيّ.",
    "البنية الحجاجية":
        "اعمل على تحليل الحجج وأنواعها (المثال، الاستشهاد، الاستنتاج، المقارنة) وتقويم "
        "قوّتها. أنشطة: استخراج حجّةٍ وتسمية نوعها، بناء حجاجٍ مؤيّدٍ ثمّ معارض، وكشف "
        "المغالطات في استدلالٍ ما.",
    "المناقشة":
        "درّب على مساءلة الأطروحة ومناقشتها بموقفٍ نقديّ يفتح آفاقاً بديلة. أنشطة: نقد "
        "أطروحةٍ بحدودها ونتائجها، طرح أطروحةٍ مضادّة مدعّمة، والانتقال من التحليل إلى "
        "المناقشة دون تكرار.",
    "التركيب":
        "اشتغل على خلاصةٍ تركيبيّة تجمع نتائج التحليل والمناقشة وتصوغ موقفاً شخصيّاً "
        "مبرّراً. أنشطة: كتابة خاتمةٍ مركّبة، صياغة موقفٍ يوازن بين الأطروحات، وربط "
        "الإشكال بالخلاصة.",
}


def _pct(avg: float | None) -> float | None:
    return round(avg * 100, 1) if avg is not None else None


def class_pedagogy(class_rep: dict, student_overalls: list[float | None]) -> dict:
    """يُنتج القراءة البيداغوجيّة للتقرير الجماعيّ من التحليل الحتميّ."""
    skills = class_rep.get("skills") or {}
    detail = []
    for name, sk in skills.items():
        avg = sk.get("avg") if isinstance(sk, dict) else sk
        label, color = band(avg)
        detail.append({"name": name, "pct": _pct(avg), "band": label,
                       "color": color, "avg": avg,
                       "count": (sk.get("count") if isinstance(sk, dict) else None)})
    rated = [d for d in detail if d["avg"] is not None]
    rated.sort(key=lambda d: d["avg"])
    strongest = rated[-1]["name"] if rated else None
    weakest = rated[0]["name"] if rated else None

    # توزيع مستويات القسم من معدّلات التلاميذ (0..1).
    dist = {"متمكّن": 0, "جيّد": 0, "في طور النموّ": 0, "يحتاج دعماً مكثّفاً": 0}
    for ov in student_overalls:
        if ov is None:
            continue
        dist[band(ov)[0]] = dist.get(band(ov)[0], 0) + 1

    # توصيات: أضعف مهارتين + القصور المهيمن (إن اختلف).
    weak_names = [d["name"] for d in rated[:2]]
    dom = class_rep.get("dominant_deficit")
    if dom and dom not in weak_names:
        weak_names.append(dom)
    recommendations = [{"skill": n, "advice": SKILL_REMEDIATION.get(n, "")}
                       for n in weak_names if n]

    return {
        "detail": detail, "strongest": strongest, "weakest": weakest,
        "distribution": dist, "recommendations": recommendations,
        "overall_pct": _pct(class_rep.get("overall")),
        "student_count": class_rep.get("student_count", 0),
        "students_with_data": class_rep.get("students_with_data", 0),
        "dominant_deficit": dom,
        "dominant_share_pct": _pct(class_rep.get("dominant_share")),
    }


def class_narrative(cp: dict, group: str) -> str:
    """خلاصةٌ سرديّة قرائيّة للتقرير الجماعيّ — تُنتَج حتميّاً (بلا Ollama)."""
    parts = []
    swd, sc = cp.get("students_with_data", 0), cp.get("student_count", 0)
    ov = cp.get("overall_pct")
    ov_band = band((ov / 100) if ov is not None else None)[0]
    parts.append(
        f"أنجز تقويمَ الفوج {group} {swd} تلميذاً من أصل {sc}. "
        + (f"بلغ المعدّل العامّ للقسم {ov}٪ (تقدير عامّ: {ov_band}). " if ov is not None else ""))
    if cp.get("strongest") or cp.get("weakest"):
        parts.append(
            f"أقوى الكفايات لدى القسم «{cp.get('strongest') or '—'}»، "
            f"وأضعفها «{cp.get('weakest') or '—'}». ")
    dist = cp.get("distribution") or {}
    nonzero = [f"{v} {k}" for k, v in dist.items() if v]
    if nonzero:
        parts.append("يتوزّع التلاميذ إلى: " + "، ".join(nonzero) + ". ")
    if cp.get("dominant_deficit"):
        share = cp.get("dominant_share_pct")
        parts.append(
            f"القصور المنهجيّ المهيمن هو «{cp['dominant_deficit']}»"
            + (f"، وهو أضعف كفايةٍ لدى {share}٪ من التلاميذ. " if share else ". "))
    recs = [r["skill"] for r in (cp.get("recommendations") or [])]
    if recs:
        parts.append("يُوصى بتركيز الدعم البيداغوجيّ على: " + "، ".join(recs) + ".")
    return "".join(parts)


def student_narrative(sp: dict, name: str) -> str:
    """خلاصةٌ سرديّة قرائيّة لتقرير تلميذ."""
    ov = sp.get("overall_pct")
    ov_band = sp.get("overall_band", "—")
    s = (f"بلغ معدّل التلميذ {name} {ov}٪ (تقدير: {ov_band}). " if ov is not None
         else f"لم تُصادَق بعدُ نتائجُ التلميذ {name}. ")
    if sp.get("strongest") or sp.get("weakest"):
        s += (f"تبرز قوّته في «{sp.get('strongest') or '—'}»، "
              f"ويظهر قصورٌ في «{sp.get('weakest') or '—'}». ")
    recs = [r["skill"] for r in (sp.get("recommendations") or [])]
    if recs:
        s += "يُنصح بتمارين دعمٍ في: " + "، ".join(recs) + "."
    return s


def student_pedagogy(profile: dict) -> dict:
    """قراءةٌ بيداغوجيّة فرديّة: تصنيف نوعيّ لكلّ مهارة، معدّلٌ عامّ ووصفه، نقطة القوّة،
    القصور الحرج، وتوصية دعمٍ شخصيّة لأضعف مهارتين."""
    skills = profile.get("skills") or {}
    detail = []
    for name, sk in skills.items():
        avg = sk.get("avg") if isinstance(sk, dict) else sk
        label, color = band(avg)
        detail.append({"name": name, "pct": _pct(avg), "band": label,
                       "color": color, "avg": avg})
    rated = sorted([d for d in detail if d["avg"] is not None], key=lambda d: d["avg"])
    overall = profile.get("overall")
    overall_label = band(overall)[0] if overall is not None else "لم يُقيَّم بعد"
    weak_names = [d["name"] for d in rated[:2]]
    recommendations = [{"skill": n, "advice": SKILL_REMEDIATION.get(n, "")}
                       for n in weak_names if n]
    return {
        "detail": detail,
        "overall_pct": _pct(overall),
        "overall_band": overall_label,
        "strongest": (rated[-1]["name"] if rated else None),
        "weakest": (rated[0]["name"] if rated else None),
        "recommendations": recommendations,
    }
