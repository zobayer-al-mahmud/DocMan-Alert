"""
Excel import for initial migration / controlled onboarding (Super Admin only).

Supports three source layouts:
    - "licenses"  : Utility/Electrician licenses
    - "medical"   : Medical licenses
    - "health"    : Health checkup wide sheet (normalized into exam rows)

Design rules (see master spec section 19):
    - map employee identity carefully (factory + employee code)
    - normalize AGL/ASL etc. to factory records
    - convert Excel serial dates correctly
    - validate required columns, reject invalid dates
    - never store "Till Now" as a date -> no_expiry instead
    - prevent duplicate employees / compliance records (idempotent)
    - preview before final import
"""
import re
from datetime import datetime, date, time, timedelta

from app import db
from app.models import (
    Company, Department, Employee, EmployeeCompliance, Factory, HealthCheckup, User,
    company_alert_recipients, department_alert_recipients,
)
from app.utils import GLOBAL_ALERT_ROLES, compute_status, valid_email

NO_EXPIRY_TOKENS = {"till now", "tillnow", "n/a", "na", "no expiry", "noexpiry",
                    "permanent", "lifetime", "-", "--", "none", ""}

IMPORT_TYPES = {
    "licenses": "Employee Licenses (Utility / Electrician)",
    "medical": "Medical Licenses",
    "health": "Health Checkups",
    "employees": "Employees",
    "recipients": "Alert Recipients",
    "workbook": "Full Workbook (Employees, Licenses, Health Checkups, Alert Recipients)",
}

RECIPIENT_SCOPES = {"COMPANY", "DEPARTMENT"}
_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

HEALTH_EXAMS = [
    ("cbc", "CBC"),
    ("hearingwidal", "Hearing / Widal"),
    ("chestxray", "Chest X-Ray P/A"),
    ("urinetest", "Urine Test"),
]


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", str(text or "").strip().lower())


# Real compliance dates are all modern. Excel "empty-but-formatted-as-date"
# cells surface as epoch artifacts (serial 0 -> 1899-12-30, small serials ->
# 1900-xx-xx, e.g. the "1900-12-29" seen on health sheets). Anything before this
# floor is a placeholder, not a real date, and must be treated as a blank cell
# so phantom "Expired 1900" records are never created.
_DATE_FLOOR = date(1950, 1, 1)


def _bounded(parsed):
    """Blank out sub-floor epoch placeholders; otherwise a real date."""
    if parsed is None or parsed < _DATE_FLOOR:
        return None, False, None
    return parsed, False, None


def parse_date(value):
    """Return (date|None, no_expiry: bool, error: str|None)."""
    if value is None:
        return None, False, None
    if isinstance(value, datetime):
        return _bounded(value.date())
    # A bare time with no date (e.g. Excel cell holding 00:00:00) is not a real
    # expiry — treat it as blank rather than an error. Checked before `date`
    # because datetime is a subclass of date but time is not.
    if isinstance(value, time):
        return None, False, None
    if isinstance(value, date):
        return _bounded(value)
    if isinstance(value, (int, float)):
        # Excel serial date (1900 date system). Day 1 = 1900-01-01.
        try:
            base = datetime(1899, 12, 30)  # accounts for Excel's 1900 leap-year bug
            return _bounded((base + timedelta(days=float(value))).date())
        except Exception:
            return None, False, f"invalid date serial '{value}'"
    text = str(value).strip().translate(_BN_DIGITS)
    if _norm(text) in {_norm(t) for t in NO_EXPIRY_TOKENS}:
        return None, True, None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d %b %Y",
                "%Y/%m/%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return _bounded(datetime.strptime(text, fmt).date())
        except ValueError:
            continue
    return None, False, f"unrecognized date '{text}'"


def build_factory_lookup():
    """Map normalized factory tokens -> factory_id, including AGL/ASL style
    abbreviations derived from capital letters in the factory name."""
    lookup = {}
    for f in Factory.query.all():
        lookup[_norm(f.name)] = f.id
        # Abbreviation from capitals, e.g. "Ananta Garments Ltd" -> "AGL".
        abbr = "".join(ch for ch in f.name if ch.isupper())
        if len(abbr) >= 2:
            lookup[_norm(abbr)] = f.id
    return lookup


def resolve_factory(value, lookup):
    key = _norm(value)
    if not key:
        return None
    if key in lookup:
        return lookup[key]
    # Partial contains match (e.g. sheet says "AGL Washing" -> "AGL").
    for token, fid in lookup.items():
        if token and (token in key or key in token):
            return fid
    return None


def _read_rows(ws):
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], []
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    return header, rows[1:]


_ID_HEADER_KEYS = {"id", "idno", "employeeid", "fid", "fidhide", "idno."}


