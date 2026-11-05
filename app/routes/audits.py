"""Compliance Audit Tracker blueprint.

A Compliance-only module: Super Admin + the three Compliance roles. Audits are
linked to zero or more factories (audit_factories) for factory-wise filtering;
factory-scoped users see audits that include their factory plus audits with no
factory mapping. Validity (certificate expiry) drives status, alerts, and the
Overdue dashboard bucket; audits with no validity are one-time events.
"""
import os
import secrets
from datetime import date, timedelta

from flask import (Blueprint, render_template, redirect, url_for, flash,
                   current_app, request, abort)
from flask_login import login_required, current_user
from sqlalchemy.orm import selectinload

from app import db
from app.models import Audit, Factory, AUDIT_NO_VALIDITY
from app.forms import AuditForm
from app.utils import (
    roles_required, compute_status, ALL_STATUSES, log_event, log_record, bst_today,
    EXPIRING_STATUSES, STATUS_EXPIRED, ATTENTION_STATUSES,
    resolve_audit_alert_recipients, send_manual_audit_alert,
)

audits_bp = Blueprint("audits", __name__, url_prefix="/audits")

VIEW_ROLES = ("Super Admin", "Compliance Manager", "Compliance User", "Compliance Viewer")
MANAGE_ROLES = ("Super Admin", "Compliance Manager", "Compliance User")
DELETE_ROLES = ("Super Admin", "Compliance Manager")
IMPORT_ROLES = ("Super Admin", "Compliance Manager")
ALERT_ROLES = ("Super Admin", "Compliance Manager", "Compliance User")

# Status choices offered in the list filter (shared engine statuses + the
# audit-only sentinel for one-time audits).
AUDIT_STATUS_CHOICES = list(ALL_STATUSES) + [AUDIT_NO_VALIDITY]
# Validity statuses that make an audit's certificate overdue (needs renewal).
OVERDUE_STATUSES = (STATUS_EXPIRED,)
# Meta status value from the dashboard "Validity Expiring" card: expands to the
# four 90/60/30/7-day buckets so the filtered list matches the card count.
AUDIT_STATUS_EXPIRING_ANY = "expiring"

AUDIT_TYPE_SUGGESTIONS = [
    "Social", "Technical", "Security", "Certification", "Safety",
    "Environment", "Social + Env",
]


# --- Scope helpers ----------------------------------------------------------

def _accessible():
    """True when the current user may see every audit regardless of factory."""
    return current_user.can_access_all_factories()


def _scope_query(query):
    """Factory-scoped users see audits linked to their factory, plus audits with
    no factory mapping. Global-access users see everything."""
    if _accessible():
        return query
    fid = current_user.factory_id
    if not fid:
        # A factory-scoped user with no factory set: only unmapped audits.
        return query.filter(~Audit.factories.any())
    return query.filter(db.or_(
        Audit.factories.any(Factory.id == fid),
        ~Audit.factories.any(),
    ))


def _get_audit_or_404(audit_id):
    audit = Audit.query.options(
        selectinload(Audit.factories),
        selectinload(Audit.alert_recipients),
    ).filter(Audit.id == audit_id).first()
    if audit is None:
        abort(404)
    if not _accessible():
        fid = current_user.factory_id
        linked = {f.id for f in audit.factories}
        if linked and (not fid or fid not in linked):
            abort(403)
    return audit


def _imports_dir():
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], "_audit_imports")
    os.makedirs(path, exist_ok=True)
    return path


def _factory_choices():
    return [(f.id, f.name) for f in Factory.query.order_by(Factory.name).all()]


# --- Dashboard overview (used by the main dashboard) ------------------------

