"""OCR extraction, suggestions, search, isolation, and safety tests."""
import io
import os
import sys
import hashlib
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "ocr-test-key")

from app import create_app, db
from app.models import User, Role, Factory, Document, DocumentVersion, OcrResult, SystemLog
from app.ocr_service import (
    extract_document, extract_fields, interpret_date, normalize_bangla_digits,
    detect_language, OcrUnavailable, OcrError,
)

PASS = "ocr-test-pass-1"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  [{'OK' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail and not ok else ""))


def main():
    check("bangla digits normalize", normalize_bangla_digits("০৫/০১/২০২৬") == "05/01/2026")
    parsed = interpret_date("০৫/০১/২০২৬")
    check("bangla date suggestion keeps normalized value", parsed and parsed["value"] == "05/01/2026")
    check("bangla date is not silently resolved", parsed and parsed["ambiguous"] and parsed["iso"] is None)
    named = interpret_date("05 Jan 2026")
    check("named date is unambiguous", named and named["iso"] == "2026-01-05" and not named["ambiguous"])
    amb = interpret_date("05/06/2026")
    check("ambiguous numeric date is not guessed", amb and amb["ambiguous"] and amb["iso"] is None)
    day_first = interpret_date("15/06/2026")
    check("day > 12 resolves as day/month", day_first and day_first["iso"] == "2026-06-15")

    sample = "Certificate No: FL-2026-001\nIssue Date: 10 Jan 2025\nExpiry Date: 09 Oct 2026\nIssuing Authority: Fire Service\nName: মোঃ আব্দুর রহমান"
    fields = extract_fields(sample)
    check("certificate number suggested", fields.get("cert_no") == "FL-2026-001", str(fields.get("cert_no")))
    check("named issue date suggested", fields.get("issue_date", {}).get("iso") == "2025-01-10")
    check("named expiry date suggested", fields.get("expiry_date", {}).get("iso") == "2026-10-09")
    check("authority suggested", fields.get("issuing_authority") == "Fire Service")
    check("bangla name preserved", fields.get("name") == "মোঃ আব্দুর রহমান")
    stacked = (
        "DEMO BUSINESS LICENSE\n"
        "License No.\nDEMO-LIC-2026-001\n"
        "License Type\nBusiness Operating License\n"
        "Issue Date\n06 October 2026\n"
        "Expiry Date\n16 October 2026\n"
        "Issuing Authority\nDemo Regulatory Authority\n"
        "Responsible Person\nTest Administrator\n"
        "Company / Organization\nAnanta Demo Company Ltd.\n"
        "Department\nICT Department\n"
    )
    stacked_fields = extract_fields(stacked)
    check("stacked license number", stacked_fields.get("cert_no") == "DEMO-LIC-2026-001", str(stacked_fields.get("cert_no")))
    check("stacked document name", stacked_fields.get("doc_name") == "DEMO BUSINESS LICENSE")
    check("stacked document type", stacked_fields.get("doc_type") == "Business Operating License")
    check("stacked authority", stacked_fields.get("issuing_authority") == "Demo Regulatory Authority")
    check("stacked responsible person", stacked_fields.get("responsible_person") == "Test Administrator")
    check("stacked issue date", stacked_fields.get("issue_date", {}).get("iso") == "2026-10-06")
    check("stacked expiry date", stacked_fields.get("expiry_date", {}).get("iso") == "2026-10-16")
    check("stacked company", stacked_fields.get("company") == "Ananta Demo Company Ltd.")
    check("stacked department", stacked_fields.get("department") == "ICT Department")
    check("title is not read as a license number", stacked_fields.get("license_no") != "TEST")
    check("mixed language label", detect_language(sample) == "English + Bangla")
    check("bangla only label", detect_language("মোঃ করিম") == "Bangla")
    check("english only label", detect_language("Fire License") == "English")

    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True
    app.config["OCR_ENABLED"] = True
    cfg = app.config

    import fitz
    from PIL import Image, ImageDraw

    work = os.path.join(ROOT, "instance", "ocr_test_files")
    os.makedirs(work, exist_ok=True)

    en_img = os.path.join(work, "english.png")
    img = Image.new("RGB", (900, 200), "white")
    ImageDraw.Draw(img).text((20, 40), "License No: EL-2026-1025", fill="black")
    img.save(en_img)
    bn_img = os.path.join(work, "bangla.png")
    img.save(bn_img)
    mix_img = os.path.join(work, "mixed.png")
    img.save(mix_img)

    def fake_en(_path):
        return "License No: EL-2026-1025\nName: Rahim Ahmed", 92.0

    def fake_bn(_path):
        return "নাম: মোঃ আব্দুর রহমান", 88.0

    def fake_mix(_path):
        return "Name: মোঃ আব্দুর রহমান\nLicense No: EL-2026-1025", 90.0

    en = extract_document(en_img, cfg, ocr_image_fn=fake_en)
    check("english image text extracted", "EL-2026-1025" in en["extracted_text"] and en["language"] == "English")
    bn = extract_document(bn_img, cfg, ocr_image_fn=fake_bn)
    check("bangla image text extracted", "আব্দুর রহমান" in bn["extracted_text"] and bn["language"] == "Bangla")
    check("bangla text is not translated", "Abdul" not in bn["extracted_text"])
    mix = extract_document(mix_img, cfg, ocr_image_fn=fake_mix)
    check("mixed image keeps both languages", mix["language"] == "English + Bangla" and "আব্দুর" in mix["extracted_text"])

    text_pdf = os.path.join(work, "text.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Certificate No: FL-2026-001 Fire Service digital text " * 3)
    doc.save(text_pdf)
    doc.close()
    called = {"n": 0}

    def boom(_path):
        called["n"] += 1
        return "should not ocr", 1

    embedded = extract_document(text_pdf, cfg, ocr_image_fn=boom)
    check("text pdf uses embedded text", "FL-2026-001" in embedded["extracted_text"] and not embedded["used_ocr"])
    check("text pdf skips image OCR", called["n"] == 0)

    scan_pdf = os.path.join(work, "scan.pdf")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(fitz.Rect(72, 72, 300, 180), filename=en_img)
    doc.save(scan_pdf)
    doc.close()
    scanned = extract_document(scan_pdf, cfg, ocr_image_fn=fake_en)
    check("scanned pdf is OCR processed", scanned["used_ocr"] and "EL-2026-1025" in scanned["extracted_text"])

    digest_before = hashlib.sha256(open(en_img, "rb").read()).hexdigest()
    extract_document(en_img, cfg, ocr_image_fn=fake_en)
    digest_after = hashlib.sha256(open(en_img, "rb").read()).hexdigest()
    check("original file unchanged", digest_before == digest_after)

    disabled = dict(cfg)
    disabled["OCR_ENABLED"] = False
    unavailable = False
    try:
        extract_document(en_img, disabled)
    except OcrUnavailable:
        unavailable = True
    check("tesseract/ocr unavailable is a clear error", unavailable)

    bad_pdf = os.path.join(work, "bad.pdf")
    open(bad_pdf, "wb").write(b"not a pdf")
    failed = False
    try:
        extract_document(bad_pdf, cfg)
    except OcrError:
        failed = True
    check("bad file raises OCR error", failed)

    # temp dirs cleaned: run and ensure no compliance_ocr_ left in temp from this call
    import tempfile
    before_dirs = set(os.listdir(tempfile.gettempdir()))
    extract_document(en_img, cfg, ocr_image_fn=fake_en)
    after_dirs = set(os.listdir(tempfile.gettempdir()))
    leftover = [n for n in after_dirs - before_dirs if n.startswith("compliance_ocr_")]
    check("temporary OCR files removed", leftover == [], str(leftover))

    with app.app_context():
        sa = User.query.join(Role).filter(Role.name == "Super Admin", User.is_active.is_(True)).first()
        viewer = User.query.join(Role).filter(Role.name == "Compliance Viewer", User.is_active.is_(True)).first()
        fa_a = User.query.join(Role).filter(Role.name == "Factory Admin", User.factory_id.isnot(None), User.is_active.is_(True)).first()
        fa_b = User.query.join(Role).filter(
            Role.name == "Factory Admin", User.is_active.is_(True),
            User.factory_id.isnot(None), User.factory_id != fa_a.factory_id,
        ).first()
        saved = {}
        for u in (sa, viewer, fa_a, fa_b):
            saved[u.id] = u.password_hash
            u.set_password(PASS)
        factory_a = Factory.query.get(fa_a.factory_id)
        doc_row = Document(
            factory_id=factory_a.id, doc_name="ZZ OCR Search Doc", doc_type="Fire License",
            cert_no="KEEP-ME", status="Valid", is_active=True, created_by=sa.id,
        )
        db.session.add(doc_row)
        db.session.flush()
        version = DocumentVersion(
            document_id=doc_row.id, version_no=1, file_path="factory_x/missing.pdf", uploaded_by=sa.id,
        )
        db.session.add(version)
        db.session.flush()
        db.session.add(OcrResult(
            document_id=doc_row.id, document_version_id=version.id,
            extracted_text="মোঃ আব্দুর রহমান\nLicense No: EL-2026-1025",
            search_text=normalize_bangla_digits("মোঃ আব্দুর রহমান\nLicense No: EL-2026-1025"),
            language="English + Bangla", ocr_status="COMPLETED",
        ))
        db.session.commit()
        doc_id = doc_row.id
        version_id = version.id
        names = {"sa": sa.username, "view": viewer.username, "a": fa_a.username, "b": fa_b.username}
        original_cert = doc_row.cert_no

    client = app.test_client()

    def login(username):
        client.post("/login", data={"username": username, "password": PASS}, follow_redirects=True)

    try:
        _run_http_checks(app, client, login, names, doc_id, version_id, original_cert)
    finally:
        with app.app_context():
            doomed = Document.query.get(doc_id)
            if doomed:
                db.session.delete(doomed)
            for uid, pw in saved.items():
                user = User.query.get(uid)
                if user:
                    user.password_hash = pw
            db.session.commit()

    failed_n = sum(1 for _, ok in results if not ok)
    print(f"\nOCR TEST: {'PASSED' if failed_n == 0 else 'FAILED'} ({len(results) - failed_n}/{len(results)})")
    return 0 if failed_n == 0 else 1


