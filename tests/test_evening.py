"""اختبار المساعدة المسائية (CLAUDE.md §14، §19/10، §19/11)."""

from app.evening import OfflineError, indicators_needing_model, run_evening, verify_and_score
from app.settings import EveningAI


INDICATORS = [
    {"id": "i1", "text": "صيغة استفهامية", "points": 1,
     "check_rule": {"type": "any_of", "patterns": ["؟"]}},   # محسوم يقيناً — لا يُسأل النموذج
    {"id": "i4", "text": "توتّر بين موقفين", "points": 2},    # يحتاج النموذج
]


def test_only_unruled_indicators_go_to_model():
    need = indicators_needing_model(INDICATORS)
    assert [i["id"] for i in need] == ["i4"]


def test_quote_must_exist_verbatim():
    text = "هناك توتّر واضح بين الوعي واللاوعي في تفسير السلوك."
    # النموذج يزعم التحقّق باقتباس موجود فعلاً (بعد التطبيع)
    verdicts, score, flagged = verify_and_score(
        INDICATORS, [{"id": "i4", "met": True, "quote": "توتّر واضح بين الوعي واللاوعي"}], text
    )
    assert verdicts["i4"]["met"] is True
    assert score == 2 and flagged is False


def test_hallucinated_quote_is_rejected_and_flagged():
    # §19/10: مخرَج يستشهد بمقطع غير موجود ← المؤشّر يُرفض تلقائياً ويُوسم.
    text = "جواب قصير لا يذكر التوتّر."
    verdicts, score, flagged = verify_and_score(
        INDICATORS, [{"id": "i4", "met": True, "quote": "عبارة مخترعة غير موجودة"}], text
    )
    assert verdicts["i4"]["met"] is False
    assert verdicts["i4"]["flagged"] is True
    assert score == 0 and flagged is True


def test_offline_when_no_key():
    cfg = EveningAI(enabled=True, api_key="")   # مفعّلة لكن بلا مفتاح
    assert cfg.usable is False


def test_run_evening_offline_raises():
    cfg = EveningAI(enabled=False)
    try:
        run_evening(conn=None, cfg=cfg, session_id=1)  # لا يصل إلى القاعدة
        assert False, "expected OfflineError"
    except OfflineError:
        pass
