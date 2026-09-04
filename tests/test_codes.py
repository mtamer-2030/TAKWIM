"""اختبار ترميز التلاميذ (CLAUDE.md §3، §7)."""

from app.codes import (
    build_login_code,
    is_valid_roster_id,
    local_part,
    make_login_code,
    parse_roster_id,
    random_suffix,
    split_login_code,
)
from app.constants import CODE_ALPHABET, CODE_SUFFIX_LEN


def test_parse_roster_id_levels():
    assert parse_roster_id("TC1-07") == ("TC1", "07")
    assert parse_roster_id("1BAC2-14") == ("1BAC2", "14")
    assert parse_roster_id("2BAC1-07") == ("2BAC1", "07")


def test_invalid_roster_ids():
    assert parse_roster_id("XX1-07") is None
    assert parse_roster_id("TC1_07") is None
    assert parse_roster_id("TC1-") is None
    assert not is_valid_roster_id("bad")


def test_suffix_alphabet_has_no_confusing_chars():
    for bad in "IOSZ015":
        assert bad not in CODE_ALPHABET
    s = random_suffix()
    assert len(s) == CODE_SUFFIX_LEN
    assert all(ch in CODE_ALPHABET for ch in s)


def test_make_login_code_unique_and_shaped():
    used = set()
    code = make_login_code("TC1-07", lambda c: c in used)
    assert code.startswith("TC1-07-")
    assert split_login_code(code) == ("TC1-07", code.split("-")[-1])


def test_local_part_five_chars():
    # الجزء الذي يُدخله التلميذ على الهاتف: 07-K7 (خمسة محارف).
    assert local_part("TC1-07-K7") == "07-K7"
    assert len(local_part("TC1-07-K7")) == 5


def test_build_login_code_from_typed_local():
    assert build_login_code("TC1", "07-K7") == "TC1-07-K7"
    assert build_login_code("TC1", " 07-k7 ") == "TC1-07-K7"  # يتساهل مع المسافات/الأحرف
    # لصق الرمز الكامل بالخطأ يُقبل أيضاً
    assert build_login_code("TC1", "TC1-07-K7") == "TC1-07-K7"


def test_build_login_code_rejects_garbage():
    assert build_login_code("TC1", "hello") is None
    assert build_login_code("TC1", "") is None
