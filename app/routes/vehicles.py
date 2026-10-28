"""Vehicle fleet management blueprint.

Scope:
- Layer 2: Excel import (upload -> preview -> confirm).
- Layer 3: list, detail, add/edit, archive, file upload per doc.
- Layer 4 (next): transport dashboard, role-based redirect.
"""
import os
import secrets
from datetime import date

from flask import (Blueprint, render_template, redirect, url_for, flash,
                   current_app, request, abort, send_from_directory)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    Factory, Vehicle, VehicleDocument, VehicleDocumentVersion,
    VEHICLE_DOC_TYPES, VEHICLE_DOC_LABELS,
)
from app.forms import VehicleForm, VehicleDocumentForm, VehicleDocumentRenewForm
from app.utils import (
    roles_required, compute_status, ALL_STATUSES, log_event, log_record, bst_today,
    EXPIRING_STATUSES, STATUS_VALID, STATUS_EXPIRED, ATTENTION_STATUSES,
    resolve_vehicle_alert_recipients, send_manual_vehicle_doc_alert,
    valid_email,
)
from app.models import AlertDispatch

vehicles_bp = Blueprint("vehicles", __name__, url_prefix="/vehicles")

# Who may do what. Vehicle Documents are a Transport-oriented module; the
# Transport roles own the data lifecycle. Factory Admin and Compliance
# Manager get read-only visibility so they can send manual email alerts
# (see ALERT_ROLES below).
VIEW_ROLES = (
    "Super Admin", "Administration", "Transport Manager", "Transport User",
    "Factory Admin", "Compliance Manager",
)
MANAGE_ROLES = ("Super Admin", "Administration", "Transport Manager", "Transport User")
DELETE_ROLES = ("Super Admin", "Administration", "Transport Manager")
# Bulk (multi-select) delete is Super Admin only. Other DELETE_ROLES delete
# one vehicle at a time.
BULK_DELETE_ROLES = ("Super Admin",)
IMPORT_ROLES = ("Super Admin", "Administration", "Transport Manager")
# Manual email alert roles. Transport User is deliberately excluded here
# (they view the fleet but cannot send alerts); Factory Admin and Compliance
# Manager are allowed per Transport policy.
ALERT_ROLES = (
    "Super Admin", "Administration",
    "Factory Admin", "Compliance Manager", "Transport Manager",
)
# Documents treated as "urgent" on the dashboard Urgent Renewals panel and by
# the list page's `urgent=1` "See all" filter. Defined once so both agree.
URGENT_STATUSES = (STATUS_EXPIRED, "Expiring in 7 Days", "Expiring in 30 Days")


# --- Scope helpers ----------------------------------------------------------

def _can_view(user):
    return user.role.name in VIEW_ROLES


def _apply_vehicle_scope(query):
    """Transport roles see the whole fleet; everyone else allowed in VIEW_ROLES
    (Super Admin, Administration) sees everything too. Factory-scoped RBAC
    isn't applied: the fleet is a centralized asset list."""
    return query


def _get_vehicle_or_404(vehicle_id):
    vehicle = Vehicle.query.options(
        selectinload(Vehicle.documents).selectinload(VehicleDocument.file_versions),
        joinedload(Vehicle.factory),
        joinedload(Vehicle.company),
    ).filter(Vehicle.id == vehicle_id).first()
    if vehicle is None:
        abort(404)
    return vehicle


def _set_factory_choices(form):
    factories = Factory.query.order_by(Factory.name).all()
    form.factory_id.choices = [(0, "-- None --")] + [(f.id, f.name) for f in factories]


def _factory_id_or_none(value):
    if not value:
        return None
    return int(value)


def _imports_dir():
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], "_vehicle_imports")
    os.makedirs(path, exist_ok=True)
    return path


def _save_vehicle_doc_file(file_storage, vehicle, doc_type, version_no):
    """Validate + store an uploaded file. Returns the forward-slash relative
    path stored on VehicleDocumentVersion."""
    original = secure_filename(file_storage.filename or "")
    if not original or "." not in original:
        raise ValueError("Invalid file name.")
    ext = original.rsplit(".", 1)[1].lower()
    if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
        raise ValueError("File type is not allowed.")
    subfolder = f"vehicles/vehicle_{vehicle.id}"
    unique_name = f"{doc_type}_v{version_no}_{original}"
    relative_path = f"{subfolder}/{unique_name}"
    folder_path = os.path.join(current_app.config["UPLOAD_FOLDER"], subfolder)
    os.makedirs(folder_path, exist_ok=True)
    file_storage.stream.seek(0)
    file_storage.save(os.path.join(current_app.config["UPLOAD_FOLDER"], relative_path))
    return relative_path


# --- List -------------------------------------------------------------------

