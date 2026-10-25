"""Vehicle fleet tests: model, import pipeline, routes, dashboard, RBAC.

Standalone script (no pytest dependency).

    venv\\Scripts\\python.exe tests\\test_vehicles.py
"""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "test-key")

from openpyxl import Workbook

from app import create_app, db
from app.models import (
    Role, User, Factory, Vehicle, VehicleDocument,
    VEHICLE_DOC_TYPES, VEHICLE_DOC_LABELS,
)
from app.utils import compute_status, bst_today, STATUS_VALID, STATUS_EXPIRED

OK = 0
FAIL = 0


def check(cond, label):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK  ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def build_fixture(path, today):
    """Synthetic 10-row workbook that covers every parser branch."""
    wb = Workbook()
    ws = wb.active
    ws.title = "2026"
    # Headers on row 3 (matches real workbook layout)
    headers = ["SL", "Last 4 Digit", "User Office Name", "Vehicle Type",
               "Registration No", "Owner Name", "Modified/New Blue Book",
               "Tax Token Expiry date", "Fitness Expiry Date",
               "Route permit expiry date"]
    for col, h in enumerate(headers, 1):
        ws.cell(row=3, column=col, value=h)

    bb_date = datetime(today.year + 1, 3, 1)
    rows = [
        # SL, Last4, Office, Type, Reg, Owner, BB, Tax, Fit, RP
        # Full row, factory match (Ananta Garments Ltd.)
        [1, 1001, "AGL", "Toyota Axio",
         "DM GA 11-1001", "Ananta Garments Ltd.",
         bb_date, datetime(today.year + 1, 1, 1), datetime(today.year + 1, 2, 1),
         datetime(today.year + 1, 3, 1)],
        # Blue Book note only; Fitness blank (optional)
        [2, 1002, "HO", "Pickup",
         "DM GA-11-1002", "Ananta Sportswear Ltd.",
         "Smart Card received", datetime(today.year + 1, 4, 5), None, None],
        # Personal owner (unmapped), only Tax Token
        [3, 1003, "Personal", "Honda",
         "DM MA 11-1003", "John Doe",
         None, datetime(today.year + 1, 5, 1), None, None],
        # Expired Tax Token today-1
        [4, 1004, "AGL", "Corolla",
         "DM GA 11-1004", "Ananta Garments Ltd.",
         None, today - timedelta(days=1), None, None],
        # Expiring in 7 Days: today+5
        [5, 1005, "AGL", "Fielder",
         "DM GA 11-1005", "Ananta Garments Ltd.",
         None, today + timedelta(days=5), None, None],
        # Reg-no with dashes/dots: should normalize to same key as row 1 variant
        [6, 1006, "HO", "Van",
         "DM.GA-11-1006", "ABM Fashions Ltd.",
         None, datetime(today.year + 1, 7, 1), None, None],
        # Duplicate reg no (should flag row error on second occurrence)
        [7, 1007, "AGL", "Dup",
         "DM GA 11-1001", "Ananta Garments Ltd.",
         None, datetime(today.year + 1, 1, 1), None, None],
        # Blank registration no (empty row-no-op)
        [8, None, None, None, None, None, None, None, None, None],
        # Missing Tax Token (row error: required)
        [9, 1009, "AGL", "Noplate",
         "DM GA 11-1009", "Ananta Garments Ltd.",
         None, None, datetime(today.year + 1, 8, 1), None],
        # Invalid date string in Fitness column
        [10, 1010, "AGL", "BadDate",
         "DM GA 11-1010", "Ananta Garments Ltd.",
         None, datetime(today.year + 1, 9, 1), "notadate!!", None],
    ]
    for i, row in enumerate(rows, start=4):
        for j, val in enumerate(row, start=1):
            ws.cell(row=i, column=j, value=val)
    wb.save(path)


def test_normalization():
    print("\n--- normalization ---")
    check(Vehicle.normalize_reg_no("DM GA 11-1001") == "DMGA111001", "spaces + dash")
    check(Vehicle.normalize_reg_no("DM.GA-11-1001") == "DMGA111001", "dots + dash")
    check(Vehicle.normalize_reg_no("  dm   ga 11-1001 ") == "DMGA111001", "caseless + trim")
    check(Vehicle.normalize_reg_no(None) == "", "None")
    check(Vehicle.normalize_reg_no("") == "", "empty")


