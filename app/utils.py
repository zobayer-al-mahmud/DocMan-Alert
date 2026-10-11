import html
import re
from datetime import datetime, timedelta
from functools import wraps

from flask import abort, current_app, has_request_context, request
from flask_login import current_user
from flask_mail import Message

from app import db, mail
from app.models import (
    AlertDispatch, Document, Employee, EmployeeCompliance, HealthCheckup, SystemLog,
    User, company_alert_recipients, department_alert_recipients,
    AlertEmail, AlertEmailItem,
)


# ---------------------------------------------------------------------------
# Time helpers - Bangladesh Standard Time (UTC+6) used consistently for all
# status/date comparisons and scheduled jobs.
# ---------------------------------------------------------------------------
BST_OFFSET = timedelta(hours=6)


def bst_now():
    return datetime.utcnow() + BST_OFFSET


def bst_today():
    return bst_now().date()


def to_bst(utc_dt):
    """Format a stored UTC datetime as a human BST string."""
    if utc_dt is None:
        return None
    return (utc_dt + BST_OFFSET).strftime("%d-%b-%Y %I:%M %p")


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Email address validation (lightweight; avoids an extra dependency).
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(value):
    return bool(value) and bool(_EMAIL_RE.match(value.strip()))


def document_recipient_emails(document):
    """Valid email addresses of a document's selected alert recipients
    (active users only). Returns a de-duplicated list."""
    emails = []
    for user in document.alert_recipients:
        if user.is_active and valid_email(user.email):
            e = user.email.strip()
            if e not in emails:
                emails.append(e)
    return emails


def roles_required(*role_names):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role.name not in role_names:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# ---------------------------------------------------------------------------
# System logging (technical/operational events only - NOT an audit trail).
# Never pass passwords, secrets, document contents, or before/after business
# values into here.
# ---------------------------------------------------------------------------

VALID_SYSTEM_EVENTS = {
    "APP_START", "APP_STOP", "APP_FAILURE",
    "LOGIN_SUCCESS", "LOGIN_FAILURE", "ACCOUNT_SECURITY_EVENT",
    "JOB_START", "JOB_SUCCESS", "JOB_FAILURE",
    "BACKUP_SUCCESS", "BACKUP_FAILURE",
    "RESTORE_SUCCESS", "RESTORE_FAILURE",
    "EMAIL_SUCCESS", "EMAIL_FAILURE",
    "XLSX_IMPORT_STARTED", "XLSX_IMPORT_COMPLETED", "XLSX_IMPORT_FAILED",
    "OCR_STARTED", "OCR_COMPLETED", "OCR_FAILED",
    "ALERT_SENT", "ALERT_FAILED",
    "EMERGENCY_ALERT_SENT", "EMERGENCY_ALERT_FAILED",
    "ALERT_RECIPIENT_SKIPPED",
    "UNHANDLED_EXCEPTION",
}


def log_event(event_type, message="", level="INFO", username_or_process=None,
              ip_address=None, commit=True):
    """Write one SystemLog row. Safe to call inside or outside a request/app
    context; failures to log are swallowed so logging never breaks a request."""
    try:
        if username_or_process is None:
            if has_request_context() and getattr(current_user, "is_authenticated", False):
                username_or_process = current_user.username
            else:
                username_or_process = "system"
        if ip_address is None and has_request_context():
            ip_address = request.remote_addr

        entry = SystemLog(
            timestamp=datetime.utcnow(),
            level=level,
            event_type=event_type,
            username_or_process=username_or_process,
            ip_address=ip_address,
            message=(message or "")[:2000],
        )
        db.session.add(entry)
        if commit:
            db.session.commit()
    except Exception:
        # Logging must never raise into the caller.
        try:
            db.session.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Centralized expiry/status engine
# Shared by factory documents, employee licenses, and health checkups.
#
#   > 90 days   -> Valid
#   61-90 days  -> Expiring in 90 Days
#   31-60 days  -> Expiring in 60 Days
#   8-30 days   -> Expiring in 30 Days
#   1-7 days    -> Expiring in 7 Days
#   0 or less   -> Expired
#   no expiry   -> Valid
# ---------------------------------------------------------------------------

STATUS_VALID = "Valid"
STATUS_90 = "Expiring in 90 Days"
STATUS_60 = "Expiring in 60 Days"
STATUS_30 = "Expiring in 30 Days"
STATUS_7 = "Expiring in 7 Days"
STATUS_EXPIRED = "Expired"

EXPIRING_STATUSES = [STATUS_90, STATUS_60, STATUS_30, STATUS_7]
ALL_STATUSES = [STATUS_VALID] + EXPIRING_STATUSES + [STATUS_EXPIRED]
ATTENTION_STATUSES = EXPIRING_STATUSES + [STATUS_EXPIRED]
# Saturday summary. 7-day and expired records use the emergency alert instead.
NORMAL_ALERT_STATUSES = [STATUS_90, STATUS_60, STATUS_30]
EMERGENCY_STATUSES = [STATUS_7, STATUS_EXPIRED]
CRITICAL_STATUSES = EMERGENCY_STATUSES

