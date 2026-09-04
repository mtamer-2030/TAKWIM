"""نقطة تشغيل مِحَكّ — استماع على 0.0.0.0 (CLAUDE.md §5)."""

from __future__ import annotations

import uvicorn

from app.settings import settings

if __name__ == "__main__":
    # 0.0.0.0 حتى تصل الهواتف عبر الشبكة المحلّية إلى IP الثابت للحاسوب.
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, workers=1)
