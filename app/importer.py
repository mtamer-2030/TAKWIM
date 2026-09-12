"""PHILO-TECH v2 — محرك الاستيراد متعدد الصيغ (المرحلة 3).

- لوائح التلاميذ من Excel (.xlsx).
- استخراج نصوص الدروس وأسئلة التقويم من Word / PowerPoint / PDF.
- OCR لصور صفحات الكتب المدرسية (.jpg/.png) عبر Tesseract (اللغة العربية 'ara').

مبدأ التصميم: الاستيراد بطيء (lazy): تُستورَد المكتبة داخل الدالّة، فتبقى الوحدة
قابلة للتحميل حتى لو غابت مكتبة أو ثنائي Tesseract، وترفع خطأً واضحاً عند الحاجة.
هذه الطبقة تستخرج وتنظّم فقط؛ الحفظ في القاعدة يجري في طبقة الرفع (المرحلة 4).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from .normalize import normalize

# صيغ مدعومة
DOC_EXTS = {".docx"}
PPT_EXTS = {".pptx"}
PDF_EXTS = {".pdf"}
IMG_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
XLSX_EXTS = {".xlsx", ".xlsm"}


class ImporterError(Exception):
    """خطأ استيراد واضح للأستاذ (صيغة غير مدعومة، مكتبة/ثنائي مفقود، ملفّ تالف)."""


# ═══════════════════════ استخراج النصوص ═══════════════════════


def extract_text_from_docx(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError as e:  # pragma: no cover
        raise ImporterError("مكتبة python-docx غير مثبّتة.") from e
    try:
        doc = Document(io.BytesIO(data))
    except Exception as e:
        raise ImporterError(f"تعذّرت قراءة ملفّ Word: {e}") from e
    parts: list[str] = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for tbl in doc.tables:                       # نصوص الجداول أيضاً
        for row in tbl.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def extract_text_from_pptx(data: bytes) -> str:
    try:
        from pptx import Presentation
    except ImportError as e:  # pragma: no cover
        raise ImporterError("مكتبة python-pptx غير مثبّتة.") from e
    try:
        prs = Presentation(io.BytesIO(data))
    except Exception as e:
        raise ImporterError(f"تعذّرت قراءة ملفّ PowerPoint: {e}") from e
    parts: list[str] = []
    for i, slide in enumerate(prs.slides, start=1):
        slide_lines = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    txt = "".join(run.text for run in para.runs).strip()
                    if txt:
                        slide_lines.append(txt)
        if slide_lines:
            parts.append(f"[شريحة {i}]\n" + "\n".join(slide_lines))
    return "\n\n".join(parts)


def extract_text_from_pdf(data: bytes) -> str:
    """نصّ PDF عبر pdfplumber. إن عاد فارغاً (PDF ممسوح ضوئياً = صور صفحات)، يُجرَّب
    OCR على صور الصفحات إن توفّر مُصيّر؛ وإلّا تُرفع رسالة واضحة بدل نصّ فارغ صامت."""
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover
        raise ImporterError("مكتبة pdfplumber غير مثبّتة.") from e
    try:
        parts: list[str] = []
        page_images = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                txt = (page.extract_text() or "").strip()
                if txt:
                    parts.append(f"[صفحة {i}]\n{txt}")
                else:
                    page_images.append((i, page))
            # صفحات بلا نصّ مضمَّن ← محاولة OCR (رجوع تلقائي، ٥-ب) إن توفّر تصيير.
            if page_images and not parts:
                ocr = _ocr_pdf_pages(page_images)
                if ocr:
                    return ocr
        result = "\n\n".join(parts)
        if not result:
            raise ImporterError(
                "لم يُستخرَج أيّ نصّ: يبدو الملفّ PDF ممسوحاً ضوئياً (صور صفحات) "
                "بلا نصّ مضمَّن. صدّر الصفحات صوَراً (JPG/PNG) لاستعمال التعرّف "
                "الضوئي على الحروف، أو استعمل قالب Word/JSON.")
        return result
    except ImporterError:
        raise
    except Exception as e:
        raise ImporterError(f"تعذّرت قراءة ملفّ PDF: {e}") from e


def _ocr_pdf_pages(page_images) -> str:
    """OCR لصفحات PDF المصوّرة إن أمكن تصييرها عبر pdfplumber. رجوع صامت (سلسلة
    فارغة) إن تعذّر التصيير (لا مُصيّر مثبَّت) — فيتولّى النداءُ رفعَ رسالة واضحة."""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return ""
    parts: list[str] = []
    for i, page in page_images:
        try:
            pil = page.to_image(resolution=300).original      # يحتاج مُصيّراً
            parts.append(f"[صفحة {i}]\n{_ocr_pil(pil)}")
        except Exception:  # noqa: BLE001 — لا مُصيّر/فشل تصيير ← نترك الرجوع للنداء
            return ""
    return "\n\n".join(p for p in parts if p.strip())


def _preprocess_for_ocr(img):
    """معالجة قبلية ترفع دقّة OCR: رمادي + تكبير الصور الصغيرة + رفع التباين + عتبة.

    دالّة نقيّة على صورة PIL (تُختبَر بلا ثنائي Tesseract)."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(img)          # تصحيح دوران الهاتف
    img = img.convert("L")                       # تدرّج رمادي
    # تكبير الصور منخفضة الدقّة (Tesseract يحبّ ~300DPI؛ العرض <1500 يُضاعَف).
    if img.width < 1500:
        scale = 1500 / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    img = ImageOps.autocontrast(img)             # رفع التباين
    img = img.point(lambda p: 255 if p > 160 else 0)  # عتبة ثنائية (أبيض/أسود)
    return img


