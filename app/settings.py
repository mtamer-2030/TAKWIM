"""تحميل الإعداد من config.ini — لا مفاتيح في الكود (CLAUDE.md §14)."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# PHILOTECH_CONFIG يسمح بإعادة توجيه ملفّ الإعداد (اختبار/نشر).
CONFIG_PATH = Path(os.environ.get("PHILOTECH_CONFIG")
                   or os.environ.get("MIHAKK_CONFIG") or (BASE_DIR / "config.ini"))
EXAMPLE_PATH = BASE_DIR / "config.ini.example"


@dataclass
class LocalAI:
    """محرك ذكاء اصطناعي محلّي (Ollama/LM Studio) — للتقارير وخطط التدخّل فقط."""
    enabled: bool = True
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:3b-instruct"
    timeout: int = 300


@dataclass
class Settings:
    teacher_password: str = "change-me-please"
    port: int = 8000
    public_url: str = "http://192.168.1.50:8000"
    local_ai: LocalAI = field(default_factory=LocalAI)


def _read_ini() -> configparser.ConfigParser:
    # strict=False يتسامح مع تكرار الأقسام/المفاتيح (يفوز الأخير) فلا يتعطّل
    # الخادم إن كرّر المستخدم قسماً في config.ini سهواً.
    cp = configparser.ConfigParser(strict=False)
    # config.ini إن وُجد، وإلّا المثال، وإلّا الافتراضات المدمجة.
    for path in (CONFIG_PATH, EXAMPLE_PATH):
        if path.exists():
            try:
                cp.read(path, encoding="utf-8")
            except configparser.Error:
                # إعداد تالف: نتجاهله ونعتمد الافتراضات المدمجة بدل تعطّل الخادم.
                return configparser.ConfigParser(strict=False)
            break
    return cp


def load_settings() -> Settings:
    cp = _read_ini()
    s = Settings()
    if cp.has_section("teacher"):
        s.teacher_password = cp.get("teacher", "password", fallback=s.teacher_password)
    if cp.has_section("server"):
        s.port = cp.getint("server", "port", fallback=s.port)
        s.public_url = cp.get("server", "public_url", fallback=s.public_url)
    if cp.has_section("local_ai"):
        s.local_ai = LocalAI(
            enabled=cp.getboolean("local_ai", "enabled", fallback=True),
            base_url=cp.get("local_ai", "base_url", fallback="http://localhost:11434"),
            model=cp.get("local_ai", "model", fallback="qwen2.5:3b-instruct"),
            timeout=cp.getint("local_ai", "timeout", fallback=300),
        )
    return s


# نسخة واحدة تُحمّل عند الإقلاع؛ تُعاد قراءتها إن لزم عبر load_settings().
settings = load_settings()