def test_status_engine_bucket_boundaries():
    print("\n--- status engine for VehicleDocument expiry ---")
    today = bst_today()
    pairs = [
        (today + timedelta(days=95), STATUS_VALID),
        (today + timedelta(days=91), STATUS_VALID),
        (today + timedelta(days=90), "Expiring in 90 Days"),
        (today + timedelta(days=60), "Expiring in 60 Days"),
        (today + timedelta(days=30), "Expiring in 30 Days"),
        (today + timedelta(days=7), "Expiring in 7 Days"),
        (today, STATUS_EXPIRED),
        (today - timedelta(days=1), STATUS_EXPIRED),
    ]
    for expiry, want in pairs:
        got = compute_status(expiry, no_expiry=False)
        check(got == want, f"{expiry} -> {want} (got {got})")
    check(compute_status(None, no_expiry=True) == STATUS_VALID, "no_expiry=True -> Valid")


def _reset_vehicle_data(app):
    with app.app_context():
        for t in ("vehicle_document_versions", "vehicle_documents",
                  "vehicles", "vehicle_import_runs"):
            db.session.execute(db.text(f"DELETE FROM {t}"))
        db.session.commit()


def test_import_pipeline(app, fixture_path):
    print("\n--- import pipeline ---")
    from app.vehicle_import import (parse_workbook, annotate_existing,
                                     preview_counts, apply_import)
    _reset_vehicle_data(app)
    with app.app_context():
        rows, errors = parse_workbook(fixture_path)
        annotate_existing(rows)
        counts = preview_counts(rows)
        check(errors == [], f"workbook-level errors empty (got {errors})")
        # Expected: 10 data rows total. SL=8 is fully blank -> skipped silently.
        # So we expect 9 parsed rows: 7 clean (incl. 1 duplicate flag), 2 with errors.
        check(len(rows) == 9, f"9 non-empty rows parsed (got {len(rows)})")
        bad = [r for r in rows if r["row_errors"]]
        check(len(bad) >= 3, f"errors on dup / missing tax / bad date (got {len(bad)})")
        # Row with duplicate reg
        dup = [r for r in bad if any("Duplicate" in e for e in r["row_errors"])]
        check(len(dup) == 1, "duplicate reg flagged exactly once")
        missing = [r for r in bad if any("Tax Token" in e for e in r["row_errors"])]
        check(len(missing) == 1, "missing Tax Token flagged")
        baddate = [r for r in bad if any("Fitness" in e for e in r["row_errors"])]
        check(len(baddate) == 1, "unrecognized date flagged on Fitness")
        # Owner resolution: AGL rows -> factory_id set
        agl_rows = [r for r in rows if r["owner_raw"] == "Ananta Garments Ltd." and not r["row_errors"]]
        check(all(r["factory_id"] for r in agl_rows), "all clean AGL rows mapped to factory")
        # Personal owner unmapped
        personal = [r for r in rows if r["owner_raw"] == "John Doe"]
        check(personal and personal[0]["factory_id"] is None, "personal owner stays unmapped")

        # Apply
        summary = apply_import(rows, user_id=1)
        new_v = summary["new_vehicles"]
        new_d = summary["new_docs"]
        # 10 fixture rows: 1 blank silently skipped, 3 row-errors (dup / missing
        # Tax Token / bad Fitness date) -> 6 clean rows become vehicles.
        check(new_v == 6, f"6 new vehicles (got {new_v})")
        # Each clean row: tax token = 5, plus bb_date, bb_note, fitness extras
        check(new_d >= 5, f"at least 5 new docs (got {new_d})")

        # Blue Book semantics: row 1 has date, row 2 has note only
        v1 = Vehicle.query.filter_by(reg_no_normalized="DMGA111001").first()
        v2 = Vehicle.query.filter_by(reg_no_normalized="DMGA111002").first()
        bb1 = next((d for d in v1.documents if d.doc_type == "blue_book"), None) if v1 else None
        bb2 = next((d for d in v2.documents if d.doc_type == "blue_book"), None) if v2 else None
        check(bb1 is not None and bb1.expiry_date is not None, "row1 Blue Book stored as date")
        check(bb2 is not None and bb2.note == "Smart Card received" and bb2.no_expiry,
              "row2 Blue Book stored as note-only, no_expiry=True")
        check(bb2.status == STATUS_VALID, "note-only Blue Book status is Valid")

        # Status for the deliberately-expired row
        v4 = Vehicle.query.filter_by(reg_no_normalized="DMGA111004").first()
        tt4 = next((d for d in v4.documents if d.doc_type == "tax_token"), None)
        check(tt4 is not None and tt4.status == STATUS_EXPIRED, "expired row stored as Expired")

        # Idempotency: parsing + applying the same file again should produce
        # zero new vehicles/docs/updates.
        rows2, _ = parse_workbook(fixture_path)
        annotate_existing(rows2)
        summary2 = apply_import(rows2, user_id=1)
        check(summary2["new_vehicles"] == 0, "idempotent: 0 new vehicles on re-run")
        check(summary2["new_docs"] == 0, "idempotent: 0 new docs on re-run")
        check(summary2["updated_vehicles"] == 0, "idempotent: 0 updated vehicles on re-run")
        check(summary2["updated_docs"] == 0, "idempotent: 0 updated docs on re-run")