GLOBAL_ALERT_ROLES = ("Super Admin", "Compliance Viewer", "Administration")
SOURCE_COMPANY = "Company"
SOURCE_DEPARTMENT = "Department"
SOURCE_GROUP = "Department group"
SOURCE_DOCUMENT = "Document-specific"
SOURCE_RECORD = "Record-specific"
SOURCE_UPLOADER = "Uploader"
SOURCE_EMPLOYEE = "Employee"
SOURCE_PRODUCTION = "Production"
SOURCE_ORDER = [
    SOURCE_PRODUCTION, SOURCE_COMPANY, SOURCE_DEPARTMENT, SOURCE_GROUP, SOURCE_DOCUMENT,
    SOURCE_RECORD, SOURCE_UPLOADER, SOURCE_EMPLOYEE,
]


def factory_concern_codes(factory):
    """Short codes used by the production recipient sheet (AGL, ASL, HO, ...)."""
    if factory is None:
        return set()
    name = (getattr(factory, "name", None) or "").strip()
    if not name:
        return set()
    codes = set()
    abbreviation = "".join(ch for ch in name if ch.isupper())
    if len(abbreviation) >= 2:
        codes.add(abbreviation)
    first = name.split()[0].upper().strip(".,")
    if first in {"AGL", "ASL", "AJL", "PWPL", "ABM", "HO"}:
        codes.add(first)
    upper_name = name.upper()
    if upper_name in {"HO", "HEAD OFFICE"} or "HEAD OFFICE" in upper_name:
        codes.add("HO")
    return codes


def production_alert_emails(factory):
    """Shared mailboxes for this factory. Rows with no Mail ID are not included."""
    codes = factory_concern_codes(factory)
    if not codes:
        return []
    routes = []
    try:
        from flask import has_app_context, current_app
        if has_app_context():
            routes = current_app.config.get("PRODUCTION_ALERT_ROUTES") or []
    except Exception:
        routes = []
    emails = []
    for tokens, email in routes:
        if not valid_email(email):
            continue
        if codes.intersection(tokens):
            address = email.strip()
            if address.lower() not in {item.lower() for item in emails}:
                emails.append(address)
    return emails


def department_group_emails(department):
    """Group mailboxes that receive the alert for this record.

    Returns the department's own group mailbox (e.g. Transport's
    `transport_agl@`), joined with any address in
    `ALWAYS_INCLUDE_GROUP_EMAILS` from config -- those are copied on every
    document alert regardless of factory or department. Case-insensitive
    de-duplication so an address does not appear twice when the department's
    own mailbox happens to be an always-on one.
    """
    emails = []
    seen = set()

    def _add(address):
        clean = (address or "").strip()
        if not clean or not valid_email(clean):
            return
        key = clean.lower()
        if key in seen:
            return
        seen.add(key)
        emails.append(clean)

    if department is not None:
        own = (getattr(department, "group_email", None) or "").strip()
        if not own:
            key = (getattr(department, "name", None) or "").strip().lower()
            try:
                from flask import current_app, has_app_context
                if has_app_context():
                    own = (current_app.config.get("DEPARTMENT_GROUP_EMAILS") or {}).get(key) or ""
            except Exception:
                own = ""
        _add(own)

    try:
        from flask import current_app, has_app_context
        if has_app_context():
            for address in current_app.config.get("ALWAYS_INCLUDE_GROUP_EMAILS") or []:
                _add(address)
    except Exception:
        pass

    return emails


def compute_status(expiry_date, no_expiry=False):
    """The single source of truth for compliance status. Use everywhere."""
    if no_expiry or not expiry_date:
        return STATUS_VALID
    days_left = (expiry_date - bst_today()).days
    if days_left <= 0:
        return STATUS_EXPIRED
    elif days_left <= 7:
        return STATUS_7
    elif days_left <= 30:
        return STATUS_30
    elif days_left <= 60:
        return STATUS_60
    elif days_left <= 90:
        return STATUS_90
    else:
        return STATUS_VALID


def _recalc_model(model):
    """Bulk-recompute status for one model using set-based UPDATEs (fast,
    constant number of statements regardless of row count)."""
    today = bst_today()
    changed = False

    no_expiry_cond = db.or_(model.expiry_date.is_(None), model.no_expiry.is_(True))

    buckets = [
        (STATUS_EXPIRED, db.and_(model.no_expiry.is_(False), model.expiry_date.isnot(None), model.expiry_date <= today)),
        (STATUS_7, db.and_(model.no_expiry.is_(False), model.expiry_date > today, model.expiry_date <= today + timedelta(days=7))),
        (STATUS_30, db.and_(model.no_expiry.is_(False), model.expiry_date > today + timedelta(days=7), model.expiry_date <= today + timedelta(days=30))),
        (STATUS_60, db.and_(model.no_expiry.is_(False), model.expiry_date > today + timedelta(days=30), model.expiry_date <= today + timedelta(days=60))),
        (STATUS_90, db.and_(model.no_expiry.is_(False), model.expiry_date > today + timedelta(days=60), model.expiry_date <= today + timedelta(days=90))),
        (STATUS_VALID, db.or_(no_expiry_cond, model.expiry_date > today + timedelta(days=90))),
    ]

    for target_status, condition in buckets:
        result = model.query.filter(condition, model.status != target_status).update(
            {"status": target_status}, synchronize_session=False
        )
        if result:
            changed = True
    return changed