def _ocr_pil(img, lang: str = "ara") -> str:
    """OCR لصورة PIL بعد المعالجة القبلية و psm مناسب لكتلة نصّ."""
    import pytesseract
    return pytesseract.image_to_string(
        _preprocess_for_ocr(img), lang=lang, config="--psm 6").strip()


def extract_text_from_image(data: bytes, lang: str = "ara") -> str:
    """OCR لصورة صفحة كتاب. يتطلّب ثنائي Tesseract + حزمة اللغة العربية 'ara'.
    مع معالجة قبلية (رمادي/تباين/عتبة/تكبير) و --psm 6 لرفع الدقّة (٥-ب)."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:  # pragma: no cover
        raise ImporterError("مكتبتا pytesseract/Pillow غير مثبّتتين.") from e
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        raise ImporterError(f"تعذّرت قراءة الصورة: {e}") from e
    try:
        return _ocr_pil(img, lang=lang)
    except pytesseract.TesseractNotFoundError as e:
        raise ImporterError(
            "لم يُعثر على Tesseract OCR. ثبّته وأضِف حزمة اللغة العربية 'ara' "
            "(انظر setup.ps1)."
        ) from e
    except Exception as e:
        raise ImporterError(f"تعذّر استخراج النصّ من الصورة: {e}") from e


def _ext(filename: str) -> str:
    name = (filename or "").lower()
    return name[name.rfind("."):] if "." in name else ""


@dataclass
class ExtractedText:
    filename: str
    kind: str            # docx | pptx | pdf | image
    text: str


def extract_text(filename: str, data: bytes) -> ExtractedText:
    """موزّع: يستخرج النصّ من أي صيغة مدعومة حسب الامتداد."""
    ext = _ext(filename)
    if ext in DOC_EXTS:
        return ExtractedText(filename, "docx", extract_text_from_docx(data))
    if ext in PPT_EXTS:
        return ExtractedText(filename, "pptx", extract_text_from_pptx(data))
    if ext in PDF_EXTS:
        return ExtractedText(filename, "pdf", extract_text_from_pdf(data))
    if ext in IMG_EXTS:
        return ExtractedText(filename, "image", extract_text_from_image(data))
    raise ImporterError(f"صيغة غير مدعومة للاستخراج: {ext or '؟'}")


# ═══════════════════════ لوائح التلاميذ من Excel ═══════════════════════

# مرادفات أعمدة اللائحة (تُطبَّع قبل المطابقة).
_COLS = {
    "full_name": ["full_name", "الاسم الكامل", "الاسم والنسب", "النسب والاسم",
                  "اسم التلميذ", "الاسم", "nom", "nom complet"],
    "massar": ["massar", "مسار", "رمز مسار", "رقم مسار", "code massar", "cne",
               "رقم التلميذ", "الرمز", "رمز التلميذ"],
    "group": ["group", "group_name", "القسم", "الفوج", "المجموعة", "classe"],
    "level": ["level", "المستوى", "المستوى الدراسي", "niveau"],
}


@dataclass
class StudentRow:
    line: int
    full_name: str
    massar_code: str | None = None
    group_name: str | None = None
    level_hint: str | None = None


@dataclass
class RosterImport:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    rows: list[StudentRow] = field(default_factory=list)


def _match_columns(headers: list[str]) -> dict[str, str]:
    """يربط الأعمدة المكتشفة بالحقول المعروفة (بالتطبيع العربي)."""
    norm_headers = {h: normalize(str(h)) for h in headers}
    mapping: dict[str, str] = {}
    for field_name, synonyms in _COLS.items():
        norm_syn = {normalize(s) for s in synonyms}
        for original, nh in norm_headers.items():
            if nh in norm_syn:
                mapping[field_name] = original
                break
    return mapping


def parse_students_excel(data: bytes) -> RosterImport:
    """يقرأ لائحة تلاميذ من .xlsx ويعيد صفوفاً منظّمة (بلا كتابة في القاعدة)."""
    result = RosterImport()
    try:
        import pandas as pd
    except ImportError as e:  # pragma: no cover
        result.ok = False
        result.errors.append("مكتبة pandas غير مثبّتة.")
        return result
    try:
        df = pd.read_excel(io.BytesIO(data), dtype=str)
    except Exception as e:
        result.ok = False
        result.errors.append(f"تعذّرت قراءة ملفّ Excel: {e}")
        return result

    headers = list(df.columns)
    cols = _match_columns(headers)
    if "full_name" not in cols:
        result.ok = False
        result.errors.append(
            "لم يُعثر على عمود الاسم الكامل. المتوقّع أحد: "
            + "، ".join(_COLS["full_name"][:4])
        )
        return result

    for idx, raw in df.iterrows():
        line = int(idx) + 2  # الصفّ 1 ترويسة
        full_name = (str(raw[cols["full_name"]]).strip()
                     if raw.get(cols["full_name"]) is not None else "")
        full_name = "" if full_name.lower() == "nan" else full_name
        if not full_name:
            continue  # صفّ فارغ يُتجاهَل

        def _cell(field_name: str) -> str | None:
            col = cols.get(field_name)
            if not col:
                return None
            v = raw.get(col)
            if v is None:
                return None
            s = str(v).strip()
            return None if s == "" or s.lower() == "nan" else s

        result.rows.append(StudentRow(
            line=line, full_name=full_name,
            massar_code=_cell("massar"),
            group_name=_cell("group"),
            level_hint=_cell("level"),
        ))

    # تكرار رمز مسار داخل الملفّ ← تنبيه
    seen: dict[str, int] = {}
    for r in result.rows:
        if r.massar_code:
            if r.massar_code in seen:
                result.ok = False
                result.errors.append(
                    f"السطر {r.line}: رمز مسار «{r.massar_code}» مكرّر "
                    f"(ظهر في السطر {seen[r.massar_code]})."
                )
            else:
                seen[r.massar_code] = r.line
    return result
