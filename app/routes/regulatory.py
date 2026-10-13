from flask import Blueprint, render_template, redirect, url_for, flash, jsonify
from flask_login import login_required
from app import db
from app.models import ServiceCategory, RegulatoryAuthority, Service
from app.forms import ServiceCategoryForm, RegulatoryAuthorityForm, ServiceForm
from app.utils import roles_required

regulatory_bp = Blueprint("regulatory", __name__, url_prefix="/regulatory")


# ---------- Categories ----------

@regulatory_bp.route("/categories")
@login_required
@roles_required("Super Admin", "Administration")
def list_categories():
    categories = ServiceCategory.query.order_by(ServiceCategory.name).all()
    return render_template("regulatory/categories_list.html", categories=categories)


@regulatory_bp.route("/categories/add", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin", "Administration")
def add_category():
    form = ServiceCategoryForm()

    if form.validate_on_submit():
        existing = ServiceCategory.query.filter(
            db.func.lower(ServiceCategory.name) == form.name.data.strip().lower()
        ).first()
        if existing:
            flash(f"A category named '{form.name.data.strip()}' already exists.", "danger")
            return render_template("regulatory/category_form.html", form=form, title="Add Category")

        category = ServiceCategory(name=form.name.data.strip())
        db.session.add(category)
        db.session.commit()
        flash("Category added successfully.", "success")
        return redirect(url_for("regulatory.list_categories"))

    return render_template("regulatory/category_form.html", form=form, title="Add Category")


@regulatory_bp.route("/categories/edit/<int:category_id>", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin", "Administration")
def edit_category(category_id):
    category = ServiceCategory.query.get_or_404(category_id)
    form = ServiceCategoryForm(obj=category)

    if form.validate_on_submit():
        existing = ServiceCategory.query.filter(
            db.func.lower(ServiceCategory.name) == form.name.data.strip().lower(),
            ServiceCategory.id != category.id
        ).first()
        if existing:
            flash(f"A category named '{form.name.data.strip()}' already exists.", "danger")
            return render_template("regulatory/category_form.html", form=form, title="Edit Category")

        category.name = form.name.data.strip()
        db.session.commit()
        flash("Category updated successfully.", "success")
        return redirect(url_for("regulatory.list_categories"))

    return render_template("regulatory/category_form.html", form=form, title="Edit Category")


@regulatory_bp.route("/categories/delete/<int:category_id>", methods=["POST"])
@login_required
@roles_required("Super Admin", "Administration")
def delete_category(category_id):
    category = ServiceCategory.query.get_or_404(category_id)

    if category.authorities:
        flash("Cannot delete a category that has regulatory authorities linked to it.", "danger")
    else:
        db.session.delete(category)
        db.session.commit()
        flash("Category deleted successfully.", "success")

    return redirect(url_for("regulatory.list_categories"))


# ---------- Regulatory Authorities ----------

@regulatory_bp.route("/authorities")
@login_required
@roles_required("Super Admin", "Administration")
def list_authorities():
    authorities = RegulatoryAuthority.query.join(ServiceCategory).order_by(
        ServiceCategory.name, RegulatoryAuthority.name
    ).all()
    return render_template("regulatory/authorities_list.html", authorities=authorities)


@regulatory_bp.route("/authorities/add", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin", "Administration")
def add_authority():
    form = RegulatoryAuthorityForm()
    form.category_id.choices = [(c.id, c.name) for c in ServiceCategory.query.order_by(ServiceCategory.name).all()]

    if form.validate_on_submit():
        existing = RegulatoryAuthority.query.filter(
            db.func.lower(RegulatoryAuthority.name) == form.name.data.strip().lower(),
            RegulatoryAuthority.category_id == form.category_id.data
        ).first()
        if existing:
            flash(f"An authority named '{form.name.data.strip()}' already exists in this category.", "danger")
            return render_template("regulatory/authority_form.html", form=form, title="Add Regulatory Authority")

        authority = RegulatoryAuthority(category_id=form.category_id.data, name=form.name.data.strip())
        db.session.add(authority)
        db.session.commit()
        flash("Regulatory authority added successfully.", "success")
        return redirect(url_for("regulatory.list_authorities"))

    return render_template("regulatory/authority_form.html", form=form, title="Add Regulatory Authority")


@regulatory_bp.route("/authorities/edit/<int:authority_id>", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin", "Administration")
def edit_authority(authority_id):
    authority = RegulatoryAuthority.query.get_or_404(authority_id)
    form = RegulatoryAuthorityForm(obj=authority)
    form.category_id.choices = [(c.id, c.name) for c in ServiceCategory.query.order_by(ServiceCategory.name).all()]

    if form.validate_on_submit():
        existing = RegulatoryAuthority.query.filter(
            db.func.lower(RegulatoryAuthority.name) == form.name.data.strip().lower(),
            RegulatoryAuthority.category_id == form.category_id.data,
            RegulatoryAuthority.id != authority.id
        ).first()
        if existing:
            flash(f"An authority named '{form.name.data.strip()}' already exists in this category.", "danger")
            return render_template("regulatory/authority_form.html", form=form, title="Edit Regulatory Authority")

        authority.category_id = form.category_id.data
        authority.name = form.name.data.strip()
        db.session.commit()
        flash("Regulatory authority updated successfully.", "success")
        return redirect(url_for("regulatory.list_authorities"))

    return render_template("regulatory/authority_form.html", form=form, title="Edit Regulatory Authority")


@regulatory_bp.route("/authorities/delete/<int:authority_id>", methods=["POST"])
@login_required
@roles_required("Super Admin", "Administration")
def delete_authority(authority_id):
    authority = RegulatoryAuthority.query.get_or_404(authority_id)

    if authority.services:
        flash("Cannot delete an authority that has services linked to it.", "danger")
    else:
        db.session.delete(authority)
        db.session.commit()
        flash("Regulatory authority deleted successfully.", "success")

    return redirect(url_for("regulatory.list_authorities"))


# ---------- Services ----------

@regulatory_bp.route("/services")
@login_required
@roles_required("Super Admin", "Administration")
def list_services():
    services = Service.query.join(RegulatoryAuthority).join(ServiceCategory).order_by(
        ServiceCategory.name, RegulatoryAuthority.name, Service.name
    ).all()
    return render_template("regulatory/services_list.html", services=services)


@regulatory_bp.route("/services/add", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin", "Administration")
def add_service():
    form = ServiceForm()
    form.authority_id.choices = [
        (a.id, f"{a.name} ({a.category.name})")
        for a in RegulatoryAuthority.query.join(ServiceCategory).order_by(ServiceCategory.name, RegulatoryAuthority.name).all()
    ]

    if form.validate_on_submit():
        existing = Service.query.filter(
            db.func.lower(Service.name) == form.name.data.strip().lower(),
            Service.authority_id == form.authority_id.data
        ).first()
        if existing:
            flash(f"A service named '{form.name.data.strip()}' already exists under this authority.", "danger")
            return render_template("regulatory/service_form.html", form=form, title="Add Service")

        service = Service(authority_id=form.authority_id.data, name=form.name.data.strip())
        db.session.add(service)
        db.session.commit()
        flash("Service added successfully.", "success")
        return redirect(url_for("regulatory.list_services"))

    return render_template("regulatory/service_form.html", form=form, title="Add Service")


@regulatory_bp.route("/services/edit/<int:service_id>", methods=["GET", "POST"])
@login_required
@roles_required("Super Admin", "Administration")
def edit_service(service_id):
    service = Service.query.get_or_404(service_id)
    form = ServiceForm(obj=service)
    form.authority_id.choices = [
        (a.id, f"{a.name} ({a.category.name})")
        for a in RegulatoryAuthority.query.join(ServiceCategory).order_by(ServiceCategory.name, RegulatoryAuthority.name).all()
    ]

    if form.validate_on_submit():
        existing = Service.query.filter(
            db.func.lower(Service.name) == form.name.data.strip().lower(),
            Service.authority_id == form.authority_id.data,
            Service.id != service.id
        ).first()
        if existing:
            flash(f"A service named '{form.name.data.strip()}' already exists under this authority.", "danger")
            return render_template("regulatory/service_form.html", form=form, title="Edit Service")

        service.authority_id = form.authority_id.data
        service.name = form.name.data.strip()
        db.session.commit()
        flash("Service updated successfully.", "success")
        return redirect(url_for("regulatory.list_services"))

    return render_template("regulatory/service_form.html", form=form, title="Edit Service")


@regulatory_bp.route("/services/delete/<int:service_id>", methods=["POST"])
@login_required
@roles_required("Super Admin", "Administration")
def delete_service(service_id):
    from app.models import Document
    service = Service.query.get_or_404(service_id)

    if Document.query.filter_by(service_id=service.id).first():
        flash("Cannot delete a service that is already linked to one or more documents.", "danger")
    else:
        db.session.delete(service)
        db.session.commit()
        flash("Service deleted successfully.", "success")

    return redirect(url_for("regulatory.list_services"))


# ---------- Cascading dropdown APIs (used by the Document Add/Edit form) ----------

@regulatory_bp.route("/api/authorities/<int:category_id>")
@login_required
@roles_required("Super Admin", "Administration")
def get_authorities_by_category(category_id):
    authorities = RegulatoryAuthority.query.filter_by(category_id=category_id).order_by(RegulatoryAuthority.name).all()
    return jsonify([{"id": a.id, "name": a.name} for a in authorities])


@regulatory_bp.route("/api/services/<int:authority_id>")
@login_required
@roles_required("Super Admin", "Administration")
def get_services_by_authority(authority_id):
    services = Service.query.filter_by(authority_id=authority_id).order_by(Service.name).all()
    return jsonify([{"id": s.id, "name": s.name} for s in services])
