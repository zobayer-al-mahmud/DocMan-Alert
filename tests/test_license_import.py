"""Employee Compliance (license) import against the real Ananta license layout.

Proves the Utility/Medical importer:
  * finds the real header on row 6 under the title banner, with a leading empty
    column A (the "Electrician and Medical License Details" workbook shape),
  * maps every field (name, designation, ID, section, registration, category,
    issue/expiry dates, factory, remarks),
  * auto-maps the AGL / ASL factory abbreviations to the full factory names,
  * parses dot-format dates ("10.03.2016") and real datetimes,
  * treats "Till Now" as no-expiry,
  * keeps Category for Utility and drops it for Medical,
  * reports blank employee-ID rows as errors (does not invent an ID),
  * is idempotent on re-run,
  * and that an automatic license alert copies the configurable compliance
    group mailbox by default.

Standalone script (no pytest).
    venv\\Scripts\\python.exe tests\\test_license_import.py
"""
import os
import sys
import tempfile
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "test-key")

from openpyxl import Workbook, load_workbook

from app import create_app, db
from app.models import Employee, EmployeeCompliance, Factory
from app.import_utils import (
    parse_workbook, apply_import, build_factory_lookup, resolve_factory,
)
from app.utils import resolve_alert_recipients

OK = 0
FAIL = 0
COMPLIANCE_GROUP = "compliance_dept@ananta.com.bd"


def check(cond, label):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK  ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def _banner(ws, title):
    """Write the title-banner rows and the real header on row 6, with a
    leading empty column A (columns start at B), exactly like the source."""
    ws.cell(row=1, column=2, value="Ananta Garments Ltd./Ananta Sportswear Ltd.")
    ws.cell(row=3, column=2, value=title)


def build_workbook(path):
    wb = Workbook()
    # ---- Utility sheet --------------------------------------------------
    u = wb.active
    u.title = "Utility"
    _banner(u, "Electrician License Details-2025")
    u_header = ["Sl No.", "Name", "Designation", "ID No", "Section",
                "Registration No.", "Category", "Issue Date", "Expiry Date",
                "Factory", "Remarks"]
    for c, v in enumerate(u_header, start=2):  # leading empty column A
        u.cell(row=6, column=c, value=v)
    u_rows = [
        # Sl, Name, Desig, ID, Section, Reg, Cat, Issue, Expiry, Factory, Remarks
        [1, "ALPHA ONE", "TECHNICIAN", "EMP-U1", "UTILITY", "S20210004805", "BC",
         datetime(2024, 5, 23), datetime(2025, 5, 22), "AGL", "first row"],
        [2, "BETA TWO", "SR. TECHNICIAN", "EMP-U2", "UTILITY", "E20220025159", "C",
         "10.03.2016", "09.03.2027", "ASL", None],           # dot-format dates
        [3, "GAMMA THREE", "BOILER OPERATOR", "EMP-U3", "UTILITY", "2000/840/919",
         "2nd class", "03.05.2000", "Till Now", "AGL", None],  # no-expiry
        [4, "DELTA FOUR", "HELPER", None, "UTILITY", None, None, None, None,
         "AGL", None],                                         # blank ID -> error
    ]
    for i, r in enumerate(u_rows, start=7):
        for c, v in enumerate(r, start=2):
            if v is not None:
                u.cell(row=i, column=c, value=v)

    # ---- Medical sheet (no Category column) -----------------------------
    m = wb.create_sheet("Medical")
    _banner(m, "Medical License Details-2025")
    m_header = ["Sl No.", "Name", "Designation", "ID No", "Section",
                "Registration No.", "Issue Date", "Expiry Date", "Factory", "Remarks"]
    for c, v in enumerate(m_header, start=2):
        m.cell(row=6, column=c, value=v)
    m_rows = [
        [1, "MED ONE", "DOCTOR", "EMP-M1", "MEDICAL", "126691",
         datetime(2023, 11, 7), datetime(2028, 11, 6), "AGL", "doctor"],
    ]
    for i, r in enumerate(m_rows, start=7):
        for c, v in enumerate(r, start=2):
            if v is not None:
                m.cell(row=i, column=c, value=v)
    wb.save(path)