def audit_dashboard_overview(attention_limit=12):
    """Compliance-audit summary for the dashboard. Respects factory scope."""
    today = bst_today()
    base = _scope_query(Audit.query.filter(Audit.is_active.is_(True)))

    total = base.count()
    # Upcoming: start date in the future (scheduled, not yet conducted).
    upcoming = base.filter(Audit.date_start > today).count()
    # Completed: conducted on or before today.
    completed = base.filter(Audit.date_start <= today).count()
    # Overdue: a certificate validity date that has passed (needs renewal).
    overdue = base.filter(Audit.status == STATUS_EXPIRED).count()
    # Validity expiring soon (90/60/30/7 buckets).
    expiring = base.filter(Audit.status.in_(EXPIRING_STATUSES)).count()

    # Upcoming audits table (soonest first).
    upcoming_rows = (
        _scope_query(Audit.query.filter(Audit.is_active.is_(True), Audit.date_start > today))
        .order_by(Audit.date_start.asc()).limit(attention_limit).all()
    )
    # Overdue/expiring validity table (most overdue first).
    attention_rows = (
        _scope_query(Audit.query.filter(
            Audit.is_active.is_(True),
            Audit.status.in_(ATTENTION_STATUSES),
            Audit.validity_date.isnot(None),
        )).order_by(Audit.validity_date.asc()).limit(attention_limit).all()
    )
    return {
        "total": total,
        "upcoming": upcoming,
        "completed": completed,
        "overdue": overdue,
        "expiring": expiring,
        "upcoming_rows": [_row_brief(a) for a in upcoming_rows],
        "attention_rows": [_row_brief(a) for a in attention_rows],
    }


def _row_brief(a):
    return {
        "id": a.id,
        "audit_name": a.audit_name,
        "audited_by": a.audited_by or "-",
        "audit_type": a.audit_type or "-",
        "factory": a.factory_raw or "-",
        "date_start": a.date_start,
        "validity_date": a.validity_date,
        "status": a.status,
        "url": url_for("audits.view_audit", audit_id=a.id),
    }


# --- List -------------------------------------------------------------------

@audits_bp.route("/")
@login_required
@roles_required(*VIEW_ROLES)
def list_audits():
    show_archived = request.args.get("archived") == "1"
    query = Audit.query.options(selectinload(Audit.factories))
    query = _scope_query(query)
    query = query.filter(Audit.is_active.is_(not show_archived)) if not show_archived \
        else query.filter(Audit.is_active.is_(False))

    search = request.args.get("search", "").strip()
    factory_id = request.args.get("factory_id", type=int)
    audit_type = request.args.get("audit_type", "").strip()
    status = request.args.get("status", "").strip()
    timeframe = request.args.get("timeframe", "").strip()

    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(
            Audit.audit_name.ilike(like),
            Audit.audited_by.ilike(like),
            Audit.buyer.ilike(like),
            Audit.auditor_name.ilike(like),
            Audit.factory_raw.ilike(like),
        ))
    if factory_id:
        query = query.filter(Audit.factories.any(Factory.id == factory_id))
    if audit_type:
        query = query.filter(Audit.audit_type.ilike(f"%{audit_type}%"))
    # Dashboard "Validity Expiring" card aggregates the four expiring buckets.
    if status == AUDIT_STATUS_EXPIRING_ANY:
        query = query.filter(Audit.status.in_(EXPIRING_STATUSES))
    elif status:
        query = query.filter(Audit.status == status)
    # Dashboard "Upcoming" / "Completed" cards filter on the audit date, not the
    # validity status, so they use a separate timeframe parameter.
    if timeframe == "upcoming":
        query = query.filter(Audit.date_start > bst_today())
    elif timeframe == "completed":
        query = query.filter(Audit.date_start <= bst_today())

    page = request.args.get("page", 1, type=int)
    pagination = query.order_by(Audit.date_start.desc().nullslast()).paginate(
        page=page, per_page=25, error_out=False)

    return render_template(
        "audits/list.html",
        audits=pagination.items,
        pagination=pagination,
        factories=_factory_choices(),
        selected_search=search,
        selected_factory_id=factory_id,
        selected_audit_type=audit_type,
        selected_status=status,
        selected_timeframe=timeframe,
        status_choices=AUDIT_STATUS_CHOICES,
        type_suggestions=AUDIT_TYPE_SUGGESTIONS,
        show_archived=show_archived,
        can_manage=current_user.role.name in MANAGE_ROLES,
        can_delete=current_user.role.name in DELETE_ROLES,
        can_import=current_user.role.name in IMPORT_ROLES,
        can_alert=current_user.role.name in ALERT_ROLES,
        today=bst_today(),
    )


