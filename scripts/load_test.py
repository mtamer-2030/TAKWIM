"""اختبار حمولة واقعيّ على الشبكة (البند ٤-ب) — يُشغَّل على جهاز آخر مقابل الخادم الحيّ.

يحاكي عدداً من المتعلّمين يلجون ويسلّمون تقويماً منشوراً في الآن نفسه، ثمّ يطبع
كم تسليماً نجح. **المعيار الوحيد المقبول: نجاح تسليمات المتعلّمين جميعاً** (٤٥ لا ٤٣)،
ويؤكَّد ذلك بمطابقة عدد الأجوبة في «التقارير التراكمية» بلوحة الأستاذ.

التهيئة قبل التشغيل:
  1) انشر تقويماً كواجب منزليّ لمستوى معيّن (بلا جلسة صفّية، فلا قفل جهاز).
  2) جهّز رموز دخول المتعلّمين في ذلك المستوى.

الاستعمال:
  python scripts/load_test.py --url http://192.168.1.50:8000 --quiz-id 3 \
      --codes 07-AB,07-CD,07-EF   # أو: --codes-file codes.txt (رمز في كلّ سطر)

يتطلّب httpx (مثبَّت أصلاً). لا يكتب شيئاً في القاعدة سوى أجوبة المتعلّمين الوهميّين.
"""

from __future__ import annotations

import argparse
import asyncio
import time


async def _one_client(base: str, quiz_id: int, code: str) -> tuple[str, bool, str]:
    import httpx
    try:
        async with httpx.AsyncClient(base_url=base, timeout=30, follow_redirects=False) as c:
            # 1) دخول + تأكيد الاسم (يضبط كوكي المتعلّم الموقَّع)
            await c.post("/student/login", data={"code": code})
            r = await c.post("/student/login/confirm", data={"code": code})
            if r.status_code not in (200, 303):
                return code, False, f"login {r.status_code}"
            # 2) فتح التقويم (قد يضبط كوكي الجهاز)
            r = await c.get(f"/student/quiz/{quiz_id}")
            if r.status_code != 200:
                return code, False, f"open {r.status_code}"
            # 3) التسليم (بأجوبة فارغة يكفي لاختبار وصول الكتابة)
            r = await c.post(f"/student/quiz/{quiz_id}/submit", data={})
            ok = r.status_code in (200, 303)
            return code, ok, f"submit {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return code, False, f"{type(e).__name__}: {e}"


async def _main(base: str, quiz_id: int, codes: list[str]) -> None:
    t0 = time.time()
    results = await asyncio.gather(*(_one_client(base, quiz_id, c) for c in codes))
    dt = time.time() - t0
    ok = sum(1 for _, good, _ in results if good)
    print(f"\nنجح {ok}/{len(codes)} تسليماً في {dt:.1f}ث "
          f"(متوسّط {dt / max(len(codes), 1):.2f}ث/عميل)\n")
    for code, good, info in results:
        print(f"  {'✓' if good else '✗'} {code}: {info}")
    if ok != len(codes):
        print("\n⚠ لم تنجح كلّ التسليمات — راجع سعة الراوتر (سقف ٣٢ لاسلكياً) والسجلّ.")
    print("\nأكّد الآن من «التقارير التراكمية» أنّ عدد الأجوبة يطابق عدد المتعلّمين.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="اختبار حمولة PHILO-TECH")
    ap.add_argument("--url", required=True, help="عنوان الخادم، مثل http://192.168.1.50:8000")
    ap.add_argument("--quiz-id", type=int, required=True, help="معرّف تقويم منشور")
    ap.add_argument("--codes", default="", help="رموز دخول مفصولة بفواصل")
    ap.add_argument("--codes-file", default="", help="ملفّ رموز (رمز في كلّ سطر)")
    a = ap.parse_args()
    codes = [x.strip() for x in a.codes.split(",") if x.strip()]
    if a.codes_file:
        with open(a.codes_file, encoding="utf-8") as f:
            codes += [ln.strip() for ln in f if ln.strip()]
    if not codes:
        ap.error("لا رموز: مرّر --codes أو --codes-file")
    asyncio.run(_main(a.url.rstrip("/"), a.quiz_id, codes))