def _apply_filters(query):
    search = (request.args.get("search") or "").strip()
    factory_id = request.args.get("factory_id", type=int)
    office = (request.args.get("office") or "").strip()
    doc_type = request.args.get("doc_type", "")
    status = request.args.get("status", "")
    missing_only = request.args.get("missing") == "1"

    if search:
        like = f"%{search}%"
        norm = Vehicle.normalize_reg_no(search)
        query = query.filter(db.or_(
            Vehicle.registration_no.ilike(like),
            Vehicle.reg_no_normalized.ilike(f"%{norm}%"),
            Vehicle.vehicle_type.ilike(like),
            Vehicle.owner_name_raw.ilike(like),
        ))
    if factory_id:
        query = query.filter(Vehicle.factory_id == factory_id)
    if office:
        query = query.filter(Vehicle.office_label == office)
    if doc_type and doc_type in VEHICLE_DOC_TYPES:
        if status:
            query = query.filter(Vehicle.documents.any(
                db.and_(VehicleDocument.doc_type == doc_type,
                        VehicleDocument.status == status,
                        VehicleDocument.is_active.is_(True))
            ))
        else:
            query = query.filter(Vehicle.documents.any(
                db.and_(VehicleDocument.doc_type == doc_type,
                        VehicleDocument.is_active.is_(True))
            ))
    elif status:
        query = query.filter(Vehicle.documents.any(
            db.and_(VehicleDocument.status == status,
                    VehicleDocument.is_active.is_(True))
        ))
    if missing_only:
        # Vehicles missing an active Tax Token (the always-required doc).
        subq = db.session.query(VehicleDocument.vehicle_id).filter(
            VehicleDocument.doc_type == "tax_token",
            VehicleDocument.is_active.is_(True),
        )
        query = query.filter(~Vehicle.id.in_(subq))
    if request.args.get("urgent") == "1":
        # "See all" from the dashboard Urgent Renewals panel: vehicles with any
        # active document that is Expired or due within 30 days.
        query = query.filter(Vehicle.documents.any(
            db.and_(VehicleDocument.is_active.is_(True),
                    VehicleDocument.status.in_(URGENT_STATUSES))
        ))
    return query


@vehicles_bp.route("/")
@login_required
@roles_required(*VIEW_ROLES)
def list_vehicles():
    show_archived = request.args.get("archived") == "1"
    query = Vehicle.query.options(
        joinedload(Vehicle.factory),
        selectinload(Vehicle.documents),
    ).filter(Vehicle.is_active.is_(not show_archived))
    query = _apply_filters(_apply_vehicle_scope(query))
    query = query.order_by(Vehicle.reg_no_normalized)

    page = request.args.get("page", 1, type=int)
    pagination = query.paginate(page=page, per_page=20, error_out=False)

    factories = Factory.query.order_by(Factory.name).all()
    office_rows = db.session.query(Vehicle.office_label).filter(
        Vehicle.office_label.isnot(None), Vehicle.office_label != ""
    ).distinct().order_by(Vehicle.office_label).all()
    offices = [row[0] for row in office_rows]

    return render_template(
        "vehicles/list.html",
        vehicles=pagination.items,
        pagination=pagination,
        factories=factories,
        offices=offices,
        doc_types=VEHICLE_DOC_TYPES,
        doc_type_labels=VEHICLE_DOC_LABELS,
        all_statuses=ALL_STATUSES,
        selected_search=request.args.get("search", "").strip(),
        selected_factory_id=request.args.get("factory_id", type=int),
        selected_office=request.args.get("office", "").strip(),
        selected_doc_type=request.args.get("doc_type", ""),
        selected_status=request.args.get("status", ""),
        missing_only=request.args.get("missing") == "1",
        urgent_only=request.args.get("urgent") == "1",
        show_archived=show_archived,
        can_manage=current_user.role.name in MANAGE_ROLES,
        can_delete=current_user.role.name in DELETE_ROLES,
        can_bulk_delete=current_user.role.name in BULK_DELETE_ROLES,
        can_import=current_user.role.name in IMPORT_ROLES,
        can_alert=current_user.role.name in ALERT_ROLES,
        today=bst_today(),
    )


# --- Detail -----------------------------------------------------------------

@vehicles_bp.route("/<int:vehicle_id>")
@login_required
@roles_required(*VIEW_ROLES)
def view_vehicle(vehicle_id):
    vehicle = _get_vehicle_or_404(vehicle_id)
    # Build a doc-type -> doc map so the template can show one card per type,
    # including missing types with a "Not recorded" placeholder.
    docs_by_type = {d.doc_type: d for d in vehicle.documents if d.is_active}
    doc_rows = []
    for t in VEHICLE_DOC_TYPES:
        doc = docs_by_type.get(t)
        doc_rows.append({
            "doc_type": t,
            "label": VEHICLE_DOC_LABELS[t],
            "doc": doc,
            "days": None if (not doc or doc.no_expiry or not doc.expiry_date)
                    else (doc.expiry_date - bst_today()).days,
        })
    missing_required = vehicle.missing_required()
    return render_template(
        "vehicles/detail.html",
        vehicle=vehicle,
        doc_rows=doc_rows,
        missing_required=missing_required,
        doc_type_labels=VEHICLE_DOC_LABELS,
        can_manage=current_user.role.name in MANAGE_ROLES,
        can_delete=current_user.role.name in DELETE_ROLES,
        can_alert=current_user.role.name in ALERT_ROLES,
        today=bst_today(),
    )


