"""Compliance Audit Tracker Excel import.

The source workbook ("Audit Tracker") has a two-row merged header:

    Row 3:  Date | (merged) | Audit Name | Audited By | Auditor Name | Buyer |
            ID | Audit Type | Announcement Type | Monitoring Type | Factory |
            Rating | Validity | Cost | ...
    Row 4:  Start | End | ...
    Row 5+: data

Columns A..N are the real fields; O..R are stray scratch cells in the source
file and are ignored. A row is treated as a real audit when it has a start
date. Blank-separator rows and the stray-column rows are skipped.

This module parses the sheet into plain dicts, annotates each against the DB
by a normalized import_key (so a re-import updates instead of duplicating),
and upserts. Nothing here commits until apply_import.
"""
from datetime import datetime, date

from openpyxl import load_workbook

from app import db
from app.models import Audit, Factory, AUDIT_FACTORY_TOKENS, AUDIT_NO_VALIDITY
from app.utils import compute_status

# Dates Excel writes for an empty/zero cell. Treated as "no date".
_EPOCH_MARKERS = {
    date(1899, 12, 30), date(1900, 1, 1),
    date(1900, 12, 29), date(1900, 12, 30), date(1900, 12, 31),
}

HEADER_DATA_START = 5  # first data row (1-indexed)

# 0-indexed source columns -> field name. O..R (14..17) are intentionally absent.
_COLS = {
    0: "date_start", 1: "date_end", 2: "audit_name", 3: "audited_by",
    4: "auditor_name", 5: "buyer", 6: "reference_id", 7: "audit_type",
    8: "announcement_type", 9: "monitoring_type", 10: "factory_raw",
    11: "rating", 12: "validity", 13: "cost",
}


