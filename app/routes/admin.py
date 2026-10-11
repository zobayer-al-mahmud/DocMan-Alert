import os

from flask import Blueprint, current_app, render_template, redirect, url_for, flash, abort, request, send_from_directory
from flask_login import login_required, current_user
from sqlalchemy.orm import joinedload
from app import db
from app.models import Company, Factory, Department, User
from app.forms import CompanyForm, FactoryForm, DepartmentForm
from app.utils import GLOBAL_ALERT_ROLES, roles_required, valid_email

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def can_access_factory_for_dept(factory_id):
    if current_user.role.name in ["Super Admin", "Compliance Viewer", "Administration"]:
        return True
    return current_user.factory_id == factory_id


def _users_in_company(company):
    factory_ids = {factory.id for factory in company.factories}
    users = User.query.order_by(User.full_name, User.username).all()
    return [
        user for user in users
        if (user.role and user.role.name in GLOBAL_ALERT_ROLES) or user.factory_id in factory_ids
    ]


def _users_in_department(department):
    """Only users assigned to this department."""
    return (
        User.query.options(joinedload(User.factory), joinedload(User.department))
        .filter(User.department_id == department.id)
        .order_by(User.full_name, User.username)
        .all()
    )


def _selected_recipient_ids(current_users, scoped_users):
    """Posted ids must be in the active, in-scope, valid-email set.

    Inactive assignments and assignments with no email are kept so a later
    reactivation or email fix starts delivery again without a new selection.
    """
    posted = set()
    for raw in request.form.getlist("user_id"):
        try:
            posted.add(int(raw))
        except (TypeError, ValueError):
            continue
    shown = {user.id for user in scoped_users if user.is_active and valid_email(user.email)}
    in_scope = {user.id for user in scoped_users}
    keep = {user.id for user in current_users if user.id in in_scope and user.id not in shown}
    chosen = (posted & shown) | keep
    if not chosen:
        return []
    return User.query.filter(User.id.in_(chosen)).all()


