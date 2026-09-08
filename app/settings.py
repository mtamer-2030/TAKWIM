"""تحميل الإعداد من config.ini — لا مفاتيح في الكود (CLAUDE.md §14)."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# MIHAKK_CONFIG / MIHAKK_DATA_DIR يسمحان بإعادة توجيه الإعداد والبيانات (اختبار/نشر).
CONFIG_PATH = Path(os.environ.get("MIHAKK_CONFIG") or (BASE_DIR / "config.ini"))
EXAMPLE_PATH = BASE_DIR / "config.ini.example"
DATA_DIR = Path(os.environ.get("MIHAKK_DATA_DIR") or (BASE_DIR / "data"))
DB_PATH = DATA_DIR / "mihakk.db"
BACKUP_DIR = Path(os.environ.get("MIHAKK_BACKUP_DIR") or (BASE_DIR / "backups"))


@dataclass
class EveningAI:
    enabled: bool = False
    provider: str = "anthropic"
    api_key: str = ""
    model: str = "claude-sonnet-4-5"
    base_url: str = "https://api.anthropic.com"
    batch_size: int = 10

    @property
    def usable(self) -> bool:
        # بلا مفتاح أو معطّلة ← المساعدة غير متاحة، وليست حالة خطأ (§14).
        return bool(self.enabled and self.api_key.strip())


@dataclass
class LocalAI:
    """محرك ذكاء اصطناعي محلّي (Ollama/LM Studio) — للتقارير وخطط التدخّل فقط."""
    enabled: bool = True
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b-instruct"
    timeout: int = 120


@dataclass
class Settings:
    teacher_password: str = "change-me-please"
    port: int = 8000
    public_url: str = "http://192.168.1.50:8000"
    evening: EveningAI = field(default_factory=EveningAI)
    local_ai: LocalAI = field(default_factory=LocalAI)


def _read_ini() -> configparser.ConfigParser:
    cp = configparser.ConfigParser()
    # config.ini إن وُجد، وإلّا المثال، وإلّا الافتراضات المدمجة.
    for path in (CONFIG_PATH, EXAMPLE_PATH):
        if path.exists():
            cp.read(path, encoding="utf-8")
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
            model=cp.get("local_ai", "model", fallback="qwen2.5:7b-instruct"),
            timeout=cp.getint("local_ai", "timeout", fallback=120),
        )
    if cp.has_section("evening_ai"):
        s.evening = EveningAI(
            enabled=cp.getboolean("evening_ai", "enabled", fallback=False),
            provider=cp.get("evening_ai", "provider", fallback="anthropic"),
            api_key=cp.get("evening_ai", "api_key", fallback=""),
            model=cp.get("evening_ai", "model", fallback="claude-sonnet-4-5"),
            base_url=cp.get("evening_ai", "base_url", fallback="https://api.anthropic.com"),
            batch_size=cp.getint("evening_ai", "batch_size", fallback=10),
        )
    return s


# نسخة واحدة تُحمّل عند الإقلاع؛ تُعاد قراءتها إن لزم عبر load_settings().
settings = load_settings()
