import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "test-key")

from openpyxl import Workbook
from app import create_app, db
from app.models import Factory, Employee, EmployeeCompliance, HealthCheckup
from app.import_utils import parse_workbook, apply_import, build_factory_lookup

app = create_app()
results = []

with app.app_context():
    factory = Factory.query.order_by(Factory.id).first()
    fname = factory.name
    lookup = build_factory_lookup()
    print("Using factory:", fname, "id", factory.id)

    # --- licenses workbook ---
    wb = Workbook(); ws = wb.active
    ws.append(["Name", "Designation", "ID No", "Section", "Registration No", "Category", "Issue Date", "Expiry Date", "Factory", "Remarks"])
    ws.append(["Test Person A", "Electrician", "ZZTEST1", "Maintenance", "REG-111", "LT", "2024-01-01", "2027-01-01", fname, "note"])
    ws.append(["Test Person B", "Electrician", "ZZTEST2", "Maintenance", "REG-222", "HT", "2024-01-01", "Till Now", fname, ""])
    parsed, errors, _warnings = parse_workbook(ws, "licenses", lookup)
    results.append(("licenses parsed==2", len(parsed) == 2, f"got {len(parsed)}, errors={errors}"))
    no_exp = [p for p in parsed if p["no_expiry"]]
    results.append(("licenses Till Now -> no_expiry", len(no_exp) == 1 and no_exp[0]["expiry_date"] is None, str(no_exp)))

    summary = apply_import(parsed)
    results.append(("licenses apply created 2 emp + 2 lic",
                    summary["employees_created"] == 2 and summary["licenses_created"] == 2, str(summary)))
    summary2 = apply_import(parsed)
    results.append(("licenses re-import idempotent (0 new, dups skipped)",
                    summary2["employees_created"] == 0 and summary2["licenses_created"] == 0 and summary2["skipped_duplicates"] == 2, str(summary2)))

    # --- health workbook ---
    wb2 = Workbook(); ws2 = wb2.active
    ws2.append(["Employee ID", "Name", "Section", "Designation", "DOJ", "Factory", "CBC Report", "CBC Expiry", "Urine Test Report", "Urine Test Expiry", "PRE Y/N", "Test Center"])
    ws2.append(["ZZTEST1", "Test Person A", "Maintenance", "Electrician", "2020-05-05", fname, "2025-01-01", "2026-01-01", "2025-01-01", "Till Now", "Y", "City Lab"])
    parsed_h, errors_h, _warnings_h = parse_workbook(ws2, "health", lookup)
    results.append(("health parsed==1", len(parsed_h) == 1, f"errors={errors_h}"))
    exams = parsed_h[0]["exams"] if parsed_h else []
    results.append(("health 2 exams (CBC+Urine)", len(exams) == 2, str([e['exam_type'] for e in exams])))
    sh = apply_import(parsed_h)
    results.append(("health apply reuses existing employee (0 new emp), 2 checkups",
                    sh["employees_created"] == 0 and sh["checkups_created"] == 2, str(sh)))

    # cleanup
    test_emps = Employee.query.filter(Employee.employee_code.in_(["ZZTEST1", "ZZTEST2"])).all()
    for e in test_emps:
        db.session.delete(e)
    db.session.commit()
    remaining = Employee.query.filter(Employee.employee_code.in_(["ZZTEST1", "ZZTEST2"])).count()
    results.append(("cleanup removed test employees", remaining == 0, f"remaining={remaining}"))

print("\n=== IMPORT TEST RESULTS ===")
ok = True
for name, passed, detail in results:
    if not passed: ok = False
    print(f"  [{'OK' if passed else 'FAIL'}] {name} :: {detail}")
print("\nIMPORT TEST:", "PASSED" if ok else "FAILED")
sys.exit(0 if ok else 1)
