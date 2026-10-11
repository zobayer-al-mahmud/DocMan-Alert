from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import (StringField, PasswordField, SubmitField, SelectField,
                     SelectMultipleField, DateField, TextAreaField, BooleanField)
from wtforms.validators import DataRequired, Length, Optional, EqualTo, Regexp

EMAIL_REGEX = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

UPLOAD_EXTENSIONS = ["pdf", "jpg", "jpeg", "png", "docx", "xlsx"]
UPLOAD_MESSAGE = "Only PDF, image (JPG/PNG), or Office (DOCX/XLSX) files are allowed."


def _blank_int(value):
    if value in (None, "", "None"):
        return 0
    return int(value)


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Login")


class CompanyForm(FlaskForm):
    name = StringField("Company Name", validators=[DataRequired(), Length(max=150)])
    code = StringField("Company Code", validators=[DataRequired(), Length(max=20)])
    submit = SubmitField("Save")


class FactoryForm(FlaskForm):
    company_id = SelectField("Company", coerce=int, validators=[DataRequired()])
    name = StringField("Factory Name", validators=[DataRequired(), Length(max=150)])
    location = StringField("Location", validators=[Length(max=200)])
    submit = SubmitField("Save")


class DepartmentForm(FlaskForm):
    name = StringField("Department Name", validators=[DataRequired(), Length(max=150)])
    group_email = StringField("Group Email", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Save")


class DocumentForm(FlaskForm):
    factory_id = SelectField("Factory", coerce=int, validators=[DataRequired()])
    department_id = SelectField("Department", coerce=int, validators=[Optional()])
    doc_name = StringField("Document Name", validators=[DataRequired(), Length(max=200)])
    # Free text with suggestions (see template datalist) so administrators can
    # maintain the list of document types without code changes.
    doc_type = StringField("Document Type", validators=[DataRequired(), Length(max=100)])
    cert_no = StringField("Certificate/License No.", validators=[DataRequired(), Length(max=100)])
    issue_date = DateField("Issue Date", validators=[Optional()])
    expiry_date = DateField("Expiry Date", validators=[Optional()])
    no_expiry = BooleanField("This document has no expiry date")
    issuing_authority = StringField("Issuing Authority", validators=[Length(max=200)])
    responsible_person = StringField("Responsible Person", validators=[Length(max=150)])
    renewal_status = SelectField("Renewal Status", choices=[
        ("Not Started", "Not Started"),
        ("In Progress", "In Progress"),
        ("Completed", "Completed"),
    ])
    remarks = TextAreaField("Remarks", validators=[Optional()])
    category_id = SelectField("Category", coerce=_blank_int, validators=[Optional()], default=0)
    authority_id = SelectField("Regulatory Authority", coerce=_blank_int, validators=[Optional()], default=0)
    service_id = SelectField("Service", coerce=_blank_int, validators=[Optional()], default=0)
    alert_recipients = SelectMultipleField("Alert Recipients", coerce=int, validators=[Optional()])
    file = FileField("Upload File", validators=[FileAllowed(UPLOAD_EXTENSIONS, UPLOAD_MESSAGE)])
    submit = SubmitField("Save")


class DocumentRenewForm(FlaskForm):
    issue_date = DateField("New Issue Date", validators=[Optional()])
    expiry_date = DateField("New Expiry Date", validators=[Optional()])
    no_expiry = BooleanField("No expiry date")
    cert_no = StringField("New Certificate/License No.", validators=[Length(max=100)])
    remarks = TextAreaField("Remarks", validators=[Optional()])
    file = FileField("New File", validators=[FileAllowed(UPLOAD_EXTENSIONS, UPLOAD_MESSAGE)])
    submit = SubmitField("Create New Version")


class ServiceCategoryForm(FlaskForm):
    name = StringField("Category Name", validators=[DataRequired(), Length(max=150)])
    submit = SubmitField("Save")


class RegulatoryAuthorityForm(FlaskForm):
    category_id = SelectField("Category", coerce=int, validators=[DataRequired()])
    name = StringField("Authority / Office Name", validators=[DataRequired(), Length(max=250)])
    submit = SubmitField("Save")


class ServiceForm(FlaskForm):
    authority_id = SelectField("Regulatory Authority", coerce=int, validators=[DataRequired()])
    name = StringField("Service Name", validators=[DataRequired(), Length(max=300)])
    submit = SubmitField("Save")


class UserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=80)])
    full_name = StringField("Full Name", validators=[DataRequired(), Length(max=150)])
    email = StringField("Email", validators=[
        Optional(), Length(max=255),
        Regexp(EMAIL_REGEX, message="Enter a valid email address."),
    ])
    role_id = SelectField("Role", coerce=int, validators=[DataRequired()])
    factory_id = SelectField("Factory", coerce=int, validators=[Optional()])
    department_id = SelectField("Department", coerce=_blank_int, validators=[Optional()], default=0)
    password = PasswordField("Password", validators=[Optional(), Length(min=6)])
    confirm_password = PasswordField("Confirm Password", validators=[EqualTo("password", message="Passwords must match")])
    is_active = SelectField("Status", choices=[("1", "Active"), ("0", "Inactive")], default="1")
    submit = SubmitField("Save")