# --- Add / Edit vehicle (not its docs) --------------------------------------

@vehicles_bp.route("/add", methods=["GET", "POST"])
@login_required
@roles_required(*MANAGE_ROLES)
def add_vehicle():
    form = VehicleForm()
    _set_factory_choices(form)
    if form.validate_on_submit():
        normalized = Vehicle.normalize_reg_no(form.registration_no.data)
        if Vehicle.query.filter_by(reg_no_normalized=normalized).first():
            flash("A vehicle with that registration number already exists.", "danger")
        else:
            vehicle = Vehicle()
            vehicle.set_registration(form.registration_no.data)
            vehicle.vehicle_type = form.vehicle_type.data or None
            factory_id = _factory_id_or_none(form.factory_id.data)
            vehicle.factory_id = factory_id
            if factory_id:
                factory = db.session.get(Factory, factory_id)
                vehicle.company_id = factory.company_id if factory else None
            vehicle.office_label = form.office_label.data or None
            vehicle.owner_name_raw = form.owner_name_raw.data or None
            vehicle.created_by = current_user.id
            db.session.add(vehicle)
            db.session.commit()
            log_record("created", "Vehicle", f"{vehicle.registration_no!r} (id {vehicle.id})")
            flash("Vehicle added.", "success")
            return redirect(url_for("vehicles.view_vehicle", vehicle_id=vehicle.id))
    return render_template("vehicles/form.html", form=form, title="Add Vehicle")


