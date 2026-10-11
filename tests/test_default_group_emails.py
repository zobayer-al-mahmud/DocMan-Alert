"""Default department group-email policy.

Verifies the rule the operations team enforces:

- compliance_dept@ananta.com.bd must receive EVERY document alert, regardless
  of the record's factory or department (manual + automated + any kind of
  record).
- transport_agl@ananta.com.bd must receive only Transport-related alerts.
- Both must be delivered automatically without any manual UI selection.
"""
import os
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "test-key")

from app import create_app, db
from app import mail as flask_mail
from app.models import (
    AlertDispatch, AlertEmail, AlertEmailItem, Department, Document,
    EmployeeCompliance, Employee, Factory, HealthCheckup, Role, User,
)
from app.utils import (
    department_group_emails, resolve_alert_recipients,
    send_compliance_alert, send_emergency_alerts, send_manual_document_alert,
    recalculate_all_statuses,
)

COMPLIANCE_GROUP = "compliance_dept@ananta.com.bd"
TRANSPORT_GROUP = "transport_agl@ananta.com.bd"
TAG = "ZZ_GRPDEFAULT"


def cleanup(app):
    with app.app_context():
        for cls in (Document,):
            rows = cls.query.filter(cls.doc_name.like(f"{TAG}%")).all()
            for r in rows:
                r.alert_recipients = []
                AlertDispatch.query.filter_by(record_type="document", record_id=r.id).delete()
                parent_ids = [
                    row[0] for row in db.session.query(AlertEmailItem.alert_email_id).filter(
                        AlertEmailItem.record_type == "document",
                        AlertEmailItem.record_id == r.id,
                    ).distinct()
                ]
                if parent_ids:
                    AlertEmailItem.query.filter(AlertEmailItem.alert_email_id.in_(parent_ids)).delete(
                        synchronize_session=False
                    )
                    AlertEmail.query.filter(AlertEmail.id.in_(parent_ids)).delete(
                        synchronize_session=False
                    )
                db.session.delete(r)
        for emp in Employee.query.filter(Employee.name.like(f"{TAG}%")).all():
            for lic in list(emp.compliances):
                db.session.delete(lic)
            for chk in list(emp.health_checkups):
                db.session.delete(chk)
            db.session.delete(emp)
        db.session.commit()


def _find_dept(name):
    """Fetch (do not create) a department by name, case-insensitive."""
    return Department.query.filter(db.func.lower(Department.name) == name.lower()).first()