@admin_bp.route("/companies/<int:company_id>/alert-recipients", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin", "Administration")
def company_alert_recipients(company_id):
    company = Company.query.get_or_404(company_id)
    scoped = _users_in_company(company)
    if request.method == "POST":
        company.alert_recipients = _selected_recipient_ids(list(company.alert_recipients), scoped)
        db.session.commit()
        flash("Company alert recipients saved.", "success")
        return redirect(url_for("admin.company_alert_recipients", company_id=company.id))
    return render_template(
        "admin/alert_recipients.html",
        title=f"Company Alert Recipients — {company.name}",
        heading="Company Alert Recipients",
        scope_label=company.name,
        help_text="These people receive alerts for records in this company, together with the matching department recipients and the person who uploaded the record.",
        selected_ids={user.id for user in company.alert_recipients},
        eligible=[user for user in scoped if user.is_active and valid_email(user.email)],
        ineligible=[user for user in scoped if user.is_active and not valid_email(user.email)],
        back_url=url_for("admin.list_companies"),
    )


@admin_bp.route("/departments/<int:department_id>/alert-recipients", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin", "Administration", "Factory Admin")
def department_alert_recipients(department_id):
    department = Department.query.get_or_404(department_id)
    scoped = _users_in_department(department)
    if request.method == "POST":
        department.alert_recipients = _selected_recipient_ids(list(department.alert_recipients), scoped)
        db.session.commit()
        flash("Department alert recipients saved.", "success")
        return redirect(url_for("admin.department_alert_recipients", department_id=department.id))
    return render_template(
        "admin/alert_recipients.html",
        title=f"Department Alert Recipients — {department.name}",
        heading="Department Alert Recipients",
        scope_label=department.name,
        help_text="Only users assigned to this department are listed. They receive alerts for this department when the record is in their factory. The department group mailbox is included as well when one is set.",
        selected_ids={user.id for user in department.alert_recipients},
        eligible=[user for user in scoped if user.is_active and valid_email(user.email)],
        ineligible=[user for user in scoped if user.is_active and not valid_email(user.email)],
        back_url=url_for("admin.list_departments"),
    )


@admin_bp.route("/department-recipients", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin", "Administration", "Factory Admin")
def factory_department_recipients():
    """Factory-specific department alert recipients.

    Pick a Factory + Department; only users who belong to that exact
    Factory + Department are shown and selectable. Saving updates the
    department's recipient list but touches ONLY those in-scope users, so
    recipients configured for other factories (and global-access users) are
    left untouched. Delivery scope is still enforced per factory at send time
    (see matching_alert_users / resolve_alert_recipients in app.utils), and the
    department group mailbox continues to be included globally.
    """
    from app.utils import matching_alert_users

    if current_user.role.name == "Factory Admin":
        factories = Factory.query.filter_by(id=current_user.factory_id).order_by(Factory.name).all()
    else:
        factories = Factory.query.order_by(Factory.name).all()
    departments = Department.query.order_by(Department.name).all()

    def _int(name):
        try:
            return int(request.values.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    factory_id = _int("factory_id")
    department_id = _int("department_id")

    if factory_id and not can_access_factory_for_dept(factory_id):
        abort(403)

    factory = db.session.get(Factory, factory_id) if factory_id else None
    department = db.session.get(Department, department_id) if department_id else None

    # Users that belong to THIS factory and THIS department only. Global-access
    # users (no factory) are intentionally excluded here — they are not
    # factory-specific and keep their existing assignment untouched on save.
    manageable = []
    if factory and department:
        manageable = (
            User.query.options(joinedload(User.factory), joinedload(User.department))
            .filter(
                User.factory_id == factory_id,
                User.department_id == department_id,
            )
            .order_by(User.full_name, User.username)
            .all()
        )

    if request.method == "POST":
        if not (factory and department):
            flash("Choose a factory and a department first.", "danger")
            return redirect(url_for("admin.factory_department_recipients"))
        shown_eligible = {u.id for u in manageable if u.is_active and valid_email(u.email)}
        shown_all = {u.id for u in manageable}
        posted = set()
        for raw in request.form.getlist("user_id"):
            try:
                posted.add(int(raw))
            except (TypeError, ValueError):
                continue
        # Keep every current recipient who is NOT in this factory+department
        # scope (other factories, or global-access users), then apply the
        # checkboxes for the in-scope users.
        keep_outside = [u for u in department.alert_recipients if u.id not in shown_all]
        chosen_ids = posted & shown_eligible
        chosen_here = User.query.filter(User.id.in_(chosen_ids)).all() if chosen_ids else []
        department.alert_recipients = keep_outside + chosen_here
        db.session.commit()
        flash(
            f"Alert recipients saved for {factory.name} · {department.name}.",
            "success",
        )
        return redirect(url_for(
            "admin.factory_department_recipients",
            factory_id=factory_id, department_id=department_id,
        ))

    selected_ids = {u.id for u in department.alert_recipients} if department else set()
    # Informational: users who also receive these alerts but are managed
    # elsewhere (global-access users with no factory, matched by send logic).
    global_receivers = []
    if factory and department:
        global_receivers = [
            u for u in matching_alert_users(factory_id, department_id)
            if u.factory_id is None
        ]
    group_emails = []
    if department:
        from app.utils import department_group_emails
        group_emails = department_group_emails(department)

    return render_template(
        "admin/factory_department_recipients.html",
        factories=factories,
        departments=departments,
        factory_id=factory_id,
        department_id=department_id,
        factory=factory,
        department=department,
        eligible=[u for u in manageable if u.is_active and valid_email(u.email)],
        ineligible=[u for u in manageable if u.is_active and not valid_email(u.email)],
        selected_ids=selected_ids,
        global_receivers=global_receivers,
        group_emails=group_emails,
    )


@admin_bp.route("/companies")
@login_required
@roles_required("Super Admin", "Administration")
def list_companies():
    companies = Company.query.order_by(Company.name).all()
    return render_template("companies/list.html", companies=companies)


@admin_bp.route("/companies/add", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin")
def add_company():
    form = CompanyForm()

    if form.validate_on_submit():
        existing = Company.query.filter_by(code=form.code.data).first()
        if existing:
            flash("A company with this code already exists.", "danger")
        else:
            company = Company(name=form.name.data, code=form.code.data)
            db.session.add(company)
            db.session.commit()
            flash("Company added successfully.", "success")
            return redirect(url_for("admin.list_companies"))

    return render_template("companies/form.html", form=form, title="Add Company")


@admin_bp.route("/companies/edit/<int:company_id>", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin")
def edit_company(company_id):
    company = Company.query.get_or_404(company_id)
    form = CompanyForm(obj=company)

    if form.validate_on_submit():
        company.name = form.name.data
        company.code = form.code.data
        db.session.commit()
        flash("Company updated successfully.", "success")
        return redirect(url_for("admin.list_companies"))

    return render_template("companies/form.html", form=form, title="Edit Company")


@admin_bp.route("/companies/delete/<int:company_id>", methods=["POST"])
@login_required
@roles_required("Super Admin")
def delete_company(company_id):
    company = Company.query.get_or_404(company_id)

    if company.factories:
        flash("Cannot delete a company that has factories linked to it.", "danger")
    else:
        db.session.delete(company)
        db.session.commit()
        flash("Company deleted successfully.", "success")

    return redirect(url_for("admin.list_companies"))


@admin_bp.route("/factories")
@login_required
@roles_required("Super Admin")
def list_factories():
    factories = Factory.query.order_by(Factory.name).all()
    return render_template("factories/list.html", factories=factories)


@admin_bp.route("/factories/add", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin")
def add_factory():
    form = FactoryForm()
    form.company_id.choices = [(c.id, c.name) for c in Company.query.order_by(Company.name).all()]

    if form.validate_on_submit():
        existing = Factory.query.filter(db.func.lower(Factory.name) == form.name.data.strip().lower()).first()
        if existing:
            flash(f"A factory named '{form.name.data.strip()}' already exists.", "danger")
            return render_template("factories/form.html", form=form, title="Add Factory")

        factory = Factory(
            company_id=form.company_id.data,
            name=form.name.data.strip(),
            location=form.location.data
        )
        db.session.add(factory)
        db.session.commit()
        flash("Factory added successfully.", "success")
        return redirect(url_for("admin.list_factories"))

    return render_template("factories/form.html", form=form, title="Add Factory")


@admin_bp.route("/factories/edit/<int:factory_id>", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin")
def edit_factory(factory_id):
    factory = Factory.query.get_or_404(factory_id)
    form = FactoryForm(obj=factory)
    form.company_id.choices = [(c.id, c.name) for c in Company.query.order_by(Company.name).all()]

    if form.validate_on_submit():
        existing = Factory.query.filter(
            db.func.lower(Factory.name) == form.name.data.strip().lower(),
            Factory.id != factory.id
        ).first()
        if existing:
            flash(f"A factory named '{form.name.data.strip()}' already exists.", "danger")
            return render_template("factories/form.html", form=form, title="Edit Factory")

        factory.company_id = form.company_id.data
        factory.name = form.name.data.strip()
        factory.location = form.location.data
        db.session.commit()
        flash("Factory updated successfully.", "success")
        return redirect(url_for("admin.list_factories"))

    return render_template("factories/form.html", form=form, title="Edit Factory")


@admin_bp.route("/factories/delete/<int:factory_id>", methods=["POST"])
@login_required
@roles_required("Super Admin")
def delete_factory(factory_id):
    factory = Factory.query.get_or_404(factory_id)

    if factory.documents or factory.users or factory.employees:
        flash("Cannot delete a factory that has documents, employees, or users linked to it.", "danger")
    else:
        db.session.delete(factory)
        db.session.commit()
        flash("Factory deleted successfully.", "success")

    return redirect(url_for("admin.list_factories"))


def _clean_group_email(value):
    from app.utils import valid_email
    raw = (value or "").strip()
    if raw and not valid_email(raw):
        return False, None
    return True, (raw or None)


@admin_bp.route("/departments")
@login_required
@roles_required("Super Admin", "Administration", "Factory Admin")
def list_departments():
    departments = Department.query.order_by(Department.name).all()
    return render_template(
        "departments/list.html",
        departments=departments,
        can_edit=current_user.role.name in ("Super Admin", "Factory Admin"),
    )


@admin_bp.route("/departments/add", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin", "Factory Admin")
def add_department():
    form = DepartmentForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        ok, group_email = _clean_group_email(form.group_email.data)
        if not ok:
            flash("Enter a valid group email address.", "danger")
        elif Department.query.filter(db.func.lower(Department.name) == name.lower()).first():
            flash("A department with this name already exists.", "danger")
        else:
            db.session.add(Department(name=name, group_email=group_email))
            db.session.commit()
            flash("Department added successfully.", "success")
            return redirect(url_for("admin.list_departments"))
    return render_template("departments/form.html", form=form, title="Add Department")


@admin_bp.route("/departments/edit/<int:department_id>", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin", "Factory Admin")
def edit_department(department_id):
    department = Department.query.get_or_404(department_id)
    form = DepartmentForm(obj=department)
    if form.validate_on_submit():
        name = form.name.data.strip()
        ok, group_email = _clean_group_email(form.group_email.data)
        duplicate = Department.query.filter(
            db.func.lower(Department.name) == name.lower(),
            Department.id != department.id,
        ).first()
        if not ok:
            flash("Enter a valid group email address.", "danger")
        elif duplicate:
            flash("A department with this name already exists.", "danger")
        else:
            department.name = name
            department.group_email = group_email
            db.session.commit()
            flash("Department updated successfully.", "success")
            return redirect(url_for("admin.list_departments"))
    return render_template("departments/form.html", form=form, title="Edit Department")


@admin_bp.route("/departments/delete/<int:department_id>", methods=["POST"])
@login_required
@roles_required("Super Admin", "Factory Admin")
def delete_department(department_id):
    department = Department.query.get_or_404(department_id)
    if department.documents or department.employees:
        flash("Cannot delete a department that has documents or employees linked to it.", "danger")
    else:
        db.session.delete(department)
        db.session.commit()
        flash("Department deleted successfully.", "success")
    return redirect(url_for("admin.list_departments"))


def _backup_filename_ok(filename):
    return (
        filename
        and filename.startswith("compliance_backup_")
        and filename.endswith(".zip")
        and os.path.basename(filename) == filename
    )


@admin_bp.route("/backups", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin")
def backups():
    from app.backup_service import list_backups, perform_backup

    if request.method == "POST":
        ok, summary = perform_backup()
        flash(summary, "success" if ok else "danger")
        return redirect(url_for("admin.backups"))
    return render_template(
        "admin/backups.html",
        backups=list_backups(),
        folder=current_app.config.get("BACKUP_FOLDER") or "",
        retention=current_app.config.get("BACKUP_RETENTION_DAYS") or 30,
    )


@admin_bp.route("/backups/download/<filename>")
@login_required
@roles_required("Super Admin")
def download_backup(filename):
    if not _backup_filename_ok(filename):
        abort(404)
    folder = current_app.config.get("BACKUP_FOLDER") or ""
    if not os.path.isfile(os.path.join(folder, filename)):
        abort(404)
    return send_from_directory(folder, filename, as_attachment=True)


