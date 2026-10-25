"""
Excel import for the Vehicle fleet (Super Admin, Administration, Transport Manager).

Design rules carried from app/import_utils.py:
    - Preview before applying. Nothing is committed on the preview page.
    - Idempotent upserts keyed on `reg_no_normalized`. Running the same file
      twice is safe and produces zero new rows.
    - Blank date in the sheet does NOT clear an existing date; a legitimately
      optional document (Fitness, Route Permit) stays absent, not invalid.
    - Blue Book cell can hold either a status note ("Smart Card received",
      "Old Format Bluebook", ...) or an expiry date. Split into note +
      optional expiry.
    - Owner Name: match to existing companies when possible; keep raw name
      in owner_name_raw for personal owners (company_id stays NULL).
    - Office Name is free-text, stored as office_label.
"""
import re
from datetime import datetime, date, timedelta

from app import db
from app.models import (
    Company, Factory, Vehicle, VehicleDocument, VehicleImportRun,
    VEHICLE_DOC_TYPES,
)
from app.utils import compute_status

# Column headers accepted in the source workbook. Case-insensitive, spaces
# collapsed. The map value is the canonical key the parser produces.
_HEADER_ALIASES = {
    "sl": "sl",
    "last4digit": "last4",
    "useroffice": "office",
    "useroffi": "office",
    "useroffice name": "office",
    "useroffeicename": "office",
    "office": "office",
    "officename": "office",
    "vehicletype": "vehicle_type",
    "registrationno": "registration_no",
    "regno": "registration_no",
    "registration": "registration_no",
    "ownername": "owner",
    "modifiednewbluebook": "blue_book",
    "modifiedbluebook": "blue_book",
    "newbluebook": "blue_book",
    "bluebook": "blue_book",
    "taxtokenexpirydate": "tax_token_expiry",
    "taxtokenexpiry": "tax_token_expiry",
    "fitnessexpirydate": "fitness_expiry",
    "fitnessexpiry": "fitness_expiry",
    "routepermitexpirydate": "route_permit_expiry",
    "routepermitexpiry": "route_permit_expiry",
}

REQUIRED_CANONICAL = {"registration_no", "owner", "tax_token_expiry"}


