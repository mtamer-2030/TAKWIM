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


DEFAULT_PASSWORD = "change-me-please"


@dataclass
class Settings:
    # لا نحتفظ بكلمة السرّ نصّاً: تُخزَّن تجزئتها فقط (ح-٨).
    teacher_password_hash: str = ""
    password_is_default: bool = True   # كلمة السرّ ما زالت الافتراضية/فارغة → يُرفَض الإقلاع
    port: int = 8000
    public_url: str = "http://192.168.1.50:8000"
    secret_key: str = ""              # سرّ توقيع الكوكيز (يُحلّ في load_settings)
    local_ai: LocalAI = field(default_factory=LocalAI)


def _hash_password(raw: str) -> str:
    import hashlib
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


# مسار السرّ المُولَّد إن غاب من config.ini — يُحفظ فيصمد عبر إعادة التشغيل (ح-٩).
_SECRET_FILE = BASE_DIR / "data" / ".secret"


def _load_or_create_secret() -> str:
    """يقرأ سرّ التوقيع المحفوظ، أو يولّد واحداً ويحفظه (فتصمد الجلسات عبر التشغيل)."""
    import secrets
    try:
        if _SECRET_FILE.exists():
            val = _SECRET_FILE.read_text(encoding="utf-8").strip()
            if val:
                return val
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        val = secrets.token_urlsafe(32)
        _SECRET_FILE.write_text(val, encoding="utf-8")
        return val
    except OSError:
        # تعذّر الحفظ (قرص للقراءة فقط مثلاً): سرّ عابر لهذه الجلسة على الأقلّ.
        return secrets.token_urlsafe(32)


def _read_ini() -> configparser.ConfigParser:
    # strict=False يتسامح مع تكرار الأقسام/المفاتيح (يفوز الأخير) فلا يتعطّل
    # الخادم إن كرّر المستخدم قسماً في config.ini سهواً.
    cp = configparser.ConfigParser(strict=False)
    # config.ini فقط — بلا الرجوع إلى config.ini.example (ح-٨: لئلّا يُقلع النظام
    # سرّاً بكلمة سرّ المثال «change-me-please»). إن غاب فالافتراضات المدمجة.
    if CONFIG_PATH.exists():
        try:
            cp.read(CONFIG_PATH, encoding="utf-8")
        except configparser.Error:
            # إعداد تالف: نتجاهله ونعتمد الافتراضات المدمجة بدل تعطّل الخادم.
            return configparser.ConfigParser(strict=False)
    return cp


def load_settings() -> Settings:
    cp = _read_ini()
    s = Settings()
    raw_password = cp.get("teacher", "password", fallback=DEFAULT_PASSWORD) \
        if cp.has_section("teacher") else DEFAULT_PASSWORD
    s.password_is_default = raw_password.strip() in ("", DEFAULT_PASSWORD)
    s.teacher_password_hash = _hash_password(raw_password)
    if cp.has_section("server"):
        s.port = cp.getint("server", "port", fallback=s.port)
        s.public_url = cp.get("server", "public_url", fallback=s.public_url)
        s.secret_key = cp.get("server", "secret_key", fallback="")
    # سرّ التوقيع: من config.ini إن وُجد، وإلّا سرّ مُولَّد محفوظ.
    s.secret_key = s.secret_key or _load_or_create_secret()
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