def test_blank_date_does_not_clear(app, fixture_path):
    """A blank cell in the sheet must never clear an existing expiry."""
    print("\n--- blank-date-doesn't-clear rule ---")
    from app.vehicle_import import parse_workbook, annotate_existing, apply_import
    _reset_vehicle_data(app)
    with app.app_context():
        # First import populates v1 with Fitness=today+1year.
        rows, _ = parse_workbook(fixture_path)
        annotate_existing(rows)
        apply_import(rows, user_id=1)
        v1 = Vehicle.query.filter_by(reg_no_normalized="DMGA111001").first()
        fit = next((d for d in v1.documents if d.doc_type == "fitness"), None)
        original_expiry = fit.expiry_date
        check(original_expiry is not None, "fitness has an expiry after first import")

        # Build a second workbook where that vehicle's Fitness column is blank.
        today = bst_today()
        tmp2 = fixture_path + ".blank.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "2026"
        for col, h in enumerate(["SL", "Last 4 Digit", "User Office Name",
                                 "Vehicle Type", "Registration No", "Owner Name",
                                 "Modified/New Blue Book", "Tax Token Expiry date",
                                 "Fitness Expiry Date", "Route permit expiry date"], 1):
            ws.cell(row=3, column=col, value=h)
        ws.cell(row=4, column=1, value=1)
        ws.cell(row=4, column=5, value="DM GA 11-1001")
        ws.cell(row=4, column=6, value="Ananta Garments Ltd.")
        ws.cell(row=4, column=8, value=datetime(today.year + 1, 1, 1))
        # cols 9 (Fitness) + 10 (Route Permit) deliberately blank
        wb.save(tmp2)

        rows2, _ = parse_workbook(tmp2)
        annotate_existing(rows2)
        apply_import(rows2, user_id=1)

        v1 = Vehicle.query.filter_by(reg_no_normalized="DMGA111001").first()
        fit = next((d for d in v1.documents if d.doc_type == "fitness"), None)
        check(fit is not None and fit.expiry_date == original_expiry,
              "blank Fitness in re-upload preserved existing expiry")
        os.unlink(tmp2)


def test_routes_rbac(app):
    print("\n--- routes + RBAC ---")
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()

    with app.app_context():
        u = User.query.filter_by(username="admin").first()
        u.set_password("Ananta@2026")
        u.failed_login_attempts = 0
        u.locked_until = None
        orig_role = u.role_id
        db.session.commit()

    r = client.post("/login", data={"username": "admin", "password": "Ananta@2026"},
                    follow_redirects=True)
    check(b"Dashboard" in r.data, "admin login")

    r = client.get("/vehicles/")
    check(r.status_code == 200, f"/vehicles/ as Super Admin = 200 (got {r.status_code})")
    r = client.get("/vehicles/dashboard")
    check(r.status_code == 200, f"/vehicles/dashboard as Super Admin = 200 (got {r.status_code})")
    r = client.get("/vehicles/import")
    check(r.status_code == 200, f"/vehicles/import as Super Admin = 200 (got {r.status_code})")

    def _swap_role(role_name):
        with app.app_context():
            u = User.query.filter_by(username="admin").first()
            role = Role.query.filter_by(name=role_name).first()
            u.role_id = role.id
            db.session.commit()

    def _restore():
        with app.app_context():
            u = User.query.filter_by(username="admin").first()
            u.role_id = orig_role
            db.session.commit()

    # Compliance User: 403 on all three
    _swap_role("Compliance User")
    for path in ("/vehicles/", "/vehicles/dashboard", "/vehicles/import"):
        r = client.get(path)
        check(r.status_code == 403, f"Compliance User {path} = 403 (got {r.status_code})")

    # Transport Manager: 200 on all three, / redirects
    _swap_role("Transport Manager")
    r = client.get("/", follow_redirects=False)
    check(r.status_code == 302 and "/vehicles/dashboard" in r.headers.get("Location", ""),
          "Transport Manager / redirects to /vehicles/dashboard")
    for path in ("/vehicles/", "/vehicles/dashboard", "/vehicles/import"):
        r = client.get(path)
        check(r.status_code == 200, f"Transport Manager {path} = 200 (got {r.status_code})")

    # Transport User: 200 on list/dashboard, 403 on import
    _swap_role("Transport User")
    r = client.get("/vehicles/")
    check(r.status_code == 200, "Transport User /vehicles/ = 200")
    r = client.get("/vehicles/dashboard")
    check(r.status_code == 200, "Transport User /vehicles/dashboard = 200")
    r = client.get("/vehicles/import")
    check(r.status_code == 403, f"Transport User /vehicles/import = 403 (got {r.status_code})")

    _restore()