def _license_header_and_rows(ws):
    """Return (header, data_rows) for a Utility/Medical license sheet.

    The Ananta "Electrician and Medical License Details" workbooks put a title
    banner on row 1 ("Ananta Garments Ltd./..."), a subtitle on row 3
    ("Electrician License Details-2025"), blank rows, then the real header on
    row 6: 'Sl No.', 'Name', 'Designation', 'ID No', 'Section',
    'Registration No.', 'Category', 'Issue Date', 'Expiry Date', 'Factory',
    'Remarks' -- usually preceded by an empty column A. Scan the first rows for
    the header row (Name + Factory + an ID or Registration column) and return
    it with the rows beneath it. `_header_index` searches every column, so the
    leading empty column is harmless once the correct row is found. Falls back
    to the first row so the flat single-row test fixtures keep working.
    """
    all_rows = list(ws.iter_rows(values_only=True))
    if not all_rows:
        return [], []
    for i in range(min(len(all_rows), 12)):
        cells = [_norm(c) for c in all_rows[i]]
        has_name = any(c in ("name", "employeename") for c in cells)
        has_factory = any(c == "factory" for c in cells)
        has_id = any(c in _ID_HEADER_KEYS for c in cells)
        has_reg = any(c and ("registration" in c or c == "regno") for c in cells)
        if has_name and has_factory and (has_id or has_reg):
            header = [str(c).strip() if c is not None else "" for c in all_rows[i]]
            return header, all_rows[i + 1:]
    return [str(c).strip() if c is not None else "" for c in all_rows[0]], all_rows[1:]


def _health_header_and_rows(ws):
    """Flatten a health sheet's header and return (flat_header, data_rows).

    Handles two real-world layouts:
      * Flat single-row header (as the test fixtures use): the exam columns are
        named e.g. "CBC Report", "CBC Expiry" on one row.
      * Merged multi-row header (the Ananta "Health Checkup Summary" workbook):
        title banner rows, then a main header row (ID / NAME / Factory / ...),
        then an exam-name row (CBC, HEARING / Widal, ... each merged across its
        REPORT/EXPIRE pair) and a "REPORT DATE / EXPIRE DATE" row.

    For the merged layout the exam name is folded into each date column so the
    result looks like "CBC REPORT DATE" / "CBC EXPIRE DATE", which the existing
    exam-column matcher understands. Non-exam columns keep their main-header
    name. Title/sub-header rows are stripped from the returned data rows.
    """
    all_rows = list(ws.iter_rows(values_only=True))
    if not all_rows:
        return [], []
    width = max((len(r) for r in all_rows), default=0)

    # Locate the main header row: has an ID-like column + Factory + Name.
    main_i = None
    for i in range(min(len(all_rows), 12)):
        cells = [_norm(c) for c in all_rows[i]]
        has_id = any(c in _ID_HEADER_KEYS for c in cells)
        has_factory = any("factory" in c for c in cells if c)
        has_name = any(c == "name" or c == "employeename" for c in cells)
        if has_id and has_factory and has_name:
            main_i = i
            break
    if main_i is None:
        # No recognizable header; fall back to the first row as a flat header.
        return [str(c).strip() if c is not None else "" for c in all_rows[0]], all_rows[1:]

    main = list(all_rows[main_i])
    id_col = next(
        (ci for ci, c in enumerate(main) if _norm(c) in _ID_HEADER_KEYS), None
    )

    # Gather sub-header rows directly under the main header: rows whose ID
    # column is empty and which carry exam-name / report-expire labels. Stop at
    # the first real data row (ID column populated).
    sub_rows = []
    j = main_i + 1
    while j < len(all_rows):
        row = all_rows[j]
        id_val = row[id_col] if (id_col is not None and id_col < len(row)) else None
        if id_val not in (None, ""):
            break
        texts = [c for c in row if c not in (None, "")]
        if not texts:
            break
        sub_rows.append(list(row))
        j += 1
        if len(sub_rows) >= 3:
            break
    data_rows = all_rows[j:]

    if not sub_rows:
        return [str(c).strip() if c is not None else "" for c in main], data_rows

    def _re_score(row):
        return sum(
            1 for c in row
            if c and ("report" in _norm(c) or "expire" in _norm(c) or "expiry" in _norm(c))
        )

    report_row = max(sub_rows, key=_re_score)
    name_rows = [r for r in sub_rows if r is not report_row]

    # Forward-fill exam-name rows so merged-away cells inherit the exam name.
    filled_name_rows = []
    for nr in name_rows:
        filled, last = [], ""
        for ci in range(width):
            val = nr[ci] if ci < len(nr) else None
            if val not in (None, ""):
                last = str(val).strip()
            filled.append(last)
        filled_name_rows.append(filled)

    header = []
    for ci in range(width):
        rep_val = report_row[ci] if ci < len(report_row) else None
        if rep_val not in (None, ""):
            exam_name = ""
            for fr in filled_name_rows:
                if fr[ci]:
                    exam_name = fr[ci]
                    break
            header.append(f"{exam_name} {str(rep_val).strip()}".strip())
        else:
            main_val = main[ci] if ci < len(main) else None
            header.append(str(main_val).strip() if main_val is not None else "")
    return header, data_rows


