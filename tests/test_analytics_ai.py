"""اختبار طبقة التحليل والذكاء الاصطناعي المحلّي (المرحلة 5).

التحليل يُختبَر على قاعدة لامتزامنة في الذاكرة؛ الذكاء الاصطناعي يُختبَر بمحاكاة
استجابة Ollama (Mocking) وبمحاكاة انقطاعه (تدهور لطيف).
"""

import asyncio

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai_feedback import (
    AIUnavailable,
    OFFLINE_MESSAGE,
    build_class_prompt,
    build_student_prompt,
    generate_student_plan,
    ollama_available,
)
from app.constants import SKILLS
from app.models import (
    AnalysisQuestion,
    Base,
    EvaluationEvent,
    EventType,
    Level,
    PhilosophicalText,
    Skill,
    Student,
    Submission,
    TextType,
)
from app.services.analytics import (
    generate_class_report,
    generate_student_skill_profile,
)


async def _seed():
    """قاعدة في الذاكرة: تلميذان، أسئلة بمهارات مختلفة، إنجازات مصادَقة."""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    async with Session() as s:
        lvl = Level(name="الجذع المشترك", code="TC"); s.add(lvl); await s.flush()
        # هيكل أدنى لسؤال تحليل
        from app.models import Axis, Module
        m = Module(level_id=lvl.id, title="الفلسفة"); s.add(m); await s.flush()
        ax = Axis(module_id=m.id, title="نشأة الفلسفة"); s.add(ax); await s.flush()
        txt = PhilosophicalText(axis_id=ax.id, title="نص", content="…",
                                text_type=TextType.BASIC); s.add(txt); await s.flush()
        # المهارات الستّ الموحّدة مبذورة في جدول skills
        skills = {name: Skill(name=name, position=i) for i, name in enumerate(SKILLS)}
        s.add_all(list(skills.values())); await s.flush()
        # أسئلة: صياغة الإشكال (ضعيف)، البنية الحجاجية (قوي)
        q_prob = AnalysisQuestion(text_id=txt.id, prompt="أشكل", max_score=4,
                                  skill_id=skills["صياغة الإشكال"].id)
        q_arg = AnalysisQuestion(text_id=txt.id, prompt="حاجج", max_score=4,
                                 skill_id=skills["البنية الحجاجية"].id)
        s.add_all([q_prob, q_arg]); await s.flush()
        ev = EvaluationEvent(title="تشخيصي", event_type=EventType.DIAGNOSTIC)
        s.add(ev); await s.flush()
        s1 = Student(full_name="تلميذ أ", level_id=lvl.id, group_name="TC1", massar_code="A")
        s2 = Student(full_name="تلميذ ب", level_id=lvl.id, group_name="TC1", massar_code="B")
        s.add_all([s1, s2]); await s.flush()
        # الأشكلة ضعيفة (1/4=25%)، الحجاج قوية (3.5/4≈88%)، مصادَق عليها
        for st in (s1, s2):
            s.add(Submission(student_id=st.id, event_id=ev.id, question_id=q_prob.id,
                             score=1, max_score=4, teacher_confirmed=True))
            s.add(Submission(student_id=st.id, event_id=ev.id, question_id=q_arg.id,
                             score=3.5, max_score=4, teacher_confirmed=True))
        # إنجاز غير مصادَق لا يُحتسب
        s.add(Submission(student_id=s1.id, event_id=ev.id, question_id=q_prob.id,
                         score=4, max_score=4, teacher_confirmed=False))
        await s.commit()
        return eng, Session, s1.id


def test_student_skill_profile():
    async def run():
        eng, Session, sid = await _seed()
        async with Session() as s:
            p = await generate_student_skill_profile(s, sid)
        await eng.dispose()
        return p
    p = asyncio.run(run())
    assert p["has_data"]
    assert p["skills"]["صياغة الإشكال"]["avg"] == 0.25
    assert p["skills"]["البنية الحجاجية"]["avg"] == pytest.approx(0.875)
    assert p["critical_deficit"] == "صياغة الإشكال"     # أضعف مهارة
    assert p["strength"] == "البنية الحجاجية"            # أقوى مهارة


