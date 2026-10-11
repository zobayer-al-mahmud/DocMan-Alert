"""Company/department recipients, emergency alerts, Excel, and license files."""
import io
import os
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "scope-test-key")
os.environ["COMPLIANCE_SKIP_EMERGENCY"] = "1"

from openpyxl import Workbook

from app import create_app, db
from app.models import (
    AlertDispatch, AlertEmail, AlertEmailItem, Company, Department, Document, Employee,
    EmployeeCompliance, EmployeeComplianceVersion, Factory, HealthCheckup,
    HealthCheckupVersion, Role, User,
)
from app.import_utils import annotate_existing, apply_import, build_factory_lookup, parse_date, parse_workbook
from app.utils import (
    STATUS_30, STATUS_7, STATUS_EXPIRED, compute_status, resolve_alert_recipients,
    send_compliance_alert, send_emergency_alerts,
)

PASS = "zz-scope-pass-1"
results = []


def check(name, passed, detail=""):
    results.append((name, bool(passed)))
    extra = f" :: {detail}" if detail and not passed else ""
    print(f"  [{'OK' if passed else 'FAIL'}] {name}{extra}")


def emails_of(record, kind):
    return [row["email"].lower() for row in resolve_alert_recipients(record, kind)]


def sources_of(record, kind, email):
    for row in resolve_alert_recipients(record, kind):
        if row["email"].lower() == email.lower():
            return row["sources"]
    return []