def _header_index(header, *aliases):
    norm_aliases = {_norm(a) for a in aliases}
    for i, h in enumerate(header):
        if _norm(h) in norm_aliases:
            return i
    return None


def _cell(row, idx):
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def build_org_lookups():
    """Company, factory->company, and department indexes used while validating."""
    companies = {}
    for company in Company.query.all():
        companies[_norm(company.name)] = company
        companies[_norm(company.code)] = company
    factory_company = {factory.id: factory.company_id for factory in Factory.query.all()}
    departments_by_name = {}
    for department in Department.query.all():
        departments_by_name.setdefault(_norm(department.name), []).append(department)
    return {
        "companies": companies,
        "factory_company": factory_company,
        "departments_by_name": departments_by_name,
    }


def resolve_company(value, lookups):
    return lookups["companies"].get(_norm(value))


def resolve_department_name(factory_id, company_id, value, lookups):
    """Return (department|None, error|None). Blank value is not an error.

    Departments are shared. The factory and company are not used to pick one.
    """
    name = _text(value)
    if not name:
        return None, None
    matches = lookups["departments_by_name"].get(_norm(name), [])
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, f"department '{name}' matches more than one record."
    return None, f"unknown department '{name}'."


def parse_workbook(ws, import_type, factory_lookup, default_factory_id=None):
    """Return (parsed_rows, errors, warnings). Does not write to the database.

    warnings are rows that will not be imported (duplicates / existing links).
    Notes about rows that WILL be imported are stored on the row as "notes".

    `default_factory_id`, when set, is used only as a fallback for rows whose
    own Factory cell is blank or cannot be matched to a factory record. It is
    validated by the caller (route) against the real factory list.
    """
    header, data_rows = _read_rows(ws)
    parsed, errors, warnings = [], [], []
    lookups = build_org_lookups()

    if import_type in ("licenses", "medical"):
        # Skip the title-banner rows and find the real column header (row 6 in
        # the Ananta license workbooks), then parse the rows beneath it.
        header, data_rows = _license_header_and_rows(ws)
        idx = {
            "name": _header_index(header, "Name", "Employee Name"),
            "designation": _header_index(header, "Designation"),
            "code": _header_index(header, "ID No", "Employee ID", "ID", "ID No."),
            "section": _header_index(header, "Section"),
            "reg": _header_index(header, "Registration No", "Registration Number", "Reg No"),
            "category": _header_index(header, "Category"),
            "issue": _header_index(header, "Issue Date"),
            "expiry": _header_index(header, "Expiry Date"),
            "factory": _header_index(header, "Factory"),
            "remarks": _header_index(header, "Remarks"),
        }
        missing = [k for k in ("name", "code", "factory") if idx[k] is None]
        if missing:
            errors.append(f"Missing required column(s): {', '.join(missing)}")
            return parsed, errors, warnings

        idx["company"] = _header_index(header, "Company")
        idx["department"] = _header_index(header, "Department")
        idx["license_category"] = _header_index(header, "License Category", "Compliance Type")
        default_type = "Medical License" if import_type == "medical" else "Electrician License"
        for n, row in enumerate(data_rows, start=2):
            name = _cell(row, idx["name"])
            code = _cell(row, idx["code"])
            factory_val = _cell(row, idx["factory"])
            if not name and not code:
                continue  # blank line
            factory_id = resolve_factory(factory_val, factory_lookup)
            if factory_id is None:
                factory_id = default_factory_id
            if factory_id is None:
                errors.append(f"Row {n}: unknown factory '{factory_val}'.")
                continue
            if not code:
                errors.append(f"Row {n}: missing employee ID.")
                continue
            company = None
            if idx.get("company") is not None and _text(_cell(row, idx["company"])):
                company = resolve_company(_cell(row, idx["company"]), lookups)
                if company is None:
                    errors.append(f"Row {n}: unknown company '{_text(_cell(row, idx['company']))}'.")
                    continue
                if lookups["factory_company"].get(factory_id) != company.id:
                    errors.append(f"Row {n}: factory does not belong to that company.")
                    continue
            department, dept_err = resolve_department_name(
                factory_id,
                company.id if company else lookups["factory_company"].get(factory_id),
                _cell(row, idx.get("department")),
                lookups,
            )
            if dept_err:
                errors.append(f"Row {n}: {dept_err}")
                continue
            issue_d, _, issue_err = parse_date(_cell(row, idx["issue"]))
            expiry_d, no_expiry, expiry_err = parse_date(_cell(row, idx["expiry"]))
            if issue_err:
                errors.append(f"Row {n}: {issue_err} (issue date).")
                continue
            if expiry_err:
                errors.append(f"Row {n}: {expiry_err} (expiry date).")
                continue
            parsed.append({
                "kind": "license",
                "employee": {
                    "employee_code": str(code).strip(),
                    "name": str(name).strip(),
                    "designation": (str(_cell(row, idx["designation"])).strip() if _cell(row, idx["designation"]) else None),
                    "section": (str(_cell(row, idx["section"])).strip() if _cell(row, idx["section"]) else None),
                    "factory_id": factory_id,
                    "department_id": department.id if department else None,
                },
                "compliance_type": (
                    _text(_cell(row, idx.get("license_category")))
                    if idx.get("license_category") is not None and _text(_cell(row, idx.get("license_category")))
                    else default_type
                ),
                "registration_number": (str(_cell(row, idx["reg"])).strip() if _cell(row, idx["reg"]) else None),
                "category": (str(_cell(row, idx["category"])).strip() if idx["category"] is not None and _cell(row, idx["category"]) else None),
                "issue_date": issue_d,
                "expiry_date": expiry_d,
                "no_expiry": no_expiry,
                "remarks": (str(_cell(row, idx["remarks"])).strip() if idx["remarks"] is not None and _cell(row, idx["remarks"]) else None),
            })

    elif import_type == "health":
        # Health sheets may carry a merged multi-row header (title banner + exam
        # groups). Re-derive a flattened header and the true data rows.
        header, data_rows = _health_header_and_rows(ws)
        idx = {
            # Prefer the globally-unique "F ID (Hide)" over the short "ID" (the
            # short ID collides within a factory for distinct people).
            "code": _header_index(header, "F ID (Hide)", "F ID", "FID",
                                  "Employee ID", "ID No", "ID"),
            "name": _header_index(header, "Name", "Employee Name"),
            "section": _header_index(header, "Section"),
            "designation": _header_index(header, "Designation"),
            "doj": _header_index(header, "DOJ", "Date of Joining"),
            "factory": _header_index(header, "Factory"),
            "pre": _header_index(header, "PRE Y/N", "PRE", "PRE YN"),
            "center": _header_index(header, "Test Center", "Test Centre"),
        }
        # Per-exam report/expiry columns, matched loosely.
        exam_cols = {}
        for key, label in HEALTH_EXAMS:
            report_i = expiry_i = None
            for i, h in enumerate(header):
                nh = _norm(h)
                if key not in nh:
                    continue
                # Check expiry first: "EXPIRE DATE" also contains "date", so it
                # must not be mistaken for the report column.
                if "expiry" in nh or "expire" in nh:
                    expiry_i = i
                elif "report" in nh or "date" in nh:
                    report_i = i
            exam_cols[label] = (report_i, expiry_i)

        missing = [k for k in ("code", "name", "factory") if idx[k] is None]
        if missing:
            errors.append(f"Missing required column(s): {', '.join(missing)}")
            return parsed, errors, warnings

        idx["company"] = _header_index(header, "Company")
        idx["department"] = _header_index(header, "Department")
        idx["remarks"] = _header_index(header, "Remarks")

        for n, row in enumerate(data_rows, start=2):
            code = _cell(row, idx["code"])
            name = _cell(row, idx["name"])
            if not code and not name:
                continue
            factory_id = resolve_factory(_cell(row, idx["factory"]), factory_lookup)
            if factory_id is None:
                factory_id = default_factory_id
            if factory_id is None:
                errors.append(f"Row {n}: unknown factory.")
                continue
            if not code:
                errors.append(f"Row {n}: missing employee ID.")
                continue
            company = None
            if idx.get("company") is not None and _text(_cell(row, idx["company"])):
                company = resolve_company(_cell(row, idx["company"]), lookups)
                if company is None:
                    errors.append(f"Row {n}: unknown company '{_text(_cell(row, idx['company']))}'.")
                    continue
                if lookups["factory_company"].get(factory_id) != company.id:
                    errors.append(f"Row {n}: factory does not belong to that company.")
                    continue
            department, dept_err = resolve_department_name(
                factory_id,
                company.id if company else lookups["factory_company"].get(factory_id),
                _cell(row, idx.get("department")),
                lookups,
            )
            if dept_err:
                errors.append(f"Row {n}: {dept_err}")
                continue
            doj, _, doj_err = parse_date(_cell(row, idx["doj"]))
            if doj_err:
                errors.append(f"Row {n}: {doj_err} (date of joining).")
                continue
            exams = []
            for label, (ri, ei) in exam_cols.items():
                report_d = expiry_d = None
                no_expiry = False
                if ri is not None:
                    report_d, _, rerr = parse_date(_cell(row, ri))
                    if rerr:
                        errors.append(f"Row {n}: {rerr} ({label} report).")
                        continue
                if ei is not None:
                    expiry_d, no_expiry, eerr = parse_date(_cell(row, ei))
                    if eerr:
                        errors.append(f"Row {n}: {eerr} ({label} expiry).")
                        continue
                if report_d or expiry_d:
                    exams.append({"exam_type": label, "report_date": report_d,
                                  "expiry_date": expiry_d, "no_expiry": no_expiry})
            parsed.append({
                "kind": "health",
                "employee": {
                    "employee_code": str(code).strip(),
                    "name": str(name).strip() if name else str(code).strip(),
                    "designation": (str(_cell(row, idx["designation"])).strip() if idx["designation"] is not None and _cell(row, idx["designation"]) else None),
                    "section": (str(_cell(row, idx["section"])).strip() if idx["section"] is not None and _cell(row, idx["section"]) else None),
                    "factory_id": factory_id,
                    "department_id": department.id if department else None,
                    "date_of_joining": doj,
                },
                "pre_yn": (str(_cell(row, idx["pre"])).strip() if idx["pre"] is not None and _cell(row, idx["pre"]) else None),
                "test_center": (str(_cell(row, idx["center"])).strip() if idx["center"] is not None and _cell(row, idx["center"]) else None),
                "remarks": _text(_cell(row, idx.get("remarks"))) or None,
                "exams": exams,
            })

    elif import_type == "employees":
        parsed, errors, warnings = _parse_employees(header, data_rows, factory_lookup, lookups, parsed, errors, warnings, default_factory_id)
    elif import_type == "recipients":
        parsed, errors, warnings = _parse_recipients(header, data_rows, factory_lookup, lookups, parsed, errors, warnings)
    else:
        errors.append(f"Unknown import type '{import_type}'.")

    return parsed, errors, warnings