# --- Detail -----------------------------------------------------------------

@audits_bp.route("/<int:audit_id>")
@login_required
@roles_required(*VIEW_ROLES)
def view_audit(audit_id):
    audit = _get_audit_or_404(audit_id)
    return render_template(
        "audits/detail.html",
        audit=audit,
        can_manage=current_user.role.name in MANAGE_ROLES,
        can_delete=current_user.role.name in DELETE_ROLES,
        can_alert=current_user.role.name in ALERT_ROLES,
        today=bst_today(),
    )


# --- Add / Edit -------------------------------------------------------------

def _recipient_choices():
    """Compliance-role users eligible to be extra alert recipients."""
    from app.models import User, Role
    role_ids = [r.id for r in Role.query.filter(
        Role.name.in_(("Compliance Manager", "Compliance User", "Compliance Viewer"))
    ).all()]
    users = []
    if role_ids:
        users = User.query.filter(User.is_active.is_(True), User.role_id.in_(role_ids)) \
            .order_by(User.full_name, User.username).all()
    return [(u.id, f"{u.full_name or u.username} ({u.email or 'no email'})") for u in users], \
           {u.id for u in users}


def _apply_factory_links(audit, factory_ids):
    audit.factories = Factory.query.filter(Factory.id.in_(factory_ids)).all() if factory_ids else []


@audits_bp.route("/add", methods=["GET", "POST"])
@login_required
@roles_required(*MANAGE_ROLES)
def add_audit():
    form = AuditForm()
    form.factory_ids.choices = _factory_choices()
    recip_choices, recip_ids = _recipient_choices()
    form.alert_recipients.choices = recip_choices
    if form.validate_on_submit():
        if form.date_end.data and form.date_end.data < form.date_start.data:
            flash("End date cannot be before the start date.", "danger")
            return render_template("audits/form.html", form=form, title="Add Audit",
                                   type_suggestions=AUDIT_TYPE_SUGGESTIONS)
        audit = Audit(created_by=current_user.id)
        _write_form(audit, form)
        db.session.add(audit)
        db.session.flush()
        _apply_factory_links(audit, form.factory_ids.data or [])
        chosen = [uid for uid in (form.alert_recipients.data or []) if uid in recip_ids]
        from app.models import User
        audit.alert_recipients = User.query.filter(User.id.in_(chosen)).all() if chosen else []
        db.session.commit()
        log_record("created", "Audit", f"{audit.audit_name} (id {audit.id})")
        flash("Audit added.", "success")
        return redirect(url_for("audits.view_audit", audit_id=audit.id))
    return render_template("audits/form.html", form=form, title="Add Audit",
                           type_suggestions=AUDIT_TYPE_SUGGESTIONS)


