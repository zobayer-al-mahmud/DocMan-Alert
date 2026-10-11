from datetime import datetime
from flask_login import UserMixin
from app import db


class Company(db.Model):
    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)

    factories = db.relationship("Factory", backref="company", lazy=True)

    def __repr__(self):
        return f"<Company {self.name}>"


# Users who receive alerts for every applicable record in a company.
# The address always comes from users.email, never a copied address.
company_alert_recipients = db.Table(
    "company_alert_recipients",
    db.Column("company_id", db.Integer, db.ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True),
)


Company.alert_recipients = db.relationship(
    "User",
    secondary=company_alert_recipients,
    backref=db.backref("alert_companies", lazy="select"),
    lazy="select",
)


class Factory(db.Model):
    __tablename__ = "factories"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    location = db.Column(db.String(200))

    documents = db.relationship("Document", backref="factory", lazy=True)
    users = db.relationship("User", backref="factory", lazy=True)
    employees = db.relationship("Employee", backref="factory", lazy=True)

    def __repr__(self):
        return f"<Factory {self.name}>"


class Department(db.Model):
    """A department is shared across factories. It is not owned by one location."""
    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)
    group_email = db.Column(db.String(255))

    documents = db.relationship("Document", backref="department", lazy=True)

    def __repr__(self):
        return f"<Department {self.name}>"


# Users who receive alerts only for records in this department.
department_alert_recipients = db.Table(
    "department_alert_recipients",
    db.Column("department_id", db.Integer, db.ForeignKey("departments.id", ondelete="CASCADE"), primary_key=True),
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True),
)


Department.alert_recipients = db.relationship(
    "User",
    secondary=department_alert_recipients,
    backref=db.backref("alert_departments", lazy="select"),
    lazy="select",
)


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

    users = db.relationship("User", backref="role", lazy=True)

    def __repr__(self):
        return f"<Role {self.name}>"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), nullable=True, index=True)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=True, index=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(150))
    email = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Persistent login-lockout state (survives app restarts, unlike the old
    # in-memory tracker). Enforced in routes/auth.py.
    failed_login_attempts = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash, password)

    department = db.relationship("Department", foreign_keys=[department_id])

    def can_access_all_factories(self):
        return self.role.name in ["Super Admin", "Compliance Viewer", "Administration"]

    def __repr__(self):
        return f"<User {self.username}>"


# Optional extra recipients for one document. Company and department recipients
# are added automatically at send time and are not stored here.
document_alert_recipients = db.Table(
    "document_alert_recipients",
    db.Column("document_id", db.Integer,
              db.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    db.Column("user_id", db.Integer,
              db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class ServiceCategory(db.Model):
    __tablename__ = "service_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False)

    authorities = db.relationship("RegulatoryAuthority", backref="category", lazy=True)

    def __repr__(self):
        return f"<ServiceCategory {self.name}>"


class RegulatoryAuthority(db.Model):
    __tablename__ = "regulatory_authorities"

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("service_categories.id"), nullable=False, index=True)
    name = db.Column(db.String(250), nullable=False)

    services = db.relationship("Service", backref="authority", lazy=True)

    def __repr__(self):
        return f"<RegulatoryAuthority {self.name}>"


class Service(db.Model):
    __tablename__ = "services"

    id = db.Column(db.Integer, primary_key=True)
    authority_id = db.Column(db.Integer, db.ForeignKey("regulatory_authorities.id"), nullable=False, index=True)
    name = db.Column(db.String(300), nullable=False)

    def __repr__(self):
        return f"<Service {self.name}>"


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), nullable=False, index=True)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("service_categories.id"), index=True)
    authority_id = db.Column(db.Integer, db.ForeignKey("regulatory_authorities.id"), index=True)
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), index=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)

    doc_name = db.Column(db.String(200), nullable=False)
    doc_type = db.Column(db.String(100), index=True)
    cert_no = db.Column(db.String(100))
    issue_date = db.Column(db.Date)
    expiry_date = db.Column(db.Date, index=True)
    # No-expiry documents use expiry_date = NULL together with no_expiry = True.
    # We never store placeholder text like "Till Now" in a date field.
    no_expiry = db.Column(db.Boolean, default=False, nullable=False)
    issuing_authority = db.Column(db.String(200))
    responsible_person = db.Column(db.String(150))
    status = db.Column(db.String(30), default="Valid", index=True)
    renewal_status = db.Column(db.String(50), default="Not Started")
    remarks = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    versions = db.relationship("DocumentVersion", backref="document", lazy=True,
                                order_by="DocumentVersion.version_no",
                                cascade="all, delete-orphan")
    creator = db.relationship("User", foreign_keys=[created_by])
    category = db.relationship("ServiceCategory", foreign_keys=[category_id])
    authority = db.relationship("RegulatoryAuthority", foreign_keys=[authority_id])
    service = db.relationship("Service", foreign_keys=[service_id])

    # Optional document-specific recipients. Company, department, and the
    # uploader are resolved separately and are not stored in this list.
    alert_recipients = db.relationship(
        "User", secondary=document_alert_recipients,
        backref=db.backref("alert_documents", lazy="dynamic"), lazy="selectin",
    )

    def __repr__(self):
        return f"<Document {self.doc_name}>"