app = create_app()
with app.app_context():
    lookup = build_factory_lookup()
    agl_id = resolve_factory("AGL", lookup)
    asl_id = resolve_factory("ASL", lookup)
    agl = db.session.get(Factory, agl_id) if agl_id else None
    asl = db.session.get(Factory, asl_id) if asl_id else None

    print("\n--- factory auto-mapping (abbrev -> full name) ---")
    check(agl is not None and "Garments" in agl.name, f"AGL -> {agl.name if agl else None}")
    check(asl is not None and "Sportswear" in asl.name, f"ASL -> {asl.name if asl else None}")

    tmp = tempfile.mkdtemp(prefix="license_")
    path = os.path.join(tmp, "licenses.xlsx")
    build_workbook(path)

    # read_only=True + data_only=True mirrors the real import route.
    wb = load_workbook(path, read_only=True, data_only=True)
    uws = wb["Utility"]
    mws = wb["Medical"]

    print("\n--- Utility sheet parse ---")
    parsed, errors, warnings = parse_workbook(uws, "licenses", lookup)
    check(len(parsed) == 3, f"3 valid Utility rows parsed (got {len(parsed)})")
    check(any("missing employee id" in e.lower() for e in errors),
          f"blank-ID row reported as error (errors={errors})")

    by_code = {p["employee"]["employee_code"]: p for p in parsed}
    check(set(by_code) == {"EMP-U1", "EMP-U2", "EMP-U3"}, f"codes {sorted(by_code)}")

    r1 = by_code.get("EMP-U1", {})
    check(r1.get("employee", {}).get("name") == "ALPHA ONE", "U1 name")
    check(r1.get("employee", {}).get("designation") == "TECHNICIAN", "U1 designation")
    check(r1.get("employee", {}).get("section") == "UTILITY", "U1 section")
    check(r1.get("employee", {}).get("factory_id") == agl_id, "U1 factory = AGL (full name)")
    check(r1.get("compliance_type") == "Electrician License", f"U1 default type ({r1.get('compliance_type')})")
    check(r1.get("registration_number") == "S20210004805", "U1 registration number")
    check(r1.get("category") == "BC", "U1 category present (Utility)")
    check(r1.get("issue_date") == date(2024, 5, 23), f"U1 issue date ({r1.get('issue_date')})")
    check(r1.get("expiry_date") == date(2025, 5, 22), f"U1 expiry date ({r1.get('expiry_date')})")
    check(r1.get("no_expiry") is False, "U1 has an expiry")
    check(r1.get("remarks") == "first row", "U1 remarks")

    r2 = by_code.get("EMP-U2", {})
    check(r2.get("employee", {}).get("factory_id") == asl_id, "U2 factory = ASL (full name)")
    check(r2.get("issue_date") == date(2016, 3, 10), f"U2 dot-format issue date ({r2.get('issue_date')})")
    check(r2.get("expiry_date") == date(2027, 3, 9), f"U2 dot-format expiry date ({r2.get('expiry_date')})")

    r3 = by_code.get("EMP-U3", {})
    check(r3.get("no_expiry") is True, "U3 'Till Now' -> no_expiry")
    check(r3.get("expiry_date") is None, "U3 no expiry date stored")
    check(r3.get("category") == "2nd class", "U3 category")

    print("\n--- Medical sheet parse ---")
    mparsed, merrors, mwarnings = parse_workbook(mws, "medical", lookup)
    check(len(mparsed) == 1, f"1 Medical row parsed (got {len(mparsed)})")
    m1 = mparsed[0] if mparsed else {}
    check(m1.get("compliance_type") == "Medical License", f"Medical default type ({m1.get('compliance_type')})")
    check(m1.get("category") is None, "Medical has no Category column -> category None")
    check(m1.get("issue_date") == date(2023, 11, 7), "Medical datetime issue date")
    check(m1.get("expiry_date") == date(2028, 11, 6), "Medical datetime expiry date")
    wb.close()

    print("\n--- apply_import + idempotency ---")
    codes = ["EMP-U1", "EMP-U2", "EMP-U3", "EMP-M1"]
    pre_existing = {e.employee_code for e in Employee.query.filter(Employee.employee_code.in_(codes)).all()}
    summary = apply_import(parsed + mparsed)
    created_emps = 4 - len(pre_existing)
    check(summary["licenses_created"] == 4, f"4 licenses created (got {summary['licenses_created']})")
    check(summary["employees_created"] == created_emps,
          f"{created_emps} new employees created (got {summary['employees_created']})")
    summary2 = apply_import(parsed + mparsed)
    check(summary2["licenses_created"] == 0 and summary2["employees_created"] == 0,
          f"idempotent re-run creates nothing ({summary2})")

    print("\n--- Part 3: automatic license alert copies the compliance group ---")
    emp = Employee.query.filter_by(employee_code="EMP-U1", factory_id=agl_id).first()
    lic = EmployeeCompliance.query.filter_by(employee_id=emp.id).first() if emp else None
    if lic is not None:
        addrs = {r["email"].strip().lower() for r in resolve_alert_recipients(lic, "license")}
        check(COMPLIANCE_GROUP in addrs,
              f"license alert recipients include {COMPLIANCE_GROUP} (got {sorted(addrs)})")
    else:
        check(False, "seeded license found for alert check")

    # ---- cleanup: remove only the rows this test created ----------------
    for e in Employee.query.filter(Employee.employee_code.in_(codes)).all():
        if e.employee_code not in pre_existing:
            EmployeeCompliance.query.filter_by(employee_id=e.id).delete()
            db.session.delete(e)
    db.session.commit()
    try:
        os.unlink(path)
        os.rmdir(tmp)
    except OSError:
        pass

print(f"\n==== {OK} passed, {FAIL} failed ====")
sys.exit(0 if FAIL == 0 else 1)