def _get_or_create_employee(info):
    emp = Employee.query.filter_by(
        factory_id=info["factory_id"], employee_code=info["employee_code"]
    ).first()
    created = False
    if not emp:
        emp = Employee(
            employee_code=info["employee_code"],
            name=info["name"],
            designation=info.get("designation"),
            section=info.get("section"),
            factory_id=info["factory_id"],
            date_of_joining=info.get("date_of_joining"),
            department_id=info.get("department_id"),
            email=info.get("email"),
            receive_alerts=False,
            is_active=True,
        )
        db.session.add(emp)
        db.session.flush()
        created = True
    return emp, created


def apply_import(parsed_rows):
    """Perform the import idempotently. Returns a summary dict."""
    summary = {
        "employees_created": 0,
        "licenses_created": 0,
        "checkups_created": 0,
        "recipients_linked": 0,
        "skipped_duplicates": 0,
    }

    for row in parsed_rows:
        if row["kind"] == "employee":
            _emp, created = _get_or_create_employee(row["employee"])
            if created:
                summary["employees_created"] += 1
            else:
                summary["skipped_duplicates"] += 1
            continue

        if row["kind"] == "recipient":
            if _link_recipient(row):
                summary["recipients_linked"] += 1
            else:
                summary["skipped_duplicates"] += 1
            continue

        emp, created = _get_or_create_employee(row["employee"])
        if created:
            summary["employees_created"] += 1

        if row["kind"] == "license":
            dup = EmployeeCompliance.query.filter_by(
                employee_id=emp.id,
                compliance_type=row["compliance_type"],
                registration_number=row.get("registration_number"),
            ).first()
            if dup:
                summary["skipped_duplicates"] += 1
                continue
            db.session.add(EmployeeCompliance(
                employee_id=emp.id,
                compliance_type=row["compliance_type"],
                registration_number=row.get("registration_number"),
                category=row.get("category"),
                issue_date=row.get("issue_date"),
                expiry_date=row.get("expiry_date"),
                no_expiry=row.get("no_expiry", False),
                status=compute_status(row.get("expiry_date"), row.get("no_expiry", False)),
                remarks=row.get("remarks"),
            ))
            summary["licenses_created"] += 1

        elif row["kind"] == "health":
            for exam in row["exams"]:
                dup = HealthCheckup.query.filter_by(
                    employee_id=emp.id, exam_type=exam["exam_type"],
                    report_date=exam.get("report_date"),
                ).first()
                if dup:
                    summary["skipped_duplicates"] += 1
                    continue
                db.session.add(HealthCheckup(
                    employee_id=emp.id,
                    exam_type=exam["exam_type"],
                    report_date=exam.get("report_date"),
                    expiry_date=exam.get("expiry_date"),
                    no_expiry=exam.get("no_expiry", False),
                    status=compute_status(exam.get("expiry_date"), exam.get("no_expiry", False)),
                    pre_yn=row.get("pre_yn"),
                    test_center=row.get("test_center"),
                    remarks=row.get("remarks"),
                ))
                summary["checkups_created"] += 1

    db.session.commit()
    return summary