class DocumentVersion(db.Model):
    __tablename__ = "document_versions"
    __table_args__ = (
        db.UniqueConstraint("document_id", "version_no", name="ux_document_version"),
    )

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id"), nullable=False, index=True)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    version_no = db.Column(db.Integer, nullable=False, default=1)
    file_path = db.Column(db.String(400), nullable=False)
    # Per-version issue/expiry so renewal history is self-describing.
    issue_date = db.Column(db.Date)
    expiry_date = db.Column(db.Date)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    ocr_result = db.relationship(
        "OcrResult", backref="document_version", uselist=False,
        cascade="all, delete-orphan",
        foreign_keys="OcrResult.document_version_id",
    )

    def __repr__(self):
        return f"<DocumentVersion doc={self.document_id} v{self.version_no}>"


# ---------------------------------------------------------------------------
# Employee compliance
# ---------------------------------------------------------------------------

class Employee(db.Model):
    __tablename__ = "employees"
    __table_args__ = (
        db.UniqueConstraint("factory_id", "employee_code", name="ux_employee_factory_code"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # Business-facing employee ID (e.g. "ID No" from HR sheets). Unique within
    # a factory. Named employee_code to avoid confusion with the PK `id`.
    employee_code = db.Column(db.String(50), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    designation = db.Column(db.String(150))
    section = db.Column(db.String(150), index=True)
    factory_id = db.Column(db.Integer, db.ForeignKey("factories.id"), nullable=False, index=True)
    # Optional link so license/checkup alerts can use department recipients.
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), nullable=True, index=True)
    date_of_joining = db.Column(db.Date)
    # Personal address is never used unless receive_alerts is explicitly enabled.
    email = db.Column(db.String(255))
    receive_alerts = db.Column(db.Boolean, default=False, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    department = db.relationship("Department", backref="employees")
    compliances = db.relationship("EmployeeCompliance", backref="employee", lazy=True,
                                   cascade="all, delete-orphan")
    health_checkups = db.relationship("HealthCheckup", backref="employee", lazy=True,
                                       cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Employee {self.employee_code} {self.name}>"


class EmployeeCompliance(db.Model):
    """Person-level licenses / registrations (electrician, medical, boiler, ...)."""
    __tablename__ = "employee_compliance"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    compliance_type = db.Column(db.String(100), nullable=False, index=True)
    registration_number = db.Column(db.String(100))
    category = db.Column(db.String(100))
    issue_date = db.Column(db.Date)
    expiry_date = db.Column(db.Date, index=True)
    no_expiry = db.Column(db.Boolean, default=False, nullable=False)
    status = db.Column(db.String(30), default="Valid", index=True)
    issuing_authority = db.Column(db.String(200))
    remarks = db.Column(db.Text)
    file_path = db.Column(db.String(400))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    creator = db.relationship("User", foreign_keys=[created_by])
    file_versions = db.relationship(
        "EmployeeComplianceVersion", backref="compliance", lazy=True,
        order_by="EmployeeComplianceVersion.version_no",
        cascade="all, delete-orphan",
    )
    ocr_result = db.relationship(
        "OcrResult", backref="employee_compliance", uselist=False,
        cascade="all, delete-orphan",
        foreign_keys="OcrResult.employee_compliance_id",
    )

    def __repr__(self):
        return f"<EmployeeCompliance emp={self.employee_id} {self.compliance_type}>"


class HealthCheckup(db.Model):
    """One row per exam per employee (CBC, Hearing/Widal, Chest X-Ray, Urine...)."""
    __tablename__ = "health_checkups"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    exam_type = db.Column(db.String(100), nullable=False, index=True)
    report_date = db.Column(db.Date)
    expiry_date = db.Column(db.Date, index=True)
    no_expiry = db.Column(db.Boolean, default=False, nullable=False)
    status = db.Column(db.String(30), default="Valid", index=True)
    pre_yn = db.Column(db.String(10))      # PRE Y/N
    test_center = db.Column(db.String(200))
    file_path = db.Column(db.String(400))
    remarks = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    creator = db.relationship("User", foreign_keys=[created_by])
    file_versions = db.relationship(
        "HealthCheckupVersion", backref="checkup", lazy=True,
        order_by="HealthCheckupVersion.version_no",
        cascade="all, delete-orphan",
    )
    ocr_result = db.relationship(
        "OcrResult", backref="health_checkup", uselist=False,
        cascade="all, delete-orphan",
        foreign_keys="OcrResult.health_checkup_id",
    )

    def __repr__(self):
        return f"<HealthCheckup emp={self.employee_id} {self.exam_type}>"


license_alert_recipients = db.Table(
    "license_alert_recipients",
    db.Column("compliance_id", db.Integer, db.ForeignKey("employee_compliance.id", ondelete="CASCADE"), primary_key=True),
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True),
)

checkup_alert_recipients = db.Table(
    "checkup_alert_recipients",
    db.Column("checkup_id", db.Integer, db.ForeignKey("health_checkups.id", ondelete="CASCADE"), primary_key=True),
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True),
)

EmployeeCompliance.alert_recipients = db.relationship(
    "User", secondary=license_alert_recipients,
    backref=db.backref("alert_licenses", lazy="select"), lazy="select",
)
HealthCheckup.alert_recipients = db.relationship(
    "User", secondary=checkup_alert_recipients,
    backref=db.backref("alert_checkups", lazy="select"), lazy="select",
)


class EmployeeComplianceVersion(db.Model):
    """Previous and current files for one employee license. The current path
    is also stored on employee_compliance.file_path."""
    __tablename__ = "employee_compliance_versions"
    __table_args__ = (
        db.UniqueConstraint("compliance_id", "version_no", name="ux_license_file_version"),
    )

    id = db.Column(db.Integer, primary_key=True)
    compliance_id = db.Column(db.Integer, db.ForeignKey("employee_compliance.id"), nullable=False, index=True)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    version_no = db.Column(db.Integer, nullable=False, default=1)
    file_path = db.Column(db.String(400), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


class HealthCheckupVersion(db.Model):
    __tablename__ = "health_checkup_versions"
    __table_args__ = (
        db.UniqueConstraint("checkup_id", "version_no", name="ux_checkup_file_version"),
    )

    id = db.Column(db.Integer, primary_key=True)
    checkup_id = db.Column(db.Integer, db.ForeignKey("health_checkups.id"), nullable=False, index=True)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    version_no = db.Column(db.Integer, nullable=False, default=1)
    file_path = db.Column(db.String(400), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


class AlertDispatch(db.Model):
    """One row per emergency alert already sent for a record and threshold.

    expiry_key is part of the key so a renewal (new expiry date) can alert
    again. Weekly summary emails are not recorded here.
    """
    __tablename__ = "alert_dispatches"
    __table_args__ = (
        db.UniqueConstraint(
            "record_type", "record_id", "alert_stage", "expiry_key",
            name="ux_alert_dispatch",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    record_type = db.Column(db.String(20), nullable=False, index=True)  # document, license, checkup
    record_id = db.Column(db.Integer, nullable=False, index=True)
    alert_stage = db.Column(db.String(40), nullable=False)
    expiry_key = db.Column(db.String(10), nullable=False, default="")
    sent_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class AlertEmail(db.Model):
    """One row per alert message handed to the mail server.

    This is the history of sent and failed expiry alerts. It is separate from
    alert_dispatches, which only stops a repeat emergency for the same expiry.
    """
    __tablename__ = "alert_emails"

    id = db.Column(db.Integer, primary_key=True)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    alert_type = db.Column(db.String(20), nullable=False, index=True)  # weekly, emergency, test
    subject = db.Column(db.String(500), nullable=False, default="")
    sender = db.Column(db.String(255), nullable=False, default="")
    recipient_email = db.Column(db.String(255), nullable=False, index=True)
    recipient_name = db.Column(db.String(150), nullable=False, default="")
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False, index=True)  # sent, failed
    error_detail = db.Column(db.String(80))
    record_count = db.Column(db.Integer, nullable=False, default=0)

    recipient = db.relationship("User", foreign_keys=[user_id])
    items = db.relationship(
        "AlertEmailItem",
        backref="alert_email",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="AlertEmailItem.id",
    )


class AlertEmailItem(db.Model):
    """One compliance record named inside a sent or failed alert email."""
    __tablename__ = "alert_email_items"

    id = db.Column(db.Integer, primary_key=True)
    alert_email_id = db.Column(
        db.Integer, db.ForeignKey("alert_emails.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    record_type = db.Column(db.String(20), nullable=False, index=True)
    record_id = db.Column(db.Integer, nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False, default="")
    company_name = db.Column(db.String(200), nullable=False, default="")
    factory_name = db.Column(db.String(200), nullable=False, default="")
    factory_id = db.Column(db.Integer, index=True)
    department_name = db.Column(db.String(200), nullable=False, default="")
    expiry_date = db.Column(db.Date)
    record_status = db.Column(db.String(40), nullable=False, default="")
    sources = db.Column(db.String(200), nullable=False, default="")


class OcrResult(db.Model):
    """One current OCR result per document version, license, or checkup.

    extracted_text is stored exactly as returned. suggested_fields are hints
    only and never overwrite compliance data by themselves.
    """
    __tablename__ = "ocr_results"

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id"), nullable=True, index=True)
    document_version_id = db.Column(
        db.Integer, db.ForeignKey("document_versions.id"), nullable=True, unique=True, index=True)
    employee_compliance_id = db.Column(
        db.Integer, db.ForeignKey("employee_compliance.id"), nullable=True, unique=True, index=True)
    health_checkup_id = db.Column(
        db.Integer, db.ForeignKey("health_checkups.id"), nullable=True, unique=True, index=True)

    extracted_text = db.Column(db.Text)
    # Digit-normalized copy used only for search. The original text is never rewritten.
    search_text = db.Column(db.Text)
    language = db.Column(db.String(40))
    ocr_status = db.Column(db.String(20), default="PENDING", index=True)
    confidence = db.Column(db.Float)
    suggested_fields = db.Column(db.Text)
    confirmed_at = db.Column(db.DateTime)
    processed_at = db.Column(db.DateTime)
    processing_time = db.Column(db.Float)
    error_message = db.Column(db.String(400))

    def __repr__(self):
        return f"<OcrResult {self.ocr_status}>"


# ---------------------------------------------------------------------------
# System logs (Super Admin only). Technical/operational events ONLY.
# This is NOT an audit trail: it never stores business change history or
# before/after document values.
# ---------------------------------------------------------------------------

class SystemLog(db.Model):
    __tablename__ = "system_logs"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    level = db.Column(db.String(10), default="INFO", index=True)   # INFO / WARNING / ERROR
    event_type = db.Column(db.String(40), index=True)              # APP_START, LOGIN_SUCCESS, ...
    username_or_process = db.Column(db.String(120))
    ip_address = db.Column(db.String(50))
    message = db.Column(db.Text)

    def __repr__(self):
        return f"<SystemLog {self.level} {self.event_type}>"