def recalculate_all_statuses():
    """Recompute statuses for documents, employee licenses, and health checkups."""
    changed = False
    for model in (Document, EmployeeCompliance, HealthCheckup):
        if _recalc_model(model):
            changed = True
    if changed:
        db.session.commit()


# ---------------------------------------------------------------------------
# Alert recipients
# A record emails its specifically assigned people. When nobody is assigned,
# it emails the department, or the company if that department has no valid
# recipient. The uploader is always included. Employee personal email is
# included only when that employee record has receive_alerts enabled.
# ---------------------------------------------------------------------------

EMAIL_ROW_LIMIT = 200  # weekly summary cap per section
EMERGENCY_CHUNK = 40


def days_remaining(expiry_date, today=None):
    if not expiry_date:
        return None
    return (expiry_date - (today or bst_today())).days


def expiry_before_issue(issue_date, expiry_date):
    """True when both dates are set and expiry is earlier than issue."""
    return bool(issue_date and expiry_date and expiry_date < issue_date)


def user_can_receive_for_factory(user, factory_id):
    """Factory-scoped users only receive alerts for their own factory."""
    if user is None or not user.is_active:
        return False
    role_name = user.role.name if getattr(user, "role", None) else ""
    if role_name in GLOBAL_ALERT_ROLES:
        return True
    return factory_id is not None and user.factory_id == factory_id


def user_matches_factory_department(user, factory_id, department_id):
    """True when the user matches this factory + department pair.

    Factory match is satisfied by either:
      * `user.factory_id == factory_id`, or
      * `user.factory_id is None` (Global Access): the user acts across every
        factory, but still only within their assigned department.

    Department match is required when `department_id` is provided; a user
    without a department does not match a department-scoped record.
    """
    if user is None or not user.is_active:
        return False
    if not factory_id:
        return False
    factory_ok = user.factory_id is None or user.factory_id == factory_id
    if not factory_ok:
        return False
    if department_id:
        if user.department_id != department_id:
            return False
    return True


def matching_alert_users(factory_id, department_id):
    """Active users who match this factory + department pair.

    A user with `factory_id is None` (Global Access) and the matching
    department is included for every factory. A user whose factory is set
    must match it exactly.
    """
    if not factory_id or not department_id:
        return []
    from sqlalchemy.orm import joinedload
    users = (
        User.query.options(joinedload(User.factory), joinedload(User.department))
        .filter(
            User.is_active.is_(True),
            db.or_(User.factory_id == factory_id, User.factory_id.is_(None)),
            User.department_id == department_id,
        )
        .order_by(User.full_name, User.username)
        .all()
    )
    return [
        user for user in users
        if user_matches_factory_department(user, factory_id, department_id)
    ]


def format_sources(sources):
    return " + ".join(sources or [])


class RecipientIndex:
    """Company and department assignments loaded once per alert run."""

    def __init__(self):
        self.company_users = {}
        self.department_users = {}

    @classmethod
    def load(cls):
        from collections import defaultdict
        from sqlalchemy.orm import joinedload

        idx = cls()
        company_users = defaultdict(list)
        department_users = defaultdict(list)
        company_rows = (
            db.session.query(company_alert_recipients.c.company_id, User)
            .join(User, User.id == company_alert_recipients.c.user_id)
            .options(joinedload(User.role))
            .all()
        )
        for company_id, user in company_rows:
            company_users[company_id].append(user)
        dept_rows = (
            db.session.query(department_alert_recipients.c.department_id, User)
            .join(User, User.id == department_alert_recipients.c.user_id)
            .options(joinedload(User.role))
            .all()
        )
        for department_id, user in dept_rows:
            department_users[department_id].append(user)
        idx.company_users = company_users
        idx.department_users = department_users
        return idx


def _record_scope(record, kind):
    """Return (factory, department, company_id) for an alertable record."""
    if kind == "document":
        factory = record.factory
        company_id = factory.company_id if factory else None
        return factory, record.department, company_id
    employee = getattr(record, "employee", None)
    factory = employee.factory if employee else None
    company_id = factory.company_id if factory else None
    department = employee.department if employee else None
    return factory, department, company_id


def _assigned_users(kind_map, key, fallback):
    if kind_map is not None and key is not None:
        return list(kind_map.get(key, []))
    return list(fallback() or [])


