from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app import db
from app.models import (
    User, Role, Factory, Department, Document, DocumentVersion,
    EmployeeCompliance, EmployeeComplianceVersion, HealthCheckup, HealthCheckupVersion,
    AlertEmail, company_alert_recipients, department_alert_recipients,
    document_alert_recipients, license_alert_recipients, checkup_alert_recipients,
)
from app.forms import UserForm
from app.utils import roles_required

users_bp = Blueprint("users", __name__, url_prefix="/users")


def get_role_factory_choices(form, current_user_role, target_user=None):
    assignable_role_names = [
        "Compliance Manager", "Compliance User", "Compliance Viewer",
        "Transport Manager", "Transport User",
    ]

    if current_user_role == "Super Admin":
        form.role_id.choices = [(r.id, r.name) for r in Role.query.order_by(Role.name).all()]
    else:
        roles = Role.query.filter(Role.name.in_(assignable_role_names)).order_by(Role.name).all()
        choices = [(r.id, r.name) for r in roles]
        # Always include the target user's CURRENT role, even if it isn't one a
        # non-Super-Admin can newly assign — otherwise saving any unrelated
        # field (name, password) on that user fails "Not a valid choice"
        # purely because their existing role isn't in the assignable list.
        if target_user and target_user.role_id not in [rid for rid, _ in choices]:
            choices.append((target_user.role_id, target_user.role.name))
        form.role_id.choices = choices

    form.factory_id.choices = [(0, "-- None (Global Access) --")] + [
        (f.id, f.name) for f in Factory.query.order_by(Factory.name).all()
    ]
    form.department_id.choices = [(0, "-- None --")] + [
        (d.id, d.name) for d in Department.query.order_by(Department.name).all()
    ]


@users_bp.route("/")
@login_required
@roles_required("Super Admin", "Factory Admin")
def list_users():
    search = request.args.get("search", "").strip()
    query = User.query.options(
        joinedload(User.role),
        joinedload(User.factory),
        joinedload(User.department),
    )
    if current_user.role.name != "Super Admin":
        query = query.filter(User.factory_id == current_user.factory_id)
    if search:
        like = f"%{search}%"
        query = (
            query.outerjoin(User.role)
            .outerjoin(User.factory)
            .outerjoin(User.department)
            .filter(or_(
                User.username.ilike(like),
                User.full_name.ilike(like),
                User.email.ilike(like),
                Role.name.ilike(like),
                Factory.name.ilike(like),
                Department.name.ilike(like),
            ))
        )
    users = query.order_by(User.username).distinct().all()
    return render_template("users/list.html", users=users, search=search)