def _parse_employees(header, data_rows, factory_lookup, lookups, parsed, errors, warnings, default_factory_id=None):
    idx = {
        "code": _header_index(header, "Employee ID", "ID No", "ID", "ID No."),
        "name": _header_index(header, "Employee Name", "Name"),
        "designation": _header_index(header, "Designation"),
        "section": _header_index(header, "Section"),
        "company": _header_index(header, "Company"),
        "factory": _header_index(header, "Factory"),
        "department": _header_index(header, "Department"),
        "doj": _header_index(header, "Date of Joining", "DOJ"),
        "email": _header_index(header, "Email"),
    }
    missing = [k for k in ("code", "name", "factory") if idx[k] is None]
    if missing:
        errors.append(f"Missing required column(s): {', '.join(missing)}")
        return parsed, errors, warnings

    seen = set()
    for n, row in enumerate(data_rows, start=2):
        code = _text(_cell(row, idx["code"]))
        name = _text(_cell(row, idx["name"]))
        if not code and not name:
            continue
        if isinstance(_cell(row, idx["code"]), float) and float(_cell(row, idx["code"])).is_integer():
            code = str(int(_cell(row, idx["code"])))
        factory_id = resolve_factory(_cell(row, idx["factory"]), factory_lookup)
        if factory_id is None:
            factory_id = default_factory_id
        if not code or not name:
            errors.append(f"Row {n}: employee ID and name are required.")
            continue
        if factory_id is None:
            errors.append(f"Row {n}: unknown factory '{_text(_cell(row, idx['factory']))}'.")
            continue
        company = None
        if idx["company"] is not None and _text(_cell(row, idx["company"])):
            company = resolve_company(_cell(row, idx["company"]), lookups)
            if company is None:
                errors.append(f"Row {n}: unknown company '{_text(_cell(row, idx['company']))}'.")
                continue
            if lookups["factory_company"].get(factory_id) != company.id:
                errors.append(f"Row {n}: factory does not belong to that company.")
                continue
        department, dept_err = resolve_department_name(
            factory_id,
            company.id if company else lookups["factory_company"].get(factory_id),
            _cell(row, idx["department"]),
            lookups,
        )
        if dept_err:
            errors.append(f"Row {n}: {dept_err}")
            continue
        email = _text(_cell(row, idx["email"])) or None
        if email and not valid_email(email):
            errors.append(f"Row {n}: invalid email.")
            continue
        doj, _, doj_err = parse_date(_cell(row, idx["doj"]))
        if doj_err:
            errors.append(f"Row {n}: {doj_err} (date of joining).")
            continue
        key = (factory_id, code.lower())
        if key in seen:
            warnings.append(f"Row {n}: duplicate employee {code} in this file — skipped.")
            continue
        seen.add(key)
        if Employee.query.filter_by(factory_id=factory_id, employee_code=code).first():
            warnings.append(f"Row {n}: existing employee {code} — skipped.")
            continue
        parsed.append({
            "kind": "employee",
            "row_number": n,
            "employee": {
                "employee_code": code,
                "name": name,
                "designation": _text(_cell(row, idx["designation"])) or None,
                "section": _text(_cell(row, idx["section"])) or None,
                "factory_id": factory_id,
                "department_id": department.id if department else None,
                "date_of_joining": doj,
                "email": email,
            },
        })
    return parsed, errors, warnings


