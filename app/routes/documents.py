import os
import secrets
import time
from datetime import date, datetime

from flask import (Blueprint, render_template, redirect, url_for, flash, current_app,
                   send_from_directory, jsonify, request, abort)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    Document, Factory, Department, DocumentVersion, User, OcrResult,
    ServiceCategory, RegulatoryAuthority, Service, AlertDispatch,
)
from app.forms import DocumentForm, DocumentRenewForm
from app.utils import (
    roles_required, compute_status, ALL_STATUSES, valid_email, log_event,
    explain_alert_recipients, format_sources, expiry_before_issue,
    matching_alert_users, send_manual_document_alert, department_group_emails,
)

documents_bp = Blueprint("documents", __name__, url_prefix="/documents")

# Factory certificates are not a Transport-role concern. Transport Manager
# and Transport User have their own module at /vehicles.
VIEW_ROLES = (
    "Super Admin", "Administration", "Factory Admin",
    "Compliance Manager", "Compliance User", "Compliance Viewer",
)
MANAGE_ROLES = (
    "Super Admin", "Administration", "Factory Admin",
    "Compliance Manager", "Compliance User",
)
DELETE_ROLES = (
    "Super Admin", "Administration", "Factory Admin", "Compliance Manager",
)

DOC_TYPE_SUGGESTIONS = [
    "Fire License", "Trade License", "Boiler Certificate", "Environmental Certificate",
    "Factory License", "Amfori BSCI", "WRAP", "Sedex", "Other",
]


# --- Scope helpers ----------------------------------------------------------

def get_accessible_factories():
    if current_user.can_access_all_factories():
        return Factory.query.order_by(Factory.name).all()
    if current_user.factory_id:
        return Factory.query.filter_by(id=current_user.factory_id).order_by(Factory.name).all()
    return []


def can_access_factory(factory_id):
    if current_user.can_access_all_factories():
        return True
    return current_user.factory_id == factory_id


def apply_factory_scope(query):
    if current_user.can_access_all_factories():
        return query
    if current_user.factory_id:
        return query.filter(Document.factory_id == current_user.factory_id)
    return query.filter(db.false())


def get_factory_department_choices(form):
    accessible_factories = get_accessible_factories()
    form.factory_id.choices = [(f.id, f.name) for f in accessible_factories]
    departments = Department.query.order_by(Department.name).all()
    form.department_id.choices = [(0, "-- None --")] + [(d.id, d.name) for d in departments]


def candidate_alert_users():
    """Active users a factory-scoped editor may assign as recipients."""
    users = User.query.filter_by(is_active=True).order_by(User.full_name, User.username).all()
    if current_user.can_access_all_factories():
        return users
    if current_user.factory_id:
        return [u for u in users if u.factory_id in (None, current_user.factory_id)]
    return []


def users_for_factory_department(factory_id, department_id):
    """Active users who belong to this factory and this department."""
    return matching_alert_users(factory_id, department_id)


def eligible_recipients(factory_id=None, department_id=None):
    """Matching users who also have a valid email."""
    return [user for user in users_for_factory_department(factory_id, department_id) if valid_email(user.email)]


def ineligible_recipients(factory_id=None, department_id=None):
    """Matching users shown as disabled because they have no usable email."""
    return [user for user in users_for_factory_department(factory_id, department_id) if not valid_email(user.email)]


def set_recipient_choices(form):
    """Accept any active emailed user at validation time. Scope is enforced on save."""
    users = User.query.filter_by(is_active=True).order_by(User.username).all()
    form.alert_recipients.choices = [
        (user.id, user.email or user.username) for user in users if valid_email(user.email)
    ]
    return {user.id for user in users if valid_email(user.email)}


def recipient_view(factory_id, department_id):
    return eligible_recipients(factory_id, department_id), ineligible_recipients(factory_id, department_id)


def department_group_emails_for_form(department_id):
    """Group mailbox addresses to show on the Add/Edit document form.

    Returns [] when no department is picked or the department has no group
    mailbox. These addresses are not stored on the document; they are
    informational and are routed by the department record itself (see
    department_group_emails in app/utils.py).
    """
    if not department_id:
        return []
    department = db.session.get(Department, department_id)
    if department is None:
        return []
    return department_group_emails(department)


def apply_recipients(document, form, eligible_ids):
    """Keep only selected users who belong to this document's factory and department."""
    allowed = {
        user.id for user in eligible_recipients(document.factory_id, document.department_id)
    }
    selected_ids = [
        uid for uid in (form.alert_recipients.data or [])
        if uid in eligible_ids and uid in allowed
    ]
    document.alert_recipients = User.query.filter(User.id.in_(selected_ids)).all() if selected_ids else []


REGULATORY_ROLES = ("Super Admin", "Administration")


def can_access_regulatory():
    """Only Super Admin and Administration may see or change a regulatory link."""
    return current_user.role.name in REGULATORY_ROLES


def set_regulatory_choices(form):
    if can_access_regulatory():
        categories = ServiceCategory.query.order_by(ServiceCategory.name).all()
        authorities = RegulatoryAuthority.query.order_by(RegulatoryAuthority.name).all()
        services = Service.query.order_by(Service.name).all()
        form.category_id.choices = [(0, "-- None --")] + [(row.id, row.name) for row in categories]
        form.authority_id.choices = [(0, "-- None --")] + [(row.id, row.name) for row in authorities]
        form.service_id.choices = [(0, "-- None --")] + [(row.id, row.name) for row in services]
    else:
        form.category_id.choices = [(0, "-- None --")]
        form.authority_id.choices = [(0, "-- None --")]
        form.service_id.choices = [(0, "-- None --")]


