"""
Local bilingual document extraction.

Text PDFs are read with PyMuPDF. Scanned PDFs and images are read with
Tesseract (eng+ben) on a temporary copy. The stored original file is never
modified. Extracted fields are suggestions until a user confirms them.
"""
import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime

BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
BN_RE = re.compile(r"[\u0980-\u09FF]")
EN_RE = re.compile(r"[A-Za-z]")
MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
OCR_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}
EMBEDDED_TEXT_MIN = 40


class OcrUnavailable(Exception):
    """Tesseract is missing or OCR is turned off. The rest of the app still works."""


class OcrError(Exception):
    """The file could not be read. Original file is unchanged."""


def normalize_bangla_digits(text):
    return (text or "").translate(BN_DIGITS)


def detect_language(text):
    has_bn = bool(BN_RE.search(text or ""))
    has_en = bool(EN_RE.search(text or ""))
    if has_bn and has_en:
        return "English + Bangla"
    if has_bn:
        return "Bangla"
    if has_en:
        return "English"
    return "Unknown"


def parse_confirmed_date(value):
    """Return a date only when the format is unambiguous. Never guess 05/06/2026."""
    suggestion = interpret_date(value)
    if not suggestion or suggestion.get("ambiguous") or not suggestion.get("iso"):
        return None
    year, month, day = suggestion["iso"].split("-")
    from datetime import date
    return date(int(year), int(month), int(day))