def _user_in_company(user, company_id, factory_id=None):
    role_name = user.role.name if user.role else ""
    if role_name in GLOBAL_ALERT_ROLES:
        return True
    # Global-Access users (factory=None) act across every factory within their
    # assigned department, so they count as in-company for recipient imports.
    if not user.factory_id:
        return True
    if user.factory.company_id != company_id:
        return False
    if factory_id and user.factory_id != factory_id and role_name not in GLOBAL_ALERT_ROLES:
        return False
    return True


def _parse_recipients(header, data_rows, factory_lookup, lookups, parsed, errors, warnings):
    idx = {
        "company": _header_index(header, "Company"),
        "factory": _header_index(header, "Factory"),
        "department": _header_index(header, "Department"),
        "email": _header_index(header, "Email"),
        "scope": _header_index(header, "Recipient Scope", "Scope"),
    }
    # "Username" and "User Name" collapse to the same token if punctuation is
    # stripped, so match the visible heading text instead.
    username_idx = display_idx = None
    for i, heading in enumerate(header):
        label = str(heading or "").strip().lower()
        if label in ("username", "user id", "userid"):
            username_idx = i
        elif label in ("user name", "full name"):
            display_idx = i
    idx["username"] = username_idx
    idx["display_name"] = display_idx

    missing = [name for name, key in (("Company", "company"), ("Recipient Scope", "scope")) if idx[key] is None]
    if idx["username"] is None and idx["email"] is None:
        missing.append("Username or Email")
    if missing:
        errors.append(f"Missing required column(s): {', '.join(missing)}")
        return parsed, errors, warnings

    existing_company = {
        (company_id, user_id)
        for company_id, user_id in db.session.query(
            company_alert_recipients.c.company_id, company_alert_recipients.c.user_id
        ).all()
    }
    existing_dept = {
        (department_id, user_id)
        for department_id, user_id in db.session.query(
            department_alert_recipients.c.department_id, department_alert_recipients.c.user_id
        ).all()
    }
    seen = set()

    for n, row in enumerate(data_rows, start=2):
        company_val = _text(_cell(row, idx["company"]))
        scope = _text(_cell(row, idx["scope"])).upper()
        username = _text(_cell(row, idx["username"]))
        email = _text(_cell(row, idx["email"]))
        if not company_val and not scope and not username and not email:
            continue
        if not company_val:
            errors.append(f"Row {n}: company is required.")
            continue
        company = resolve_company(company_val, lookups)
        if company is None:
            errors.append(f"Row {n}: unknown company '{company_val}'.")
            continue
        if scope not in RECIPIENT_SCOPES:
            errors.append(f"Row {n}: recipient scope must be COMPANY or DEPARTMENT.")
            continue
        if email and not valid_email(email):
            errors.append(f"Row {n}: invalid email.")
            continue
        if not username and not email:
            errors.append(f"Row {n}: username or email is required.")
            continue

        factory_id = None
        if idx["factory"] is not None and _text(_cell(row, idx["factory"])):
            factory_id = resolve_factory(_cell(row, idx["factory"]), factory_lookup)
            if factory_id is None:
                errors.append(f"Row {n}: unknown factory '{_text(_cell(row, idx['factory']))}'.")
                continue
            if lookups["factory_company"].get(factory_id) != company.id:
                errors.append(f"Row {n}: factory does not belong to that company.")
                continue

        department = None
        if scope == "DEPARTMENT":
            department, dept_err = resolve_department_name(
                factory_id, company.id, _cell(row, idx["department"]), lookups,
            )
            if department is None:
                errors.append(f"Row {n}: {dept_err or 'department is required for DEPARTMENT scope.'}")
                continue
        elif idx["department"] is not None and _text(_cell(row, idx["department"])):
            # Company scope does not use a department. Keep the row, but say so.
            pass

        user, user_err = _match_user(username, email)
        if user_err:
            errors.append(f"Row {n}: {user_err}")
            continue
        if user is None:
            errors.append(f"Row {n}: user was not found. Users are not created by this import.")
            continue
        target_factory = factory_id
        if not _user_in_company(user, company.id, target_factory if scope == "DEPARTMENT" else None):
            errors.append(f"Row {n}: user is outside this company or factory scope.")
            continue

        notes = []
        if scope == "COMPANY" and idx["department"] is not None and _text(_cell(row, idx["department"])):
            notes.append("Department was ignored because scope is COMPANY.")
        if email and user.email and email.lower() != user.email.strip().lower():
            notes.append("Spreadsheet email differs from the user account. The account email will be used.")
        if not user.is_active:
            notes.append("User is inactive. The link will be saved and alerts resume when the user is activated.")

        if scope == "COMPANY":
            marker = ("COMPANY", company.id, user.id)
            if marker in seen or (company.id, user.id) in existing_company:
                warnings.append(f"Row {n}: existing relationship — skipped.")
                continue
            seen.add(marker)
        else:
            marker = ("DEPARTMENT", department.id, user.id)
            if marker in seen or (department.id, user.id) in existing_dept:
                warnings.append(f"Row {n}: existing relationship — skipped.")
                continue
            seen.add(marker)

        parsed.append({
            "kind": "recipient",
            "row_number": n,
            "scope": scope,
            "company_id": company.id,
            "company_name": company.name,
            "department_id": department.id if department else None,
            "department_name": department.name if department else "",
            "user_id": user.id,
            "user_label": user.full_name or user.username,
            "email": user.email or "",
            "notes": notes,
        })
    return parsed, errors, warnings


