"""استيراد لوائح الإدارة مباشرةً: كشف عمود رمز مسار بالمحتوى (لا بالعنوان فقط)،
والتعرّف على class_label كفوج — فتُستورَد لائحة الإدارة (roster_id = أرقام مسار) بلا
إعادة تسمية، ويُطابَق التلاميذ فيُحدَّث فوجهم بلا تكرار."""

import io

import pandas as pd

from app.importer import parse_students_excel


def _xlsx(rows, cols) -> bytes:
    df = pd.DataFrame(rows, columns=cols)
    b = io.BytesIO()
    with pd.ExcelWriter(b, engine="openpyxl") as x:
        df.to_excel(x, index=False)
    return b.getvalue()


def test_admin_roster_roster_id_holding_massar_is_detected():
    data = _xlsx(
        [["TC3", "C171023826", "ابايا ايمن"],
         ["TC3", "D160038918", "ازهايدي نسرين"]],
        ["class_label", "roster_id", "full_name"])
    r = parse_students_excel(data)
    assert r.ok and len(r.rows) == 2
    assert all(x.massar_code for x in r.rows)          # كُشف رمز مسار بالمحتوى
    assert r.rows[0].massar_code == "C171023826"
    assert all(x.group_name == "TC3" for x in r.rows)  # class_label كفوج


def test_real_roster_id_labels_not_mistaken_for_massar():
    """roster_id على شكل TC3-01 (مُعرّف داخليّ لا رقم مسار) لا يُكتشف كرمز مسار."""
    data = _xlsx(
        [["TC3", "TC3-01", "محمد"], ["TC3", "TC3-02", "سعاد"]],
        ["class_label", "roster_id", "full_name"])
    r = parse_students_excel(data)
    assert r.ok and len(r.rows) == 2
    assert all(x.massar_code is None for x in r.rows)   # لم يُخطئ
    assert all(x.group_name == "TC3" for x in r.rows)


def test_explicit_massar_header_still_wins():
    data = _xlsx(
        [["أحمد", "R987654321", "TC1"]],
        ["الاسم الكامل", "رمز مسار", "القسم"])
    r = parse_students_excel(data)
    assert r.rows[0].massar_code == "R987654321" and r.rows[0].group_name == "TC1"
