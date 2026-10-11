from datetime import date

from flask import Blueprint, render_template, request, url_for
from flask_login import login_required, current_user
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    Document, Factory, Employee, EmployeeCompliance, HealthCheckup,
    company_alert_recipients, department_alert_recipients, document_alert_recipients,
    license_alert_recipients, checkup_alert_recipients,
)
from app.utils import (
    recalculate_all_statuses, EXPIRING_STATUSES, ATTENTION_STATUSES,
    CRITICAL_STATUSES, STATUS_VALID, STATUS_EXPIRED, format_sources, sources_for_user,
    RecipientIndex,
)

main_bp = Blueprint("main", __name__)


def accessible_factory_ids():
    """Factory IDs the current user may see. None = all factories."""
    if current_user.can_access_all_factories():
        return None
    if current_user.factory_id:
        return [current_user.factory_id]
    return []


@main_bp.route("/")
@login_required
def dashboard():
    recalculate_all_statuses()

    scope_ids = accessible_factory_ids()

    # ---- Factory documents -------------------------------------------------
    doc_q = db.session.query(Document.factory_id, Document.status, func.count(Document.id)) \
        .filter(Document.is_active.is_(True))
    if scope_ids is not None:
        doc_q = doc_q.filter(Document.factory_id.in_(scope_ids)) if scope_ids else doc_q.filter(db.false())
    counts = doc_q.group_by(Document.factory_id, Document.status).all()

    total_documents = valid_count = expiring_count = expired_count = 0
    bucket_counts = {status: 0 for status in EXPIRING_STATUSES}
    per_factory = {}

    for factory_id, status, count in counts:
        total_documents += count
        stats = per_factory.setdefault(factory_id, {"total": 0, "valid": 0, "expiring": 0, "expired": 0})
        stats["total"] += count
        if status == STATUS_VALID:
            valid_count += count
            stats["valid"] += count
        elif status == STATUS_EXPIRED:
            expired_count += count
            stats["expired"] += count
        elif status in EXPIRING_STATUSES:
            expiring_count += count
            stats["expiring"] += count
            bucket_counts[status] += count

    factory_query = Factory.query.order_by(Factory.name)
    if scope_ids is not None:
        factory_query = factory_query.filter(Factory.id.in_(scope_ids)) if scope_ids else factory_query.filter(db.false())
    factories = factory_query.all()
    factory_stats = [
        {"name": f.name, **per_factory.get(f.id, {"total": 0, "valid": 0, "expiring": 0, "expired": 0})}
        for f in factories
    ]

    # ---- Employee compliance ----------------------------------------------
    emp_filter = []
    if scope_ids is not None:
        emp_filter = [Employee.factory_id.in_(scope_ids)] if scope_ids else [db.false()]

    active_employees = Employee.query.filter(Employee.is_active.is_(True), *emp_filter).count()

    def _emp_status_count(model, statuses):
        q = db.session.query(func.count(model.id)).join(Employee, model.employee_id == Employee.id) \
            .filter(model.is_active.is_(True), model.status.in_(statuses))
        if scope_ids is not None:
            q = q.filter(Employee.factory_id.in_(scope_ids)) if scope_ids else q.filter(db.false())
        return q.scalar() or 0

    emp_license_expiring = _emp_status_count(EmployeeCompliance, EXPIRING_STATUSES)
    emp_license_expired = _emp_status_count(EmployeeCompliance, [STATUS_EXPIRED])
    checkup_expiring = _emp_status_count(HealthCheckup, EXPIRING_STATUSES)
    checkup_expired = _emp_status_count(HealthCheckup, [STATUS_EXPIRED])

    # ---- Needs attention (critical only: 7 days and expired) ---------------
    attention_items = []

    attn_doc_q = Document.query.options(joinedload(Document.factory)) \
        .filter(Document.is_active.is_(True), Document.status.in_(CRITICAL_STATUSES))
    if scope_ids is not None:
        attn_doc_q = attn_doc_q.filter(Document.factory_id.in_(scope_ids)) if scope_ids else attn_doc_q.filter(db.false())
    for doc in attn_doc_q.order_by(Document.expiry_date).limit(8).all():
        attention_items.append({
            "kind": "Document",
            "name": doc.doc_name,
            "where": doc.factory.name if doc.factory else "-",
            "expiry": doc.expiry_date,
            "status": doc.status,
            "url": url_for("documents.view_document", document_id=doc.id),
        })

    def _critical_employee_rows(model, label_attr, kind_label):
        q = model.query.join(Employee, model.employee_id == Employee.id) \
            .options(joinedload(model.employee).joinedload(Employee.factory)) \
            .filter(model.is_active.is_(True), model.status.in_(CRITICAL_STATUSES))
        if scope_ids is not None:
            q = q.filter(Employee.factory_id.in_(scope_ids)) if scope_ids else q.filter(db.false())
        rows = []
        for record in q.order_by(model.expiry_date).limit(8).all():
            employee = record.employee
            label = getattr(record, label_attr) or kind_label
            name = f"{label} — {employee.name}" if employee else label
            rows.append({
                "kind": kind_label,
                "name": name,
                "where": employee.factory.name if employee and employee.factory else "-",
                "expiry": record.expiry_date,
                "status": record.status,
                "url": url_for("employees.view_employee", employee_id=employee.id) if employee else None,
            })
        return rows

    attention_items.extend(_critical_employee_rows(EmployeeCompliance, "compliance_type", "License"))
    attention_items.extend(_critical_employee_rows(HealthCheckup, "exam_type", "Checkup"))
    attention_items.sort(key=lambda item: (
        0 if item["status"] == STATUS_EXPIRED else 1,
        item["expiry"] or date.max,
    ))
    attention_items = attention_items[:12]

    return render_template(
        "dashboard.html",
        total_documents=total_documents,
        valid_count=valid_count,
        expiring_count=expiring_count,
        expired_count=expired_count,
        bucket_counts=bucket_counts,
        factory_stats=factory_stats,
        attention_items=attention_items,
        active_employees=active_employees,
        emp_license_expiring=emp_license_expiring,
        emp_license_expired=emp_license_expired,
        checkup_expiring=checkup_expiring,
        checkup_expired=checkup_expired,
        active_page="dashboard",
    )