def interpret_date(value):
    """Normalize Bangla digits and classify a date string.

    Ambiguous numeric dates (both day and month <= 12) are returned with
    ambiguous=True and iso=None so nothing is written automatically.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    text = normalize_bangla_digits(raw)
    named = re.fullmatch(
        r"(\d{1,2})[ \-]([A-Za-z]{3,9})[ \-](\d{4})", text
    )
    if named:
        day, month_name, year = int(named.group(1)), named.group(2).lower(), int(named.group(3))
        month = MONTHS.get(month_name)
        if month and 1 <= day <= 31:
            return {"value": f"{day:02d} {month_name.title()} {year}", "ambiguous": False,
                    "iso": f"{year:04d}-{month:02d}-{day:02d}"}
        return None

    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        year, month, day = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return {"value": f"{year:04d}-{month:02d}-{day:02d}", "ambiguous": False,
                    "iso": f"{year:04d}-{month:02d}-{day:02d}"}
        return None

    numeric = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", text)
    if not numeric:
        return None
    a, b, year = int(numeric.group(1)), int(numeric.group(2)), int(numeric.group(3))
    shown = f"{a:02d}/{b:02d}/{year}"
    if a > 12 and 1 <= b <= 12:
        return {"value": shown, "ambiguous": False, "iso": f"{year:04d}-{b:02d}-{a:02d}"}
    if b > 12 and 1 <= a <= 12:
        return {"value": shown, "ambiguous": False, "iso": f"{year:04d}-{a:02d}-{b:02d}"}
    if 1 <= a <= 12 and 1 <= b <= 12:
        return {"value": shown, "ambiguous": True, "iso": None}
    return None


# Same-line "Label: value" and a following-line value (common in PDF text).
_INLINE_FIELDS = [
    ("cert_no", re.compile(
        r"^(?:certificate|cert\.?)\s+(?:no\.?|number|#|নং)\.?\s*[:\-]\s*(.+)$", re.IGNORECASE)),
    ("license_no", re.compile(
        r"^(?:licen[cs]e|লাইসেন্স)\s+(?:no\.?|number|#|নং)\.?\s*[:\-]\s*(.+)$", re.IGNORECASE)),
    ("registration_number", re.compile(
        r"^(?:registration|reg\.?)\s+(?:no\.?|number|#|নং)\.?\s*[:\-]\s*(.+)$", re.IGNORECASE)),
    ("doc_type", re.compile(
        r"^(?:licen[cs]e|certificate|document)\s+type\s*[:\-]\s*(.+)$", re.IGNORECASE)),
    ("doc_name", re.compile(
        r"^(?:document\s+name|document\s+title)\s*[:\-]\s*(.+)$", re.IGNORECASE)),
    ("issuing_authority", re.compile(
        r"^(?:issuing\s+authority|issued\s+by|authority)\s*[:\-]\s*(.+)$", re.IGNORECASE)),
    ("responsible_person", re.compile(
        r"^(?:responsible\s+person)\s*[:\-]\s*(.+)$", re.IGNORECASE)),
    ("company", re.compile(
        r"^(?:company(?:\s*/\s*organization)?|organization)\s*[:\-]\s*(.+)$", re.IGNORECASE)),
    ("department", re.compile(r"^department\s*[:\-]\s*(.+)$", re.IGNORECASE)),
    ("name", re.compile(r"^(?:name|নাম)\s*[:\-]\s*(.+)$", re.IGNORECASE)),
    ("test_center", re.compile(r"^(?:test\s+cent(?:er|re))\s*[:\-]\s*(.+)$", re.IGNORECASE)),
    ("issue_date", re.compile(
        r"^(?:issue\s+date|date\s+of\s+issue|issued\s+on)\s*[:\-]?\s*(.+)$", re.IGNORECASE)),
    ("expiry_date", re.compile(
        r"^(?:expiry\s+date|date\s+of\s+expiry|valid\s+until|expires)\s*[:\-]?\s*(.+)$", re.IGNORECASE)),
    ("report_date", re.compile(
        r"^(?:report\s+date|examination\s+date|exam\s+date)\s*[:\-]?\s*(.+)$", re.IGNORECASE)),
]
_LABEL_ONLY = [
    ("cert_no", re.compile(
        r"^(?:certificate|cert\.?)\s+(?:no\.?|number|#|নং)\.?$", re.IGNORECASE)),
    ("license_no", re.compile(
        r"^(?:licen[cs]e|লাইসেন্স)\s+(?:no\.?|number|#|নং)\.?$", re.IGNORECASE)),
    ("registration_number", re.compile(
        r"^(?:registration|reg\.?)\s+(?:no\.?|number|#|নং)\.?$", re.IGNORECASE)),
    ("doc_type", re.compile(
        r"^(?:licen[cs]e|certificate|document)\s+type\.?$", re.IGNORECASE)),
    ("doc_name", re.compile(r"^(?:document\s+name|document\s+title)\.?$", re.IGNORECASE)),
    ("issuing_authority", re.compile(r"^(?:issuing\s+authority|issued\s+by)\.?$", re.IGNORECASE)),
    ("responsible_person", re.compile(r"^(?:responsible\s+person)\.?$", re.IGNORECASE)),
    ("company", re.compile(r"^(?:company(?:\s*/\s*organization)?|organization)\.?$", re.IGNORECASE)),
    ("department", re.compile(r"^department\.?$", re.IGNORECASE)),
    ("name", re.compile(r"^(?:name|নাম)\.?$", re.IGNORECASE)),
    ("test_center", re.compile(r"^(?:test\s+cent(?:er|re))\.?$", re.IGNORECASE)),
    ("issue_date", re.compile(r"^(?:issue\s+date|date\s+of\s+issue|issued\s+on)\.?$", re.IGNORECASE)),
    ("expiry_date", re.compile(
        r"^(?:expiry\s+date|date\s+of\s+expiry|valid\s+until|expires)\.?$", re.IGNORECASE)),
    ("report_date", re.compile(
        r"^(?:report\s+date|examination\s+date|exam\s+date)\.?$", re.IGNORECASE)),
]
_DATE_KEYS = {"issue_date", "expiry_date", "report_date"}


def _field_lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _label_key(line):
    for key, pattern in _LABEL_ONLY:
        if pattern.match(line):
            return key
    return None


def _store_suggestion(suggestions, key, raw):
    if key in suggestions:
        return
    value = (raw or "").strip().split("\n")[0].strip()
    if not value or _label_key(value):
        return
    if key in _DATE_KEYS:
        parsed = interpret_date(value)
        if parsed:
            suggestions[key] = parsed
        return
    suggestions[key] = value[:200]


def extract_fields(text):
    """Suggest fields from OCR text.

    Accepts "Label: value" on one line and a label with the value on the
    next line, which is how many PDF forms are read. The original text is
    not changed.
    """
    lines = _field_lines(text)
    suggestions = {}
    used = set()
    index = 0
    while index < len(lines):
        line = lines[index]
        inline_key = None
        inline_value = None
        for key, pattern in _INLINE_FIELDS:
            match = pattern.match(line)
            if match:
                inline_key = key
                inline_value = match.group(1)
                break
        if inline_key:
            _store_suggestion(suggestions, inline_key, inline_value)
            used.add(index)
            index += 1
            continue
        label = _label_key(line)
        if label and index + 1 < len(lines):
            _store_suggestion(suggestions, label, lines[index + 1])
            used.add(index)
            used.add(index + 1)
            index += 2
            continue
        index += 1

    if "license_no" in suggestions and "cert_no" not in suggestions:
        suggestions["cert_no"] = suggestions["license_no"]
    if "registration_number" in suggestions and "cert_no" not in suggestions:
        suggestions["cert_no"] = suggestions["registration_number"]
    if "doc_name" not in suggestions:
        for position, line in enumerate(lines):
            if position in used or _label_key(line) or len(line) > 120:
                continue
            suggestions["doc_name"] = line[:200]
            break
    return suggestions


def suggestions_json(fields):
    return json.dumps(fields, ensure_ascii=False)


def load_suggestions(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}


def _tesseract_ready(config):
    if not config.get("OCR_ENABLED", True):
        raise OcrUnavailable("OCR is currently unavailable because it is turned off.")
    try:
        import pytesseract
    except ImportError as exc:
        raise OcrUnavailable("OCR is currently unavailable because the OCR engine is not installed.") from exc
    cmd = config.get("TESSERACT_CMD") or ""
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    prefix = config.get("TESSDATA_PREFIX") or ""
    if prefix:
        os.environ["TESSDATA_PREFIX"] = prefix
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:
        raise OcrUnavailable(
            "OCR is currently unavailable because the OCR engine is not installed."
        ) from exc
    return pytesseract


def _preprocess_image(src_path, dest_path):
    """Work only on a copy. Grayscale, mild upscale, and contrast. No deskew library."""
    from PIL import Image, ImageOps
    with Image.open(src_path) as img:
        gray = ImageOps.grayscale(img)
        if gray.width < 1400:
            scale = 1400 / max(gray.width, 1)
            gray = gray.resize((int(gray.width * scale), int(gray.height * scale)))
        gray = ImageOps.autocontrast(gray)
        gray.save(dest_path, format="PNG")


def _ocr_image(pytesseract, image_path, languages, timeout):
    from PIL import Image
    with Image.open(image_path) as img:
        text = pytesseract.image_to_string(img, lang=languages, timeout=timeout) or ""
        confidence = None
        try:
            data = pytesseract.image_to_data(img, lang=languages, timeout=timeout, output_type=pytesseract.Output.DICT)
            scores = [float(c) for c in data.get("conf", []) if str(c).replace(".", "", 1).isdigit() and float(c) >= 0]
            if scores:
                confidence = round(sum(scores) / len(scores), 1)
        except Exception:
            confidence = None
    return text, confidence


def _pdf_embedded_text(path, max_pages):
    import pymupdf
    doc = pymupdf.open(path)
    try:
        if doc.page_count > max_pages:
            raise OcrError(
                f"This PDF has {doc.page_count} pages. OCR is limited to {max_pages} pages."
            )
        parts = []
        for page in doc:
            parts.append(page.get_text("text") or "")
        return "\n".join(parts).strip(), doc.page_count
    finally:
        doc.close()


def _pdf_ocr(path, pytesseract, languages, timeout, max_pages, workdir):
    import pymupdf
    doc = pymupdf.open(path)
    texts = []
    scores = []
    try:
        if doc.page_count > max_pages:
            raise OcrError(
                f"This PDF has {doc.page_count} pages. OCR is limited to {max_pages} pages."
            )
        for index, page in enumerate(doc):
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            raw = os.path.join(workdir, f"page_{index}.png")
            pix.save(raw)
            prepared = os.path.join(workdir, f"page_{index}_prep.png")
            _preprocess_image(raw, prepared)
            text, conf = _ocr_image(pytesseract, prepared, languages, timeout)
            texts.append(text)
            if conf is not None:
                scores.append(conf)
    finally:
        doc.close()
    confidence = round(sum(scores) / len(scores), 1) if scores else None
    return "\n".join(texts).strip(), confidence


def extract_document(path, config, ocr_image_fn=None):
    """Read one stored file. Returns a result dict. Never writes to path."""
    started = time.perf_counter()
    if not os.path.isfile(path):
        raise OcrError("The uploaded file could not be found.")
    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > float(config.get("OCR_MAX_FILE_MB") or 10):
        raise OcrError("This file is too large for OCR.")

    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext not in OCR_EXTENSIONS:
        raise OcrError("OCR supports PDF, JPG, and PNG files only.")

    before = os.path.getmtime(path)
    before_size = os.path.getsize(path)
    languages = config.get("OCR_LANGUAGES") or "eng+ben"
    timeout = int(config.get("OCR_TIMEOUT_SECONDS") or 120)
    max_pages = int(config.get("OCR_MAX_PDF_PAGES") or 50)
    text = ""
    confidence = None
    used_ocr = False

    workdir = tempfile.mkdtemp(prefix="compliance_ocr_")
    try:
        if ext == "pdf":
            embedded, _pages = _pdf_embedded_text(path, max_pages)
            useful = len(re.findall(r"[A-Za-z0-9\u0980-\u09FF]", embedded)) >= EMBEDDED_TEXT_MIN
            if useful:
                text = embedded
            else:
                if ocr_image_fn:
                    text, confidence = ocr_image_fn(path)
                else:
                    pytesseract = _tesseract_ready(config)
                    text, confidence = _pdf_ocr(path, pytesseract, languages, timeout, max_pages, workdir)
                used_ocr = True
        else:
            pytesseract = _tesseract_ready(config) if ocr_image_fn is None else None
            prepared = os.path.join(workdir, "prepared.png")
            _preprocess_image(path, prepared)
            if ocr_image_fn:
                text, confidence = ocr_image_fn(prepared)
            else:
                text, confidence = _ocr_image(pytesseract, prepared, languages, timeout)
            used_ocr = True
    except OcrUnavailable:
        raise
    except OcrError:
        raise
    except Exception as exc:
        message = str(exc).lower()
        if "timeout" in message or "timed out" in message:
            raise OcrError("OCR processing is taking longer than expected.") from exc
        raise OcrError("OCR could not read this file.") from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if os.path.isfile(path):
            if os.path.getmtime(path) != before or os.path.getsize(path) != before_size:
                raise OcrError("OCR stopped because the original file would have changed.")

    elapsed = round(time.perf_counter() - started, 2)
    fields = extract_fields(text)
    return {
        "extracted_text": text,
        "search_text": normalize_bangla_digits(text),
        "language": detect_language(text),
        "confidence": confidence,
        "suggested_fields": fields,
        "processing_time": elapsed,
        "used_ocr": used_ocr,
        "processed_at": datetime.utcnow(),
    }