def regulatory_ids_from_form(form):
    """Posted regulatory ids are stored only for roles allowed to set them."""
    if not can_access_regulatory():
        return None, None, None
    category_id = form.category_id.data if form.category_id.data else None
    authority_id = form.authority_id.data if form.authority_id.data else None
    service_id = form.service_id.data if form.service_id.data else None
    if authority_id and db.session.get(RegulatoryAuthority, authority_id) is None:
        authority_id = None
    if service_id:
        service = db.session.get(Service, service_id)
        if service is None or (authority_id and service.authority_id != authority_id):
            service_id = None
    if category_id and db.session.get(ServiceCategory, category_id) is None:
        category_id = None
    if authority_id and category_id:
        office = db.session.get(RegulatoryAuthority, authority_id)
        if office is None or office.category_id != category_id:
            authority_id = None
            service_id = None
    return category_id, authority_id, service_id


# --- Safe file upload -------------------------------------------------------

def _save_version_file(file_storage, document, version_no):
    """Validate + store an uploaded file for a document version.
    Returns the forward-slash relative path stored in the DB.
    Raises ValueError on a disallowed file."""
    original = secure_filename(file_storage.filename or "")
    if not original or "." not in original:
        raise ValueError("Invalid file name.")
    ext = original.rsplit(".", 1)[1].lower()
    if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
        raise ValueError("File type is not allowed.")

    subfolder = f"factory_{document.factory_id}"
    # Generated storage name: user never controls the final filesystem path.
    unique_name = f"doc{document.id}_v{version_no}_{original}"
    relative_path = f"{subfolder}/{unique_name}"  # forward slash for send_from_directory
    folder_path = os.path.join(current_app.config["UPLOAD_FOLDER"], subfolder)
    os.makedirs(folder_path, exist_ok=True)
    filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], relative_path)
    file_storage.stream.seek(0)
    file_storage.save(filepath)
    return relative_path