def _clean(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _as_date(v):
    """A real date, or None for blanks, '-', text, and epoch placeholders."""
    if v is None:
        return None
    if isinstance(v, datetime):
        d = v.date()
    elif isinstance(v, date):
        d = v
    else:
        return None
    if d in _EPOCH_MARKERS:
        return None
    return d


def normalize_import_key(date_start, audit_name, factory_raw):
    ds = date_start.isoformat() if date_start else ""
    an = (audit_name or "").strip().lower()
    fr = (factory_raw or "").strip().lower()
    return f"{ds}|{an}|{fr}"


def parse_workbook(path):
    """Return (rows, errors). Each row is a dict of the parsed fields plus a
    computed import_key. errors is a list of human-readable strings."""
    errors = []
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:
        return [], [f"Could not open workbook: {type(exc).__name__}"]

    ws = wb[wb.sheetnames[0]]
    rows = []
    for ri, raw in enumerate(ws.iter_rows(min_row=HEADER_DATA_START, values_only=True), HEADER_DATA_START):
        rec = {field: raw[idx] if idx < len(raw) else None for idx, field in _COLS.items()}
        date_start = _as_date(rec["date_start"])
        audit_name = _clean(rec["audit_name"])
        # A genuine audit row has a start date. Blank separators and the source
        # file's stray trailing-column rows have neither a date nor a name.
        if date_start is None and not audit_name:
            continue
        if date_start is None:
            errors.append(f"Row {ri}: skipped — no audit start date.")
            continue
        if not audit_name:
            errors.append(f"Row {ri}: skipped — no audit name.")
            continue

        date_end = _as_date(rec["date_end"]) or date_start
        validity_date = _as_date(rec["validity"])
        factory_raw = _clean(rec["factory_raw"])
        row = {
            "source_row": ri,
            "date_start": date_start,
            "date_end": date_end,
            "audit_name": audit_name,
            "audited_by": _clean(rec["audited_by"]),
            "auditor_name": _clean(rec["auditor_name"]),
            "buyer": _clean(rec["buyer"]),
            "reference_id": _clean(rec["reference_id"]),
            "audit_type": _clean(rec["audit_type"]),
            "announcement_type": _clean(rec["announcement_type"]),
            "monitoring_type": _clean(rec["monitoring_type"]),
            "factory_raw": factory_raw,
            "rating": _clean(rec["rating"]),
            "validity_date": validity_date,
            "cost": _clean(rec["cost"]),
            "import_key": normalize_import_key(date_start, audit_name, factory_raw),
        }
        rows.append(row)

    wb.close()
    return rows, errors


def _factory_token_cache():
    """Map each known abbreviation token -> factory_id, resolved once per run."""
    cache = {}
    for token, name_substr in AUDIT_FACTORY_TOKENS:
        f = Factory.query.filter(Factory.name.ilike(f"%{name_substr}%")).first()
        if f is not None:
            cache[token] = f.id
    return cache


def resolve_factory_ids(factory_raw, token_cache=None):
    """Tokenize the verbatim Factory cell into a set of factory_ids.

    Handles composite values like "AGL (ASL)", "AGL & ASL", "AGL Ext." and
    "ABM AJL AGL PWPL ASL". "Both" expands to AGL + ASL. Unknown text yields an
    empty set (the audit still stores factory_raw verbatim for display)."""
    if not factory_raw:
        return set()
    if token_cache is None:
        token_cache = _factory_token_cache()
    upper = factory_raw.upper()
    ids = set()
    if "BOTH" in upper:
        for tok in ("AGL", "ASL"):
            if tok in token_cache:
                ids.add(token_cache[tok])
    for token, fid in token_cache.items():
        if token in upper:
            ids.add(fid)
    return ids


def annotate_existing(rows):
    """Mark each row 'new' or 'update' by matching import_key in the DB."""
    keys = [r["import_key"] for r in rows if r["import_key"]]
    existing = {}
    if keys:
        for a in Audit.query.filter(Audit.import_key.in_(keys)).all():
            existing[a.import_key] = a.id
    for r in rows:
        r["existing_id"] = existing.get(r["import_key"])
        r["action"] = "update" if r["existing_id"] else "new"
    return rows


def preview_counts(rows):
    new = sum(1 for r in rows if r["action"] == "new")
    updated = sum(1 for r in rows if r["action"] == "update")
    with_validity = sum(1 for r in rows if r["validity_date"])
    return {
        "total": len(rows),
        "new": new,
        "updated": updated,
        "with_validity": with_validity,
        "no_validity": len(rows) - with_validity,
    }


def _status_for(validity_date):
    return compute_status(validity_date) if validity_date else AUDIT_NO_VALIDITY


def apply_import(rows, user_id):
    """Upsert audits. Updates the matching row by import_key; never deletes.
    Returns a summary dict."""
    token_cache = _factory_token_cache()
    created = updated = 0
    for r in rows:
        audit = None
        if r.get("existing_id"):
            audit = db.session.get(Audit, r["existing_id"])
        if audit is None:
            audit = Audit(created_by=user_id)
            db.session.add(audit)
            created += 1
        else:
            updated += 1
        audit.date_start = r["date_start"]
        audit.date_end = r["date_end"]
        audit.audit_name = r["audit_name"]
        audit.audited_by = r["audited_by"]
        audit.auditor_name = r["auditor_name"]
        audit.buyer = r["buyer"]
        audit.reference_id = r["reference_id"]
        audit.audit_type = r["audit_type"]
        audit.announcement_type = r["announcement_type"]
        audit.monitoring_type = r["monitoring_type"]
        audit.factory_raw = r["factory_raw"]
        audit.rating = r["rating"]
        audit.validity_date = r["validity_date"]
        audit.cost = r["cost"]
        audit.status = _status_for(r["validity_date"])
        audit.import_key = r["import_key"]
        audit.is_active = True
        db.session.flush()
        fids = resolve_factory_ids(r["factory_raw"], token_cache)
        audit.factories = Factory.query.filter(Factory.id.in_(fids)).all() if fids else []
    db.session.commit()
    return {"created": created, "updated": updated, "total": len(rows)}
