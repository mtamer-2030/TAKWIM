"""اختبار استيراد اللوائح (CLAUDE.md §8، §19/5، §19/6) — بما فيه أرقام مسار."""

from app.codes import parse_roster_id
from app.rosters import analyze_csv

GOOD = """class_label,roster_id,full_name
TC1,TC1-01,محمد أمين
TC1,TC1-02,سلمى بناني
1BAC2,1BAC2-14,ياسين العلوي
"""

DUP = """class_label,roster_id,full_name
TC1,TC1-01,محمد
TC1,TC1-01,محمد مكرّر
"""

# لائحة واقعية بأرقام مسار (letter+digits) — العمود اسمه roster_id لكنّ قيمه مسار.
MASSAR = """class_label,roster_id,full_name
TC1,D161055238,الإدريسي صفوان
TC1,F178038975,البنوري جنات
TC1,R161051872,أيت احمد حياة
"""


def test_good_preview_counts():
    p = analyze_csv(GOOD, existing_roster_ids=set())
    assert p.ok, p.errors
    assert p.to_add == 3 and p.to_update == 0
    assert set(p.new_classes) == {"TC1", "1BAC2"}


def test_update_vs_add():
    p = analyze_csv(GOOD, existing_roster_ids={"TC1-01"})
    assert p.ok
    assert p.to_add == 2 and p.to_update == 1


def test_duplicate_roster_rejects_whole_file():
    p = analyze_csv(DUP, existing_roster_ids=set())
    assert not p.ok
    assert any("مكرّر" in e and "السطر 3" in e for e in p.errors)


def test_roster_not_matching_class_rejected():
    bad = "class_label,roster_id,full_name\nTC1,TC2-01,اسم\n"
    p = analyze_csv(bad, existing_roster_ids=set())
    assert not p.ok
    assert any("لا يتبع القسم" in e for e in p.errors)


def test_bom_is_stripped():
    p = analyze_csv("﻿" + GOOD, existing_roster_ids=set())
    assert p.ok, p.errors
    assert p.to_add == 3


def test_missing_headers_rejected():
    p = analyze_csv("a,b,c\n1,2,3\n", existing_roster_ids=set())
    assert not p.ok
    assert any("أعمدة ناقصة" in e or "رمز التلميذ" in e for e in p.errors)


# ————— أرقام مسار —————

def test_massar_codes_accepted():
    p = analyze_csv(MASSAR, existing_roster_ids=set(), existing_massar_ids=set())
    assert p.ok, p.errors
    assert p.to_add == 3
    # كلّها في وضع مسار (roster_id يُولَّد لاحقاً)
    assert all(r.massar_id is not None and r.roster_id is None for r in p.rows)


def test_massar_update_matches_by_massar():
    p = analyze_csv(MASSAR, existing_roster_ids=set(),
                    existing_massar_ids={"D161055238"})
    assert p.ok
    assert p.to_update == 1 and p.to_add == 2


def test_duplicate_massar_rejected():
    dup = "class_label,roster_id,full_name\nTC1,D1,أ\nTC1,D1,ب\n"
    p = analyze_csv(dup, existing_roster_ids=set())
    assert not p.ok
    assert any("مكرّر" in e and "السطر 3" in e for e in p.errors)


def test_trailing_blank_row_ignored():
    # ذيل مثل «TC1,,» (بلا رمز ولا اسم) يُتجاهَل بهدوء.
    text = MASSAR + "TC1,,\n\n"
    p = analyze_csv(text, existing_roster_ids=set())
    assert p.ok, p.errors
    assert p.to_add == 3


def test_missing_name_still_flagged():
    text = "class_label,roster_id,full_name\nTC1,D161055238,\n"
    p = analyze_csv(text, existing_roster_ids=set())
    assert not p.ok
    assert any("الاسم الكامل مفقود" in e for e in p.errors)


def test_generated_roster_id_shape():
    # بعد التوليد يجب أن يكون roster_id بصيغة TC1-NN صالحة.
    assert parse_roster_id("TC1-01") == ("TC1", "01")