def _norm_header(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _is_blank(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _parse_date_cell(value):
    """(date | None, note | None, error | None).

    A datetime cell is a real expiry date. A non-empty string that parses as
    a date is accepted; a string that doesn't parse is returned as `note`
    (callers decide whether that's valid for this column). Blank is blank.
    """
    if _is_blank(value):
        return None, None, None
    if isinstance(value, datetime):
        return value.date(), None, None
    if isinstance(value, date):
        return value, None, None
    if isinstance(value, (int, float)):
        try:
            base = datetime(1899, 12, 30)  # Excel 1900 leap-year quirk
            return (base + timedelta(days=float(value))).date(), None, None
        except Exception:
            return None, None, f"invalid date serial '{value}'"
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y",
                "%d %b %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date(), None, None
        except ValueError:
            continue
    # Not a date — treat as a note.
    return None, text, None


def _detect_header_row(ws):
    """Return (header_row_index, canonical_headers_by_col). Scans the first
    10 rows for a row with >=5 recognizable headers.
    """
    for r in range(1, min(ws.max_row, 10) + 1):
        canonical = {}
        hits = 0
        for c in range(1, ws.max_column + 1):
            val = ws.cell(row=r, column=c).value
            key = _HEADER_ALIASES.get(_norm_header(val))
            if key:
                canonical[c] = key
                hits += 1
        if hits >= 5:
            return r, canonical
    raise ValueError(
        "Could not find a header row with Registration No, Owner Name, "
        "Tax Token Expiry, Fitness Expiry, and Route Permit Expiry columns."
    )


def _norm_owner(value):
    """Normalize an owner name for lookup. Lowercase, strip punctuation,
    expand '&' to 'and' so 'Thread & Thread' matches 'Thread And Thread'."""
    text = (value or "").lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", text)


def _build_owner_lookup():
    """Map normalized owner tokens to (factory_id, company_id).

    The Excel 'Owner Name' column holds factory-level names (e.g.
    'Ananta Garments Ltd.', 'ABM Fashions Ltd.'); we try Factory first,
    falling back to Company. Abbreviations built from capital letters
    (AGL, ASL, AJL, PWPL, ABM) also index.
    """
    lookup = {}

    def _add(token, factory_id, company_id):
        key = _norm_owner(token)
        if key and key not in lookup:
            lookup[key] = (factory_id, company_id)

    for f in Factory.query.all():
        _add(f.name, f.id, f.company_id)
        abbr = "".join(ch for ch in (f.name or "") if ch.isupper())
        if len(abbr) >= 2:
            _add(abbr, f.id, f.company_id)
    for c in Company.query.all():
        _add(c.name, None, c.id)
        abbr = "".join(ch for ch in (c.name or "") if ch.isupper())
        if len(abbr) >= 2:
            _add(abbr, None, c.id)
    return lookup


def _resolve_owner(owner_raw, lookup):
    """Return (factory_id, company_id). Both NULL => personal / unmapped."""
    key = _norm_owner(owner_raw)
    if not key:
        return None, None
    if key in lookup:
        return lookup[key]
    # Partial contains match. Prefer the longest matching token so that
    # 'AnantaGarmentsLimited' matches 'anantagarmentsltd' not 'ananta'.
    best = None
    for token, pair in lookup.items():
        if not token:
            continue
        if token in key or key in token:
            if best is None or len(token) > len(best[0]):
                best = (token, pair)
    return best[1] if best else (None, None)


def parse_workbook(path_or_wb):
    """Parse the vehicle workbook into a list of row dicts + top-level errors.

    Each dict has:
        row_no          source sheet row (1-indexed)
        registration_no raw string
        reg_norm        normalized key
        vehicle_type    string
        owner_raw       string
        company_id      int | None
        company_name    display string for the UI
        office_label    string
        blue_book_note  string | None
        blue_book_expiry date | None
        tax_token_expiry date | None
        fitness_expiry   date | None
        route_permit_expiry date | None
        row_errors      list[str]
    """
    from openpyxl import load_workbook
    if isinstance(path_or_wb, str):
        wb = load_workbook(path_or_wb, data_only=True, read_only=False)
        close_after = True
    else:
        wb = path_or_wb
        close_after = False

    errors = []
    rows = []
    try:
        ws = wb.active
        header_row, canonical = _detect_header_row(ws)
        present = set(canonical.values())
        missing = REQUIRED_CANONICAL - present
        if missing:
            errors.append(
                f"Missing required columns: {', '.join(sorted(missing))}"
            )
            return [], errors

        owner_lookup = _build_owner_lookup()
        seen_in_file = {}

        for r in range(header_row + 1, ws.max_row + 1):
            row_errors = []
            raw = {canonical[c]: ws.cell(row=r, column=c).value
                   for c in canonical}

            reg_raw = raw.get("registration_no")
            if _is_blank(reg_raw):
                # Skip empty trailing rows silently.
                if all(_is_blank(v) for v in raw.values()):
                    continue
                row_errors.append("Registration No is blank")

            reg_norm = Vehicle.normalize_reg_no(reg_raw)
            if reg_norm:
                if reg_norm in seen_in_file:
                    row_errors.append(
                        f"Duplicate Registration No {reg_raw!r} "
                        f"(also on row {seen_in_file[reg_norm]})"
                    )
                else:
                    seen_in_file[reg_norm] = r

            owner_raw = str(raw.get("owner") or "").strip()
            if not owner_raw:
                row_errors.append("Owner Name is blank")
            factory_id, company_id = _resolve_owner(owner_raw, owner_lookup)

            tt, _, err = _parse_date_cell(raw.get("tax_token_expiry"))
            if err:
                row_errors.append(f"Tax Token Expiry: {err}")
            if tt is None and not row_errors:
                row_errors.append(
                    "Tax Token Expiry is blank (required for every vehicle)"
                )

            fit, _, err = _parse_date_cell(raw.get("fitness_expiry"))
            if err:
                row_errors.append(f"Fitness Expiry: {err}")

            rp, _, err = _parse_date_cell(raw.get("route_permit_expiry"))
            if err:
                row_errors.append(f"Route Permit Expiry: {err}")

            bb_date, bb_note, err = _parse_date_cell(raw.get("blue_book"))
            if err:
                row_errors.append(f"Blue Book: {err}")

            rows.append({
                "row_no": r,
                "registration_no": str(reg_raw or "").strip(),
                "reg_norm": reg_norm,
                "vehicle_type": str(raw.get("vehicle_type") or "").strip(),
                "owner_raw": owner_raw,
                "factory_id": factory_id,
                "company_id": company_id,
                "owner_display": _owner_display_name(factory_id, company_id, owner_raw),
                "office_label": str(raw.get("office") or "").strip(),
                "blue_book_note": bb_note,
                "blue_book_expiry": bb_date,
                "tax_token_expiry": tt,
                "fitness_expiry": fit,
                "route_permit_expiry": rp,
                "row_errors": row_errors,
            })
    finally:
        if close_after:
            wb.close()

    return rows, errors


def _owner_display_name(factory_id, company_id, owner_raw):
    if factory_id:
        f = db.session.get(Factory, factory_id)
        if f:
            return f.name
    if company_id:
        c = db.session.get(Company, company_id)
        if c:
            return c.name
    return f"{owner_raw} (personal)" if owner_raw else ""


def annotate_existing(rows):
    """Attach a 'diff' to each row describing new vs. update, which fields
    change, and whether a stored (non-blank) date would be left alone.

    Returns the same list with keys added:
        existing_vehicle_id  int | None
        action               'new' | 'update' | 'no_change' | 'skip'
        field_changes        list[(field, old, new)]
    """
    norms = [r["reg_norm"] for r in rows if r["reg_norm"] and not r["row_errors"]]
    existing = {}
    if norms:
        for v in Vehicle.query.filter(Vehicle.reg_no_normalized.in_(norms)).all():
            existing[v.reg_no_normalized] = v

    for r in rows:
        if r["row_errors"]:
            r["existing_vehicle_id"] = None
            r["action"] = "skip"
            r["field_changes"] = []
            continue
        v = existing.get(r["reg_norm"])
        if not v:
            r["existing_vehicle_id"] = None
            r["action"] = "new"
            r["field_changes"] = []
            continue

        r["existing_vehicle_id"] = v.id
        changes = []
        if (v.vehicle_type or "") != r["vehicle_type"]:
            changes.append(("vehicle_type", v.vehicle_type or "", r["vehicle_type"]))
        if (v.factory_id or None) != (r["factory_id"] or None):
            changes.append(("factory_id", v.factory_id, r["factory_id"]))
        if (v.company_id or None) != (r["company_id"] or None):
            changes.append(("company_id", v.company_id, r["company_id"]))
        if (v.office_label or "") != r["office_label"]:
            changes.append(("office_label", v.office_label or "", r["office_label"]))
        if (v.owner_name_raw or "") != r["owner_raw"]:
            changes.append(("owner_name_raw", v.owner_name_raw or "", r["owner_raw"]))

        # Document-level diffs. Blank in the sheet never overwrites a stored date.
        docs_by_type = {d.doc_type: d for d in v.documents if d.is_active}
        for excel_key, doc_type in (
            ("tax_token_expiry", "tax_token"),
            ("fitness_expiry", "fitness"),
            ("route_permit_expiry", "route_permit"),
        ):
            new_date = r[excel_key]
            doc = docs_by_type.get(doc_type)
            if new_date is None:
                continue  # don't clear what's there
            if doc is None:
                changes.append((f"{doc_type}.expiry_date", None, new_date))
            elif doc.expiry_date != new_date:
                changes.append((f"{doc_type}.expiry_date", doc.expiry_date, new_date))

        # Blue Book: date wins when present, else note
        bb_doc = docs_by_type.get("blue_book")
        if r["blue_book_expiry"]:
            if bb_doc is None or bb_doc.expiry_date != r["blue_book_expiry"]:
                changes.append(
                    ("blue_book.expiry_date",
                     bb_doc.expiry_date if bb_doc else None,
                     r["blue_book_expiry"])
                )
        elif r["blue_book_note"]:
            if bb_doc is None or (bb_doc.note or "") != r["blue_book_note"]:
                changes.append(
                    ("blue_book.note",
                     bb_doc.note if bb_doc else None,
                     r["blue_book_note"])
                )

        r["field_changes"] = changes
        r["action"] = "update" if changes else "no_change"

    return rows


def preview_counts(rows):
    """Totals + buckets for the preview page.

    Key is `updated` rather than `update` because Jinja2 resolves `counts.update`
    to the dict's `.update` method, not the dict key.
    """
    return {
        "total": len(rows),
        "new": sum(1 for r in rows if r.get("action") == "new"),
        "updated": sum(1 for r in rows if r.get("action") == "update"),
        "no_change": sum(1 for r in rows if r.get("action") == "no_change"),
        "skip": sum(1 for r in rows if r.get("action") == "skip"),
    }


def apply_import(rows, user_id=None):
    """Upsert vehicles and their documents. Call ONLY on confirm.

    Returns a summary dict:
        new_vehicles, updated_vehicles, new_docs, updated_docs, skipped
    """
    summary = {
        "new_vehicles": 0,
        "updated_vehicles": 0,
        "new_docs": 0,
        "updated_docs": 0,
        "skipped": 0,
    }

    for r in rows:
        if r.get("row_errors") or r.get("action") == "skip":
            summary["skipped"] += 1
            continue

        vehicle = None
        if r.get("existing_vehicle_id"):
            vehicle = db.session.get(Vehicle, r["existing_vehicle_id"])
        if vehicle is None:
            vehicle = Vehicle()
            vehicle.set_registration(r["registration_no"])
            vehicle.vehicle_type = r["vehicle_type"] or None
            vehicle.factory_id = r["factory_id"]
            vehicle.company_id = r["company_id"]
            vehicle.office_label = r["office_label"] or None
            vehicle.owner_name_raw = r["owner_raw"] or None
            vehicle.created_by = user_id
            db.session.add(vehicle)
            db.session.flush()
            summary["new_vehicles"] += 1
        else:
            touched = False
            if (vehicle.vehicle_type or "") != r["vehicle_type"]:
                vehicle.vehicle_type = r["vehicle_type"] or None
                touched = True
            if (vehicle.factory_id or None) != (r["factory_id"] or None):
                vehicle.factory_id = r["factory_id"]
                touched = True
            if (vehicle.company_id or None) != (r["company_id"] or None):
                vehicle.company_id = r["company_id"]
                touched = True
            if (vehicle.office_label or "") != r["office_label"]:
                vehicle.office_label = r["office_label"] or None
                touched = True
            if (vehicle.owner_name_raw or "") != r["owner_raw"]:
                vehicle.owner_name_raw = r["owner_raw"] or None
                touched = True
            if touched:
                summary["updated_vehicles"] += 1

        # Docs: upsert per doc_type. Blank sheet value is never a clear.
        pairs = [
            ("tax_token", r["tax_token_expiry"], None),
            ("fitness", r["fitness_expiry"], None),
            ("route_permit", r["route_permit_expiry"], None),
            ("blue_book", r["blue_book_expiry"], r["blue_book_note"]),
        ]
        existing_docs = {
            d.doc_type: d for d in vehicle.documents if d.is_active
        }

        for doc_type, new_expiry, new_note in pairs:
            if new_expiry is None and not new_note:
                continue
            doc = existing_docs.get(doc_type)
            if doc is None:
                doc = VehicleDocument(
                    vehicle_id=vehicle.id,
                    doc_type=doc_type,
                    issue_date=None,
                    expiry_date=new_expiry,
                    no_expiry=(new_expiry is None),
                    note=new_note,
                    status=compute_status(new_expiry, no_expiry=(new_expiry is None)),
                    created_by=user_id,
                )
                db.session.add(doc)
                summary["new_docs"] += 1
            else:
                changed = False
                if new_expiry is not None and doc.expiry_date != new_expiry:
                    doc.expiry_date = new_expiry
                    doc.no_expiry = False
                    doc.status = compute_status(new_expiry, no_expiry=False)
                    changed = True
                if new_note and (doc.note or "") != new_note:
                    doc.note = new_note
                    changed = True
                    # When the sheet gives only a note (no date), mark the doc
                    # as no_expiry so status computes to Valid.
                    if new_expiry is None and doc.expiry_date is None:
                        doc.no_expiry = True
                        doc.status = compute_status(None, no_expiry=True)
                if changed:
                    summary["updated_docs"] += 1

    db.session.commit()
    return summary


def record_import_run(filename, user_id, counts, summary, status, error=None):
    """Persist one VehicleImportRun audit row."""
    run = VehicleImportRun(
        filename=(filename or "")[:300],
        imported_by=user_id,
        started_at=datetime.utcnow(),
        finished_at=datetime.utcnow(),
        status=status,
        total_rows=counts.get("total", 0),
        new_vehicles=summary.get("new_vehicles", 0),
        updated_vehicles=summary.get("updated_vehicles", 0),
        new_docs=summary.get("new_docs", 0),
        updated_docs=summary.get("updated_docs", 0),
        skipped_rows=summary.get("skipped", 0),
        error_detail=((error or "")[:400] or None),
    )
    db.session.add(run)
    db.session.commit()
    return run