@vehicles_bp.route("/<int:vehicle_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required(*MANAGE_ROLES)
def edit_vehicle(vehicle_id):
    vehicle = _get_vehicle_or_404(vehicle_id)
    form = VehicleForm(obj=vehicle)
    _set_factory_choices(form)
    if request.method == "GET":
        form.factory_id.data = vehicle.factory_id or 0
    if form.validate_on_submit():
        normalized = Vehicle.normalize_reg_no(form.registration_no.data)
        clash = Vehicle.query.filter(
            Vehicle.reg_no_normalized == normalized,
            Vehicle.id != vehicle.id,
        ).first()
        if clash:
            flash("Another vehicle already has that registration number.", "danger")
        else:
            vehicle.set_registration(form.registration_no.data)
            vehicle.vehicle_type = form.vehicle_type.data or None
            factory_id = _factory_id_or_none(form.factory_id.data)
            vehicle.factory_id = factory_id
            if factory_id:
                factory = db.session.get(Factory, factory_id)
                vehicle.company_id = factory.company_id if factory else None
            else:
                vehicle.company_id = None
            vehicle.office_label = form.office_label.data or None
            vehicle.owner_name_raw = form.owner_name_raw.data or None
            db.session.commit()
            log_record("updated", "Vehicle", f"{vehicle.registration_no!r} (id {vehicle.id})")
            flash("Vehicle updated.", "success")
            return redirect(url_for("vehicles.view_vehicle", vehicle_id=vehicle.id))
    return render_template("vehicles/form.html", form=form, title="Edit Vehicle", vehicle=vehicle)


# --- Archive / restore ------------------------------------------------------

@vehicles_bp.route("/<int:vehicle_id>/archive", methods=["POST"])
@login_required
@roles_required(*DELETE_ROLES)
def archive_vehicle(vehicle_id):
    vehicle = _get_vehicle_or_404(vehicle_id)
    vehicle.is_active = not vehicle.is_active
    db.session.commit()
    verb = "restored" if vehicle.is_active else "archived"
    flash(f"Vehicle {verb}.", "success")
    return redirect(url_for("vehicles.view_vehicle", vehicle_id=vehicle.id))


# --- Permanent delete -------------------------------------------------------

def _delete_vehicle_record(vehicle):
    """Remove a vehicle, its documents, file versions, stored files, and alert
    dispatches. Returns (registration_no, id). Does not commit."""
    for doc in list(vehicle.documents or []):
        for fv in list(doc.file_versions or []):
            safe = (fv.file_path or "").replace("\\", "/")
            if safe.startswith("vehicles/") and ".." not in safe.split("/"):
                full = os.path.join(current_app.config["UPLOAD_FOLDER"], safe)
                try:
                    if os.path.isfile(full):
                        os.remove(full)
                except OSError:
                    pass
        AlertDispatch.query.filter_by(record_type="vehicle", record_id=doc.id).delete(
            synchronize_session=False
        )
    reg = vehicle.registration_no
    vid = vehicle.id
    db.session.delete(vehicle)  # cascades to documents + versions
    return reg, vid


@vehicles_bp.route("/<int:vehicle_id>/delete", methods=["GET", "POST"])
@login_required
@roles_required(*DELETE_ROLES)
def delete_vehicle(vehicle_id):
    """Permanently remove a vehicle and all its documents/files."""
    vehicle = _get_vehicle_or_404(vehicle_id)
    doc_count = len([d for d in vehicle.documents])
    if request.method == "POST":
        typed = (request.form.get("confirm_reg") or "").strip()
        if typed != (vehicle.registration_no or ""):
            flash("Type the registration number exactly to confirm deletion.", "danger")
        else:
            reg, vid = _delete_vehicle_record(vehicle)
            db.session.commit()
            log_record("deleted", "Vehicle", f"{reg!r} (id {vid})")
            flash(f"Deleted vehicle {reg} and its documents.", "success")
            return redirect(url_for("vehicles.list_vehicles"))
    return render_template("vehicles/delete.html", vehicle=vehicle, doc_count=doc_count)


@vehicles_bp.route("/bulk-delete", methods=["POST"])
@login_required
@roles_required(*BULK_DELETE_ROLES)
def bulk_delete_vehicles():
    """Permanently delete several vehicles at once. Super Admin only."""
    ids = []
    for raw in request.form.getlist("ids"):
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not ids:
        flash("Select at least one vehicle to delete.", "warning")
        return redirect(url_for("vehicles.list_vehicles"))
    vehicles = Vehicle.query.options(
        selectinload(Vehicle.documents).selectinload(VehicleDocument.file_versions)
    ).filter(Vehicle.id.in_(ids)).all()
    deleted = []
    for vehicle in vehicles:
        reg, vid = _delete_vehicle_record(vehicle)
        deleted.append(f"{reg} (id {vid})")
    db.session.commit()
    if deleted:
        log_record("bulk_deleted", "Vehicle",
                   f"{len(deleted)} record(s): " + "; ".join(deleted[:50]))
        flash(f"Deleted {len(deleted)} vehicle(s) and their documents.", "success")
    else:
        flash("No vehicles were deleted.", "warning")
    return redirect(url_for("vehicles.list_vehicles"))


# --- Edit one document (dates / note / renewal_status / new file) -----------

@vehicles_bp.route("/<int:vehicle_id>/docs/<doc_type>/edit", methods=["GET", "POST"])
@login_required
@roles_required(*MANAGE_ROLES)
def edit_document(vehicle_id, doc_type):
    if doc_type not in VEHICLE_DOC_TYPES:
        abort(404)
    vehicle = _get_vehicle_or_404(vehicle_id)
    doc = next((d for d in vehicle.documents if d.doc_type == doc_type and d.is_active), None)
    is_new = doc is None
    form = VehicleDocumentForm(obj=doc) if doc else VehicleDocumentForm()

    if form.validate_on_submit():
        if is_new:
            doc = VehicleDocument(vehicle_id=vehicle.id, doc_type=doc_type,
                                  created_by=current_user.id)
            db.session.add(doc)
        doc.doc_number = form.doc_number.data or None
        doc.issue_date = form.issue_date.data
        doc.expiry_date = form.expiry_date.data
        doc.no_expiry = bool(form.no_expiry.data) or (
            form.expiry_date.data is None and bool(form.note.data)
        )
        doc.note = form.note.data or None
        doc.renewal_status = form.renewal_status.data or "Not Started"
        doc.status = compute_status(doc.expiry_date, doc.no_expiry)
        db.session.flush()

        if form.file.data:
            try:
                next_v = (max((v.version_no for v in doc.file_versions), default=0) + 1)
                rel = _save_vehicle_doc_file(form.file.data, vehicle, doc_type, next_v)
            except ValueError as exc:
                flash(str(exc), "danger")
            except OSError:
                flash("The file could not be stored.", "warning")
            else:
                db.session.add(VehicleDocumentVersion(
                    vehicle_document_id=doc.id,
                    version_no=next_v,
                    file_path=rel,
                    issue_date=doc.issue_date,
                    expiry_date=doc.expiry_date,
                    note=(doc.note or "")[:300] or None,
                    uploaded_by=current_user.id,
                ))

        db.session.commit()
        flash(f"{VEHICLE_DOC_LABELS[doc_type]} saved.", "success")
        return redirect(url_for("vehicles.view_vehicle", vehicle_id=vehicle.id))

    return render_template(
        "vehicles/doc_form.html",
        form=form,
        vehicle=vehicle,
        doc_type=doc_type,
        doc_type_label=VEHICLE_DOC_LABELS[doc_type],
        doc=doc,
        is_new=is_new,
    )


# --- Renew: new version with new dates --------------------------------------

@vehicles_bp.route("/<int:vehicle_id>/docs/<doc_type>/renew", methods=["GET", "POST"])
@login_required
@roles_required(*MANAGE_ROLES)
def renew_document(vehicle_id, doc_type):
    if doc_type not in VEHICLE_DOC_TYPES:
        abort(404)
    vehicle = _get_vehicle_or_404(vehicle_id)
    doc = next((d for d in vehicle.documents if d.doc_type == doc_type and d.is_active), None)
    if doc is None:
        flash("Add the document first before creating a renewal.", "warning")
        return redirect(url_for("vehicles.edit_document", vehicle_id=vehicle.id, doc_type=doc_type))

    form = VehicleDocumentRenewForm()
    if form.validate_on_submit():
        doc.issue_date = form.issue_date.data
        doc.expiry_date = form.expiry_date.data
        doc.no_expiry = bool(form.no_expiry.data)
        if form.doc_number.data:
            doc.doc_number = form.doc_number.data
        if form.note.data:
            doc.note = form.note.data
        doc.renewal_status = "Completed"
        doc.status = compute_status(doc.expiry_date, doc.no_expiry)
        if form.file.data:
            try:
                next_v = (max((v.version_no for v in doc.file_versions), default=0) + 1)
                rel = _save_vehicle_doc_file(form.file.data, vehicle, doc_type, next_v)
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("vehicles.renew_document",
                                        vehicle_id=vehicle.id, doc_type=doc_type))
            db.session.add(VehicleDocumentVersion(
                vehicle_document_id=doc.id,
                version_no=next_v,
                file_path=rel,
                issue_date=doc.issue_date,
                expiry_date=doc.expiry_date,
                note=(doc.note or "")[:300] or None,
                uploaded_by=current_user.id,
            ))
        db.session.commit()
        flash(f"New {VEHICLE_DOC_LABELS[doc_type]} version saved.", "success")
        return redirect(url_for("vehicles.view_vehicle", vehicle_id=vehicle.id))

    return render_template(
        "vehicles/doc_renew.html",
        form=form, vehicle=vehicle, doc=doc,
        doc_type=doc_type, doc_type_label=VEHICLE_DOC_LABELS[doc_type],
    )