def _match_user(username, email):
    by_username = None
    by_email = None
    if username:
        by_username = User.query.filter(db.func.lower(User.username) == username.lower()).first()
    if email:
        by_email = User.query.filter(db.func.lower(User.email) == email.strip().lower()).first()
    if by_username and by_email and by_username.id != by_email.id:
        return None, "username and email belong to different users."
    return (by_username or by_email), None


def _link_recipient(row):
    if row["scope"] == "COMPANY":
        company = db.session.get(Company, row["company_id"])
        user = db.session.get(User, row["user_id"])
        if not company or not user:
            return False
        if user in company.alert_recipients:
            return False
        company.alert_recipients.append(user)
        return True
    department = db.session.get(Department, row["department_id"])
    user = db.session.get(User, row["user_id"])
    if not department or not user:
        return False
    if user in department.alert_recipients:
        return False
    department.alert_recipients.append(user)
    return True


def _duplicate_message(row):
    if row["kind"] == "employee":
        info = row["employee"]
        if Employee.query.filter_by(factory_id=info["factory_id"], employee_code=info["employee_code"]).first():
            return f"Existing employee {info['employee_code']} — skipped."
    elif row["kind"] == "license":
        info = row["employee"]
        emp = Employee.query.filter_by(factory_id=info["factory_id"], employee_code=info["employee_code"]).first()
        if emp and EmployeeCompliance.query.filter_by(
            employee_id=emp.id,
            compliance_type=row["compliance_type"],
            registration_number=row.get("registration_number"),
        ).first():
            return f"Existing license for {info['employee_code']} ({row['compliance_type']}) — skipped."
    elif row["kind"] == "health":
        info = row["employee"]
        emp = Employee.query.filter_by(factory_id=info["factory_id"], employee_code=info["employee_code"]).first()
        if not emp or not row.get("exams"):
            return None
        all_exist = True
        for exam in row["exams"]:
            dup = HealthCheckup.query.filter_by(
                employee_id=emp.id, exam_type=exam["exam_type"], report_date=exam.get("report_date"),
            ).first()
            if not dup:
                all_exist = False
        if all_exist:
            return f"Existing health checkup rows for {info['employee_code']} — skipped."
    elif row["kind"] == "recipient":
        if row["scope"] == "COMPANY":
            company = db.session.get(Company, row["company_id"])
            user = db.session.get(User, row["user_id"])
            if company and user and user in list(company.alert_recipients):
                return "Existing relationship — skipped."
        else:
            department = db.session.get(Department, row["department_id"])
            user = db.session.get(User, row["user_id"])
            if department and user and user in list(department.alert_recipients):
                return "Existing relationship — skipped."
    return None


