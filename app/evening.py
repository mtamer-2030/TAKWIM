"""المساعدة المسائية — خارج القاعة، على جلسة مغلقة (CLAUDE.md §14).

قيود صارمة:
- لا تُستدعى أثناء أي جلسة إطلاقاً. تعمل على جلسة status='closed' فقط.
- بلا اتصال ← النظام يبقى كاملاً برسالة أن المساعدة غير متاحة (ليست خطأ).
- لا تُستشار في المؤشّرات التي لها check_rule (محسومة يقيناً).
- لا يُرسَل اسم التلميذ إطلاقاً — roster_id فقط.
- quote إجباري لكل مؤشّر متحقّق ويجب أن يوجد حرفياً في جواب التلميذ (بعد التطبيع)؛
  وإلّا فالمؤشّر غير متحقّق تلقائياً ويُوسم للمراجعة.
- ai_score مقترح ولا يدخل تقريراً قبل teacher_confirmed = 1.
- معالجة بالدفعات مع استئناف.
"""

from __future__ import annotations

import json
import sqlite3

from .normalize import normalize
from .scoring import answer_text
from .settings import EveningAI


class OfflineError(RuntimeError):
    """المساعدة غير متاحة (معطّلة أو بلا مفتاح أو تعذّر الاتصال)."""


# ————————————————————— اختيار الأجوبة المؤهّلة —————————————————————


def indicators_needing_model(indicators: list[dict]) -> list[dict]:
    """المؤشّرات التي لا check_rule لها فقط — الباقي محسوم يقيناً."""
    return [i for i in indicators if not i.get("check_rule")]