# --- Transport dashboard ----------------------------------------------------

@vehicles_bp.route("/dashboard")
@login_required
@roles_required(*VIEW_ROLES)
def dashboard():
    from app.utils import recalculate_all_statuses
    recalculate_all_statuses()

    today = bst_today()
    active_v = Vehicle.query.filter(Vehicle.is_active.is_(True))
    total_vehicles = active_v.count()

    # --- Document counts by status ---
    doc_q = db.session.query(VehicleDocument.status, db.func.count(VehicleDocument.id)) \
        .join(Vehicle, VehicleDocument.vehicle_id == Vehicle.id) \
        .filter(Vehicle.is_active.is_(True), VehicleDocument.is_active.is_(True)) \
        .group_by(VehicleDocument.status)
    status_counts = {status: 0 for status in ALL_STATUSES}
    total_docs = 0
    for status, count in doc_q.all():
        status_counts[status] = count
        total_docs += count

    expired_docs = status_counts.get(STATUS_EXPIRED, 0)
    expiring_7 = status_counts.get("Expiring in 7 Days", 0)
    expiring_30 = status_counts.get("Expiring in 30 Days", 0)
    expiring_60 = status_counts.get("Expiring in 60 Days", 0)
    expiring_90 = status_counts.get("Expiring in 90 Days", 0)

    # --- Compliant vehicles: no attention-grade doc ---
    attention_vehicle_ids = {
        row[0] for row in db.session.query(VehicleDocument.vehicle_id)
        .join(Vehicle, VehicleDocument.vehicle_id == Vehicle.id)
        .filter(Vehicle.is_active.is_(True),
                VehicleDocument.is_active.is_(True),
                VehicleDocument.status.in_(ATTENTION_STATUSES))
        .distinct()
    }
    compliant_vehicles = total_vehicles - len(attention_vehicle_ids)

    # --- Missing required (no active tax_token) ---
    has_tax_token = {
        row[0] for row in db.session.query(VehicleDocument.vehicle_id)
        .join(Vehicle, VehicleDocument.vehicle_id == Vehicle.id)
        .filter(Vehicle.is_active.is_(True),
                VehicleDocument.doc_type == "tax_token",
                VehicleDocument.is_active.is_(True))
        .distinct()
    }
    missing_required = total_vehicles - len(has_tax_token)

    # --- Urgent renewals (Expired, 7d, 30d), sorted by expiry ---
    # Expiry ascending puts the most overdue first, then the nearest upcoming
    # dates. We show up to 15 rows in a scrollable panel; "See all" on the
    # dashboard links to the list filtered by urgent=1 for the full set.
    critical_statuses = list(URGENT_STATUSES)
    urgent_base = (
        db.session.query(VehicleDocument, Vehicle)
        .join(Vehicle, VehicleDocument.vehicle_id == Vehicle.id)
        .filter(Vehicle.is_active.is_(True),
                VehicleDocument.is_active.is_(True),
                VehicleDocument.status.in_(critical_statuses),
                VehicleDocument.expiry_date.isnot(None))
    )
    urgent_total = urgent_base.count()
    urgent_rows = (
        urgent_base.options(joinedload(Vehicle.factory))
        .order_by(VehicleDocument.expiry_date.asc())
        .limit(15).all()
    )
    urgent = []
    for doc, veh in urgent_rows:
        days = (doc.expiry_date - today).days if doc.expiry_date else None
        urgent.append({
            "vehicle": veh,
            "doc_type": doc.doc_type,
            "doc_label": VEHICLE_DOC_LABELS[doc.doc_type],
            "expiry": doc.expiry_date,
            "days": days,
            "status": doc.status,
        })

    # --- Owner / office breakdown ---
    # Count docs per factory grouped by bucket (Valid / Attention / Expired).
    breakdown_rows = (
        db.session.query(
            Vehicle.factory_id, Factory.name,
            VehicleDocument.status, db.func.count(VehicleDocument.id),
        )
        .join(Vehicle, VehicleDocument.vehicle_id == Vehicle.id)
        .outerjoin(Factory, Vehicle.factory_id == Factory.id)
        .filter(Vehicle.is_active.is_(True), VehicleDocument.is_active.is_(True))
        .group_by(Vehicle.factory_id, Factory.name, VehicleDocument.status)
        .all()
    )
    breakdown = {}
    for factory_id, name, status, count in breakdown_rows:
        # Key by factory_id so each owner row can link to its filtered list.
        # None key = unmapped / personal owners (no factory to filter on).
        row = breakdown.setdefault(factory_id, {
            "name": name or "— Unmapped / Personal —",
            "factory_id": factory_id,
            "total": 0, "valid": 0, "attention": 0, "expired": 0,
        })
        row["total"] += count
        if status == STATUS_EXPIRED:
            row["expired"] += count
        elif status in EXPIRING_STATUSES:
            row["attention"] += count
        else:
            row["valid"] += count
    breakdown_list = sorted(breakdown.values(), key=lambda r: -r["total"])

    return render_template(
        "vehicles/dashboard.html",
        total_vehicles=total_vehicles,
        total_docs=total_docs,
        compliant_vehicles=compliant_vehicles,
        expired_docs=expired_docs,
        expiring_7=expiring_7,
        expiring_30=expiring_30,
        expiring_60=expiring_60,
        expiring_90=expiring_90,
        missing_required=missing_required,
        urgent=urgent,
        urgent_total=urgent_total,
        breakdown=breakdown_list,
        today=today,
        can_alert=current_user.role.name in ALERT_ROLES,
    )