def main():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True
    app.config["ALERT_RECIPIENTS"] = []

    created = {
        "company_id": None,
        "factory_ids": [],
        "department_ids": [],
        "user_ids": [],
        "doc_ids": [],
        "emp_ids": [],
        "paths": [],
    }

    def cleanup():
        with app.app_context():
            doc_ids = created["doc_ids"]
            emp_ids = created["emp_ids"]
            def drop_history(record_type, ids):
                if not ids:
                    return
                parent_ids = [
                    row[0] for row in db.session.query(AlertEmailItem.alert_email_id).filter(
                        AlertEmailItem.record_type == record_type,
                        AlertEmailItem.record_id.in_(ids),
                    ).distinct()
                ]
                if not parent_ids:
                    return
                AlertEmailItem.query.filter(
                    AlertEmailItem.alert_email_id.in_(parent_ids)
                ).delete(synchronize_session=False)
                AlertEmail.query.filter(AlertEmail.id.in_(parent_ids)).delete(synchronize_session=False)

            if doc_ids:
                drop_history("document", doc_ids)
                AlertDispatch.query.filter(
                    AlertDispatch.record_type == "document",
                    AlertDispatch.record_id.in_(doc_ids),
                ).delete(synchronize_session=False)
                for doc in Document.query.filter(Document.id.in_(doc_ids)).all():
                    doc.alert_recipients = []
                    db.session.delete(doc)
            if emp_ids:
                lic_ids = [row.id for row in EmployeeCompliance.query.filter(EmployeeCompliance.employee_id.in_(emp_ids)).all()]
                chk_ids = [row.id for row in HealthCheckup.query.filter(HealthCheckup.employee_id.in_(emp_ids)).all()]
                if lic_ids:
                    drop_history("license", lic_ids)
                    AlertDispatch.query.filter(
                        AlertDispatch.record_type == "license",
                        AlertDispatch.record_id.in_(lic_ids),
                    ).delete(synchronize_session=False)
                if chk_ids:
                    drop_history("checkup", chk_ids)
                    AlertDispatch.query.filter(
                        AlertDispatch.record_type == "checkup",
                        AlertDispatch.record_id.in_(chk_ids),
                    ).delete(synchronize_session=False)
                for emp in Employee.query.filter(Employee.id.in_(emp_ids)).all():
                    db.session.delete(emp)
            if created["user_ids"]:
                for user in User.query.filter(User.id.in_(created["user_ids"])).all():
                    user.department_id = None
                db.session.flush()
            if created["department_ids"]:
                for dept in Department.query.filter(Department.id.in_(created["department_ids"])).all():
                    dept.alert_recipients = []
                    db.session.delete(dept)
            if created["user_ids"]:
                for user in User.query.filter(User.id.in_(created["user_ids"])).all():
                    db.session.delete(user)
            if created["factory_ids"]:
                for factory in Factory.query.filter(Factory.id.in_(created["factory_ids"])).all():
                    db.session.delete(factory)
            if created["company_id"]:
                company = Company.query.get(created["company_id"])
                if company:
                    company.alert_recipients = []
                    db.session.delete(company)
            db.session.commit()
            upload = app.config["UPLOAD_FOLDER"]
            for rel in created["paths"]:
                full = os.path.join(upload, rel.replace("/", os.sep))
                if not os.path.isfile(full):
                    continue
                try:
                    os.remove(full)
                except PermissionError:
                    pass

    try:
        with app.app_context():
            bangla, _no, err = parse_date("০৫/০১/২০২৬")
            check("bangla numerals parse as a date", bangla == date(2026, 1, 5) and err is None, str(bangla))
            till, no_exp, till_err = parse_date("Till Now")
            check("Till Now stays empty", till is None and no_exp and till_err is None)

            roles = {role.name: role for role in Role.query.all()}
            company = Company(name="ZZ Scope Company", code="ZZSC")
            db.session.add(company)
            db.session.flush()
            created["company_id"] = company.id
            factory_a = Factory(company_id=company.id, name="ZZ Scope Factory A", location="A")
            factory_b = Factory(company_id=company.id, name="ZZ Scope Factory B", location="B")
            db.session.add_all([factory_a, factory_b])
            db.session.flush()
            created["factory_ids"] = [factory_a.id, factory_b.id]
            hr = Department(name="ZZ HR")
            compliance = Department(name="ZZ Compliance")
            db.session.add_all([hr, compliance])
            db.session.flush()
            created["department_ids"] = [hr.id, compliance.id]

            def make_user(username, role_name, factory, email, active=True):
                user = User(
                    username=username,
                    full_name=username,
                    role_id=roles[role_name].id,
                    factory_id=factory.id if factory else None,
                    email=email,
                    is_active=active,
                )
                user.set_password(PASS)
                db.session.add(user)
                db.session.flush()
                created["user_ids"].append(user.id)
                return user

            company_user = make_user("zz_scope_company", "Compliance User", factory_a, "zz.company@ananta.com.bd")
            hr_user = make_user("zz_scope_hr", "Compliance User", factory_a, "zz.hr@ananta.com.bd")
            comp_user = make_user("zz_scope_comp", "Compliance User", factory_a, "zz.comp@ananta.com.bd")
            uploader = make_user("zz_scope_up", "Compliance Manager", factory_a, "zz.up@ananta.com.bd")
            specific = make_user("zz_scope_doc", "Compliance User", factory_a, "zz.doc@ananta.com.bd")
            inactive = make_user("zz_scope_off", "Compliance User", factory_a, "zz.off@ananta.com.bd", active=False)
            noemail = make_user("zz_scope_nomail", "Compliance User", factory_a, "not-an-email")
            other_factory_user = make_user("zz_scope_other", "Compliance User", factory_b, "zz.other@ananta.com.bd")
            viewer = make_user("zz_scope_view", "Compliance Viewer", None, "zz.view@ananta.com.bd")
            factory_admin_b = make_user("zz_scope_fab", "Factory Admin", factory_b, "zz.fab@ananta.com.bd")

            company_user.department_id = hr.id
            hr_user.department_id = hr.id
            specific.department_id = hr.id
            uploader.department_id = hr.id
            comp_user.department_id = compliance.id
            company.alert_recipients = [company_user, inactive, noemail, other_factory_user]
            hr.alert_recipients = [hr_user]
            compliance.alert_recipients = [comp_user]
            db.session.commit()

            today = date.today()
            doc = Document(
                factory_id=factory_a.id,
                department_id=hr.id,
                created_by=uploader.id,
                doc_name="ZZ Scope Fire License",
                doc_type="Fire License",
                cert_no="ZZ-SCOPE-1",
                expiry_date=today + timedelta(days=20),
                status=compute_status(today + timedelta(days=20)),
                is_active=True,
            )
            other_doc = Document(
                factory_id=factory_a.id,
                department_id=compliance.id,
                created_by=uploader.id,
                doc_name="ZZ Scope Boiler",
                doc_type="Boiler Certificate",
                cert_no="ZZ-SCOPE-2",
                expiry_date=today + timedelta(days=5),
                status=compute_status(today + timedelta(days=5)),
                is_active=True,
            )
            bare = Document(
                factory_id=factory_a.id,
                doc_name="ZZ Scope Bare",
                doc_type="Other",
                cert_no="ZZ-SCOPE-3",
                expiry_date=today + timedelta(days=3),
                status=compute_status(today + timedelta(days=3)),
                is_active=True,
            )
            factory_b_doc = Document(
                factory_id=factory_b.id,
                doc_name="ZZ Scope Factory B",
                doc_type="Fire License",
                cert_no="ZZ-SCOPE-4",
                expiry_date=today + timedelta(days=4),
                status=compute_status(today + timedelta(days=4)),
                is_active=True,
            )
            db.session.add_all([doc, other_doc, bare, factory_b_doc])
            db.session.flush()
            doc.alert_recipients = [specific, company_user]
            db.session.commit()
            created["doc_ids"].extend([doc.id, other_doc.id, bare.id, factory_b_doc.id])
            db.session.refresh(doc)
            db.session.refresh(other_doc)

            doc_emails = emails_of(doc, "document")
            check("assigned person included", "zz.doc@ananta.com.bd" in doc_emails)
            check("assigned company user still included", "zz.company@ananta.com.bd" in doc_emails)
            check("department skipped when a person is assigned", "zz.hr@ananta.com.bd" not in doc_emails)
            check("uploader included", "zz.up@ananta.com.bd" in doc_emails)
            check("duplicate person once", doc_emails.count("zz.company@ananta.com.bd") == 1)
            check("inactive user excluded", "zz.off@ananta.com.bd" not in doc_emails)
            check("invalid email excluded", "not-an-email" not in doc_emails)
            check("other department excluded", "zz.comp@ananta.com.bd" not in doc_emails)
            saved_company_recipients = list(company.alert_recipients)
            company.alert_recipients = []
            db.session.commit()
            # Policy: the compliance group mailbox is a global watcher that is
            # copied on EVERY document alert, so an "empty" factory/department
            # setup still has it as the single recipient.
            check(
                "no recipients means only the compliance group remains",
                emails_of(bare, "document") == ["compliance_dept@ananta.com.bd"],
                str(emails_of(bare, "document")),
            )
            company.alert_recipients = saved_company_recipients
            db.session.commit()
            check(
                "factory-scoped company user misses the other factory",
                "zz.company@ananta.com.bd" not in emails_of(factory_b_doc, "document"),
            )
            check(
                "other factory user receives own factory record",
                "zz.other@ananta.com.bd" in emails_of(factory_b_doc, "document"),
            )
            check(
                "uploader source recorded",
                "Uploader" in sources_of(doc, "document", "zz.up@ananta.com.bd"),
            )
            check(
                "assigned source is document-specific only",
                sources_of(doc, "document", "zz.company@ananta.com.bd") == ["Document-specific"],
                str(sources_of(doc, "document", "zz.company@ananta.com.bd")),
            )

            employee = Employee(
                employee_code="ZZSCOPE1",
                name="ZZ Scope Employee",
                factory_id=factory_a.id,
                department_id=hr.id,
                email="zz.employee@ananta.com.bd",
                receive_alerts=False,
                is_active=True,
            )
            db.session.add(employee)
            db.session.flush()
            created["emp_ids"].append(employee.id)
            license_row = EmployeeCompliance(
                employee_id=employee.id,
                compliance_type="Electrician License",
                registration_number="ZZ-EL-1",
                expiry_date=today + timedelta(days=6),
                status=compute_status(today + timedelta(days=6)),
                created_by=uploader.id,
                is_active=True,
            )
            checkup = HealthCheckup(
                employee_id=employee.id,
                exam_type="CBC",
                report_date=today,
                expiry_date=today + timedelta(days=1),
                status=compute_status(today + timedelta(days=1)),
                created_by=uploader.id,
                is_active=True,
            )
            db.session.add_all([license_row, checkup])
            db.session.commit()
            lic_emails = emails_of(license_row, "license")
            check("license prefers department over company", "zz.company@ananta.com.bd" not in lic_emails)
            check("license uses employee department", "zz.hr@ananta.com.bd" in lic_emails)
            check("license uses uploader", "zz.up@ananta.com.bd" in lic_emails)
            check("employee email off by default", "zz.employee@ananta.com.bd" not in lic_emails)
            employee.receive_alerts = True
            db.session.commit()
            check("employee email on when enabled", "zz.employee@ananta.com.bd" in emails_of(license_row, "license"))
            check("checkup uses same department", "zz.hr@ananta.com.bd" in emails_of(checkup, "checkup"))
            check("license status is 7-day", license_row.status == STATUS_7, license_row.status)

            sent_messages = []

            def fake_send(message):
                sent_messages.append((message.subject, list(message.recipients), message.html))

            from app import mail as flask_mail
            original_send = flask_mail.send
            flask_mail.send = fake_send
            try:
                normal_doc = doc
                urgent = other_doc
                ok, _err, count = send_compliance_alert(documents=[normal_doc], licenses=[], checkups=[])
                check("30-day record uses the normal alert", ok and any(
                    subject.startswith("Compliance Alert:") and "ZZ Scope Fire License" in subject
                    for subject, _to, _html in sent_messages
                ), str(count))
                sent_messages.clear()
                ok, _err, count = send_emergency_alerts(documents=[normal_doc], licenses=[], checkups=[])
                check("30-day record is not an emergency", (not ok) and count == 0 and not sent_messages)

                ok, _err, count = send_emergency_alerts(documents=[urgent], licenses=[], checkups=[])
                subjects = [subject for subject, _to, _html in sent_messages]
                check("7-day record sends an emergency", ok and count == 1, str(subjects))
                check("emergency subject is marked urgent", any(subject.startswith("URGENT:") for subject in subjects))
                check("emergency names the document", any("ZZ Scope Boiler" in subject for subject in subjects))
                recipients = sorted({addr for _s, to, _h in sent_messages for addr in to})
                check("emergency goes to department not HR", "zz.comp@ananta.com.bd" in recipients)
                check("HR does not get the compliance emergency", "zz.hr@ananta.com.bd" not in recipients)
                check(
                    "emergency recipient matches the document department (plus global compliance watcher)",
                    recipients == sorted(["zz.comp@ananta.com.bd", "compliance_dept@ananta.com.bd"]),
                    str(recipients),
                )
                sent_messages.clear()
                ok2, _err2, count2 = send_emergency_alerts(documents=[urgent], licenses=[], checkups=[])
                check("duplicate emergency is not sent again", (not ok2) and count2 == 0 and not sent_messages)

                one_day = factory_b_doc
                ok, _err, count = send_emergency_alerts(documents=[one_day], licenses=[], checkups=[])
                check("a second critical record sends its own emergency", ok and count == 1)

                expired = bare
                expired.expiry_date = today - timedelta(days=1)
                expired.status = compute_status(expired.expiry_date)
                db.session.commit()
                sent_messages.clear()
                ok, _err, count = send_emergency_alerts(documents=[expired], licenses=[], checkups=[])
                check("expired record sends an emergency", ok and any("Expired" in subject for subject, _t, _h in sent_messages))
                check("expired body names company", any("ZZ Scope Company" in html for _s, _t, html in sent_messages))

                failed = Document(
                    factory_id=factory_a.id,
                    department_id=hr.id,
                    created_by=uploader.id,
                    doc_name="ZZ Scope SMTP",
                    doc_type="Fire License",
                    cert_no="ZZ-SCOPE-SMTP",
                    expiry_date=today,
                    status=STATUS_EXPIRED,
                    is_active=True,
                )
                db.session.add(failed)
                db.session.commit()
                created["doc_ids"].append(failed.id)

                def boom(message):
                    raise RuntimeError("smtp down")

                flask_mail.send = boom
                ok, err, count = send_emergency_alerts(documents=[failed], licenses=[], checkups=[])
                marked = AlertDispatch.query.filter_by(record_type="document", record_id=failed.id).count()
                check("failed SMTP does not mark the alert sent", (not ok) and marked == 0, err or "")
                weekly_items = AlertEmailItem.query.filter_by(
                    record_type="document", record_id=normal_doc.id,
                ).all()
                check("weekly send is stored in alert history", any(
                    item.alert_email.alert_type == "weekly" and item.alert_email.status == "sent"
                    for item in weekly_items
                ))
                check("history names the assigned recipient", any(
                    item.alert_email.recipient_email.lower() == "zz.doc@ananta.com.bd"
                    for item in weekly_items
                ))
                check("history records why the person was chosen", any(
                    "Document-specific" in (item.sources or "") for item in weekly_items
                ))
                check("assigned document was not copied to the department", not any(
                    item.alert_email.recipient_email.lower() == "zz.hr@ananta.com.bd"
                    for item in weekly_items
                ))
                failed_items = AlertEmailItem.query.filter_by(
                    record_type="document", record_id=failed.id,
                ).all()
                check("failed send is stored", any(item.alert_email.status == "failed" for item in failed_items))
                check("failed history keeps only the error type", all(
                    (item.alert_email.error_detail or "") in ("", "RuntimeError") for item in failed_items
                ))
            finally:
                flask_mail.send = original_send

            flask_mail.send = fake_send
            sent_messages.clear()
            try:
                ok, _err, count = send_emergency_alerts(documents=[], licenses=[license_row], checkups=[checkup])
                check("license and checkup can send emergencies", ok and count == 2, str([item[0] for item in sent_messages]))
            finally:
                flask_mail.send = original_send

            lookup = build_factory_lookup()
            wb = Workbook()
            ws = wb.active
            ws.title = "Alert Recipients"
            ws.append(["Company", "Factory", "Department", "Username", "User Name", "Email", "Recipient Scope"])
            ws.append(["ZZ Scope Company", "ZZ Scope Factory A", "", "zz_scope_company", "Company User", "zz.company@ananta.com.bd", "COMPANY"])
            ws.append(["ZZ Scope Company", "ZZ Scope Factory A", "ZZ HR", "zz_scope_hr", "HR", "zz.hr@ananta.com.bd", "DEPARTMENT"])
            ws.append(["Missing Co", "", "", "zz_scope_hr", "", "zz.hr@ananta.com.bd", "COMPANY"])
            ws.append(["ZZ Scope Company", "ZZ Scope Factory A", "Missing Dept", "zz_scope_hr", "", "zz.hr@ananta.com.bd", "DEPARTMENT"])
            ws.append(["ZZ Scope Company", "ZZ Scope Factory A", "ZZ HR", "nobody_user", "", "nobody@ananta.com.bd", "DEPARTMENT"])
            ws.append(["ZZ Scope Company", "ZZ Scope Factory A", "ZZ HR", "zz_scope_hr", "", "bad-email", "DEPARTMENT"])
            ws.append(["ZZ Scope Company", "ZZ Scope Factory A", "ZZ HR", "zz_scope_hr", "", "zz.hr@ananta.com.bd", "GLOBAL"])
            ws.append(["ZZ Scope Company", "ZZ Scope Factory A", "ZZ Compliance", "zz_scope_doc", "", "zz.doc@ananta.com.bd", "DEPARTMENT"])
            parsed, errors, warnings = parse_workbook(ws, "recipients", lookup)
            check("recipient import rejects missing company", any("unknown company" in e for e in errors), str(errors))
            check("recipient import rejects missing department", any("unknown department" in e for e in errors))
            check("recipient import rejects unknown user", any("not found" in e for e in errors))
            check("recipient import rejects invalid email", any("invalid email" in e for e in errors))
            check("recipient import rejects invalid scope", any("COMPANY or DEPARTMENT" in e for e in errors))
            check("existing company relationship skipped", any("existing relationship" in w.lower() for w in warnings), str(warnings))
            new_rows = [row for row in parsed if row.get("department_name") == "ZZ Compliance"]
            check("new department relationship is valid", len(new_rows) == 1, str(parsed))
            importable, more_warnings = annotate_existing(parsed)
            summary = apply_import(importable)
            check("confirmed import links the new recipient", summary["recipients_linked"] == 1, str(summary))
            check("preview skipped the existing company link", any("existing relationship" in w.lower() for w in warnings + more_warnings))
            again, _e2, warnings2 = parse_workbook(ws, "recipients", lookup)
            check("second preview skips the new link too", any("existing relationship" in w.lower() for w in warnings2))

            emp_wb = Workbook()
            emp_ws = emp_wb.active
            emp_ws.title = "Employees"
            emp_ws.append(["Employee ID", "Employee Name", "Designation", "Section", "Company", "Factory", "Department"])
            emp_ws.append(["ZZSCOPE2", "ZZ Imported", "Operator", "Boiler", "ZZ Scope Company", "ZZ Scope Factory A", "ZZ HR"])
            emp_ws.append(["ZZSCOPE2", "ZZ Imported Dup", "Operator", "Boiler", "ZZ Scope Company", "ZZ Scope Factory A", "ZZ HR"])
            parsed_e, errors_e, warnings_e = parse_workbook(emp_ws, "employees", lookup)
            check("employee sheet parses one new row", len(parsed_e) == 1 and not errors_e, str(errors_e))
            check("duplicate employee row warned", any("duplicate employee" in w for w in warnings_e))
            summary_e = apply_import(parsed_e)
            check("employee import creates the employee", summary_e["employees_created"] == 1, str(summary_e))
            imported = Employee.query.filter_by(employee_code="ZZSCOPE2").first()
            if imported:
                created["emp_ids"].append(imported.id)
                check("imported employee linked to department", imported.department_id == hr.id)
            else:
                check("imported employee linked to department", False, "missing")

        client = app.test_client()

        def login(username):
            return client.post("/login", data={"username": username, "password": PASS}, follow_redirects=True)

        login("zz_scope_view")
        with app.app_context():
            company_id = created["company_id"]
            hr_id = created["department_ids"][0]
        denied = client.post(f"/admin/companies/{company_id}/alert-recipients", data={"user_id": "1"})
        check("viewer cannot modify company recipients", denied.status_code == 403, str(denied.status_code))
        denied_dept = client.post(f"/admin/departments/{hr_id}/alert-recipients", data={"user_id": "1"})
        check("viewer cannot modify department recipients", denied_dept.status_code == 403)

        login("zz_scope_fab")
        cross = client.get(f"/admin/departments/{hr_id}/alert-recipients")
        check("shared department recipients are not factory-locked", cross.status_code == 200, str(cross.status_code))
        check(
            "department recipient page lists only that department",
            b"zz.hr@ananta.com.bd" in cross.data and b"zz.comp@ananta.com.bd" not in cross.data,
        )
        with app.app_context():
            hr_user_id = User.query.filter_by(username="zz_scope_hr").first().id
            comp_id = User.query.filter_by(username="zz_scope_comp").first().id
        forged = client.post(
            f"/admin/departments/{hr_id}/alert-recipients",
            data={"user_id": [str(hr_user_id), str(comp_id)]},
            follow_redirects=True,
        )
        with app.app_context():
            saved_ids = [user.id for user in Department.query.get(hr_id).alert_recipients]
        check(
            "department recipients reject a user from another department",
            forged.status_code == 200 and hr_user_id in saved_ids and comp_id not in saved_ids,
            str(saved_ids),
        )

        login("zz_scope_up")
        with app.app_context():
            emp = Employee.query.filter_by(employee_code="ZZSCOPE1").first()
            emp_id = emp.id
        pdf = (io.BytesIO(b"%PDF-1.4 zz scope license"), "electrician_license.pdf")
        created_resp = client.post(f"/employees/{emp_id}/license/add", data={
            "compliance_type": "Boiler Operator License",
            "registration_number": "ZZ-BL-9",
            "issue_date": "2026-01-05",
            "expiry_date": (date.today() + timedelta(days=20)).isoformat(),
            "file": pdf,
        }, content_type="multipart/form-data", follow_redirects=True)
        check("create license with file", created_resp.status_code == 200)
        with app.app_context():
            lic = EmployeeCompliance.query.filter_by(registration_number="ZZ-BL-9").first()
            check("license file stored", lic is not None and bool(lic.file_path))
            check("license version 1 created", lic is not None and len(lic.file_versions) == 1)
            check("license status from central engine", lic is not None and lic.status == STATUS_30, None if not lic else lic.status)
            check("uploader recorded on license", lic is not None and lic.created_by is not None)
            if lic and lic.file_path:
                created["paths"].append(lic.file_path)
            lic_id = lic.id if lic else None
        if lic_id:
            view = client.get(f"/employees/license/{lic_id}/file")
            check("authorized license file view", view.status_code == 200, str(view.status_code))
            view.close()
            pdf2 = (io.BytesIO(b"%PDF-1.4 renewed"), "electrician_license_v2.pdf")
            renewed = client.post(f"/employees/license/{lic_id}/edit", data={
                "compliance_type": "Boiler Operator License",
                "registration_number": "ZZ-BL-9",
                "issue_date": date.today().isoformat(),
                "expiry_date": (date.today() + timedelta(days=400)).isoformat(),
                "file": pdf2,
            }, content_type="multipart/form-data", follow_redirects=True)
            check("renew license file", renewed.status_code == 200)
            with app.app_context():
                lic = EmployeeCompliance.query.get(lic_id)
                versions = EmployeeComplianceVersion.query.filter_by(compliance_id=lic_id).count()
                check("renewal keeps both file versions", versions == 2, str(versions))
                check("renewal recalculates Valid", lic.status == "Valid", lic.status)
                for version in lic.file_versions:
                    if version.file_path not in created["paths"]:
                        created["paths"].append(version.file_path)

        chk_pdf = (io.BytesIO(b"%PDF-1.4 health"), "health_report.pdf")
        chk_resp = client.post(f"/employees/{emp_id}/checkup/add", data={
            "exam_type": "Urine Test",
            "report_date": "2026-01-05",
            "expiry_date": (date.today() + timedelta(days=2)).isoformat(),
            "pre_yn": "Y",
            "test_center": "XYZ Medical Center",
            "file": chk_pdf,
        }, content_type="multipart/form-data", follow_redirects=True)
        check("create checkup with report", chk_resp.status_code == 200)
        with app.app_context():
            chk = HealthCheckup.query.filter_by(employee_id=emp_id, exam_type="Urine Test").first()
            check("checkup file stored", chk is not None and bool(chk.file_path))
            check("checkup version stored", chk is not None and HealthCheckupVersion.query.filter_by(checkup_id=chk.id).count() == 1)
            check("checkup status is 7-day", chk is not None and chk.status == STATUS_7, None if not chk else chk.status)
            if chk and chk.file_path:
                created["paths"].append(chk.file_path)
            chk_id = chk.id if chk else None
        if chk_id:
            download = client.get(f"/employees/checkup/{chk_id}/download")
            check("authorized checkup download", download.status_code == 200, str(download.status_code))
            download.close()

        mine = client.get("/my-alerts?show=all")
        check("my alerts page loads", mine.status_code == 200 and b"My Alert Responsibilities" in mine.data)
        check("my alerts explains the source", b"Uploader" in mine.data or b"Company" in mine.data)

        with app.app_context():
            sample = (
                AlertEmailItem.query.join(AlertEmail)
                .filter(
                    AlertEmailItem.record_type == "document",
                    AlertEmailItem.record_id == created["doc_ids"][0],
                    AlertEmail.recipient_email == "zz.doc@ananta.com.bd",
                )
                .first()
            )
            history_id = sample.alert_email_id if sample else None
        history_page = client.get("/alert-history/")
        check(
            "factory user can open alert history",
            history_page.status_code == 200 and b"Alert History" in history_page.data and b"ZZ Scope Fire License" in history_page.data,
        )
        if history_id:
            detail = client.get(f"/alert-history/{history_id}")
            check(
                "history detail shows the recipient",
                detail.status_code == 200 and b"zz.doc@ananta.com.bd" in detail.data,
            )
        else:
            check("history detail shows the recipient", False, "missing history row")

        login("zz_scope_fab")
        if history_id:
            hidden = client.get(f"/alert-history/{history_id}")
            listing = client.get("/alert-history/")
            check("other factory cannot open this alert", hidden.status_code == 404, str(hidden.status_code))
            check("other factory listing omits this document", b"ZZ Scope Fire License" not in listing.data)
        login("zz_scope_view")
        viewed = client.get("/alert-history/")
        check("viewer sees alert history", viewed.status_code == 200 and b"ZZ Scope Fire License" in viewed.data)
        guest = app.test_client()
        locked = guest.get("/alert-history/")
        check("alert history requires login", locked.status_code == 302, str(locked.status_code))
        login("zz_scope_up")

        login("zz_scope_company")
        outsider = client.get(f"/documents/view/{created['doc_ids'][3]}")
        # factory B document id is the 4th. company user is factory A, so 403.
        check("factory user cannot open the other factory document", outsider.status_code == 403, str(outsider.status_code))

    finally:
        cleanup()

    print("\n=== ALERT SCOPE TESTS ===")
    failed = [name for name, passed in results if not passed]
    print("ALERT SCOPE TEST:", "PASSED" if not failed else "FAILED")
    if failed:
        print("Failed:", ", ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