def apply_document_filters(query):
    search = request.args.get("search", "").strip()
    factory_id = request.args.get("factory_id", type=int)
    department_name = request.args.get("department_name", "").strip()
    status = request.args.get("status", "")
    doc_type = request.args.get("doc_type", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

    if search:
        from app.ocr_service import normalize_bangla_digits
        like = f"%{search}%"
        norm = f"%{normalize_bangla_digits(search)}%"
        ocr_match = db.session.query(OcrResult.document_id).filter(
            OcrResult.document_id.isnot(None),
            db.or_(OcrResult.extracted_text.ilike(like), OcrResult.search_text.ilike(norm)),
        )
        query = query.filter(db.or_(
            Document.doc_name.ilike(like),
            Document.cert_no.ilike(like),
            Document.id.in_(ocr_match),
        ))
    if factory_id:
        query = query.filter(Document.factory_id == factory_id)
    if department_name:
        query = query.join(Department).filter(Department.name == department_name)
    if status:
        query = query.filter(Document.status == status)
    if doc_type:
        query = query.filter(Document.doc_type.ilike(f"%{doc_type}%"))
    if date_from:
        try:
            query = query.filter(Document.expiry_date >= date.fromisoformat(date_from))
        except ValueError:
            pass
    if date_to:
        try:
            query = query.filter(Document.expiry_date <= date.fromisoformat(date_to))
        except ValueError:
            pass
    return query


@documents_bp.route("/")
@login_required
@roles_required(*VIEW_ROLES)
def list_documents():
    show_archived = request.args.get("archived") == "1"
    query = Document.query.options(joinedload(Document.factory), joinedload(Document.department))
    query = apply_factory_scope(query)
    query = query.filter(Document.is_active.is_(not show_archived))

    page = request.args.get("page", 1, type=int)
    query = apply_document_filters(query).order_by(Document.expiry_date.is_(None), Document.expiry_date)
    pagination = query.paginate(page=page, per_page=10, error_out=False)

    factories = get_accessible_factories()
    department_names = sorted(d.name for d in Department.query.order_by(Department.name).all())

    return render_template(
        "documents/list.html",
        documents=pagination.items,
        pagination=pagination,
        factories=factories,
        department_names=department_names,
        search=request.args.get("search", "").strip(),
        selected_factory_id=request.args.get("factory_id", type=int),
        selected_department_name=request.args.get("department_name", "").strip(),
        selected_status=request.args.get("status", ""),
        selected_doc_type=request.args.get("doc_type", "").strip(),
        selected_date_from=request.args.get("date_from", "").strip(),
        selected_date_to=request.args.get("date_to", "").strip(),
        show_archived=show_archived,
        can_manage=current_user.role.name in MANAGE_ROLES,
        can_delete=current_user.role.name in DELETE_ROLES,
        all_statuses=ALL_STATUSES,
    )


def _department_on_factory(factory_id, department_id):
    """Return the department id, None when unset, or False when the id is unknown.

    factory_id is unused. Departments are not owned by a factory.
    """
    if not department_id:
        return None
    department = db.session.get(Department, department_id)
    if department is None:
        return False
    return department.id


def purge_extract_staging(instance_path, max_age_seconds=24 * 60 * 60):
    """Drop abandoned extract uploads. A JSON file with no document is removed immediately."""
    folder = os.path.join(instance_path, "ocr_staging")
    if not os.path.isdir(folder):
        return
    now = time.time()
    names = os.listdir(folder)
    for name in names:
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        stale = now - os.path.getmtime(path) > max_age_seconds
        if name.endswith(".json"):
            token = name[:-5]
            has_file = any(
                other.startswith(token + ".") and not other.endswith(".json") for other in names
            )
            if stale or not has_file:
                os.remove(path)
        elif stale:
            os.remove(path)


@documents_bp.route("/api/alert-users")
@login_required
def alert_users_for_scope():
    """Users and the department group mailbox for a factory + department pair.

    Returns the user list filtered by `user.factory_id == factory_id` and
    `user.department_id == department_id`, plus any group mailboxes set on
    the department. The group mailbox is a department-wide address and is
    deliberately not filtered by factory — the automated weekly and
    emergency alerts and the manual Send Alert page all include it for any
    factory that uses that department.
    """
    factory_id = request.args.get("factory_id", type=int)
    department_id = request.args.get("department_id", type=int)
    if factory_id and not can_access_factory(factory_id):
        abort(403)
    users_payload = []
    for user in users_for_factory_department(factory_id, department_id):
        users_payload.append({
            "id": user.id,
            "name": user.full_name or user.username,
            "email": user.email or "",
            "factory": user.factory.name if user.factory else "No factory",
            "department": user.department.name if user.department else "No department",
            "selectable": valid_email(user.email),
        })
    group_emails_payload = []
    if department_id:
        department = db.session.get(Department, department_id)
        if department is not None:
            for address in department_group_emails(department):
                group_emails_payload.append({
                    "email": address,
                    "department": department.name,
                })
    return jsonify({"users": users_payload, "group_emails": group_emails_payload})


@documents_bp.route("/api/departments/<int:factory_id>")
@login_required
def get_departments_by_factory(factory_id):
    """Departments are shared. The factory id is ignored."""
    departments = Department.query.order_by(Department.name).all()
    return jsonify([{"id": d.id, "name": d.name} for d in departments])


@documents_bp.route("/add", methods=["GET", "POST"])
@login_required
@roles_required(*MANAGE_ROLES)
def add_document():
    form = DocumentForm()
    get_factory_department_choices(form)
    eligible_ids = set_recipient_choices(form)
    set_regulatory_choices(form)

    if form.validate_on_submit():
        if not can_access_factory(form.factory_id.data):
            abort(403)

        no_expiry = bool(form.no_expiry.data)
        expiry_date = None if no_expiry else form.expiry_date.data
        department_id = _department_on_factory(form.factory_id.data, form.department_id.data)
        if department_id is False:
            flash("Choose a department from the list.", "danger")
        elif expiry_before_issue(form.issue_date.data, expiry_date):
            flash("Expiry date must be on or after the issue date.", "danger")
        else:
            category_id, authority_id, service_id = regulatory_ids_from_form(form)
            _save_new_document(form, department_id, category_id, authority_id, service_id, expiry_date, no_expiry, eligible_ids)
            return redirect(url_for("documents.list_documents"))

    eligible, ineligible = recipient_view(form.factory_id.data, form.department_id.data)
    return render_template("documents/form.html", form=form, title="Add Document",
                           doc_type_suggestions=DOC_TYPE_SUGGESTIONS,
                           eligible_recipients=eligible,
                           ineligible_recipients=ineligible,
                           group_emails=department_group_emails_for_form(form.department_id.data))


def _save_new_document(form, department_id, category_id, authority_id, service_id, expiry_date, no_expiry, eligible_ids):
    document = Document(
        factory_id=form.factory_id.data,
        department_id=department_id,
        category_id=category_id,
        authority_id=authority_id,
        service_id=service_id,
        doc_name=form.doc_name.data,
        doc_type=form.doc_type.data,
        cert_no=form.cert_no.data,
        issue_date=form.issue_date.data,
        expiry_date=expiry_date,
        no_expiry=no_expiry,
        issuing_authority=form.issuing_authority.data,
        responsible_person=form.responsible_person.data,
        renewal_status=form.renewal_status.data,
        remarks=form.remarks.data,
        status=compute_status(expiry_date, no_expiry),
        created_by=current_user.id,
    )
    db.session.add(document)
    apply_recipients(document, form, eligible_ids)
    db.session.commit()

    if form.file.data:
        try:
            relative_path = _save_version_file(form.file.data, document, 1)
        except ValueError as exc:
            flash(str(exc), "danger")
        except OSError:
            flash("Document saved, but the file could not be stored. Edit the document to upload the file again.", "warning")
        else:
            db.session.add(DocumentVersion(
                document_id=document.id, uploaded_by=current_user.id, version_no=1,
                file_path=relative_path, issue_date=form.issue_date.data, expiry_date=expiry_date,
            ))
            db.session.commit()

    flash("Document added successfully.", "success")


@documents_bp.route("/edit/<int:document_id>", methods=["GET", "POST"])
@login_required
@roles_required(*MANAGE_ROLES)
def edit_document(document_id):
    document = Document.query.get_or_404(document_id)
    if not can_access_factory(document.factory_id):
        abort(403)

    form = DocumentForm(obj=document)
    get_factory_department_choices(form)
    eligible_ids = set_recipient_choices(form)
    set_regulatory_choices(form)

    if request.method == "GET":
        if document.department_id is None:
            form.department_id.data = 0
        if document.category_id is None:
            form.category_id.data = 0
        if document.authority_id is None:
            form.authority_id.data = 0
        if document.service_id is None:
            form.service_id.data = 0
        form.alert_recipients.data = [u.id for u in document.alert_recipients]

    if form.validate_on_submit():
        if not can_access_factory(form.factory_id.data):
            abort(403)

        no_expiry = bool(form.no_expiry.data)
        expiry_date = None if no_expiry else form.expiry_date.data
        department_id = _department_on_factory(form.factory_id.data, form.department_id.data)
        if department_id is False:
            flash("Choose a department from the list.", "danger")
        elif expiry_before_issue(form.issue_date.data, expiry_date):
            flash("Expiry date must be on or after the issue date.", "danger")
        else:
            document.factory_id = form.factory_id.data
            if can_access_regulatory():
                document.category_id, document.authority_id, document.service_id = regulatory_ids_from_form(form)
            document.department_id = department_id
            document.doc_name = form.doc_name.data
            document.doc_type = form.doc_type.data
            document.cert_no = form.cert_no.data
            document.issue_date = form.issue_date.data
            document.expiry_date = expiry_date
            document.no_expiry = no_expiry
            document.issuing_authority = form.issuing_authority.data
            document.responsible_person = form.responsible_person.data
            document.renewal_status = form.renewal_status.data
            document.remarks = form.remarks.data
            document.status = compute_status(expiry_date, no_expiry)
            apply_recipients(document, form, eligible_ids)

            if form.file.data:
                _version_and_store(document, form.file.data, form.issue_date.data, expiry_date)

            db.session.commit()
            flash("Document updated successfully.", "success")
            return redirect(url_for("documents.list_documents"))

    eligible, ineligible = recipient_view(form.factory_id.data, form.department_id.data)
    return render_template("documents/form.html", form=form, title="Edit Document",
                           doc_type_suggestions=DOC_TYPE_SUGGESTIONS,
                           eligible_recipients=eligible,
                           ineligible_recipients=ineligible,
                           group_emails=department_group_emails_for_form(form.department_id.data))


def _version_and_store(document, file_storage, issue_date, expiry_date):
    """Create the next version safely, retrying on a version-number collision."""
    for attempt in range(3):
        next_version = DocumentVersion.query.filter_by(document_id=document.id).count() + 1 + attempt
        try:
            relative_path = _save_version_file(file_storage, document, next_version)
        except ValueError as e:
            flash(str(e), "danger")
            return
        except OSError:
            flash("Details updated, but the new file could not be stored. The previous version is unaffected.", "warning")
            return
        version = DocumentVersion(
            document_id=document.id, uploaded_by=current_user.id, version_no=next_version,
            file_path=relative_path, issue_date=issue_date, expiry_date=expiry_date,
        )
        db.session.add(version)
        try:
            db.session.flush()
            return
        except IntegrityError:
            db.session.rollback()
            document = db.session.get(Document, document.id)
            fp = os.path.join(current_app.config["UPLOAD_FOLDER"], relative_path)
            if os.path.exists(fp):
                os.remove(fp)
    flash("Another edit happened at the same moment and the file could not be versioned "
          "safely. Please upload the file again.", "warning")


@documents_bp.route("/renew/<int:document_id>", methods=["GET", "POST"])
@login_required
@roles_required(*MANAGE_ROLES)
def renew_document(document_id):
    document = Document.query.get_or_404(document_id)
    if not can_access_factory(document.factory_id):
        abort(403)

    form = DocumentRenewForm()
    if request.method == "GET":
        form.cert_no.data = document.cert_no

    if form.validate_on_submit():
        no_expiry = bool(form.no_expiry.data)
        expiry_date = None if no_expiry else form.expiry_date.data
        if expiry_before_issue(form.issue_date.data, expiry_date):
            flash("Expiry date must be on or after the issue date.", "danger")
            return render_template("documents/renew.html", form=form, document=document, title="Renew Document")

        document.issue_date = form.issue_date.data
        document.expiry_date = expiry_date
        document.no_expiry = no_expiry
        if form.cert_no.data:
            document.cert_no = form.cert_no.data
        if form.remarks.data:
            document.remarks = form.remarks.data
        document.status = compute_status(expiry_date, no_expiry)
        document.renewal_status = "Completed"

        if form.file.data:
            _version_and_store(document, form.file.data, form.issue_date.data, expiry_date)

        db.session.commit()
        flash("Document renewed. A new version has been created.", "success")
        return redirect(url_for("documents.list_documents"))

    return render_template("documents/renew.html", form=form, document=document, title="Renew Document")


@documents_bp.route("/archive/<int:document_id>", methods=["POST"])
@login_required
@roles_required(*DELETE_ROLES)
def archive_document(document_id):
    document = Document.query.get_or_404(document_id)
    if not can_access_factory(document.factory_id):
        abort(403)
    document.is_active = False
    db.session.commit()
    flash("Document archived. Its file versions are preserved.", "success")
    return redirect(url_for("documents.list_documents"))


@documents_bp.route("/restore/<int:document_id>", methods=["POST"])
@login_required
@roles_required(*DELETE_ROLES)
def restore_document(document_id):
    document = Document.query.get_or_404(document_id)
    if not can_access_factory(document.factory_id):
        abort(403)
    document.is_active = True
    db.session.commit()
    flash("Document restored.", "success")
    return redirect(url_for("documents.list_documents", archived="1"))


@documents_bp.route("/view/<int:document_id>")
@login_required
@roles_required(*VIEW_ROLES)
def view_document(document_id):
    document = Document.query.options(
        joinedload(Document.factory), joinedload(Document.department),
        joinedload(Document.category), joinedload(Document.authority),
        joinedload(Document.service),
    ).get_or_404(document_id)
    if not can_access_factory(document.factory_id):
        abort(403)
    versions = sorted(document.versions, key=lambda v: v.version_no, reverse=True)
    alert_plan = explain_alert_recipients(document, "document")
    return render_template("documents/detail.html", document=document, versions=versions,
                           can_manage=current_user.role.name in MANAGE_ROLES,
                           can_delete=current_user.role.name in DELETE_ROLES,
                           alert_plan=alert_plan, format_sources=format_sources)


def manual_alert_users(document):
    """Users in this document's factory and department who can be emailed."""
    return eligible_recipients(document.factory_id, document.department_id)


@documents_bp.route("/send-alert/<int:document_id>", methods=["GET", "POST"])
@login_required
@roles_required(*MANAGE_ROLES)
def send_document_alert(document_id):
    """Send this document's alert only to the users selected on the page."""
    document = Document.query.options(
        joinedload(Document.factory).joinedload(Factory.company),
        joinedload(Document.department),
        joinedload(Document.alert_recipients),
    ).get_or_404(document_id)
    if not can_access_factory(document.factory_id):
        abort(403)
    users = manual_alert_users(document)
    available_group_emails = department_group_emails(document.department)
    group_lookup = {address.lower(): address for address in available_group_emails}
    selected_ids = {user.id for user in (document.alert_recipients or [])}
    # By default the group mailbox is pre-ticked on first page load, matching
    # the "like other recipients" UX. On POST the browser only sends ticked
    # boxes, so an unticked group mailbox stays out.
    selected_group_keys = set(group_lookup)
    if request.method == "POST":
        posted_user_ids = set()
        for raw in request.form.getlist("recipient_id"):
            try:
                posted_user_ids.add(int(raw))
            except (TypeError, ValueError):
                continue
        posted_group_keys = {
            address.strip().lower()
            for address in request.form.getlist("group_email")
            if address and address.strip()
        }
        chosen_group_emails = [
            group_lookup[key] for key in posted_group_keys if key in group_lookup
        ]
        chosen = [user for user in users if user.id in posted_user_ids]
        if not chosen and not chosen_group_emails:
            if users or available_group_emails:
                flash("Select at least one recipient for this document.", "danger")
            else:
                flash("There is no valid recipient for this alert.", "danger")
            selected_ids = posted_user_ids
            selected_group_keys = posted_group_keys
        else:
            document.alert_recipients = chosen
            db.session.commit()
            sent, error, count = send_manual_document_alert(
                document, chosen, group_emails=chosen_group_emails,
            )
            if sent and not error:
                parts = []
                if chosen:
                    parts.append(", ".join(user.full_name or user.username for user in chosen))
                if chosen_group_emails:
                    parts.append("the department group mailbox")
                flash(
                    f"Alert for {document.doc_name} sent to {' and '.join(parts)}.",
                    "success",
                )
                return redirect(url_for("documents.view_document", document_id=document.id))
            if sent:
                flash("The alert was only partly sent. See Alert History.", "warning")
            else:
                flash("The alert could not be sent. See Alert History.", "danger")
            return redirect(url_for("documents.view_document", document_id=document.id))
    return render_template(
        "documents/send_alert.html",
        document=document,
        users=users,
        selected_ids=selected_ids,
        group_emails=available_group_emails,
        selected_group_keys=selected_group_keys,
    )


@documents_bp.route("/delete/<int:document_id>", methods=["GET", "POST"])
@login_required
@roles_required(*DELETE_ROLES)
def delete_document(document_id):
    """Permanently remove a document, its versions, and the stored files."""
    document = Document.query.options(joinedload(Document.versions)).get_or_404(document_id)
    if not can_access_factory(document.factory_id):
        abort(403)
    file_count = len(document.versions or [])
    if request.method == "POST":
        typed = (request.form.get("confirm_name") or "").strip()
        if typed != (document.doc_name or ""):
            flash("Type the document name exactly to confirm deletion.", "danger")
        else:
            for version in list(document.versions or []):
                _remove_stored_file(version.file_path)
            AlertDispatch.query.filter_by(record_type="document", record_id=document.id).delete(
                synchronize_session=False
            )
            name = document.doc_name
            document.alert_recipients = []
            db.session.delete(document)
            db.session.commit()
            flash(f"Deleted {name}. Its files were removed from storage.", "success")
            return redirect(url_for("documents.list_documents"))
    return render_template(
        "documents/delete.html",
        document=document,
        file_count=file_count,
    )


def _public_file_name(version):
    """Download name without the stored doc id and version prefix."""
    base = os.path.basename(version.file_path or "")
    prefix = f"doc{version.document_id}_v{version.version_no}_"
    if base.startswith(prefix) and len(base) > len(prefix):
        return base[len(prefix):]
    return base


@documents_bp.route("/download/<int:version_id>")
@login_required
@roles_required(*VIEW_ROLES)
def download_file(version_id):
    version = DocumentVersion.query.get_or_404(version_id)
    if not can_access_factory(version.document.factory_id):
        abort(403)
    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"], version.file_path,
        as_attachment=True, download_name=_public_file_name(version),
    )