def vehicle_dashboard_overview(attention_limit=12):
    """Fleet-wide vehicle-document stats for the main (Super Admin) dashboard's
    Vehicle Documents section.

    The vehicle module is a centralized asset list (see _apply_vehicle_scope),
    so this is intentionally not factory-scoped: it counts the whole active
    fleet exactly like vehicles.dashboard() above, which keeps the two views
    reconciled. Returns a plain dict the dashboard template renders.
    """
    status_q = (
        db.session.query(VehicleDocument.status, db.func.count(VehicleDocument.id))
        .join(Vehicle, VehicleDocument.vehicle_id == Vehicle.id)
        .filter(Vehicle.is_active.is_(True), VehicleDocument.is_active.is_(True))
        .group_by(VehicleDocument.status)
    )
    status_counts = {s: 0 for s in ALL_STATUSES}
    for status, count in status_q.all():
        status_counts[status] = status_counts.get(status, 0) + count

    total_vehicles = Vehicle.query.filter(Vehicle.is_active.is_(True)).count()

    attention_vehicle_ids = {
        row[0] for row in db.session.query(VehicleDocument.vehicle_id)
        .join(Vehicle, VehicleDocument.vehicle_id == Vehicle.id)
        .filter(Vehicle.is_active.is_(True),
                VehicleDocument.is_active.is_(True),
                VehicleDocument.status.in_(ATTENTION_STATUSES))
        .distinct()
    }
    compliant_vehicles = total_vehicles - len(attention_vehicle_ids)

    has_tax_token = {
        row[0] for row in db.session.query(VehicleDocument.vehicle_id)
        .join(Vehicle, VehicleDocument.vehicle_id == Vehicle.id)
        .filter(Vehicle.is_active.is_(True),
                VehicleDocument.doc_type == "tax_token",
                VehicleDocument.is_active.is_(True))
        .distinct()
    }
    missing_required = total_vehicles - len(has_tax_token)

    # Factory-wise document breakdown (total / compliant / expiring / expired).
    breakdown_rows = (
        db.session.query(
            Vehicle.factory_id, Factory.name,
            VehicleDocument.status, db.func.count(VehicleDocument.id),
        )
        .join(Vehicle, VehicleDocument.vehicle_id == Vehicle.id)
        .outerjoin(Factory, Vehicle.factory_id == Factory.id)
        .filter(Vehicle.is_active.is_(True), VehicleDocument.is_active.is_(True))
        .group_by(Vehicle.factory_id, Factory.name, VehicleDocument.status)
        .all()
    )
    breakdown = {}
    for factory_id, name, status, count in breakdown_rows:
        row = breakdown.setdefault(factory_id, {
            "name": name or "— Unmapped / Personal —",
            "total": 0, "compliant": 0, "expiring": 0, "expired": 0,
        })
        row["total"] += count
        if status == STATUS_EXPIRED:
            row["expired"] += count
        elif status in EXPIRING_STATUSES:
            row["expiring"] += count
        else:
            row["compliant"] += count
    factory_stats = sorted(breakdown.values(), key=lambda r: -r["total"])

    # Documents needing attention (expiring + expired), most overdue first.
    attn_rows = (
        db.session.query(VehicleDocument, Vehicle)
        .join(Vehicle, VehicleDocument.vehicle_id == Vehicle.id)
        .options(joinedload(Vehicle.factory).joinedload(Factory.company),
                 joinedload(Vehicle.company))
        .filter(Vehicle.is_active.is_(True),
                VehicleDocument.is_active.is_(True),
                VehicleDocument.status.in_(ATTENTION_STATUSES),
                VehicleDocument.expiry_date.isnot(None))
        .order_by(VehicleDocument.expiry_date.asc())
        .limit(attention_limit).all()
    )
    attention = []
    for doc, veh in attn_rows:
        if veh.company:
            owner = veh.company.name
        elif veh.factory and veh.factory.company:
            owner = veh.factory.company.name
        else:
            owner = veh.owner_name_raw or "—"
        attention.append({
            "registration": veh.registration_no,
            "owner": owner,
            "doc_label": VEHICLE_DOC_LABELS.get(doc.doc_type, doc.doc_type),
            "expiry": doc.expiry_date,
            "status": doc.status,
            "url": url_for("vehicles.view_vehicle", vehicle_id=veh.id),
        })

    return {
        "total_vehicles": total_vehicles,
        "compliant_vehicles": compliant_vehicles,
        "expired_docs": status_counts.get(STATUS_EXPIRED, 0),
        "missing_required": missing_required,
        "expiring_7": status_counts.get("Expiring in 7 Days", 0),
        "expiring_30": status_counts.get("Expiring in 30 Days", 0),
        "expiring_60": status_counts.get("Expiring in 60 Days", 0),
        "expiring_90": status_counts.get("Expiring in 90 Days", 0),
        "factory_stats": factory_stats,
        "attention": attention,
    }