def annotate_existing(parsed):
    """Move rows that already exist into warnings. Used by the preview/confirm UI."""
    importable, warnings = [], []
    for row in parsed:
        message = _duplicate_message(row)
        if message:
            warnings.append(message)
        else:
            importable.append(row)
    return importable, warnings


def preview_counts(parsed, errors, warnings):
    return {
        "total": len(parsed) + len(errors) + len(warnings),
        "valid": len(parsed),
        "warning": len(warnings),
        "failed": len(errors),
    }


_WORKBOOK_SHEETS = (
    ("employees", ("employees", "employee")),
    ("licenses", ("employeelicenses", "licenses", "license")),
    ("medical", ("medicallicenses", "medical")),
    ("health", ("healthcheckups", "health", "checkups")),
    ("recipients", ("alertrecipients", "recipients")),
)


def _find_sheet(wb, aliases):
    wanted = {_norm(alias) for alias in aliases}
    for name in wb.sheetnames:
        if _norm(name) in wanted:
            return wb[name]
    return None


def parse_full_workbook(wb, factory_lookup, default_factory_id=None):
    """Import sheets in dependency order. Unknown extra sheets are ignored."""
    parsed, errors, warnings = [], [], []
    found = False
    for import_type, aliases in _WORKBOOK_SHEETS:
        ws = _find_sheet(wb, aliases)
        if ws is None:
            continue
        found = True
        sheet_parsed, sheet_errors, sheet_warnings = parse_workbook(ws, import_type, factory_lookup, default_factory_id)
        prefix = ws.title
        parsed.extend(sheet_parsed)
        errors.extend(f"{prefix}: {item}" for item in sheet_errors)
        warnings.extend(f"{prefix}: {item}" for item in sheet_warnings)
    if not found:
        errors.append(
            "No recognized sheet. Expected Employees, Employee Licenses, Health Checkups, or Alert Recipients."
        )
    return parsed, errors, warnings