def resolve_alert_recipients(record, kind, index=None, skipped=None):
    """Users/emails entitled to an alert for this record.

    Returns a de-duplicated list of {user, email, sources}. Inactive users and
    users outside the record's factory are omitted. Invalid emails are omitted
    and, when `skipped` is a list, recorded for an operational log line.
    """
    factory, department, company_id = _record_scope(record, kind)
    factory_id = factory.id if factory else None
    department_id = department.id if department else None
    found = {}

    def consider(user, source):
        if user is None:
            return
        if not user_matches_factory_department(user, factory_id, department_id):
            return
        if not valid_email(user.email):
            if skipped is not None:
                skipped.append({"username": user.username or "user", "reason": "Invalid or missing email"})
            return
        key = user.email.strip().lower()
        slot = found.setdefault(key, {"user": user, "email": user.email.strip(), "sources": []})
        if source not in slot["sources"]:
            slot["sources"].append(source)

    company_map = index.company_users if index is not None else None
    dept_map = index.department_users if index is not None else None

    def load_company():
        from app.models import Company
        company = db.session.get(Company, company_id) if company_id else None
        return list(company.alert_recipients) if company else []

    def load_department():
        from app.models import Department
        dept = db.session.get(Department, department_id) if department_id else None
        return list(dept.alert_recipients) if dept else []

    def take(users, source):
        """Add one recipient layer. True when at least one address was kept."""
        added = False
        for user in users:
            keys_before = set(found)
            consider(user, source)
            for key, slot in found.items():
                if key not in keys_before and source in slot["sources"]:
                    added = True
        return added

    specific = SOURCE_DOCUMENT if kind == "document" else SOURCE_RECORD
    assigned = list(getattr(record, "alert_recipients", None) or [])
    if not take(assigned, specific):
        department_users = (
            _assigned_users(dept_map, department_id, load_department) if department_id else []
        )
        if not take(department_users, SOURCE_DEPARTMENT):
            take(_assigned_users(company_map, company_id, load_company), SOURCE_COMPANY)

    creator = getattr(record, "creator", None)
    if creator is None and getattr(record, "created_by", None):
        creator = db.session.get(User, record.created_by)
    consider(creator, SOURCE_UPLOADER)

    if kind in ("license", "checkup"):
        employee = getattr(record, "employee", None)
        if employee and employee.is_active and employee.receive_alerts:
            if valid_email(employee.email):
                key = employee.email.strip().lower()
                slot = found.setdefault(key, {"user": None, "email": employee.email.strip(), "sources": []})
                if SOURCE_EMPLOYEE not in slot["sources"]:
                    slot["sources"].append(SOURCE_EMPLOYEE)
            elif skipped is not None:
                skipped.append({
                    "username": employee.employee_code or "employee",
                    "reason": "Invalid or missing email",
                })

    for email in production_alert_emails(factory):
        key = email.lower()
        slot = found.setdefault(key, {"user": None, "email": email, "sources": []})
        if SOURCE_PRODUCTION not in slot["sources"]:
            slot["sources"].append(SOURCE_PRODUCTION)

    for email in department_group_emails(department):
        key = email.lower()
        slot = found.setdefault(key, {"user": None, "email": email, "sources": []})
        if SOURCE_GROUP not in slot["sources"]:
            slot["sources"].append(SOURCE_GROUP)

    rows = list(found.values())
    for row in rows:
        row["sources"] = [name for name in SOURCE_ORDER if name in row["sources"]]
    return rows


def explain_alert_recipients(record, kind):
    """Read-only recipient list for a detail page. Does not write logs."""
    return resolve_alert_recipients(record, kind, skipped=None)


def sources_for_user(user, record, kind, company_ids, department_ids, index=None):
    """Why this user will actually receive an alert for the record.

    company_ids and department_ids are unused. Callers still pass them so the
    My Alerts query and this check stay in step if the page is filtered first.
    """
    if user is None or not valid_email(getattr(user, "email", None)):
        return []
    email = user.email.strip().lower()
    for row in resolve_alert_recipients(record, kind, index=index, skipped=None):
        if row["email"].strip().lower() == email:
            return row["sources"]
    return []


def record_snapshot(record, kind):
    """Return the metadata used by every alert email.

    All fields come straight from the record's DB columns and related
    rows (factory → company → department / employee). Nothing is read
    from OCR output. `status` is always recomputed from the current
    expiry date and no_expiry flag so emails are consistent even if a
    stored `record.status` column is briefly stale.
    """
    factory, department, _company_id = _record_scope(record, kind)
    company_name = factory.company.name if factory and factory.company else "-"
    expiry = record.expiry_date
    no_expiry = bool(getattr(record, "no_expiry", False))
    alert_status = compute_status(expiry, no_expiry)

    if kind == "document":
        title = record.doc_name or "Document"
        doc_type = (record.doc_type or "").strip()
        issue_date = record.issue_date
        certificate = (record.cert_no or "").strip()
        subject_label = (record.doc_type or "Document").strip() or "Document"
    elif kind == "license":
        base = (record.compliance_type or "License").strip() or "License"
        employee = record.employee
        if employee:
            title = f"{base} — {employee.name} ({employee.employee_code})"
        else:
            title = base
        doc_type = base
        issue_date = record.issue_date
        certificate = (record.registration_number or "").strip()
        subject_label = base
    else:  # checkup
        base = (record.exam_type or "Health Checkup").strip() or "Health Checkup"
        employee = record.employee
        if employee:
            title = f"{base} — {employee.name} ({employee.employee_code})"
        else:
            title = base
        doc_type = base
        issue_date = record.report_date
        certificate = ""
        subject_label = base

    return {
        "kind": kind,
        "title": title,
        "doc_type": doc_type,
        "subject_label": subject_label,
        "certificate": certificate,
        "company": company_name,
        "factory": factory.name if factory else "-",
        "department": department.name if department else "-",
        "issue_date": issue_date,
        "expiry": expiry,
        "no_expiry": no_expiry,
        "status": alert_status,
        "days": None if no_expiry else days_remaining(expiry),
    }


