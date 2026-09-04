"""اختبار التطبيع العربي (CLAUDE.md §12، §19/9)."""

from app.normalize import normalize, visible_length


def test_alef_and_hamza_unified():
    assert normalize("أحمد") == normalize("احمد")
    assert normalize("إسلام") == normalize("اسلام")
    assert normalize("آية") == normalize("ايه")


def test_taa_marbuta_becomes_haa():
    assert normalize("مدرسة") == normalize("مدرسه")


def test_alef_maqsura_and_tashkeel_and_tatweel():
    # التشكيل والتطويل يُحذفان، والألف المقصورة تصير ياء.
    assert normalize("الْوَعــــى") == normalize("الوعي")


def test_wa3i_forms_all_contain_normalized_root():
    # اختبار §19/9: «الوعى / بالوعي / وعيه» تُطابق النمط «وعي» بعد التطبيع.
    root = normalize("وعي")
    for form in ["الوعى", "بالوعي", "وعيه", "الوَعْي"]:
        assert root in normalize(form), form


def test_spaces_compressed():
    assert normalize("  كلمة    ثانية ") == "كلمه ثانيه"


def test_arabic_digits_folded():
    assert normalize("٢٠٠") == "200"


def test_visible_length_counts_raw_trimmed():
    assert visible_length("  abc  ") == 3
    assert visible_length(None) == 0
