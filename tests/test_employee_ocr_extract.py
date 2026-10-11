"""End-to-end check of the new pre-save OCR flow for employee licenses and
health checkups.

Exercises the full round trip without touching Tesseract: we patch
extract_document to return a canned result so the test is deterministic and
runs without the OCR engine installed on the test box.
"""
import io
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("SECRET_KEY", "test-key")

from werkzeug.datastructures import FileStorage

from app import create_app, db
from app.models import (
    Employee, EmployeeCompliance, EmployeeComplianceVersion, Factory, Role, User,
    HealthCheckup, HealthCheckupVersion, OcrResult, Department,
)
from app import ocr_service

TAG = "ZZ_OCR_EMP"
PASS = "Testing@2026"

CANNED_LICENSE = {
    "extracted_text": "Electrician License\nRegistration No: EL-9876\nIssuing Authority: Dhaka Electricity\nIssue Date: 10-Jan-2026\nExpiry Date: 10-Jan-2028\n",
    "search_text": "Electrician License\nRegistration No: EL-9876\n...",
    "language": "English",
    "confidence": 92.5,
    "suggested_fields": {
        "doc_type": "Electrician License",
        "registration_number": "EL-9876",
        "issuing_authority": "Dhaka Electricity",
        "issue_date": {"iso": "2026-01-10", "value": "10 Jan 2026", "ambiguous": False},
        "expiry_date": {"iso": "2028-01-10", "value": "10 Jan 2028", "ambiguous": False},
    },
    "processing_time": 0.42,
    "used_ocr": True,
    "processed_at": None,
}

CANNED_CHECKUP = {
    "extracted_text": "Health Checkup Report\nTest: CBC\nTest Center: City Diagnostic\nReport Date: 15-Feb-2026\nValid Until: 15-Feb-2027\n",
    "search_text": "Health Checkup Report\nTest: CBC\n...",
    "language": "English",
    "confidence": 88.1,
    "suggested_fields": {
        "doc_type": "CBC",
        "test_center": "City Diagnostic",
        "report_date": {"iso": "2026-02-15", "value": "15 Feb 2026", "ambiguous": False},
        "expiry_date": {"iso": "2027-02-15", "value": "15 Feb 2027", "ambiguous": False},
    },
    "processing_time": 0.37,
    "used_ocr": True,
    "processed_at": None,
}


def cleanup(app):
    with app.app_context():
        for cls in (EmployeeCompliance, HealthCheckup):
            for row in cls.query.join(Employee).filter(Employee.name.like(f"{TAG}%")).all():
                for v in row.file_versions:
                    db.session.delete(v)
                if row.ocr_result:
                    db.session.delete(row.ocr_result)
                db.session.delete(row)
        for emp in Employee.query.filter(Employee.name.like(f"{TAG}%")).all():
            db.session.delete(emp)
        db.session.commit()
        # drop any staging files our test may have left behind
        folder = os.path.join(app.instance_path, "ocr_staging")
        if os.path.isdir(folder):
            for name in list(os.listdir(folder)):
                path = os.path.join(folder, name)
                if os.path.isfile(path) and ("ZZ_OCR_EMP" in open(path, "rb").read(2048).decode("utf-8", "ignore")
                                             if name.endswith(".json") else False):
                    os.remove(path)


def minimal_pdf_bytes():
    # A real PyMuPDF-openable 1-page PDF produced by hand with blank content.
    return (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 300]/Resources<<>>>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n0000000010 00000 n \n0000000053 00000 n \n0000000094 00000 n \n"
            b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n150\n%%EOF\n")