def pending_answers(conn: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    """أجوبة الأسئلة المفتوحة على الجلسة، التي فيها مؤشّرات تحتاج النموذج ولم تُعالَج."""
    return conn.execute(
        """
        SELECT an.id AS answer_id, an.raw_json AS raw_json, an.ai_run_at AS ai_run_at,
               q.id AS qid, q.type AS type, q.prompt AS prompt,
               q.indicators_json AS indicators_json, q.max_score AS max_score,
               st.roster_id AS roster_id
        FROM answers an
        JOIN attempts at ON at.id = an.attempt_id
        JOIN students st ON st.id = at.student_id
        JOIN questions q ON q.id = an.question_id
        WHERE at.session_id = ?
          AND q.type IN ('short_text','grid','long_text')
        ORDER BY q.position, st.roster_id
        """,
        (session_id,),
    ).fetchall()


# ————————————————————— العقد المفروض والتحقّق من الاقتباس —————————————————————


def verify_and_score(indicators: list[dict], model_verdicts: list[dict],
                     text: str) -> tuple[dict, float, bool]:
    """يطبّق العقد: quote حرفي إلزامي لكلّ مؤشّر متحقّق.

    يعيد (verdicts_by_id، ai_score، flagged_any).
    - المؤشّر الذي له check_rule يُحسم يقيناً (لا يُسأل النموذج عنه) خارج هذه الدالّة.
    - المؤشّر المتحقّق باقتباس غير موجود ← غير متحقّق تلقائياً ويُوسم.
    """
    norm_text = normalize(text)
    by_id_input = {v.get("id"): v for v in (model_verdicts or []) if isinstance(v, dict)}
    verdicts: dict[str, dict] = {}
    score = 0.0
    flagged_any = False

    for ind in indicators:
        iid = ind.get("id")
        pts = float(ind.get("points", 0) or 0)
        if ind.get("check_rule"):
            # لا يُستشار النموذج فيه — يُترك لِما حسمته قاعدة الفحص وقت الجلسة.
            continue
        mv = by_id_input.get(iid)
        if not mv or not mv.get("met"):
            verdicts[iid] = {"met": False, "quote": None, "flagged": False, "points": pts}
            continue
        quote = (mv.get("quote") or "").strip()
        if quote and normalize(quote) in norm_text:
            verdicts[iid] = {"met": True, "quote": quote, "flagged": False, "points": pts}
            score += pts
        else:
            # اقتباس غير موجود ← يقتل الهذيان: غير متحقّق تلقائياً ويُوسم للمراجعة.
            verdicts[iid] = {"met": False, "quote": quote or None, "flagged": True,
                             "points": pts}
            flagged_any = True

    return verdicts, round(score, 4), flagged_any


# ————————————————————— نداء المزوّد (معزول، قابل للتعطيل) —————————————————————


def _build_messages(prompt: str, indicators: list[dict], text: str, roster_id: str):
    ind_lines = "\n".join(
        f'- id={i.get("id")}: {i.get("text")}' for i in indicators
    )
    system = (
        "أنت مساعد تصحيح لأستاذ فلسفة. مهمّتك فحص مؤشّرات جواب تلميذ فحصاً محافظاً. "
        "لكلّ مؤشّر قرّر met=true فقط إذا تحقّق صراحةً في نصّ الجواب، وأرفق quote مقطعاً "
        "منسوخاً حرفياً من جواب التلميذ يثبت التحقّق. لا تخترع اقتباساً ولا تعمّم. "
        "أعِد JSON فقط بالشكل: {\"verdicts\":[{\"id\":\"i4\",\"met\":true,"
        "\"quote\":\"...\"}]}."
    )
    user = (
        f"التلميذ (رمز): {roster_id}\n"
        f"السؤال: {prompt}\n\n"
        f"المؤشّرات المطلوب فحصها:\n{ind_lines}\n\n"
        f"جواب التلميذ:\n«{text}»"
    )
    return system, user


def call_provider(cfg: EveningAI, prompt: str, indicators: list[dict],
                  text: str, roster_id: str) -> list[dict]:
    """ينادي المزوّد ويعيد قائمة verdicts خاماً. يرمي OfflineError عند التعذّر."""
    if not cfg.usable:
        raise OfflineError("المساعدة المسائية غير مفعّلة أو بلا مفتاح.")
    system, user = _build_messages(prompt, indicators, text, roster_id)
    try:
        import httpx  # يُستورد هنا حتى لا يكون شرطاً للإقلاع
    except ImportError as exc:  # pragma: no cover
        raise OfflineError("httpx غير متوفّرة.") from exc

    try:
        if cfg.provider == "anthropic":
            resp = httpx.post(
                f"{cfg.base_url}/v1/messages",
                headers={
                    "x-api-key": cfg.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": cfg.model,
                    "max_tokens": 1024,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            content = "".join(
                block.get("text", "") for block in data.get("content", [])
                if block.get("type") == "text"
            )
        else:
            raise OfflineError(f"مزوّد غير مدعوم: {cfg.provider}")
    except OfflineError:
        raise
    except Exception as exc:  # شبكة/مهلة/رفض ← غير متاحة، وليست حالة خطأ للنظام
        raise OfflineError(f"تعذّر الاتصال بالمساعدة المسائية: {exc}") from exc

    return _extract_verdicts(content)


def _extract_verdicts(content: str) -> list[dict]:
    """يستخرج verdicts من نصّ النموذج بتسامح (قد يُحيط JSON بكلام)."""
    content = (content or "").strip()
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1:
        return []
    try:
        obj = json.loads(content[start:end + 1])
    except ValueError:
        return []
    verdicts = obj.get("verdicts", [])
    return verdicts if isinstance(verdicts, list) else []


# ————————————————————— المرور الدفعي مع الاستئناف —————————————————————


def run_evening(conn: sqlite3.Connection, cfg: EveningAI, session_id: int,
                limit: int | None = None) -> dict:
    """يمرّ على الأجوبة المفتوحة غير المعالَجة ويخزّن اقتراحات النموذج.

    يخزّن التقدّم بعد كلّ جواب، فالمرور قابل للاستئناف إن انقطع.
    يرمي OfflineError إن كانت المساعدة غير متاحة (يُلتقط في الوجهة).
    """
    if not cfg.usable:
        raise OfflineError("المساعدة المسائية غير متاحة.")

    session = conn.execute("SELECT status FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if session is None or session["status"] != "closed":
        raise RuntimeError("المساعدة المسائية تعمل على جلسة مغلقة فقط.")

    rows = pending_answers(conn, session_id)
    todo = [r for r in rows if not r["ai_run_at"]]
    if limit:
        todo = todo[:limit]

    processed = flagged = 0
    for r in todo:
        blob = json.loads(r["indicators_json"] or "{}")
        indicators = blob.get("indicators", [])
        need = indicators_needing_model(indicators)
        if not need:
            # لا شيء للنموذج ليفعله — نُعلّمه كمعالَج حتى لا يتكرّر.
            conn.execute(
                "UPDATE answers SET ai_run_at = datetime('now'), ai_model = ? WHERE id = ?",
                (cfg.model, r["answer_id"]),
            )
            processed += 1
            continue

        raw = json.loads(r["raw_json"]) if r["raw_json"] else None
        text = answer_text(r["type"], raw)
        raw_verdicts = call_provider(cfg, r["prompt"], need, text, r["roster_id"])
        verdicts, ai_score, flagged_any = verify_and_score(indicators, raw_verdicts, text)

        conn.execute(
            "UPDATE answers SET ai_score = ?, ai_verdicts_json = ?, ai_model = ?, "
            "ai_run_at = datetime('now') WHERE id = ?",
            (ai_score, json.dumps(verdicts, ensure_ascii=False), cfg.model, r["answer_id"]),
        )
        processed += 1
        flagged += 1 if flagged_any else 0

    remaining = len([r for r in pending_answers(conn, session_id) if not r["ai_run_at"]])
    return {"processed": processed, "flagged": flagged, "remaining": remaining}


def agreement_stats(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    """شاشة «تطابق» (§14): نسبة اتفاق المقترح مع تصديق الأستاذ لكلّ مؤشّر.

    تُحسب على المؤشّرات المصادَق عليها فقط (teacher_confirmed=1)، حيث يمكن
    مقارنة met المقترح بقرار الأستاذ الضمنيّ (manual_score مقابل ai_score).
    مؤشّر تتكرّر مخالفته غالباً سوء صياغة لا غباء نموذج؛ تنبيه تحت ٧٠٪.
    """
    rows = conn.execute(
        """
        SELECT q.id AS qid, q.prompt AS prompt, an.ai_verdicts_json AS aiv,
               an.rule_verdicts_json AS rv, an.teacher_confirmed AS confirmed,
               an.manual_score AS manual, an.ai_score AS ai
        FROM answers an
        JOIN attempts at ON at.id = an.attempt_id
        JOIN questions q ON q.id = an.question_id
        WHERE at.session_id = ? AND an.ai_verdicts_json IS NOT NULL
        """,
        (session_id,),
    ).fetchall()
    # تجميع بسيط لكلّ سؤال: كم اقتراحاً صادق عليه الأستاذ دون تعديل النقطة.
    agg: dict[int, dict] = {}
    for r in rows:
        a = agg.setdefault(r["qid"], {"prompt": r["prompt"], "total": 0, "agree": 0})
        a["total"] += 1
        if r["confirmed"] and r["manual"] is not None and r["ai"] is not None:
            if abs(float(r["manual"]) - float(r["ai"])) < 1e-6:
                a["agree"] += 1
    out = []
    for qid, a in agg.items():
        rate = (a["agree"] / a["total"]) if a["total"] else None
        out.append({"qid": qid, "prompt": a["prompt"], "total": a["total"],
                    "agree": a["agree"], "rate": rate,
                    "low": rate is not None and rate < 0.70})
    return out