def _fmt_date(value):
    if not value:
        return "-"
    return value.strftime("%d-%b-%Y")


def _expiry_label(snapshot):
    if snapshot.get("no_expiry"):
        return "No expiry"
    return _fmt_date(snapshot.get("expiry"))


def _remaining_label(snapshot):
    if snapshot.get("no_expiry"):
        return "No expiry"
    days = snapshot.get("days")
    if snapshot.get("status") == STATUS_EXPIRED or (days is not None and days <= 0):
        return "Expired"
    if days is None:
        return "-"
    unit = "Day" if days == 1 else "Days"
    return f"{days} {unit}"


def emergency_subject_for(snapshot):
    if snapshot["status"] == STATUS_EXPIRED or (snapshot["days"] is not None and snapshot["days"] <= 0):
        return f"URGENT: {snapshot['title']} Expired"
    days = snapshot["days"]
    if days is None:
        return f"URGENT: {snapshot['title']}"
    unit = "Day" if days == 1 else "Days"
    return f"URGENT: {snapshot['title']} Expires in {days} {unit}"


# Standardized field order used by every alert email (manual, weekly, urgent).
# Each entry is (label, snapshot-key, optional). Rows marked optional are only
# rendered when the record actually has a value, so the same template adapts
# to every current and future document / license / checkup type without any
# per-type branching.
_SNAPSHOT_FIELDS = (
    ("Document", "title", False, "text"),
    ("Document Type", "doc_type", True, "text"),
    ("Certificate / License No.", "certificate", True, "text"),
    ("Company", "company", False, "text"),
    ("Factory", "factory", False, "text"),
    ("Department", "department", False, "text"),
    ("Issue Date", "issue_date", True, "date"),
    ("Expiry Date", None, False, "expiry"),
    ("Remaining", None, False, "remaining"),
    ("Alert Status", "status", False, "text"),
)


def _snapshot_rows_html(snapshot):
    rows = []
    for label, key, optional, kind in _SNAPSHOT_FIELDS:
        if kind == "expiry":
            value_text = _expiry_label(snapshot)
        elif kind == "remaining":
            value_text = _remaining_label(snapshot)
        elif kind == "date":
            raw = snapshot.get(key)
            if optional and not raw:
                continue
            value_text = _fmt_date(raw)
        else:
            raw = snapshot.get(key) or ""
            if optional and not str(raw).strip():
                continue
            value_text = str(raw) if raw else "-"
        rows.append(
            f"<strong>{html.escape(label)}:</strong> {html.escape(value_text)}<br>"
        )
    return "".join(rows)


def _snapshot_block(snapshot):
    return f"<p>{_snapshot_rows_html(snapshot)}</p>"


def weekly_subject_for(snapshot):
    return f"Compliance Alert: {snapshot['title']} — {snapshot['status']}"


def manual_subject_for(snapshot):
    return f"Compliance Notice: {snapshot['title']}"


def build_single_alert_html(snapshot, intro, recipient_name="Recipient"):
    """One record per message. Field layout is identical across weekly,
    emergency, and manual alerts so the recipient always sees the same shape."""
    return f"""
    <p>Dear {html.escape(recipient_name)},</p>
    <p>{html.escape(intro)}</p>
    {_snapshot_block(snapshot)}
    <p>This message is only about the record above. Please take the necessary action.</p>
    <p>Regards,<br>Ananta DocMan Alert</p>
    """


def _deliver_alert(alert_type, subject, email, user, html_body, items):
    """Send one message and store it in alert history. Returns an error name or None."""
    sender = current_app.config.get("MAIL_DEFAULT_SENDER")
    message = Message(
        subject=subject,
        sender=sender,
        recipients=[email],
        html=html_body,
    )
    try:
        mail.send(message)
        record_sent_alert(alert_type, subject, sender, email, user, "sent", None, items)
        return None
    except Exception as exc:
        error_name = _safe_mail_error(exc)
        record_sent_alert(alert_type, subject, sender, email, user, "failed", error_name, items)
        return error_name


def _safe_mail_error(exc):
    """Exception class only. SMTP messages are not logged (they can echo config)."""
    return type(exc).__name__


def _log_skipped(skipped, label):
    if not skipped:
        return
    names = []
    for item in skipped:
        username = item.get("username") or "user"
        if username not in names:
            names.append(username)
    shown = ", ".join(names[:20])
    extra = "" if len(names) <= 20 else f" (+{len(names) - 20} more)"
    log_event(
        "ALERT_RECIPIENT_SKIPPED",
        f"{label}: {shown}{extra}. Reason: Invalid or missing email.",
        level="WARNING",
        username_or_process="system",
    )