@documents_bp.route("/file/<int:version_id>")
@login_required
@roles_required(*VIEW_ROLES)
def view_file(version_id):
    version = DocumentVersion.query.get_or_404(version_id)
    if not can_access_factory(version.document.factory_id):
        abort(403)
    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"], version.file_path,
        as_attachment=False, download_name=_public_file_name(version),
    )


def _remove_stored_file(relative_path):
    """Delete one uploaded file that lives inside the upload folder."""
    if not relative_path:
        return
    root = os.path.realpath(current_app.config["UPLOAD_FOLDER"])
    full = os.path.realpath(os.path.join(root, str(relative_path).replace("\\", "/")))
    try:
        if os.path.commonpath([root, full]) != root:
            return
    except ValueError:
        return
    if os.path.isfile(full):
        os.remove(full)


def _safe_stored_path(relative_path):
    from app.ocr_service import OcrError
    root = os.path.realpath(current_app.config["UPLOAD_FOLDER"])
    full = os.path.realpath(os.path.join(root, (relative_path or "").replace("\\", "/")))
    if os.path.commonpath([root, full]) != root:
        abort(404)
    if not os.path.isfile(full):
        raise OcrError("The uploaded file could not be found.")
    return full


def _ocr_extensions_ok(filename):
    ext = (filename or "").rsplit(".", 1)[-1].lower()
    return ext in {"pdf", "jpg", "jpeg", "png"}