def _apply_factory_column(query, column):
    scope_ids = accessible_factory_ids()
    if scope_ids is None:
        return query
    if not scope_ids:
        return query.filter(db.false())
    return query.filter(column.in_(scope_ids))


@main_bp.route("/my-alerts")
@login_required
def my_alerts():
    """Read-only list of records that can email the signed-in user."""
    show_all = request.args.get("show") == "all"
    user = current_user
    company_ids = {
        row[0] for row in db.session.query(company_alert_recipients.c.company_id)
        .filter(company_alert_recipients.c.user_id == user.id).all()
    }
    department_ids = {
        row[0] for row in db.session.query(department_alert_recipients.c.department_id)
        .filter(department_alert_recipients.c.user_id == user.id).all()
    }

    doc_ids = db.session.query(document_alert_recipients.c.document_id).filter(
        document_alert_recipients.c.user_id == user.id
    )
    doc_filters = [Document.created_by == user.id, Document.id.in_(doc_ids)]
    if department_ids:
        doc_filters.append(Document.department_id.in_(department_ids))
    if company_ids:
        doc_filters.append(Document.factory.has(Factory.company_id.in_(company_ids)))
    documents = _apply_factory_column(
        Document.query.options(
            joinedload(Document.factory).joinedload(Factory.company),
            joinedload(Document.department),
            selectinload(Document.alert_recipients),
        ).filter(Document.is_active.is_(True), db.or_(*doc_filters)),
        Document.factory_id,
    )
    if not show_all:
        documents = documents.filter(Document.status.in_(ATTENTION_STATUSES))
    documents = documents.order_by(Document.expiry_date).limit(200).all()

    license_ids = db.session.query(license_alert_recipients.c.compliance_id).filter(
        license_alert_recipients.c.user_id == user.id
    )
    license_filters = [EmployeeCompliance.created_by == user.id, EmployeeCompliance.id.in_(license_ids)]
    if department_ids:
        license_filters.append(Employee.department_id.in_(department_ids))
    if company_ids:
        license_filters.append(Employee.factory.has(Factory.company_id.in_(company_ids)))
    licenses = _apply_factory_column(
        EmployeeCompliance.query.join(Employee).options(
            joinedload(EmployeeCompliance.employee).joinedload(Employee.factory).joinedload(Factory.company),
            joinedload(EmployeeCompliance.employee).joinedload(Employee.department),
            selectinload(EmployeeCompliance.alert_recipients),
        ).filter(EmployeeCompliance.is_active.is_(True), db.or_(*license_filters)),
        Employee.factory_id,
    )
    if not show_all:
        licenses = licenses.filter(EmployeeCompliance.status.in_(ATTENTION_STATUSES))
    licenses = licenses.order_by(EmployeeCompliance.expiry_date).limit(200).all()

    checkup_ids = db.session.query(checkup_alert_recipients.c.checkup_id).filter(
        checkup_alert_recipients.c.user_id == user.id
    )
    checkup_filters = [HealthCheckup.created_by == user.id, HealthCheckup.id.in_(checkup_ids)]
    if department_ids:
        checkup_filters.append(Employee.department_id.in_(department_ids))
    if company_ids:
        checkup_filters.append(Employee.factory.has(Factory.company_id.in_(company_ids)))
    checkups = _apply_factory_column(
        HealthCheckup.query.join(Employee).options(
            joinedload(HealthCheckup.employee).joinedload(Employee.factory).joinedload(Factory.company),
            joinedload(HealthCheckup.employee).joinedload(Employee.department),
            selectinload(HealthCheckup.alert_recipients),
        ).filter(HealthCheckup.is_active.is_(True), db.or_(*checkup_filters)),
        Employee.factory_id,
    )
    if not show_all:
        checkups = checkups.filter(HealthCheckup.status.in_(ATTENTION_STATUSES))
    checkups = checkups.order_by(HealthCheckup.expiry_date).limit(200).all()

    recipient_index = RecipientIndex.load()
    items = []
    for doc in documents:
        sources = sources_for_user(
            user, doc, "document", company_ids, department_ids, index=recipient_index,
        )
        if not sources:
            continue
        items.append({
            "kind": "Factory Document",
            "name": doc.doc_name,
            "company": doc.factory.company.name if doc.factory and doc.factory.company else "-",
            "factory": doc.factory.name if doc.factory else "-",
            "department": doc.department.name if doc.department else "-",
            "expiry": doc.expiry_date,
            "status": doc.status,
            "source": format_sources(sources),
            "url": url_for("documents.view_document", document_id=doc.id),
        })
    for record in licenses:
        sources = sources_for_user(
            user, record, "license", company_ids, department_ids, index=recipient_index,
        )
        if not sources:
            continue
        employee = record.employee
        factory = employee.factory if employee else None
        items.append({
            "kind": "Employee License",
            "name": f"{record.compliance_type} — {employee.name if employee else ''}",
            "company": factory.company.name if factory and factory.company else "-",
            "factory": factory.name if factory else "-",
            "department": employee.department.name if employee and employee.department else "-",
            "expiry": record.expiry_date,
            "status": record.status,
            "source": format_sources(sources),
            "url": url_for("employees.view_employee", employee_id=employee.id) if employee else None,
        })
    for record in checkups:
        sources = sources_for_user(
            user, record, "checkup", company_ids, department_ids, index=recipient_index,
        )
        if not sources:
            continue
        employee = record.employee
        factory = employee.factory if employee else None
        items.append({
            "kind": "Health Checkup",
            "name": f"{record.exam_type} — {employee.name if employee else ''}",
            "company": factory.company.name if factory and factory.company else "-",
            "factory": factory.name if factory else "-",
            "department": employee.department.name if employee and employee.department else "-",
            "expiry": record.expiry_date,
            "status": record.status,
            "source": format_sources(sources),
            "url": url_for("employees.view_employee", employee_id=employee.id) if employee else None,
        })
    items.sort(key=lambda item: (item["expiry"] is None, item["expiry"] or date.max))

    return render_template(
        "my_alerts.html",
        items=items,
        show_all=show_all,
        active_page="my_alerts",
    )
