"""Health import against a synthetic merged multi-row header (the Ananta layout).

Proves the importer folds a title-banner + exam-group header into usable
columns, uses F ID as the unique key, and treats stray time cells as blank.
"""
import os
import sys
import tempfile
from datetime import datetime, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "test-key")

from openpyxl import Workbook
from app import create_app, db
from app.models import Employee, Factory
from app.import_utils import parse_workbook, apply_import, build_factory_lookup

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


def build_merged(path, factory_name):
    wb = Workbook()
    ws = wb.active
    ws.title = "2026-27"
    # Row 1-2: title banners
    ws.cell(row=1, column=1, value=factory_name)
    ws.cell(row=2, column=1, value="Health Checkup Reports")
    # Row 3: main header (F ID, ID, NAME, SECTION, DESIGNATION, DOJ, AGE, Factory,
    #        Mode, EXAM NAME..., PRE, TEST CENTER)
    main = ["F ID (Hide)", "ID", "NAME ", "SECTION", "DESIGNATION", "DOJ", "AGE",
            "Factory", "Mode\n(Hide)", "EXAM NAME", None, None, None, None, None,
            None, None, "PRE\nY/N", "TEST CENTER"]
    for c, v in enumerate(main, 1):
        if v is not None:
            ws.cell(row=3, column=c, value=v)
    # Row 4: exam names, each merged across its 2 columns (we leave the 2nd cell
    # blank to simulate a merged cell)
    ws.cell(row=4, column=10, value="CBC")
    ws.cell(row=4, column=12, value="HEARING / Widal")
    ws.cell(row=4, column=14, value="CHEST X-RAY P/A")
    ws.cell(row=4, column=16, value="Urine Test")
    # Row 5: REPORT DATE / EXPIRE DATE pairs
    for c in (10, 12, 14, 16):
        ws.cell(row=5, column=c, value="REPORT DATE")
        ws.cell(row=5, column=c + 1, value="EXPIRE DATE")
    # Data rows (6+). Two people share short ID "034" but differ by F ID.
    rows = [
        # F ID,       ID,   NAME,            SEC, DESG, DOJ,                 AGE, Factory, Mode, CBCr, CBCe, HWr, HWe, CXr, CXe, URr, URe, PRE, CENTER
        ["GSCS034", "034", "PERSON ONE", "SPOT", "JR", datetime(2016,10,13), "29", "AGLX", None,
         datetime(2026,8,22), datetime(2027,8,21), None, datetime(2027,8,21), None, None, None, None, None, "Checkup Point"],
        ["AGWP034", "034", "PERSON TWO", "WASH", "SR", datetime(2018,1,1), "31", "AGLX", None,
         None, None, datetime(2026,9,1), datetime(2027,9,1), time(0, 0), None, None, None, None, None],
        # Blank row (should be skipped)
        [None]*19,
    ]
    for i, r in enumerate(rows, start=6):
        for c, v in enumerate(r, 1):
            if v is not None:
                ws.cell(row=i, column=c, value=v)
    wb.save(path)


app = create_app()
with app.app_context():
    # Use a real factory but give it a unique abbreviation the sheet references.
    factory = Factory.query.first()
    fname = factory.name
    lookup = build_factory_lookup()

    tmp = tempfile.mkdtemp(prefix="merged_")
    path = os.path.join(tmp, "health.xlsx")
    # Reference the factory by its real name so resolve_factory maps it.
    build_merged(path, fname)
    # Patch the Factory column values to the real factory name (the builder put
    # "AGLX"); simplest is to rewrite with fname.
    from openpyxl import load_workbook
    wb = load_workbook(path)
    ws = wb["2026-27"]
    for r in (6, 7):
        ws.cell(row=r, column=8, value=fname)
    wb.save(path)

    ws = load_workbook(path, data_only=True)["2026-27"]
    parsed, errors, warnings = parse_workbook(ws, "health", lookup)
    check(len(errors) == 0, f"no errors ({errors})")
    check(len(parsed) == 2, f"2 data rows parsed, blank skipped (got {len(parsed)})")
    codes = sorted(p["employee"]["employee_code"] for p in parsed)
    check(codes == ["AGWP034", "GSCS034"], f"F ID used as code, not short ID (got {codes})")
    # Person one: CBC (report+expire) + Hearing (expire only) = 2 exams
    p1 = next(p for p in parsed if p["employee"]["employee_code"] == "GSCS034")
    kinds1 = sorted(e["exam_type"] for e in p1["exams"])
    check(kinds1 == ["CBC", "Hearing / Widal"], f"person one exams {kinds1}")
    # Person two: Hearing only (the Chest report cell is a stray time -> blank)
    p2 = next(p for p in parsed if p["employee"]["employee_code"] == "AGWP034")
    kinds2 = sorted(e["exam_type"] for e in p2["exams"])
    check(kinds2 == ["Hearing / Widal"], f"person two: stray time ignored, only Hearing ({kinds2})")

    # Apply + idempotency
    pre = {e.employee_code for e in Employee.query.all()}
    summary = apply_import(parsed)
    check(summary["employees_created"] == 2 and summary["checkups_created"] == 3,
          f"apply created 2 emp + 3 checkups ({summary})")
    summary2 = apply_import(parsed)
    check(summary2["employees_created"] == 0 and summary2["checkups_created"] == 0,
          f"idempotent re-run ({summary2})")

    # cleanup
    for e in Employee.query.filter(Employee.employee_code.in_(["GSCS034", "AGWP034"])).all():
        if e.employee_code not in pre:
            db.session.delete(e)
    db.session.commit()
    try:
        os.unlink(path); os.rmdir(tmp)
    except OSError:
        pass

print(f"\n==== {OK} passed, {FAIL} failed ====")
sys.exit(0 if FAIL == 0 else 1)