def _mark_ocr_failed(version, message):
    row = version.ocr_result or OcrResult(
        document_id=version.document_id, document_version_id=version.id)
    row.ocr_status = "FAILED"
    row.error_message = (message or "OCR failed.")[:400]
    row.processed_at = datetime.utcnow()
    db.session.add(row)
    db.session.commit()
    log_event("OCR_FAILED",
              f"Document ID: {version.document_id}. Version ID: {version.id}. Failed.",
              level="ERROR")


def _run_ocr_for_version(version):
    from app.ocr_service import extract_document, suggestions_json
    path = _safe_stored_path(version.file_path)
    log_event("OCR_STARTED", f"Document ID: {version.document_id}. Version ID: {version.id}.")
    result = extract_document(path, current_app.config)
    row = version.ocr_result or OcrResult(
        document_id=version.document_id, document_version_id=version.id)
    row.extracted_text = result["extracted_text"]
    row.search_text = result["search_text"]
    row.language = result["language"]
    row.confidence = result["confidence"]
    row.suggested_fields = suggestions_json(result["suggested_fields"])
    row.ocr_status = "COMPLETED"
    row.error_message = None
    row.confirmed_at = None
    row.processed_at = result["processed_at"]
    row.processing_time = result["processing_time"]
    db.session.add(row)
    db.session.commit()
    log_event("OCR_COMPLETED",
              f"Document ID: {version.document_id}. Version ID: {version.id}. Language: {result['language']}.")
    return row


