"""القراءة البيداغوجيّة الحتميّة للتقارير: تصنيف نوعيّ، توزيع مستويات، قوّة/قصور،
وتوصيات دعمٍ عمليّة — جماعيّاً وفرديّاً (بلا Ollama)."""

from app.services.pedagogy import (band, class_narrative, class_pedagogy,
                                   student_narrative, student_pedagogy,
                                   SKILL_REMEDIATION)


def test_band_thresholds():
    assert band(0.9)[0] == "متمكّن"
    assert band(0.65)[0] == "جيّد"
    assert band(0.5)[0] == "في طور النموّ"
    assert band(0.2)[0] == "يحتاج دعماً مكثّفاً"
    assert band(None)[0] == "لم يُقيَّم بعد"


def test_class_pedagogy_complete():
    cr = {
        "skills": {"صياغة الإشكال": {"avg": 0.82, "count": 4},
                   "التركيب": {"avg": 0.30, "count": 3},
                   "المناقشة": {"avg": 0.55, "count": 2}},
        "overall": 0.55, "dominant_deficit": "التركيب", "dominant_share": 0.6,
        "student_count": 5, "students_with_data": 4,
    }
    cp = class_pedagogy(cr, [0.85, 0.55, 0.30, None])
    assert cp["strongest"] == "صياغة الإشكال" and cp["weakest"] == "التركيب"
    assert cp["overall_pct"] == 55.0
    assert cp["distribution"]["متمكّن"] == 1
    assert cp["distribution"]["في طور النموّ"] == 1
    assert cp["distribution"]["يحتاج دعماً مكثّفاً"] == 1
    # التوصيات تشمل أضعف كفايةٍ بنصٍّ بيداغوجيّ عمليّ
    skills_reco = {r["skill"] for r in cp["recommendations"]}
    assert "التركيب" in skills_reco
    assert all(r["advice"] for r in cp["recommendations"])
    assert SKILL_REMEDIATION["التركيب"] in [r["advice"] for r in cp["recommendations"]]


def test_student_pedagogy_complete():
    sp = student_pedagogy({
        "skills": {"صياغة الإشكال": {"avg": 0.9}, "التركيب": {"avg": 0.2}},
        "overall": 0.55})
    assert sp["overall_pct"] == 55.0 and sp["overall_band"] == "في طور النموّ"
    assert sp["strongest"] == "صياغة الإشكال" and sp["weakest"] == "التركيب"
    assert sp["recommendations"] and sp["recommendations"][0]["advice"]


def test_empty_class_pedagogy_no_crash():
    cp = class_pedagogy({"skills": {}, "overall": None}, [])
    assert cp["strongest"] is None and cp["weakest"] is None
    assert cp["recommendations"] == []


def test_narratives_are_readable():
    cr = {"skills": {"صياغة الإشكال": {"avg": 0.9, "count": 3},
                     "التركيب": {"avg": 0.3, "count": 3}},
          "overall": 0.6, "dominant_deficit": "التركيب", "dominant_share": 0.5,
          "student_count": 20, "students_with_data": 12}
    cp = class_pedagogy(cr, [0.9, 0.6, 0.3])
    text = class_narrative(cp, "TC1")
    assert "TC1" in text and "12" in text and "التركيب" in text and "يُوصى" in text
    sp = student_pedagogy({"skills": {"صياغة الإشكال": {"avg": 0.9}},
                           "overall": 0.7})
    st = student_narrative(sp, "أمين")
    assert "أمين" in st and "70.0٪" in st