def main():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True

    results = []

    def check(label, cond, extra=""):
        results.append((label, bool(cond)))
        tag = "OK  " if cond else "FAIL"
        print(f"  [{tag}] {label}" + (f"  :: {extra}" if extra and not cond else ""))

    cleanup(app)

    # --- Seed fixtures: a factory + employee owned by that factory ---------
    with app.app_context():
        factory = Factory.query.order_by(Factory.id).first()
        dept = Department.query.order_by(Department.id).first()
        emp = Employee(
            factory_id=factory.id, department_id=dept.id if dept else None,
            name=f"{TAG}_person", employee_code=f"{TAG}_01",
            designation="QA", is_active=True,
        )
        db.session.add(emp)
        db.session.commit()
        emp_id = emp.id
        sa = User.query.filter_by(username="admin").first()
        # make sure the admin password matches the known test password
        sa.set_password(PASS)
        sa.failed_login_attempts = 0
        sa.locked_until = None
        db.session.commit()

    client = app.test_client()
    r = client.post("/login", data={"username": "admin", "password": PASS}, follow_redirects=True)
    check("admin login", r.status_code == 200 and b"logout" in r.data.lower(), f"status={r.status_code}")

    # Patch out the OCR engine with a canned result so we don't need Tesseract.
    orig_extract = ocr_service.extract_document
    canned = {"license": CANNED_LICENSE, "checkup": CANNED_CHECKUP}
    current = {"val": None}

    def fake_extract(path, config, ocr_image_fn=None):
        return dict(canned[current["val"]])

    # Monkey-patch where it's imported — routes/employees imports it inside
    # the stage helper, so patch the module attribute.
    ocr_service.extract_document = fake_extract
    try:
        # --- License flow -------------------------------------------------
        current["val"] = "license"
        pdf = io.BytesIO(minimal_pdf_bytes())
        r = client.post(f"/employees/{emp_id}/license/extract",
                        data={"file": (pdf, "sample.pdf")},
                        content_type="multipart/form-data",
                        follow_redirects=False)
        check("license extract upload redirects to preview",
              r.status_code == 302 and "/license/extract/" in r.location,
              f"status={r.status_code} loc={r.location}")
        token = r.location.rsplit("/", 1)[-1] if r.status_code == 302 else ""

        # GET preview — fields pre-filled from the canned suggestions
        r = client.get(f"/employees/{emp_id}/license/extract/{token}")
        page = r.get_data(as_text=True)
        check("license preview opens", r.status_code == 200, f"status={r.status_code}")
        check("license preview pre-fills compliance_type",
              'value="Electrician License"' in page)
        check("license preview pre-fills registration_number",
              'value="EL-9876"' in page)
        check("license preview pre-fills issuing_authority",
              'value="Dhaka Electricity"' in page)
        check("license preview pre-fills issue_date",
              'value="2026-01-10"' in page)
        check("license preview pre-fills expiry_date",
              'value="2028-01-10"' in page)
        check("license preview shows extracted text",
              "Electrician License" in page and "EL-9876" in page)

        # POST confirm — record saved + file version + OcrResult COMPLETED
        r = client.post(f"/employees/{emp_id}/license/extract/{token}", data={
            "compliance_type": "Electrician License",
            "registration_number": "EL-9876",
            "category": "",
            "issuing_authority": "Dhaka Electricity",
            "issue_date": "2026-01-10",
            "expiry_date": "2028-01-10",
            "remarks": "",
            "extracted_text": CANNED_LICENSE["extracted_text"],
            "submit": "Save",
        }, follow_redirects=False)
        check("license confirm redirects to employee view",
              r.status_code == 302 and f"/employees/{emp_id}" in r.location,
              f"status={r.status_code} loc={r.location}")

        with app.app_context():
            rec = EmployeeCompliance.query.filter_by(
                employee_id=emp_id, compliance_type="Electrician License",
            ).first()
            check("license record created", rec is not None)
            if rec:
                check("license record uses canned reg number",
                      rec.registration_number == "EL-9876")
                check("license record uses canned issue date",
                      rec.issue_date == date(2026, 1, 10))
                check("license record uses canned expiry date",
                      rec.expiry_date == date(2028, 1, 10))
                versions = EmployeeComplianceVersion.query.filter_by(compliance_id=rec.id).all()
                check("license file version attached", len(versions) == 1)
                ocr = OcrResult.query.filter_by(employee_compliance_id=rec.id).first()
                check("license ocr result COMPLETED",
                      ocr is not None and ocr.ocr_status == "COMPLETED")
                check("license ocr result confirmed_at set",
                      ocr is not None and ocr.confirmed_at is not None)
                check("license ocr result keeps extracted text",
                      ocr is not None and "Electrician" in (ocr.extracted_text or ""))

        # --- Checkup flow -------------------------------------------------
        current["val"] = "checkup"
        pdf = io.BytesIO(minimal_pdf_bytes())
        r = client.post(f"/employees/{emp_id}/checkup/extract",
                        data={"file": (pdf, "report.pdf")},
                        content_type="multipart/form-data",
                        follow_redirects=False)
        check("checkup extract upload redirects to preview",
              r.status_code == 302 and "/checkup/extract/" in r.location,
              f"status={r.status_code} loc={r.location}")
        token = r.location.rsplit("/", 1)[-1] if r.status_code == 302 else ""

        r = client.get(f"/employees/{emp_id}/checkup/extract/{token}")
        page = r.get_data(as_text=True)
        check("checkup preview opens", r.status_code == 200, f"status={r.status_code}")
        check("checkup preview pre-selects CBC exam type",
              '<option selected value="CBC">' in page or '<option value="CBC" selected>' in page)
        check("checkup preview pre-fills test_center",
              'value="City Diagnostic"' in page)
        check("checkup preview pre-fills report_date",
              'value="2026-02-15"' in page)
        check("checkup preview pre-fills expiry_date",
              'value="2027-02-15"' in page)

        r = client.post(f"/employees/{emp_id}/checkup/extract/{token}", data={
            "exam_type": "CBC",
            "test_center": "City Diagnostic",
            "report_date": "2026-02-15",
            "expiry_date": "2027-02-15",
            "pre_yn": "",
            "remarks": "",
            "extracted_text": CANNED_CHECKUP["extracted_text"],
            "submit": "Save",
        }, follow_redirects=False)
        check("checkup confirm redirects to employee view",
              r.status_code == 302 and f"/employees/{emp_id}" in r.location,
              f"status={r.status_code} loc={r.location}")

        with app.app_context():
            rec = HealthCheckup.query.filter_by(
                employee_id=emp_id, exam_type="CBC",
            ).first()
            check("checkup record created", rec is not None)
            if rec:
                check("checkup record uses canned test center",
                      rec.test_center == "City Diagnostic")
                check("checkup record uses canned report date",
                      rec.report_date == date(2026, 2, 15))
                check("checkup record uses canned expiry date",
                      rec.expiry_date == date(2027, 2, 15))
                versions = HealthCheckupVersion.query.filter_by(checkup_id=rec.id).all()
                check("checkup file version attached", len(versions) == 1)
                ocr = OcrResult.query.filter_by(health_checkup_id=rec.id).first()
                check("checkup ocr result COMPLETED",
                      ocr is not None and ocr.ocr_status == "COMPLETED")
                check("checkup ocr result confirmed_at set",
                      ocr is not None and ocr.confirmed_at is not None)

        # --- Add-form shows the Extract button ---------------------------
        r = client.get(f"/employees/{emp_id}/license/add")
        page = r.get_data(as_text=True)
        check("license add form shows Extract button",
              "Extract Data from License" in page and
              f"/employees/{emp_id}/license/extract" in page)
        r = client.get(f"/employees/{emp_id}/checkup/add")
        page = r.get_data(as_text=True)
        check("checkup add form shows Extract button",
              "Extract Data from Report" in page and
              f"/employees/{emp_id}/checkup/extract" in page)
    finally:
        ocr_service.extract_document = orig_extract

    cleanup(app)

    failed = sum(1 for _l, ok in results if not ok)
    print(f"\nEMPLOYEE OCR EXTRACT TEST: {'PASSED' if failed == 0 else 'FAILED'} "
          f"({len(results) - failed}/{len(results)} checks)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