@users_bp.route("/add", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin", "Factory Admin")
def add_user():
    form = UserForm()
    get_role_factory_choices(form, current_user.role.name)

    if form.validate_on_submit():
        if not form.password.data:
            flash("Password is required for new users.", "danger")
            return render_template("users/form.html", form=form, title="Add User")

        existing = User.query.filter_by(username=form.username.data).first()
        if existing:
            flash("A user with this username already exists.", "danger")
            return render_template("users/form.html", form=form, title="Add User")

        selected_factory_id = form.factory_id.data if form.factory_id.data != 0 else None

        if current_user.role.name == "Factory Admin":
            selected_factory_id = current_user.factory_id

        user = User(
            username=form.username.data,
            full_name=form.full_name.data,
            email=(form.email.data.strip() if form.email.data else None),
            role_id=form.role_id.data,
            factory_id=selected_factory_id,
            department_id=form.department_id.data or None,
            is_active=(form.is_active.data == "1")
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()

        flash("User added successfully.", "success")
        return redirect(url_for("users.list_users"))

    return render_template("users/form.html", form=form, title="Add User")


@users_bp.route("/edit/<int:user_id>", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin", "Factory Admin")
def edit_user(user_id):
    user = User.query.get_or_404(user_id)

    if current_user.role.name == "Factory Admin" and user.factory_id != current_user.factory_id:
        flash("You do not have permission to edit this user.", "danger")
        return redirect(url_for("users.list_users"))

    is_protected_super_admin = (user.username == "admin" and user.role.name == "Super Admin")

    if is_protected_super_admin:
        if request.method == "POST" and current_user.role.name == "Super Admin":
            from app.utils import valid_email
            raw = (request.form.get("email") or "").strip()
            if raw and not valid_email(raw):
                flash("Enter a valid email address.", "danger")
            else:
                user.email = raw or None
                db.session.commit()
                flash("Email updated.", "success")
            return redirect(url_for("users.edit_user", user_id=user.id))
        return render_template("users/view_protected.html", user=user, title="View User")

    form = UserForm(obj=user)
    get_role_factory_choices(form, current_user.role.name, target_user=user)

    if request.method == "GET":
        form.is_active.data = "1" if user.is_active else "0"
        form.factory_id.data = user.factory_id if user.factory_id else 0
        form.department_id.data = user.department_id or 0

    is_editing_self = (user.id == current_user.id)
    assignable_role_names = [
        "Compliance Manager", "Compliance User", "Compliance Viewer",
        "Transport Manager", "Transport User",
    ]

    if form.validate_on_submit():
        if is_editing_self and (form.is_active.data != ("1" if user.is_active else "0") or form.role_id.data != user.role_id):
            flash("You can't change your own role or active status — ask another admin to do that for you.", "danger")
            return render_template("users/form.html", form=form, title="Edit User", is_editing_self=is_editing_self)

        if current_user.role.name != "Super Admin" and form.role_id.data != user.role_id:
            new_role = db.session.get(Role, form.role_id.data)
            if not new_role or new_role.name not in assignable_role_names:
                flash("You don't have permission to assign that role.", "danger")
                return render_template("users/form.html", form=form, title="Edit User", is_editing_self=is_editing_self)

        user.username = form.username.data
        user.full_name = form.full_name.data
        user.email = (form.email.data.strip() if form.email.data else None)
        user.role_id = form.role_id.data
        user.is_active = (form.is_active.data == "1")

        if current_user.role.name == "Super Admin":
            user.factory_id = form.factory_id.data if form.factory_id.data != 0 else None
        user.department_id = form.department_id.data or None

        if form.password.data:
            user.set_password(form.password.data)

        db.session.commit()

        flash("User updated successfully.", "success")
        return redirect(url_for("users.list_users"))

    return render_template("users/form.html", form=form, title="Edit User", is_editing_self=is_editing_self)


def can_delete_user(actor, user):
    """Super Admin may delete other accounts. Factory Admin may delete staff in their factory."""
    if actor is None or user is None or actor.id == user.id:
        return False
    if user.username == "admin" and user.role and user.role.name == "Super Admin":
        return False
    if actor.role.name == "Super Admin":
        return True
    if actor.role.name != "Factory Admin" or user.factory_id != actor.factory_id:
        return False
    if user.role and user.role.name in ("Super Admin", "Administration", "Factory Admin"):
        return False
    return True


def _release_user_links(user):
    """Keep documents and history. Drop only the links that point at this account."""
    user_id = user.id
    Document.query.filter_by(created_by=user_id).update({Document.created_by: None}, synchronize_session=False)
    DocumentVersion.query.filter_by(uploaded_by=user_id).update(
        {DocumentVersion.uploaded_by: None}, synchronize_session=False
    )
    EmployeeCompliance.query.filter_by(created_by=user_id).update(
        {EmployeeCompliance.created_by: None}, synchronize_session=False
    )
    EmployeeComplianceVersion.query.filter_by(uploaded_by=user_id).update(
        {EmployeeComplianceVersion.uploaded_by: None}, synchronize_session=False
    )
    HealthCheckup.query.filter_by(created_by=user_id).update(
        {HealthCheckup.created_by: None}, synchronize_session=False
    )
    HealthCheckupVersion.query.filter_by(uploaded_by=user_id).update(
        {HealthCheckupVersion.uploaded_by: None}, synchronize_session=False
    )
    AlertEmail.query.filter_by(user_id=user_id).update({AlertEmail.user_id: None}, synchronize_session=False)
    for table in (
        company_alert_recipients, department_alert_recipients, document_alert_recipients,
        license_alert_recipients, checkup_alert_recipients,
    ):
        db.session.execute(table.delete().where(table.c.user_id == user_id))


@users_bp.route("/delete/<int:user_id>", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin", "Factory Admin")
def delete_user(user_id):
    user = User.query.options(joinedload(User.role)).get_or_404(user_id)
    if not can_delete_user(current_user, user):
        flash("You cannot delete this user.", "danger")
        return redirect(url_for("users.list_users"))
    if user.role and user.role.name == "Super Admin":
        others = User.query.join(Role).filter(Role.name == "Super Admin", User.id != user.id).count()
        if others < 1:
            flash("The last Super Admin cannot be deleted.", "danger")
            return redirect(url_for("users.list_users"))
    if request.method == "POST":
        typed = (request.form.get("confirm_name") or "").strip()
        if typed != user.username:
            flash("Type the username exactly to confirm deletion.", "danger")
            return render_template("users/delete.html", user=user)
        username = user.username
        _release_user_links(user)
        db.session.delete(user)
        db.session.commit()
        flash(f"Deleted user {username}.", "success")
        return redirect(url_for("users.list_users"))
    return render_template("users/delete.html", user=user)