def _chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _alert_load_options(kind):
    from sqlalchemy.orm import joinedload, selectinload
    from app.models import Factory
    if kind == "document":
        return (
            joinedload(Document.factory).joinedload(Factory.company),
            joinedload(Document.department),
            joinedload(Document.creator).joinedload(User.role),
            selectinload(Document.alert_recipients).joinedload(User.role),
        )
    if kind == "license":
        return (
            joinedload(EmployeeCompliance.employee).joinedload(Employee.factory).joinedload(Factory.company),
            joinedload(EmployeeCompliance.employee).joinedload(Employee.department),
            joinedload(EmployeeCompliance.creator).joinedload(User.role),
            selectinload(EmployeeCompliance.alert_recipients).joinedload(User.role),
        )
    return (
        joinedload(HealthCheckup.employee).joinedload(Employee.factory).joinedload(Factory.company),
        joinedload(HealthCheckup.employee).joinedload(Employee.department),
        joinedload(HealthCheckup.creator).joinedload(User.role),
        selectinload(HealthCheckup.alert_recipients).joinedload(User.role),
    )


def _load_attention(model, statuses, kind):
    query = model.query.options(*_alert_load_options(kind)).filter(
        model.is_active.is_(True),
        model.status.in_(statuses),
    ).order_by(model.expiry_date)
    if statuses == NORMAL_ALERT_STATUSES or set(statuses) == set(NORMAL_ALERT_STATUSES):
        query = query.limit(EMAIL_ROW_LIMIT)
    return query.all()


def _history_item(kind, record, sources):
    """Snapshot stored with an alert email. None when the record has no id."""
    if not getattr(record, "id", None):
        return None
    factory, _department, _company_id = _record_scope(record, kind)
    snapshot = record_snapshot(record, kind)
    return {
        "record_type": kind,
        "record_id": record.id,
        "title": (snapshot["title"] or "")[:300],
        "company_name": (snapshot["company"] or "")[:200],
        "factory_name": (snapshot["factory"] or "")[:200],
        "factory_id": factory.id if factory else None,
        "department_name": (snapshot["department"] or "")[:200],
        "expiry_date": record.expiry_date,
        "record_status": (snapshot["status"] or "")[:40],
        "sources": format_sources(sources)[:200],
    }


def record_sent_alert(alert_type, subject, sender, email, user, status, error, items):
    """Persist one alert message. Failures here must not hide a mail result."""
    try:
        name = ""
        user_id = None
        if user is not None:
            user_id = user.id
            name = (user.full_name or user.username or "")[:150]
        clean_items = [item for item in (items or []) if item]
        row = AlertEmail(
            sent_at=datetime.utcnow(),
            alert_type=(alert_type or "")[:20],
            subject=(subject or "")[:500],
            sender=(sender or "")[:255],
            recipient_email=(email or "")[:255],
            recipient_name=name,
            user_id=user_id,
            status=status,
            error_detail=((error or "")[:80] or None),
            record_count=len(clean_items),
        )
        db.session.add(row)
        db.session.flush()
        for item in clean_items:
            db.session.add(AlertEmailItem(alert_email_id=row.id, **item))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _bucket_records(records_by_kind, index, skipped):
    """email -> user plus (record, sources) lists for each record kind."""
    buckets = {}
    for kind, records in records_by_kind:
        for record in records:
            recipients = resolve_alert_recipients(record, kind, index=index, skipped=skipped)
            if not recipients:
                continue
            for person in recipients:
                bucket = buckets.setdefault(person["email"], {
                    "user": person["user"],
                    "document": [],
                    "license": [],
                    "checkup": [],
                })
                if bucket["user"] is None and person["user"] is not None:
                    bucket["user"] = person["user"]
                bucket[kind].append((record, person["sources"]))
    return buckets


def _bucket_items(bucket):
    items = []
    records = {"document": [], "license": [], "checkup": []}
    for kind in ("document", "license", "checkup"):
        for record, sources in bucket[kind]:
            records[kind].append(record)
            item = _history_item(kind, record, sources)
            if item:
                items.append(item)
    return records, items