# --- Manual Send Alert ------------------------------------------------------

@vehicles_bp.route("/<int:vehicle_id>/docs/<doc_type>/send-alert", methods=["GET", "POST"])
@login_required
@roles_required(*ALERT_ROLES)
def send_doc_alert(vehicle_id, doc_type):
    if doc_type not in VEHICLE_DOC_TYPES:
        abort(404)
    vehicle = _get_vehicle_or_404(vehicle_id)
    if not vehicle.is_active:
        flash("This vehicle is archived. Restore it before sending an alert.", "warning")
        return redirect(url_for("vehicles.view_vehicle", vehicle_id=vehicle.id))
    doc = next((d for d in vehicle.documents if d.doc_type == doc_type and d.is_active), None)
    if doc is None:
        flash("Add the document first before sending an alert.", "warning")
        return redirect(url_for("vehicles.view_vehicle", vehicle_id=vehicle.id))

    allowed = resolve_vehicle_alert_recipients(doc)
    allowed_user_ids = {row["user"].id for row in allowed if row["user"] is not None}
    allowed_group_emails = [row["email"] for row in allowed if row["user"] is None]

    if request.method == "POST":
        posted_user_ids = set()
        for raw in request.form.getlist("recipient_id"):
            try:
                posted_user_ids.add(int(raw))
            except (TypeError, ValueError):
                continue
        posted_group = {
            address.strip().lower()
            for address in request.form.getlist("group_email")
            if address and address.strip()
        }
        chosen_users = [
            row["user"] for row in allowed
            if row["user"] is not None and row["user"].id in posted_user_ids
        ]
        chosen_group = [
            addr for addr in allowed_group_emails
            if addr.lower() in posted_group
        ]
        if not chosen_users and not chosen_group:
            flash("Select at least one recipient for this alert.", "danger")
        else:
            sent, error, count = send_manual_vehicle_doc_alert(
                doc, chosen_users, group_emails=chosen_group,
            )
            if sent and not error:
                flash(f"Alert sent to {count} recipient(s).", "success")
                return redirect(url_for("vehicles.view_vehicle", vehicle_id=vehicle.id))
            if sent:
                flash("The alert was only partly sent. See Alert History.", "warning")
            else:
                flash("The alert could not be sent. See Alert History.", "danger")
            return redirect(url_for("vehicles.view_vehicle", vehicle_id=vehicle.id))

    return render_template(
        "vehicles/send_alert.html",
        vehicle=vehicle,
        doc=doc,
        doc_type_label=VEHICLE_DOC_LABELS[doc_type],
        allowed_users=[row["user"] for row in allowed if row["user"] is not None],
        allowed_group_emails=allowed_group_emails,
    )


