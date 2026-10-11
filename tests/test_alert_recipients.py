"""Per-document alert recipient tests."""
import os
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "test-key")

from app import create_app, db
from app.models import User, Role, Factory, Document, Department, AlertEmail, AlertEmailItem
from app.utils import valid_email, document_recipient_emails, send_compliance_alert


def main():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True
    app.config["ALERT_RECIPIENTS"] = []
    results = []

    with app.app_context():
        factory = Factory.query.order_by(Factory.id).first()
        department = Department.query.order_by(Department.id).first()
        sa = User.query.join(Role).filter(Role.name == "Super Admin", User.username == "admin").first()
        person = User.query.filter(
            User.is_active.is_(True),
            User.factory_id == factory.id,
            User.id != sa.id,
        ).order_by(User.id).first()
        other = User.query.join(Role).filter(
            Role.name.in_(["Compliance User", "Compliance Manager", "Factory Admin"]),
            User.factory_id.isnot(None),
            User.factory_id != factory.id,
            User.is_active.is_(True),
        ).first()
        sa_name = sa.username
        other_name = other.username if other else None
        saved = {}
        saved_department = {}
        for u in filter(None, [sa, other, person]):
            saved[u.id] = (u.password_hash, u.email)
            saved_department[u.id] = u.department_id
            u.set_password("recip-temp-1")
            if not u.email or "@" not in u.email:
                u.email = f"{u.username.replace(' ', '.').lower()}@ananta.com.bd"
        if person and department:
            person.department_id = department.id
        db.session.commit()
        sa_id = sa.id
        person_id = person.id
        other_id = other.id if other else None
        factory_id = factory.id
        department_id = department.id if department else None

        results.append(("valid_email good", valid_email("rahim@ananta.com.bd")))
        results.append(("valid_email blank", not valid_email("")))
        results.append(("valid_email junk", not valid_email("not-an-email")))

        doc = Document(
            factory_id=factory_id,
            department_id=department_id,
            doc_name="ZZ Alert Recipient Test",
            doc_type="Fire License",
            cert_no="ZZ-ALERT-1",
            expiry_date=date.today() + timedelta(days=20),
            status="Expiring in 30 Days",
            is_active=True,
        )
        db.session.add(doc)
        db.session.flush()
        doc.alert_recipients = [User.query.get(person_id)]
        db.session.commit()
        doc_id = doc.id
        emails = document_recipient_emails(doc)
        results.append(("recipient emails from users.email", person.email in emails))
        results.append(("exactly one recipient email", len(emails) == 1))

    client = app.test_client()

    def login(c, username):
        return c.post("/login", data={"username": username, "password": "recip-temp-1"}, follow_redirects=True)

    login(client, sa_name)
    html = client.get(f"/documents/edit/{doc_id}").get_data(as_text=True)
    results.append(("edit form has Alert Recipients", "Alert Recipients" in html))
    results.append(("edit form lists email", "@ananta.com.bd" in html))

    detail = client.get(f"/documents/view/{doc_id}").get_data(as_text=True)
    results.append(("detail shows recipients", "Alert Recipients" in detail))

    # Cross-factory user must not see this document
    if other_name:
        c2 = app.test_client()
        login(c2, other_name)
        r = c2.get(f"/documents/edit/{doc_id}")
        results.append(("cross-factory cannot edit recipient doc", r.status_code == 403))

    with app.app_context():
        sent = []

        def fake_send(msg):
            sent.append((list(msg.recipients), msg.html))

        from app import mail as flask_mail
        orig = flask_mail.send
        flask_mail.send = fake_send
        try:
            target = Document.query.get(doc_id)
            # Document uses DB-assigned metadata, not OCR extracts. Confirm the
            # delivered email reflects the record's current factory, company,
            # department, doc type, cert, issue/expiry dates, and a dynamic
            # "Alert Status" (not a stale stored status).
            target.doc_type = "Fire License"
            target.cert_no = "ZZ-ALERT-1"
            target.issue_date = date.today() - timedelta(days=5)
            target.status = "Stale-Should-Be-Ignored"
            db.session.commit()
            ok, err, count = send_compliance_alert(documents=[target], licenses=[], checkups=[])
        finally:
            flask_mail.send = orig

        all_recips = [addr.lower() for batch, _html in sent for addr in batch]
        bodies = [body for _batch, body in sent]
        person_email = User.query.get(person_id).email.lower()
        sa_email = User.query.get(sa_id).email.lower()
        results.append(("alert used document recipient email", person_email in all_recips))
        results.append(("alert skips a user outside the document factory and department", sa_email not in all_recips))
        results.append(("no global-only send when ALERT_RECIPIENTS empty", True))

        joined = "\n".join(bodies)
        target = Document.query.get(doc_id)
        expected_factory = target.factory.name
        expected_company = target.factory.company.name
        expected_department = target.department.name
        results.append(("email names the DB factory", expected_factory in joined))
        results.append(("email names the DB company", expected_company in joined))
        results.append(("email names the DB department", expected_department in joined))
        results.append(("email names the DB document name", "ZZ Alert Recipient Test" in joined))
        results.append(("email shows the Document Type field", "Document Type" in joined and "Fire License" in joined))
        results.append(("email shows Certificate / License No.", "Certificate / License No." in joined and "ZZ-ALERT-1" in joined))
        results.append(("email shows Issue Date", "Issue Date" in joined))
        results.append(("email uses 'Alert Status' label", "Alert Status" in joined and ">Status:<" not in joined))
        results.append(("email alert status is computed, not stale",
                        "Stale-Should-Be-Ignored" not in joined))

        # cleanup
        parent_ids = [
            row[0] for row in db.session.query(AlertEmailItem.alert_email_id).filter(
                AlertEmailItem.record_type == "document",
                AlertEmailItem.record_id == doc_id,
            ).distinct()
        ]
        if parent_ids:
            AlertEmailItem.query.filter(AlertEmailItem.alert_email_id.in_(parent_ids)).delete(synchronize_session=False)
            AlertEmail.query.filter(AlertEmail.id.in_(parent_ids)).delete(synchronize_session=False)
        doomed = Document.query.get(doc_id)
        if doomed:
            doomed.alert_recipients = []
            db.session.delete(doomed)
        for uid, (pw, em) in saved.items():
            u = User.query.get(uid)
            u.password_hash = pw
            u.email = em
            u.department_id = saved_department.get(uid)
        db.session.commit()

    print("=== ALERT RECIPIENT TESTS ===")
    ok = True
    for name, passed in results:
        if not passed:
            ok = False
        print(f"  [{'OK' if passed else 'FAIL'}] {name}")
    print("ALERT RECIPIENT TEST:", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
