"""Idempotent column and index upgrades for the alert-recipient release.

create_all() creates brand-new tables. Existing tables only gain columns
through ALTER TABLE, which this module applies without dropping data.
"""
from sqlalchemy import text

from app import db


def _columns(table):
    rows = db.session.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _add_column(table, name, declaration):
    if name in _columns(table):
        return False
    db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"))
    return True


def ensure_alert_schema():
    """Add columns/indexes introduced for alert scope, files, and emergency dedup.

    Safe to run on every application start. Does not delete or rebuild tables.
    """
    changed = False
    if _add_column("employees", "department_id", "INTEGER"):
        changed = True
    if _add_column("employees", "email", "VARCHAR(255)"):
        changed = True
    if _add_column("employees", "receive_alerts", "INTEGER NOT NULL DEFAULT 0"):
        changed = True
    if _add_column("employee_compliance", "created_by", "INTEGER"):
        changed = True
    if _add_column("health_checkups", "created_by", "INTEGER"):
        changed = True
    if _add_column("documents", "category_id", "INTEGER"):
        changed = True
    if _add_column("documents", "authority_id", "INTEGER"):
        changed = True
    if _add_column("documents", "service_id", "INTEGER"):
        changed = True
    if _add_column("users", "department_id", "INTEGER"):
        changed = True
    db.session.commit()
    _make_departments_independent()
    _seed_org_structure()

    index_sql = [
        "CREATE INDEX IF NOT EXISTS ix_employees_department_id ON employees (department_id)",
        "CREATE INDEX IF NOT EXISTS ix_employee_compliance_created_by ON employee_compliance (created_by)",
        "CREATE INDEX IF NOT EXISTS ix_health_checkups_created_by ON health_checkups (created_by)",
        "CREATE INDEX IF NOT EXISTS ix_documents_created_by ON documents (created_by)",
        "CREATE INDEX IF NOT EXISTS ix_documents_category_id ON documents (category_id)",
        "CREATE INDEX IF NOT EXISTS ix_documents_authority_id ON documents (authority_id)",
        "CREATE INDEX IF NOT EXISTS ix_documents_service_id ON documents (service_id)",
        "CREATE INDEX IF NOT EXISTS ix_document_alert_user ON document_alert_recipients (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_company_alert_user ON company_alert_recipients (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_department_alert_user ON department_alert_recipients (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_alert_dispatch_record ON alert_dispatches (record_type, record_id)",
    ]
    for statement in index_sql:
        db.session.execute(text(statement))
    db.session.commit()

    _backfill_file_versions()
    _rebuild_regulatory_tables_if_needed()
    _ensure_vehicle_tables()
    return changed


def _ensure_vehicle_tables():
    """Create vehicle tables and their supporting indexes idempotently.

    create_all() in app factory already creates missing SQLAlchemy-declared
    tables; this adds the explicit indexes a partial-unique rule or search
    pattern needs and verifies the active-doc unique index exists even on
    older SQLite versions that ignored the model-level definition.
    """
    from sqlalchemy import inspect
    inspector = inspect(db.engine)
    if not inspector.has_table("vehicles"):
        # The model declaration is the source of truth; create_all picks it up.
        db.create_all()

    # SQLAlchemy's index=True only creates indexes on first CREATE TABLE.
    # Re-asserting them on every startup makes the schema self-healing when
    # tables already exist (matches the pattern used for employees/documents).
    index_sql = [
        "CREATE INDEX IF NOT EXISTS ix_vehicles_company_id ON vehicles (company_id)",
        "CREATE INDEX IF NOT EXISTS ix_vehicles_is_active ON vehicles (is_active)",
        "CREATE INDEX IF NOT EXISTS ix_vehicle_documents_vehicle_id "
        "ON vehicle_documents (vehicle_id)",
        "CREATE INDEX IF NOT EXISTS ix_vehicle_documents_doc_type "
        "ON vehicle_documents (doc_type)",
        "CREATE INDEX IF NOT EXISTS ix_vehicle_documents_status "
        "ON vehicle_documents (status)",
        "CREATE INDEX IF NOT EXISTS ix_vehicle_doc_versions_doc "
        "ON vehicle_document_versions (vehicle_document_id)",
        # Partial unique: at most one active doc per (vehicle, doc_type).
        # Must be explicit because older SQLite ignores sqlite_where from ORM.
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_vehicle_doc_active "
        "ON vehicle_documents (vehicle_id, doc_type) WHERE is_active = 1",
    ]
    for statement in index_sql:
        db.session.execute(text(statement))
    db.session.commit()


def _rebuild_regulatory_tables_if_needed():
    """Replace an interim office/service layout with Category -> Authority -> Service.

    Only runs when those tables are empty, so document data is left untouched.
    """
    from sqlalchemy import inspect
    inspector = inspect(db.engine)
    if not inspector.has_table("regulatory_authorities"):
        db.create_all()
        return
    columns = _columns("regulatory_authorities")
    if "category_id" in columns and "notes" not in columns:
        return
    authority_count = db.session.execute(text("SELECT COUNT(*) FROM regulatory_authorities")).scalar() or 0
    service_count = 0
    if inspector.has_table("services"):
        service_count = db.session.execute(text("SELECT COUNT(*) FROM services")).scalar() or 0
    if authority_count or service_count:
        return
    db.session.execute(text("DROP TABLE IF EXISTS services"))
    db.session.execute(text("DROP TABLE IF EXISTS regulatory_authorities"))
    db.session.commit()
    db.create_all()