@audits_bp.route("/<int:audit_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required(*MANAGE_ROLES)
def edit_audit(audit_id):
    audit = _get_audit_or_404(audit_id)
    form = AuditForm(obj=audit)
    form.factory_ids.choices = _factory_choices()
    recip_choices, recip_ids = _recipient_choices()
    form.alert_recipients.choices = recip_choices
    if request.method == "GET":
        form.factory_ids.data = [f.id for f in audit.factories]
        form.alert_recipients.data = [u.id for u in audit.alert_recipients]
    if form.validate_on_submit():
        if form.date_end.data and form.date_end.data < form.date_start.data:
            flash("End date cannot be before the start date.", "danger")
            return render_template("audits/form.html", form=form, title="Edit Audit",
                                   audit=audit, type_suggestions=AUDIT_TYPE_SUGGESTIONS)
        _write_form(audit, form)
        _apply_factory_links(audit, form.factory_ids.data or [])
        chosen = [uid for uid in (form.alert_recipients.data or []) if uid in recip_ids]
        from app.models import User
        audit.alert_recipients = User.query.filter(User.id.in_(chosen)).all() if chosen else []
        db.session.commit()
        log_record("updated", "Audit", f"{audit.audit_name} (id {audit.id})")
        flash("Audit updated.", "success")
        return redirect(url_for("audits.view_audit", audit_id=audit.id))
    return render_template("audits/form.html", form=form, title="Edit Audit",
                           audit=audit, type_suggestions=AUDIT_TYPE_SUGGESTIONS)


def _write_form(audit, form):
    audit.date_start = form.date_start.data
    audit.date_end = form.date_end.data or form.date_start.data
    audit.audit_name = (form.audit_name.data or "").strip()
    audit.audited_by = (form.audited_by.data or "").strip() or None
    audit.auditor_name = (form.auditor_name.data or "").strip() or None
    audit.buyer = (form.buyer.data or "").strip() or None
    audit.reference_id = (form.reference_id.data or "").strip() or None
    audit.audit_type = (form.audit_type.data or "").strip() or None
    audit.announcement_type = (form.announcement_type.data or "").strip() or None
    audit.monitoring_type = (form.monitoring_type.data or "").strip() or None
    audit.factory_raw = (form.factory_raw.data or "").strip() or None
    audit.rating = (form.rating.data or "").strip() or None
    audit.validity_date = form.validity_date.data
    audit.cost = (form.cost.data or "").strip() or None
    audit.status = compute_status(form.validity_date.data) if form.validity_date.data else AUDIT_NO_VALIDITY
    from app.audit_import import normalize_import_key
    audit.import_key = normalize_import_key(audit.date_start, audit.audit_name, audit.factory_raw)


# --- Archive / Delete -------------------------------------------------------

@audits_bp.route("/<int:audit_id>/archive", methods=["POST"])
@login_required
@roles_required(*DELETE_ROLES)
def archive_audit(audit_id):
    audit = _get_audit_or_404(audit_id)
    audit.is_active = not audit.is_active
    db.session.commit()
    flash("Audit archived." if not audit.is_active else "Audit restored.", "success")
    return redirect(url_for("audits.view_audit", audit_id=audit.id))


@audits_bp.route("/<int:audit_id>/delete", methods=["GET", "POST"])
@login_required
@roles_required(*DELETE_ROLES)
def delete_audit(audit_id):
    audit = _get_audit_or_404(audit_id)
    if request.method == "POST":
        typed = (request.form.get("confirm_name") or "").strip()
        if typed != (audit.audit_name or ""):
            flash("Type the audit name exactly to confirm deletion.", "danger")
            return render_template("audits/delete.html", audit=audit)
        from app.models import AlertDispatch
        AlertDispatch.query.filter_by(record_type="audit", record_id=audit.id).delete(
            synchronize_session=False)
        name, aid = audit.audit_name, audit.id
        audit.factories = []
        audit.alert_recipients = []
        db.session.delete(audit)
        db.session.commit()
        log_record("deleted", "Audit", f"{name} (id {aid})")
        flash(f"Audit '{name}' deleted.", "success")
        return redirect(url_for("audits.list_audits"))
    return render_template("audits/delete.html", audit=audit)


# --- Manual alert -----------------------------------------------------------

@audits_bp.route("/<int:audit_id>/send-alert", methods=["GET", "POST"])
@login_required
@roles_required(*ALERT_ROLES)
def send_audit_alert(audit_id):
    audit = _get_audit_or_404(audit_id)
    if not audit.is_active:
        flash("This audit is archived. Restore it before sending an alert.", "warning")
        return redirect(url_for("audits.view_audit", audit_id=audit.id))

    allowed = resolve_audit_alert_recipients(audit)
    allowed_user_ids = {row["user"].id for row in allowed if row["user"] is not None}
    allowed_group = [row["email"] for row in allowed if row["user"] is None]

    if request.method == "POST":
        posted_user_ids = set()
        for raw in request.form.getlist("recipient_id"):
            try:
                posted_user_ids.add(int(raw))
            except (TypeError, ValueError):
                continue
        posted_group = {a.strip().lower() for a in request.form.getlist("group_email") if a and a.strip()}
        chosen_users = [row["user"] for row in allowed
                        if row["user"] is not None and row["user"].id in posted_user_ids]
        chosen_group = [a for a in allowed_group if a.lower() in posted_group]
        if not chosen_users and not chosen_group:
            flash("Select at least one recipient for this alert.", "danger")
        else:
            sent, error, count = send_manual_audit_alert(audit, chosen_users, group_emails=chosen_group)
            if sent and not error:
                flash(f"Alert sent to {count} recipient(s).", "success")
            elif sent:
                flash("The alert was only partly sent. See Alert History.", "warning")
            else:
                flash("The alert could not be sent. See Alert History.", "danger")
            return redirect(url_for("audits.view_audit", audit_id=audit.id))

    return render_template(
        "audits/send_alert.html",
        audit=audit,
        allowed_users=[row["user"] for row in allowed if row["user"] is not None],
        allowed_group_emails=allowed_group,
    )


# --- Import (upload -> preview -> confirm) ----------------------------------

@audits_bp.route("/import", methods=["GET", "POST"])
@login_required
@roles_required(*IMPORT_ROLES)
def import_audits():
    from app.audit_import import parse_workbook, annotate_existing, preview_counts
    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Please choose an Excel (.xlsx) file.", "danger")
            return redirect(url_for("audits.import_audits"))
        if not file.filename.lower().endswith(".xlsx"):
            flash("Only .xlsx files are supported for import.", "danger")
            return redirect(url_for("audits.import_audits"))
        token = secrets.token_hex(16)
        temp_path = os.path.join(_imports_dir(), f"{token}.xlsx")
        file.save(temp_path)
        try:
            rows, errors = parse_workbook(temp_path)
            annotate_existing(rows)
            counts = preview_counts(rows)
        except Exception as exc:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            log_event("XLSX_IMPORT_FAILED", f"Audit Excel preview failed: {type(exc).__name__}", level="ERROR")
            flash("Could not read the Excel file. Use a valid .xlsx workbook that "
                  "matches the Audit Tracker header.", "danger")
            return redirect(url_for("audits.import_audits"))
        return render_template(
            "audits/import_preview.html",
            rows=rows, errors=errors, counts=counts, token=token,
            filename=file.filename, preview_rows=rows[:60],
        )
    return render_template("audits/import.html")


@audits_bp.route("/import/confirm", methods=["POST"])
@login_required
@roles_required(*IMPORT_ROLES)
def import_confirm():
    from app.audit_import import parse_workbook, annotate_existing, preview_counts, apply_import
    token = request.form.get("token", "")
    if not token or not token.isalnum() or len(token) != 32:
        flash("Invalid import request.", "danger")
        return redirect(url_for("audits.import_audits"))
    temp_path = os.path.join(_imports_dir(), f"{token}.xlsx")
    if not os.path.exists(temp_path):
        flash("The uploaded file expired. Please upload again.", "warning")
        return redirect(url_for("audits.import_audits"))
    log_event("XLSX_IMPORT_STARTED", "Audit Excel import started.")
    try:
        rows, errors = parse_workbook(temp_path)
        annotate_existing(rows)
        counts = preview_counts(rows)
        summary = apply_import(rows, user_id=current_user.id)
    except Exception as exc:
        db.session.rollback()
        log_event("XLSX_IMPORT_FAILED", f"Audit Excel import failed: {type(exc).__name__}", level="ERROR")
        flash("Import failed. The file was not applied.", "danger")
        return redirect(url_for("audits.import_audits"))
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    log_event(
        "XLSX_IMPORT_COMPLETED",
        f"Audit Excel import: total {counts.get('total', 0)}, "
        f"created {summary['created']}, updated {summary['updated']}.",
    )
    flash(
        f"Import summary — Rows: {counts.get('total', 0)}. "
        f"Created: {summary['created']}. Updated: {summary['updated']}. "
        f"Skipped (errors): {len(errors)}.",
        "success",
    )
    return redirect(url_for("audits.list_audits"))