def _staging_dir():
    path = os.path.join(current_app.instance_path, "ocr_staging")
    os.makedirs(path, exist_ok=True)
    return path


@documents_bp.route("/ocr/<int:version_id>", methods=["POST"])
@login_required
@roles_required(*MANAGE_ROLES)
def run_ocr(version_id):
    from app.ocr_service import OcrUnavailable, OcrError
    version = DocumentVersion.query.get_or_404(version_id)
    if not can_access_factory(version.document.factory_id):
        abort(403)
    if not _ocr_extensions_ok(version.file_path):
        flash("OCR supports PDF, JPG, and PNG files only.", "warning")
        return redirect(url_for("documents.view_document", document_id=version.document_id))
    try:
        _run_ocr_for_version(version)
    except OcrUnavailable as exc:
        log_event("OCR_FAILED",
                  f"Document ID: {version.document_id}. Version ID: {version.id}. Engine unavailable.",
                  level="ERROR")
        flash(str(exc), "warning")
        return redirect(url_for("documents.view_document", document_id=version.document_id))
    except OcrError as exc:
        _mark_ocr_failed(version, str(exc))
        flash(str(exc), "danger")
        return redirect(url_for("documents.view_ocr", version_id=version.id))
    flash("OCR finished. Review the suggested text before saving any changes.", "success")
    return redirect(url_for("documents.view_ocr", version_id=version.id))


@documents_bp.route("/ocr/<int:version_id>/view", methods=["GET", "POST"])
@login_required
def view_ocr(version_id):
    from app.ocr_service import load_suggestions, normalize_bangla_digits, parse_confirmed_date
    version = DocumentVersion.query.get_or_404(version_id)
    document = version.document
    if not can_access_factory(document.factory_id):
        abort(403)
    row = version.ocr_result
    can_manage = current_user.role.name in MANAGE_ROLES
    if request.method == "POST":
        if not can_manage:
            abort(403)
        if not row or row.ocr_status != "COMPLETED":
            flash("There is no completed OCR result to confirm.", "warning")
            return redirect(url_for("documents.view_document", document_id=document.id))
        edited = request.form.get("extracted_text", "")
        row.extracted_text = edited
        row.search_text = normalize_bangla_digits(edited)
        applied = []
        skipped = []
        cert = (request.form.get("cert_no") or "").strip()
        if request.form.get("apply_cert") and cert:
            document.cert_no = cert
            applied.append("certificate number")
        for field, label in (("issue_date", "issue date"), ("expiry_date", "expiry date")):
            raw = (request.form.get(field) or "").strip()
            if not request.form.get(f"apply_{field}") or not raw:
                continue
            parsed = parse_confirmed_date(raw)
            if parsed is None:
                skipped.append(label)
                continue
            setattr(document, field, parsed)
            if field == "expiry_date":
                document.no_expiry = False
                document.status = compute_status(parsed, False)
            applied.append(label)
        authority = (request.form.get("issuing_authority") or "").strip()
        if request.form.get("apply_authority") and authority:
            document.issuing_authority = authority
            applied.append("issuing authority")
        row.confirmed_at = datetime.utcnow()
        db.session.commit()
        if applied:
            flash("Saved confirmed fields: " + ", ".join(applied) + ".", "success")
        else:
            flash("Extracted text saved. No structured fields were changed.", "info")
        if skipped:
            flash("These dates were not applied because the format is ambiguous. Enter them as 05 Jan 2026 or 2026-01-05: "
                  + ", ".join(skipped) + ".", "warning")
        return redirect(url_for("documents.view_document", document_id=document.id))
    return render_template(
        "documents/ocr.html",
        document=document,
        version=version,
        ocr=row,
        suggestions=load_suggestions(row.suggested_fields) if row else {},
        can_manage=can_manage,
    )