# --- Employee compliance ----------------------------------------------------

class EmployeeForm(FlaskForm):
    employee_code = StringField("Employee ID", validators=[DataRequired(), Length(max=50)])
    name = StringField("Name", validators=[DataRequired(), Length(max=150)])
    designation = StringField("Designation", validators=[Optional(), Length(max=150)])
    section = StringField("Section", validators=[Optional(), Length(max=150)])
    factory_id = SelectField("Factory", coerce=int, validators=[DataRequired()])
    department_id = SelectField("Department", coerce=_blank_int, validators=[Optional()], default=0)
    date_of_joining = DateField("Date of Joining", validators=[Optional()])
    email = StringField("Email", validators=[
        Optional(), Length(max=255),
        Regexp(EMAIL_REGEX, message="Enter a valid email address."),
    ])
    receive_alerts = BooleanField("Send alerts to this employee")
    is_active = SelectField("Status", choices=[("1", "Active"), ("0", "Inactive")], default="1")
    submit = SubmitField("Save")


class EmployeeComplianceForm(FlaskForm):
    compliance_type = StringField("Compliance Type", validators=[DataRequired(), Length(max=100)])
    registration_number = StringField("Registration Number", validators=[Optional(), Length(max=100)])
    category = StringField("Category", validators=[Optional(), Length(max=100)])
    issuing_authority = StringField("Issuing Authority", validators=[Optional(), Length(max=200)])
    issue_date = DateField("Issue Date", validators=[Optional()])
    expiry_date = DateField("Expiry Date", validators=[Optional()])
    no_expiry = BooleanField("No expiry date")
    remarks = TextAreaField("Remarks", validators=[Optional()])
    alert_recipients = SelectMultipleField("Additional Alert Recipients", coerce=int, validators=[Optional()])
    file = FileField("Upload File", validators=[FileAllowed(UPLOAD_EXTENSIONS, UPLOAD_MESSAGE)])
    submit = SubmitField("Save")


class HealthCheckupForm(FlaskForm):
    exam_type = SelectField("Exam Type", choices=[
        ("CBC", "CBC"),
        ("Hearing / Widal", "Hearing / Widal"),
        ("Chest X-Ray P/A", "Chest X-Ray P/A"),
        ("Urine Test", "Urine Test"),
    ], validators=[DataRequired()])
    report_date = DateField("Report Date", validators=[Optional()])
    expiry_date = DateField("Expiry Date", validators=[Optional()])
    no_expiry = BooleanField("No expiry date")
    pre_yn = SelectField("PRE (Y/N)", choices=[("", "-"), ("Y", "Y"), ("N", "N")], validators=[Optional()])
    test_center = StringField("Test Center", validators=[Optional(), Length(max=200)])
    remarks = TextAreaField("Remarks", validators=[Optional()])
    alert_recipients = SelectMultipleField("Additional Alert Recipients", coerce=int, validators=[Optional()])
    file = FileField("Upload File", validators=[FileAllowed(UPLOAD_EXTENSIONS, UPLOAD_MESSAGE)])
    submit = SubmitField("Save")