def test_class_report_dominant_deficit():
    async def run():
        eng, Session, _ = await _seed()
        async with Session() as s:
            r = await generate_class_report(s, "TC1")
        await eng.dispose()
        return r
    r = asyncio.run(run())
    assert r["has_data"] and r["student_count"] == 2
    assert r["dominant_deficit"] == "صياغة الإشكال"   # القصور المنهجي المهيمن
    assert r["dominant_share"] == 1.0                 # هو أضعف مهارة لدى الجميع


# ————— الذكاء الاصطناعي: هندسة الأمر والتدهور اللطيف —————

def test_prompts_are_arabic_and_contain_skills():
    profile = {"student_name": "تلميذ أ", "skills": {
        "صياغة الإشكال": {"avg": 0.25, "count": 2},
        "البنية المفاهيمية": {"avg": None, "count": 0},
        "البنية الحجاجية": {"avg": 0.88, "count": 2},
        "الأطروحة": {"avg": None, "count": 0},
        "المناقشة": {"avg": None, "count": 0},
        "التركيب": {"avg": None, "count": 0}},
        "strength": "البنية الحجاجية", "critical_deficit": "صياغة الإشكال",
        "overall": 0.56}
    txt = build_student_prompt(profile)
    assert "صياغة الإشكال" in txt and "القصور الحرج" in txt and "٣ نقاط" in txt
    creport = {"group_name": "TC1", "student_count": 2, "skills": profile["skills"],
               "dominant_deficit": "صياغة الإشكال", "dominant_share": 1.0, "overall": 0.56}
    assert "القصور المنهجي المهيمن" in build_class_prompt(creport)


def test_ai_offline_graceful(monkeypatch):
    # محاكاة انقطاع Ollama: httpx.post يرمي ConnectError ← AIUnavailable برسالة واضحة.
    def boom(*a, **k):
        raise httpx.ConnectError("Connection refused")
    monkeypatch.setattr("httpx.post", boom)
    with pytest.raises(AIUnavailable) as exc:
        generate_student_plan({"skills": {}, "has_data": True, "student_name": "x"})
    # لا 500؛ يُرفع AIUnavailable برسالة واضحة تدلّ على تعذّر الاتصال (تدهور لطيف).
    assert "المحرّك المحلّي" in str(exc.value)


def test_ai_success_mocked(monkeypatch):
    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"response": "١) ... ٢) ... ٣) ..."}
    monkeypatch.setattr("httpx.post", lambda *a, **k: FakeResp())
    out = generate_student_plan({"student_name": "أ", "skills": {}, "strength": None,
                                 "critical_deficit": "إشكال", "overall": 0.5})
    assert "٣)" in out


def test_ollama_available_false_when_unreachable(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr("httpx.get", boom)
    assert ollama_available() is False


def test_suggest_open_score_parses_and_caps(monkeypatch):
    from app.ai_feedback import suggest_open_score

    class FakeResp:
        def __init__(self, txt): self._t = txt
        def raise_for_status(self): pass
        def json(self): return {"response": self._t}

    monkeypatch.setattr("httpx.post",
                        lambda *a, **k: FakeResp("النقطة: 2.5\nالتعليل: صاغ الإشكال بوضوح."))
    score, note = suggest_open_score("صغ الإشكال", "- ذكر التوتّر (2 ن)", "هل الوعي شفّاف؟", 4)
    assert score == 2.5 and "الإشكال" in note

    # لا يتجاوز السقف مهما اقترح النموذج
    monkeypatch.setattr("httpx.post", lambda *a, **k: FakeResp("النقطة: 99\nالتعليل: ممتاز"))
    capped, _ = suggest_open_score("x", "", ".", 4)
    assert capped == 4.0
