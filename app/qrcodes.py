"""رمز QR ثابت واحد يشير إلى عنوان الخادم (CLAUDE.md §5، §15).

يُولَّد محلّياً بمكتبة qrcode — لا إنترنت ولا CDN.
"""

from __future__ import annotations

import io

import qrcode


def qr_png(url: str) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