def send_compliance_alert(documents=None, licenses=None, checkups=None):
    """Send the Saturday summary for 90/60/30-day records.

    Each record goes to its assigned people, or to the department, or to the
    company. The uploader is included. One address receives one email. A
    failure for one address does not stop the rest. Every attempt is stored
    in alert history.

    Pass explicit record lists from tests. The scheduled job omits them and
    this function loads the current normal-alert records.

    Returns (sent: bool, error: str|None, total_count: int).
    """
    queried = documents is None and licenses is None and checkups is None
    if queried:
        recalculate_all_statuses()
        documents = _load_attention(Document, NORMAL_ALERT_STATUSES, "document")
        licenses = _load_attention(EmployeeCompliance, NORMAL_ALERT_STATUSES, "license")
        checkups = _load_attention(HealthCheckup, NORMAL_ALERT_STATUSES, "checkup")
    else:
        # Explicit record lists are still filtered to the weekly buckets
        # (90 / 60 / 30 days). Classification is computed from the current
        # expiry date, not the stored record.status column, so a caller
        # that passes a record whose stored status is stale still gets the
        # correct routing: a 7-day or Expired record is held back from the
        # weekly send and left to send_emergency_alerts; a Valid record is
        # dropped entirely.
        def _in_weekly(r):
            return compute_status(
                r.expiry_date, bool(getattr(r, "no_expiry", False))
            ) in NORMAL_ALERT_STATUSES
        documents = [d for d in (documents or []) if _in_weekly(d)]
        licenses = [l for l in (licenses or []) if _in_weekly(l)]
        checkups = [c for c in (checkups or []) if _in_weekly(c)]

    total_count = len(documents) + len(licenses) + len(checkups)
    if total_count == 0:
        return False, None, 0

    index = RecipientIndex.load()
    skipped = []
    buckets = _bucket_records(
        (("document", documents), ("license", licenses), ("checkup", checkups)),
        index,
        skipped,
    )
    _log_skipped(skipped, "Weekly alert")

    sent_count = 0
    errors = []
    intro = (
        "Scheduled weekly alert for this document. "
        "It goes to the assigned person, or to the document's department, "
        "or to the company when that department has no recipient."
    )
    for email in sorted(buckets):
        bucket = buckets[email]
        for kind in ("document", "license", "checkup"):
            for record, sources in bucket[kind]:
                snapshot = record_snapshot(record, kind)
                subject = weekly_subject_for(snapshot)
                item = _history_item(kind, record, sources)
                error_name = _deliver_alert(
                    "weekly", subject, email, bucket["user"],
                    build_single_alert_html(snapshot, intro),
                    [item] if item else [],
                )
                if error_name:
                    errors.append(f"{email}: {error_name}")
                else:
                    sent_count += 1

    if sent_count and not errors:
        log_event(
            "ALERT_SENT",
            f"Weekly compliance alert sent to {sent_count} recipient(s) covering {total_count} record(s).",
            username_or_process="system",
        )
        log_event(
            "EMAIL_SUCCESS",
            f"Weekly compliance alert sent to {sent_count} recipient(s) covering {total_count} record(s).",
            username_or_process="system",
        )
        return True, None, total_count

    if not buckets:
        error_text = "No valid recipients to send to."
    else:
        error_text = "; ".join(errors) if errors else "No valid recipients to send to."
    log_event(
        "ALERT_FAILED",
        f"Weekly compliance alert: sent {sent_count}, issues: {error_text}"[:2000],
        level="ERROR",
        username_or_process="system",
    )
    log_event(
        "EMAIL_FAILURE",
        f"Weekly compliance alert: sent {sent_count}, issues: {error_text}"[:2000],
        level="ERROR",
        username_or_process="system",
    )
    return (sent_count > 0), error_text, total_count


def _expiry_key(record):
    if record.expiry_date:
        return record.expiry_date.isoformat()
    return ""


def _dispatch_tuple(kind, record):
    return (kind, record.id, record.status, _expiry_key(record))


def _existing_dispatches(records_by_kind):
    found = set()
    ids_by_kind = {}
    for kind, records in records_by_kind:
        ids_by_kind[kind] = [record.id for record in records if record.id]
    for kind, ids in ids_by_kind.items():
        if not ids:
            continue
        rows = AlertDispatch.query.filter(
            AlertDispatch.record_type == kind,
            AlertDispatch.record_id.in_(ids),
        ).all()
        for row in rows:
            found.add((row.record_type, row.record_id, row.alert_stage, row.expiry_key))
    return found