def main():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True

    results = []

    def check(label, cond, extra=""):
        results.append((label, bool(cond)))
        tag = "OK  " if cond else "FAIL"
        print(f"  [{tag}] {label}" + (f"  :: {extra}" if extra and not cond else ""))

    cleanup(app)

    # --- Helper layer: department_group_emails() for each department ---------
    with app.app_context():
        for dept_name in ("ICT", "Administration", "Compliance", "Transport"):
            d = _find_dept(dept_name)
            if d is None:
                continue
            emails = {e.lower() for e in department_group_emails(d)}
            check(f"{dept_name} dept includes compliance group email",
                  COMPLIANCE_GROUP in emails, f"got={emails}")
            if dept_name == "Transport":
                check("Transport dept also includes transport group email",
                      TRANSPORT_GROUP in emails, f"got={emails}")
            else:
                check(f"{dept_name} dept does NOT include transport group email",
                      TRANSPORT_GROUP not in emails, f"got={emails}")
        check("A None department still copies compliance group email",
              COMPLIANCE_GROUP in {e.lower() for e in department_group_emails(None)})

    # --- Fixtures: 3 documents, one per non-Compliance/non-Transport dept ---
    captured = []
    flask_mail.send = lambda m: captured.append(
        {"subject": m.subject, "to": [a.lower() for a in m.recipients], "html": m.html}
    )

    today = date.today()
    with app.app_context():
        factory = Factory.query.order_by(Factory.id).first()
        cm_role = Role.query.filter_by(name="Compliance Manager").first()
        ict_dept = _find_dept("ICT") or _find_dept("Administration")
        admin_dept = _find_dept("Administration") or _find_dept("ICT")
        transport_dept = _find_dept("Transport")
        creator = (User.query.filter_by(username="admin").first()
                   or User.query.first())

        docs = []
        plan = [
            (f"{TAG}_ict",       ict_dept,       45),   # weekly bucket
            (f"{TAG}_admin",     admin_dept,     45),
            (f"{TAG}_transport", transport_dept, 45),
        ]
        for name, dept, offset in plan:
            if dept is None:
                continue
            d = Document(
                factory_id=factory.id,
                department_id=dept.id,
                doc_name=name,
                doc_type="Test Document",
                cert_no=f"CERT-{name}",
                issue_date=today - timedelta(days=10),
                expiry_date=today + timedelta(days=offset),
                no_expiry=False,
                status="Valid",
                is_active=True,
                created_by=creator.id if creator else None,
            )
            db.session.add(d)
            db.session.flush()
            # Capture plain-string dept name so later checks don't re-trigger a
            # lazy load on a detached SQLAlchemy instance.
            docs.append((name, dept.name, d.id))
        db.session.commit()
        recalculate_all_statuses()

    # --- Automated: send_compliance_alert fans out through resolve_alert_recipients
    captured.clear()
    with app.app_context():
        all_docs = Document.query.filter(Document.doc_name.like(f"{TAG}%")).all()
        send_compliance_alert(documents=all_docs, licenses=[], checkups=[])
    # Combine every captured recipient address for each doc into one set
    by_doc = {}
    for msg in captured:
        for name, _dname, _id in docs:
            if name in msg["subject"]:
                by_doc.setdefault(name, set()).update(msg["to"])
    for name, dname, _id in docs:
        addrs = by_doc.get(name, set())
        check(f"auto alert for {name} (dept={dname}) includes compliance group",
              COMPLIANCE_GROUP in addrs, f"got={addrs}")
        if dname.lower() == "transport":
            check(f"auto alert for Transport doc includes transport group",
                  TRANSPORT_GROUP in addrs, f"got={addrs}")
        else:
            check(f"auto alert for {dname} doc does NOT include transport group",
                  TRANSPORT_GROUP not in addrs, f"got={addrs}")

    # --- Manual: send_manual_document_alert with no explicit group_emails ----
    # Each group address receives its own message so the recipient sees only the
    # record that concerns them; union the per-doc envelopes to assert coverage.
    captured.clear()
    with app.app_context():
        for name, dname, doc_id in docs:
            d = db.session.get(Document, doc_id)
            send_manual_document_alert(d, users=[])
    manual_by_doc = {}
    for msg in captured:
        for name, _dname, _id in docs:
            if name in msg["subject"]:
                manual_by_doc.setdefault(name, set()).update(msg["to"])
    for name, dname, _id in docs:
        addrs = manual_by_doc.get(name, set())
        check(f"manual alert for {name} (dept={dname}) hits compliance group",
              COMPLIANCE_GROUP in addrs, f"to={addrs}")
        if dname.lower() == "transport":
            check("manual Transport alert also hits transport group",
                  TRANSPORT_GROUP in addrs, f"to={addrs}")
        else:
            check(f"manual {dname} alert does NOT hit transport group",
                  TRANSPORT_GROUP not in addrs, f"to={addrs}")

    # --- resolve_alert_recipients() UI payload includes the group as SOURCE_GROUP
    with app.app_context():
        for name, dname, doc_id in docs:
            d = db.session.get(Document, doc_id)
            rows = resolve_alert_recipients(d, "document")
            addr_set = {r["email"].lower() for r in rows}
            check(f"recipient list for {name} ({dname}) shows compliance group",
                  COMPLIANCE_GROUP in addr_set, f"got={addr_set}")

    cleanup(app)

    failed = sum(1 for _l, ok in results if not ok)
    print(f"\nDEFAULT GROUP EMAIL TEST: {'PASSED' if failed == 0 else 'FAILED'} "
          f"({len(results) - failed}/{len(results)} checks)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
