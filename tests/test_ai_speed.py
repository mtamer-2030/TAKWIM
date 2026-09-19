"""تسريعُ التصحيح المسائي المُعان (Ollama): اقتراحُ النقطة يستعمل سقفَ توليدٍ صغيراً
ونافذةَ سياقٍ صغيرة ومُدخَلاً مُقلَّماً — فتقلّ مدّةُ الاستجابة على العتاد الضعيف."""

import app.ai_feedback as ai


def test_suggest_open_score_uses_fast_params(monkeypatch):
    captured = {}

    def fake_generate(system, prompt, fmt=None, num_predict=350, num_ctx=None, timeout=None):
        captured.update(num_predict=num_predict, num_ctx=num_ctx, timeout=timeout,
                        fmt=fmt, prompt=prompt)
        return '{"score": 2.5, "note": "جيد"}'

    monkeypatch.setattr(ai, "_generate", fake_generate)
    score, note = ai.suggest_open_score("سؤالٌ طويل" * 200, "عناصر" * 400,
                                        "جواب التلميذ" * 400, 4)
    assert score == 2.5 and note == "جيد"
    assert captured["fmt"] == "json"
    assert captured["num_predict"] <= 128          # لا 350 — مخرَجٌ قصير
    assert captured["num_ctx"] <= 2048             # نافذةٌ صغيرة
    assert captured["timeout"] <= 120              # مهلةٌ أقصر تفشل سريعاً لا تتعلّق
    assert len(captured["prompt"]) < 6000          # المُدخَل مُقلَّم (لا آلاف الأحرف)


def test_warm_up_silent_when_engine_down(monkeypatch):
    def boom(*a, **k):
        raise ai.AIUnavailable("مغلق")

    monkeypatch.setattr(ai, "_generate", boom)
    ai.warm_up()          # لا يرمي استثناءً — أفضل جهدٍ صامت


def test_generate_applies_small_ctx_and_gpu(monkeypatch):
    """يُثبت أنّ _generate يحقن num_ctx الصغير من الإعداد وnum_gpu عند ضبطه."""
    sent = {}

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"response": "ok"}

    class FakeHTTPX:
        TimeoutException = ai.AIUnavailable        # لن يُرمى هنا
        HTTPStatusError = ai.AIUnavailable

        @staticmethod
        def post(url, json=None, timeout=None):
            sent.update(json)
            return FakeResp()

    monkeypatch.setattr(ai.settings.local_ai, "num_ctx", 1024, raising=False)
    monkeypatch.setattr(ai.settings.local_ai, "num_gpu", 99, raising=False)
    import sys
    monkeypatch.setitem(sys.modules, "httpx", FakeHTTPX)
    out = ai._generate("s", "u")
    assert out == "ok"
    opts = sent["options"]
    assert opts["num_ctx"] == 1024 and opts["num_gpu"] == 99