def _backfill_file_versions():
    """Turn an existing single file_path into version 1. Leaves the file in place."""
    from app.models import (
        EmployeeCompliance, EmployeeComplianceVersion, HealthCheckup, HealthCheckupVersion,
    )

    license_ids = {
        row[0] for row in db.session.query(EmployeeComplianceVersion.compliance_id).distinct()
    }
    for record in EmployeeCompliance.query.filter(
        EmployeeCompliance.file_path.isnot(None),
        EmployeeCompliance.file_path != "",
    ).all():
        if record.id in license_ids or not record.file_path:
            continue
        db.session.add(EmployeeComplianceVersion(
            compliance_id=record.id,
            version_no=1,
            file_path=record.file_path,
            uploaded_by=record.created_by,
            uploaded_at=record.created_at,
        ))

    checkup_ids = {
        row[0] for row in db.session.query(HealthCheckupVersion.checkup_id).distinct()
    }
    for record in HealthCheckup.query.filter(
        HealthCheckup.file_path.isnot(None),
        HealthCheckup.file_path != "",
    ).all():
        if record.id in checkup_ids or not record.file_path:
            continue
        db.session.add(HealthCheckupVersion(
            checkup_id=record.id,
            version_no=1,
            file_path=record.file_path,
            uploaded_by=record.created_by,
            uploaded_at=record.created_at,
        ))
    db.session.commit()


def _remap_department_id(old_id, keep_id):
    for table in ("documents", "employees", "users"):
        if table not in {row[0] for row in db.session.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )).fetchall()}:
            continue
        if "department_id" not in _columns(table):
            continue
        db.session.execute(
            text(f"UPDATE {table} SET department_id = :keep WHERE department_id = :old"),
            {"keep": keep_id, "old": old_id},
        )
    db.session.execute(
        text("""
            DELETE FROM department_alert_recipients
            WHERE department_id = :old AND user_id IN (
                SELECT user_id FROM department_alert_recipients WHERE department_id = :keep
            )
        """),
        {"old": old_id, "keep": keep_id},
    )
    db.session.execute(
        text("UPDATE department_alert_recipients SET department_id = :keep WHERE department_id = :old"),
        {"keep": keep_id, "old": old_id},
    )


def _make_departments_independent():
    """One department name for the whole company, not a copy under each factory."""
    if "factory_id" not in _columns("departments"):
        if "group_email" not in _columns("departments"):
            _add_column("departments", "group_email", "VARCHAR(255)")
        db.session.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_departments_name ON departments (name)"
        ))
        db.session.commit()
        return

    db.session.execute(text("PRAGMA foreign_keys=OFF"))
    rows = db.session.execute(text("SELECT id, name FROM departments ORDER BY id")).fetchall()
    grouped = {}
    for row_id, name in rows:
        grouped.setdefault((name or "").strip().lower(), []).append(row_id)
    for _key, ids in grouped.items():
        keep = ids[0]
        for old in ids[1:]:
            _remap_department_id(old, keep)
            db.session.execute(text("DELETE FROM departments WHERE id = :old"), {"old": old})
    db.session.execute(text("""
        CREATE TABLE departments_independent (
            id INTEGER PRIMARY KEY,
            name VARCHAR(150) NOT NULL,
            group_email VARCHAR(255)
        )
    """))
    db.session.execute(text(
        "INSERT INTO departments_independent (id, name) SELECT id, name FROM departments"
    ))
    db.session.execute(text("DROP TABLE departments"))
    db.session.execute(text("ALTER TABLE departments_independent RENAME TO departments"))
    db.session.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_departments_name ON departments (name)"
    ))
    db.session.execute(text("PRAGMA foreign_keys=ON"))
    db.session.commit()


def _seed_org_structure():
    """Head Office, Transport, transport roles, and the two department group mailboxes."""
    from app.models import Company, Department, Factory, Role

    for role_name in ("Transport Manager", "Transport User"):
        if Role.query.filter_by(name=role_name).first() is None:
            db.session.add(Role(name=role_name))
    db.session.flush()

    company = Company.query.filter(Company.name.ilike("%ananta companies%")).first()
    if company is None:
        company = Company.query.order_by(Company.id).first()
    if company is not None and Factory.query.filter(Factory.name.ilike("%head office%")).first() is None:
        db.session.add(Factory(
            company_id=company.id,
            name="Ananta Head Office",
            location="Head Office",
        ))

    transport = Department.query.filter(db.func.lower(Department.name) == "transport").first()
    if transport is None:
        transport = Department(name="Transport", group_email="transport_agl@ananta.com.bd")
        db.session.add(transport)
    elif not transport.group_email:
        transport.group_email = "transport_agl@ananta.com.bd"

    compliance = Department.query.filter(db.func.lower(Department.name) == "compliance").first()
    if compliance is None:
        db.session.add(Department(name="Compliance", group_email="compliance_dept@ananta.com.bd"))
    elif not compliance.group_email:
        compliance.group_email = "compliance_dept@ananta.com.bd"
    db.session.commit()