def send_emergency_alerts(documents=None, licenses=None, checkups=None):
    """Send urgent alerts for 7-day and expired records, once per threshold.

    The same record and expiry are not emailed again for the same status.
    A later expiry date (renewal) or a move from 7-day to expired can alert
    again. Opening a page does not call this.

    Returns (sent: bool, error: str|None, newly_alerted_count: int).
    """
    queried = documents is None and licenses is None and checkups is None
    if queried:
        documents = _load_attention(Document, EMERGENCY_STATUSES, "document")
        licenses = _load_attention(EmployeeCompliance, EMERGENCY_STATUSES, "license")
        checkups = _load_attention(HealthCheckup, EMERGENCY_STATUSES, "checkup")
    else:
        documents = documents or []
        licenses = licenses or []
        checkups = checkups or []

    groups = (("document", documents), ("license", licenses), ("checkup", checkups))
    already = _existing_dispatches(groups)
    index = RecipientIndex.load()
    skipped = []
    pending = []  # email, user, snapshot, marker, history item
    for kind, records in groups:
        for record in records:
            if record.status not in EMERGENCY_STATUSES:
                continue
            marker = _dispatch_tuple(kind, record)
            if marker in already:
                continue
            recipients = resolve_alert_recipients(record, kind, index=index, skipped=skipped)
            if not recipients:
                continue
            snapshot = record_snapshot(record, kind)
            for person in recipients:
                pending.append((
                    person["email"],
                    person["user"],
                    snapshot,
                    marker,
                    _history_item(kind, record, person["sources"]),
                ))

    _log_skipped(skipped, "Emergency alert")
    if not pending:
        return False, None, 0

    by_email = {}
    for email, user, snapshot, marker, history_item in pending:
        slot = by_email.setdefault(email, {"user": user, "rows": []})
        if slot["user"] is None and user is not None:
            slot["user"] = user
        slot["rows"].append((snapshot, marker, history_item))

    sent_count = 0
    errors = []
    delivered = set()
    urgent_intro = (
        "Scheduled urgent alert for this document. "
        "It goes to the assigned person, or to the document's department, "
        "or to the company when that department has no recipient."
    )
    for email in sorted(by_email):
        slot = by_email[email]
        for snapshot, marker, history_item in slot["rows"]:
            subject = emergency_subject_for(snapshot)
            error_name = _deliver_alert(
                "emergency", subject, email, slot["user"],
                build_single_alert_html(snapshot, urgent_intro),
                [history_item] if history_item else [],
            )
            if error_name:
                errors.append(f"{email}: {error_name}")
            else:
                sent_count += 1
                delivered.add(marker)

    for kind, record_id, stage, expiry_key in delivered:
        exists = AlertDispatch.query.filter_by(
            record_type=kind,
            record_id=record_id,
            alert_stage=stage,
            expiry_key=expiry_key,
        ).first()
        if exists:
            continue
        db.session.add(AlertDispatch(
            record_type=kind,
            record_id=record_id,
            alert_stage=stage,
            expiry_key=expiry_key,
            sent_at=datetime.utcnow(),
        ))
    if delivered:
        db.session.commit()

    if sent_count and not errors:
        log_event(
            "EMERGENCY_ALERT_SENT",
            f"Emergency alert sent to {sent_count} recipient(s) for {len(delivered)} record threshold(s).",
            username_or_process="system",
        )
        return True, None, len(delivered)

    if sent_count and errors:
        log_event(
            "EMERGENCY_ALERT_FAILED",
            f"Emergency alert partial: sent {sent_count}, issues: {'; '.join(errors)}"[:2000],
            level="ERROR",
            username_or_process="system",
        )
        return True, "; ".join(errors), len(delivered)

    error_text = "; ".join(errors) if errors else "No valid recipients to send to."
    log_event(
        "EMERGENCY_ALERT_FAILED",
        f"Emergency alert failed: {error_text}"[:2000],
        level="ERROR",
        username_or_process="system",
    )
    return False, error_text, 0


def send_manual_document_alert(document, users, group_emails=None):
    """Email the selected users and selected department group mailbox(es).

    `users` is the list of User objects the caller chose on the Send Alert
    page. `group_emails` is the list of department group mailbox addresses
    the caller chose. When `group_emails` is None the department's full
    group mailbox list is used (backward compatible with callers that only
    pass users). An empty list means "do not send to the group mailbox."

    Department group mailboxes are not filtered by factory. The caller is
    expected to validate the posted addresses against
    `department_group_emails(document.department)` before passing them in.

    Does not add company, uploader, or production mailboxes. Does not
    stamp the scheduled-alert dedup row.
    """
    factory_id = getattr(document, "factory_id", None)
    department_id = getattr(document, "department_id", None)
    selected = []
    seen = set()
    for user in users or []:
        if not department_id or not user_matches_factory_department(user, factory_id, department_id):
            continue
        if not valid_email(user.email):
            continue
        address = user.email.strip()
        key = address.lower()
        if key in seen:
            continue
        seen.add(key)
        selected.append((user, address, [SOURCE_DOCUMENT]))
    if group_emails is None:
        group_emails = department_group_emails(getattr(document, "department", None))
    for address in group_emails or []:
        if not valid_email(address):
            continue
        clean = address.strip()
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        selected.append((None, clean, [SOURCE_GROUP]))
    if not selected:
        return False, "No valid recipients to send to.", 0
    snapshot = record_snapshot(document, "document")
    subject = manual_subject_for(snapshot)
    intro = "This manual alert is for the document below."
    sent_count = 0
    errors = []
    for user, address, sources in selected:
        name = (user.full_name or user.username) if user is not None else "Recipient"
        item = _history_item("document", document, sources)
        error_name = _deliver_alert(
            "manual",
            subject,
            address,
            user,
            build_single_alert_html(snapshot, intro, name),
            [item] if item else [],
        )
        if error_name:
            errors.append(f"{address}: {error_name}")
        else:
            sent_count += 1
    if sent_count and not errors:
        log_event(
            "ALERT_SENT",
            f"Manual alert for document {document.id} sent to {sent_count} recipient(s).",
            username_or_process="system",
        )
        return True, None, sent_count
    error_text = "; ".join(errors) if errors else "Manual alert was not sent."
    log_event(
        "ALERT_FAILED",
        f"Manual alert for document {document.id}: sent {sent_count}, issues: {error_text}"[:2000],
        level="ERROR",
        username_or_process="system",
    )
    return sent_count > 0, error_text, sent_count