def test_dashboard_counts(app, fixture_path):
    """Dashboard aggregates must equal raw DB queries."""
    print("\n--- dashboard counts ---")
    from app.vehicle_import import parse_workbook, annotate_existing, apply_import
    _reset_vehicle_data(app)
    with app.app_context():
        rows, _ = parse_workbook(fixture_path)
        annotate_existing(rows)
        apply_import(rows, user_id=1)

    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()
    with app.app_context():
        u = User.query.filter_by(username="admin").first()
        u.set_password("Ananta@2026")
        u.failed_login_attempts = 0
        u.locked_until = None
        db.session.commit()
    client.post("/login", data={"username": "admin", "password": "Ananta@2026"})
    r = client.get("/vehicles/dashboard")
    check(r.status_code == 200, "dashboard renders")

    import re
    html = r.data.decode()
    with app.app_context():
        total_v = Vehicle.query.filter(Vehicle.is_active.is_(True)).count()
        expired_d = (VehicleDocument.query.join(Vehicle)
                     .filter(Vehicle.is_active.is_(True),
                             VehicleDocument.is_active.is_(True),
                             VehicleDocument.status == STATUS_EXPIRED).count())
        exp7 = (VehicleDocument.query.join(Vehicle)
                .filter(Vehicle.is_active.is_(True),
                        VehicleDocument.is_active.is_(True),
                        VehicleDocument.status == "Expiring in 7 Days").count())
    for label, want in [("Total vehicles", total_v),
                         ("Expired documents", expired_d),
                         ("Expiring in 7 days", exp7)]:
        m = re.search(label + r'</div><div class="stat-value">(\d+)</div>', html)
        got = int(m.group(1)) if m else -1
        check(got == want, f"card '{label}' = DB count ({got} vs {want})")


def test_download_file_path_escape(app):
    print("\n--- download endpoint path safety ---")
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()
    with app.app_context():
        u = User.query.filter_by(username="admin").first()
        u.set_password("Ananta@2026")
        u.failed_login_attempts = 0
        u.locked_until = None
        db.session.commit()
    client.post("/login", data={"username": "admin", "password": "Ananta@2026"})
    # Outside the vehicles/ subtree -> 404
    r = client.get("/vehicles/files/factory_1/something.pdf")
    check(r.status_code == 404, f"non-vehicles path = 404 (got {r.status_code})")
    # Path traversal attempt
    r = client.get("/vehicles/files/vehicles/../factory_1/x.pdf")
    check(r.status_code == 404, f"../ traversal = 404 (got {r.status_code})")


def main():
    app = create_app()
    tmp = tempfile.mkdtemp(prefix="veh_fixture_")
    fixture_path = os.path.join(tmp, "vehicles.xlsx")
    today = date.today()
    build_fixture(fixture_path, today)

    test_normalization()
    test_status_engine_bucket_boundaries()
    test_import_pipeline(app, fixture_path)
    test_blank_date_does_not_clear(app, fixture_path)
    test_routes_rbac(app)
    test_dashboard_counts(app, fixture_path)
    test_download_file_path_escape(app)

    try:
        os.unlink(fixture_path)
        os.rmdir(tmp)
    except Exception:
        pass

    print(f"\n==== {OK} passed, {FAIL} failed ====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
