"""اختبار استيراد اللوائح (CLAUDE.md §8، §19/5، §19/6)."""

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
    # §19/5: تكرار roster_id ← يُرفض الملفّ كلّه مع تحديد السطر.
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
    assert any("أعمدة ناقصة" in e for e in p.errors)