# --- Download a stored file -------------------------------------------------

@vehicles_bp.route("/files/<path:relative_path>")
@login_required
@roles_required(*VIEW_ROLES)
def download_file(relative_path):
    """Only files under the vehicles subtree may be downloaded."""
    safe = relative_path.replace("\\", "/")
    if not safe.startswith("vehicles/") or ".." in safe.split("/"):
        abort(404)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], safe,
                               as_attachment=False)


@vehicles_bp.route("/import", methods=["GET", "POST"])
@login_required
@roles_required(*IMPORT_ROLES)
def import_vehicles():
    """Upload a vehicle workbook. Nothing is written until the next step."""
    from app.vehicle_import import parse_workbook, annotate_existing, preview_counts

    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Please choose an Excel (.xlsx) file.", "danger")
            return redirect(url_for("vehicles.import_vehicles"))
        if not file.filename.lower().endswith(".xlsx"):
            flash("Only .xlsx files are supported for import.", "danger")
            return redirect(url_for("vehicles.import_vehicles"))

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
            log_event(
                "XLSX_IMPORT_FAILED",
                f"Vehicle Excel preview failed: {type(exc).__name__}",
                level="ERROR",
            )
            flash("Could not read the Excel file. Use a valid .xlsx workbook "
                  "that matches the expected header row.", "danger")
            return redirect(url_for("vehicles.import_vehicles"))

        return render_template(
            "vehicles/import_preview.html",
            rows=rows,
            errors=errors,
            counts=counts,
            token=token,
            filename=file.filename,
            preview_rows=rows[:60],
        )

    return render_template("vehicles/import.html")


@vehicles_bp.route("/import/confirm", methods=["POST"])
@login_required
@roles_required(*IMPORT_ROLES)
def import_confirm():
    """Apply the previously-previewed workbook. The server re-parses the
    stored file, never trusting posted row data."""
    from app.vehicle_import import (
        parse_workbook, annotate_existing, preview_counts, apply_import,
        record_import_run,
    )

    token = request.form.get("token", "")
    if not token or not token.isalnum() or len(token) != 32:
        flash("Invalid import request.", "danger")
        return redirect(url_for("vehicles.import_vehicles"))

    temp_path = os.path.join(_imports_dir(), f"{token}.xlsx")
    if not os.path.exists(temp_path):
        flash("The uploaded file expired. Please upload again.", "warning")
        return redirect(url_for("vehicles.import_vehicles"))

    log_event("XLSX_IMPORT_STARTED", "Vehicle Excel import started.")
    filename = request.form.get("filename") or os.path.basename(temp_path)
    try:
        rows, errors = parse_workbook(temp_path)
        annotate_existing(rows)
        counts = preview_counts(rows)
        summary = apply_import(rows, user_id=current_user.id)
        record_import_run(filename, current_user.id, counts, summary, status="success")
    except Exception as exc:
        db.session.rollback()
        record_import_run(
            filename, current_user.id, {}, {},
            status="failed", error=f"{type(exc).__name__}: {exc}",
        )
        log_event(
            "XLSX_IMPORT_FAILED",
            f"Vehicle Excel import failed: {type(exc).__name__}",
            level="ERROR",
        )
        flash("Import failed. The file was not applied. Check the file and "
              "try again.", "danger")
        return redirect(url_for("vehicles.import_vehicles"))
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    log_event(
        "XLSX_IMPORT_COMPLETED",
        f"Vehicle Excel import: total {counts.get('total', 0)}, "
        f"new vehicles {summary['new_vehicles']}, "
        f"updated vehicles {summary['updated_vehicles']}, "
        f"new docs {summary['new_docs']}, "
        f"updated docs {summary['updated_docs']}, "
        f"skipped {summary['skipped']}.",
    )
    flash(
        f"Import summary — Rows: {counts.get('total', 0)}. "
        f"New vehicles: {summary['new_vehicles']}. "
        f"Updated vehicles: {summary['updated_vehicles']}. "
        f"New documents: {summary['new_docs']}. "
        f"Updated documents: {summary['updated_docs']}. "
        f"Skipped rows: {summary['skipped']}.",
        "success" if summary["skipped"] == 0 else "warning",
    )
    return redirect(url_for("vehicles.import_vehicles"))