@documents_bp.route("/extract", methods=["POST"])
@login_required
@roles_required(*MANAGE_ROLES)
def extract_upload():
    """OCR a new upload before a document exists. Does not create a document."""
    import json
    from app.ocr_service import extract_document, OcrUnavailable, OcrError
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("Choose a PDF, JPG, or PNG file to extract.", "warning")
        return redirect(url_for("documents.add_document"))
    if not _ocr_extensions_ok(upload.filename):
        flash("OCR supports PDF, JPG, and PNG files only.", "warning")
        return redirect(url_for("documents.add_document"))
    token = secrets.token_hex(16)
    ext = upload.filename.rsplit(".", 1)[-1].lower()
    folder = _staging_dir()
    stored = os.path.join(folder, f"{token}.{ext}")
    upload.save(stored)
    log_event("OCR_STARTED", "New document upload extraction.")
    try:
        result = extract_document(stored, current_app.config)
    except (OcrUnavailable, OcrError) as exc:
        if os.path.exists(stored):
            os.remove(stored)
        log_event("OCR_FAILED", "New document upload extraction failed.", level="ERROR")
        flash(str(exc), "warning" if isinstance(exc, OcrUnavailable) else "danger")
        return redirect(url_for("documents.add_document"))
    meta = {
        "user_id": current_user.id,
        "original_name": upload.filename,
        "result": {
            "extracted_text": result["extracted_text"],
            "language": result["language"],
            "confidence": result["confidence"],
            "suggested_fields": result["suggested_fields"],
            "processing_time": result["processing_time"],
        },
    }
    with open(os.path.join(folder, f"{token}.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False)
    log_event("OCR_COMPLETED", f"New upload extraction. Language: {result['language']}.")
    return redirect(url_for("documents.extract_preview", token=token))


def _fill_document_form(form, suggestions):
    """Copy extracted document details into the confirm form."""
    suggestions = suggestions or {}
    cert = suggestions.get("cert_no") or suggestions.get("license_no") or suggestions.get("registration_number")
    form.cert_no.data = cert or ""
    form.issuing_authority.data = suggestions.get("issuing_authority") or ""
    form.doc_name.data = suggestions.get("doc_name") or suggestions.get("name") or ""
    doc_type = (suggestions.get("doc_type") or "").strip()
    if doc_type:
        canonical = next((item for item in DOC_TYPE_SUGGESTIONS if item.lower() == doc_type.lower()), doc_type)
        form.doc_type.data = canonical[:100]
    form.responsible_person.data = (suggestions.get("responsible_person") or "")[:150]
    for field_name in ("issue_date", "expiry_date"):
        hint = suggestions.get(field_name) or {}
        if hint.get("iso") and not hint.get("ambiguous"):
            getattr(form, field_name).data = date.fromisoformat(hint["iso"])
    _fill_org_suggestions(form, suggestions)
    _fill_regulatory_suggestions(form, suggestions)


def _fill_org_suggestions(form, suggestions):
    """Select a factory and a department independently when the names match."""
    company_name = (suggestions.get("company") or "").strip().lower()
    department_name = (suggestions.get("department") or "").strip().lower()
    if not company_name and not department_name:
        return
    factories = get_accessible_factories()
    if company_name:
        company_hits = [
            factory for factory in factories
            if (factory.company and factory.company.name.strip().lower() == company_name)
            or factory.name.strip().lower() == company_name
        ]
        if len(company_hits) == 1:
            form.factory_id.data = company_hits[0].id
    if department_name:
        matches = [
            department for department in Department.query.order_by(Department.name).all()
            if department.name.strip().lower() == department_name
        ]
        if len(matches) == 1:
            form.department_id.data = matches[0].id


def _fill_regulatory_suggestions(form, suggestions):
    if not can_access_regulatory():
        return
    authority_name = (suggestions.get("issuing_authority") or "").strip().lower()
    if not authority_name:
        return
    matches = [
        row for row in RegulatoryAuthority.query.all()
        if row.name.strip().lower() == authority_name
    ]
    if len(matches) != 1:
        return
    form.authority_id.data = matches[0].id
    form.category_id.data = matches[0].category_id


def _load_staging(token):
    import json
    if not token or not token.isalnum():
        abort(404)
    folder = _staging_dir()
    meta_path = os.path.join(folder, f"{token}.json")
    if not os.path.isfile(meta_path):
        abort(404)
    with open(meta_path, encoding="utf-8") as handle:
        meta = json.load(handle)
    if meta.get("user_id") != current_user.id and not current_user.can_access_all_factories():
        abort(403)
    return meta, meta_path


@documents_bp.route("/extract/<token>", methods=["GET", "POST"])
@login_required
@roles_required(*MANAGE_ROLES)
def extract_preview(token):
    from app.ocr_service import parse_confirmed_date, normalize_bangla_digits, suggestions_json
    meta, meta_path = _load_staging(token)
    folder = _staging_dir()
    stored = next((os.path.join(folder, name) for name in os.listdir(folder)
                   if name.startswith(token + ".") and not name.endswith(".json")), None)
    suggestions = meta["result"].get("suggested_fields") or {}
    form = DocumentForm()
    get_factory_department_choices(form)
    set_recipient_choices(form)
    set_regulatory_choices(form)
    if request.method == "GET":
        _fill_document_form(form, suggestions)
    if request.method == "POST" and request.form.get("action") == "cancel":
        if stored and os.path.exists(stored):
            os.remove(stored)
        if os.path.exists(meta_path):
            os.remove(meta_path)
        flash("Extraction cancelled. Nothing was saved.", "info")
        return redirect(url_for("documents.add_document"))
    if form.validate_on_submit():
        if not can_access_factory(form.factory_id.data):
            abort(403)
        no_expiry = bool(form.no_expiry.data)
        expiry_date = None if no_expiry else form.expiry_date.data
        issue_raw = (request.form.get("issue_date") or "").strip()
        expiry_raw = (request.form.get("expiry_date") or "").strip()
        if issue_raw and form.issue_date.data is None:
            parsed = parse_confirmed_date(issue_raw)
            if parsed:
                form.issue_date.data = parsed
            else:
                flash("Issue date was not applied because the format is ambiguous.", "warning")
        if expiry_raw and not no_expiry and form.expiry_date.data is None:
            parsed = parse_confirmed_date(expiry_raw)
            if parsed:
                expiry_date = parsed
            else:
                flash("Expiry date was not applied because the format is ambiguous.", "warning")
        department_id = _department_on_factory(form.factory_id.data, form.department_id.data)
        if department_id is False:
            flash("Choose a department from the list.", "danger")
            return render_template(
                "documents/extract_preview.html", form=form, token=token,
                result=meta["result"], suggestions=suggestions, doc_type_suggestions=DOC_TYPE_SUGGESTIONS,
            )
        if expiry_before_issue(form.issue_date.data, expiry_date):
            flash("Expiry date must be on or after the issue date.", "danger")
            return render_template(
                "documents/extract_preview.html", form=form, token=token,
                result=meta["result"], suggestions=suggestions, doc_type_suggestions=DOC_TYPE_SUGGESTIONS,
            )
        category_id, authority_id, service_id = regulatory_ids_from_form(form)
        document = Document(
            factory_id=form.factory_id.data,
            department_id=department_id,
            category_id=category_id,
            authority_id=authority_id,
            service_id=service_id,
            doc_name=form.doc_name.data,
            doc_type=form.doc_type.data,
            cert_no=form.cert_no.data,
            issue_date=form.issue_date.data,
            expiry_date=expiry_date,
            no_expiry=no_expiry,
            issuing_authority=form.issuing_authority.data,
            responsible_person=form.responsible_person.data,
            renewal_status=form.renewal_status.data,
            remarks=form.remarks.data,
            status=compute_status(expiry_date, no_expiry),
            created_by=current_user.id,
        )
        db.session.add(document)
        apply_recipients(document, form, set_recipient_choices(form))
        db.session.commit()
        if stored and os.path.exists(stored):
            from werkzeug.datastructures import FileStorage
            with open(stored, "rb") as handle:
                storage = FileStorage(handle, filename=os.path.basename(meta.get("original_name") or stored))
                relative = _save_version_file(storage, document, 1)
            version = DocumentVersion(
                document_id=document.id, uploaded_by=current_user.id, version_no=1,
                file_path=relative, issue_date=document.issue_date, expiry_date=document.expiry_date,
            )
            db.session.add(version)
            db.session.flush()
            text = request.form.get("extracted_text", meta["result"].get("extracted_text") or "")
            db.session.add(OcrResult(
                document_id=document.id,
                document_version_id=version.id,
                extracted_text=text,
                search_text=normalize_bangla_digits(text),
                language=meta["result"].get("language"),
                confidence=meta["result"].get("confidence"),
                suggested_fields=suggestions_json(suggestions),
                ocr_status="COMPLETED",
                confirmed_at=datetime.utcnow(),
                processed_at=datetime.utcnow(),
                processing_time=meta["result"].get("processing_time"),
            ))
            db.session.commit()
            os.remove(stored)
        if os.path.exists(meta_path):
            os.remove(meta_path)
        flash("Document saved from the confirmed extraction.", "success")
        return redirect(url_for("documents.view_document", document_id=document.id))
    return render_template(
        "documents/extract_preview.html",
        form=form,
        token=token,
        result=meta["result"],
        suggestions=suggestions,
        doc_type_suggestions=DOC_TYPE_SUGGESTIONS,
    )


@documents_bp.route("/extract/<token>/cancel", methods=["POST"])
@login_required
@roles_required(*MANAGE_ROLES)
def extract_cancel(token):
    meta, meta_path = _load_staging(token)
    folder = _staging_dir()
    for name in os.listdir(folder):
        if name.startswith(token + "."):
            os.remove(os.path.join(folder, name))
    flash("Extraction cancelled. Nothing was saved.", "info")
    return redirect(url_for("documents.add_document"))