def _check_extract_autofill(client):
    import fitz
    pdf = fitz.open()
    page = pdf.new_page()
    lines = [
        "DEMO BUSINESS LICENSE",
        "License No.",
        "DEMO-LIC-2026-001",
        "License Type",
        "Business Operating License",
        "Issue Date",
        "06 October 2026",
        "Expiry Date",
        "16 October 2026",
        "Issuing Authority",
        "Demo Regulatory Authority",
        "Responsible Person",
        "Test Administrator",
    ]
    y_pos = 72
    for line in lines:
        page.insert_text((72, y_pos), line, fontsize=12)
        y_pos += 18
    buffer = io.BytesIO()
    pdf.save(buffer)
    pdf.close()
    buffer.seek(0)
    response = client.post(
        "/documents/extract",
        data={"file": (buffer, "demo_license_document.pdf")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    check("extract confirmation opens", response.status_code == 200 and "Confirm Extracted Data" in html, str(response.status_code))
    expected = {
        "document name": 'value="DEMO BUSINESS LICENSE"',
        "document type": 'value="Business Operating License"',
        "certificate number": 'value="DEMO-LIC-2026-001"',
        "issuing authority": 'value="Demo Regulatory Authority"',
        "responsible person": 'value="Test Administrator"',
        "issue date": 'value="2026-10-06"',
        "expiry date": 'value="2026-10-16"',
    }
    for label, needle in expected.items():
        check(f"extract fills {label}", needle in html)
    if "action" in html:
        client.post(response.request.path, data={"action": "cancel"}, follow_redirects=True)


def _run_http_checks(app, client, login, names, doc_id, version_id, original_cert):
    login(names["sa"])
    _check_extract_autofill(client)
    page = client.get("/documents/?search=EL-2026-1025").get_data(as_text=True)
    check("english OCR search finds document", "ZZ OCR Search Doc" in page)
    page = client.get("/documents/?search=আব্দুর রহমান").get_data(as_text=True)
    check("bangla OCR search finds document", "ZZ OCR Search Doc" in page)

    r = client.post(f"/documents/ocr/{version_id}")
    check("missing file records failure", r.status_code in (302, 200))
    with app.app_context():
        row = OcrResult.query.filter_by(document_version_id=version_id).first()
        check("failure status is FAILED", row and row.ocr_status == "FAILED", None if not row else row.ocr_status)
        logged = SystemLog.query.filter_by(event_type="OCR_FAILED").order_by(SystemLog.id.desc()).first()
        check("failure written to system log", logged is not None and "Version ID" in (logged.message or ""))
        check("system log does not store full OCR text", logged is not None and "আব্দুর" not in (logged.message or ""))
        unchanged = Document.query.get(doc_id)
        check("failed OCR did not change certificate number", unchanged.cert_no == original_cert)

    # Reprocess path uses same file; original bytes of a real image stay same.
    # Viewer can open OCR page but cannot confirm.
    client2 = app.test_client()
    client2.post("/login", data={"username": names["view"], "password": PASS}, follow_redirects=True)
    r = client2.get(f"/documents/ocr/{version_id}/view")
    check("viewer can view OCR page", r.status_code == 200)
    r = client2.post(f"/documents/ocr/{version_id}/view", data={"extracted_text": "changed", "apply_cert": "on", "cert_no": "HACK"})
    check("viewer cannot confirm OCR", r.status_code == 403)
    r = client2.post(f"/documents/ocr/{version_id}")
    check("viewer cannot run OCR", r.status_code == 403)

    client3 = app.test_client()
    client3.post("/login", data={"username": names["b"], "password": PASS}, follow_redirects=True)
    r = client3.get(f"/documents/ocr/{version_id}/view")
    check("other factory cannot view OCR", r.status_code == 403)
    r = client3.post(f"/documents/ocr/{version_id}")
    check("other factory cannot run OCR", r.status_code == 403)


if __name__ == "__main__":
    sys.exit(main())
