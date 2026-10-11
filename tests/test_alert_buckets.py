"""Automated alert bucket coverage.

Exercises the whole weekly + emergency pipeline across every status bucket
(Valid / 90 / 60 / 30 / 7-day / Expired), proves the emergency dedup rule
prevents spamming on a second run, and proves that a status transition
(7-day -> Expired) correctly fires a fresh urgent alert.
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
    AlertDispatch, AlertEmail, AlertEmailItem, Department, Document, Factory, Role, User,
)
from app.utils import (
    recalculate_all_statuses, send_compliance_alert, send_emergency_alerts,
    STATUS_VALID, STATUS_90, STATUS_60, STATUS_30, STATUS_7, STATUS_EXPIRED,
    NORMAL_ALERT_STATUSES, EMERGENCY_STATUSES,
)

TAG = "ZZ_BUCKET"
BUCKETS = [
    ("ZZ_BUCKET_valid",     95, STATUS_VALID),
    ("ZZ_BUCKET_90",        85, STATUS_90),
    ("ZZ_BUCKET_60",        45, STATUS_60),
    ("ZZ_BUCKET_30",        15, STATUS_30),
    ("ZZ_BUCKET_7_days_5",   5, STATUS_7),
    ("ZZ_BUCKET_7_days_2",   2, STATUS_7),
    ("ZZ_BUCKET_7_days_1",   1, STATUS_7),
    ("ZZ_BUCKET_expired_1", -1, STATUS_EXPIRED),
    ("ZZ_BUCKET_expired_7", -7, STATUS_EXPIRED),
]


def cleanup(app):
    with app.app_context():
        rows = Document.query.filter(Document.doc_name.like(f"{TAG}%")).all()
        for d in rows:
            d.alert_recipients = []
            AlertDispatch.query.filter_by(record_type="document", record_id=d.id).delete()
            parents = [
                r[0] for r in db.session.query(AlertEmailItem.alert_email_id).filter(
                    AlertEmailItem.record_type == "document",
                    AlertEmailItem.record_id == d.id,
                ).distinct()
            ]
            if parents:
                AlertEmailItem.query.filter(AlertEmailItem.alert_email_id.in_(parents)).delete(
                    synchronize_session=False
                )
                AlertEmail.query.filter(AlertEmail.id.in_(parents)).delete(
                    synchronize_session=False
                )
            db.session.delete(d)
        u = User.query.filter_by(username="zz_bucket_target").first()
        if u:
            db.session.delete(u)
        db.session.commit()


def main():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True

    cleanup(app)  # start clean in case a previous run died

    captured = []
    flask_mail.send = lambda m: captured.append(
        {"subject": m.subject, "to": list(m.recipients), "html": m.html}
    )

    results = []

    def check(label, cond, extra=""):
        results.append((label, bool(cond)))
        tag = "OK  " if cond else "FAIL"
        print(f"  [{tag}] {label}" + (f"  :: {extra}" if extra and not cond else ""))

    # --- Fixtures ---------------------------------------------------------
    today = date.today()
    with app.app_context():
        factory = Factory.query.order_by(Factory.id).first()
        dept = Department.query.filter(db.func.lower(Department.name) == "ict").first()
        if dept is None:
            dept = Department.query.order_by(Department.id).first()
        cm_role = Role.query.filter_by(name="Compliance Manager").first()
        target = User(
            username="zz_bucket_target", full_name="ZZ Bucket Target",
            email="zz_bucket_target@ananta.com.bd",
            role_id=cm_role.id, factory_id=factory.id, department_id=dept.id, is_active=True,
        )
        target.set_password("x")
        db.session.add(target)
        db.session.commit()
        for name, offset, _expected in BUCKETS:
            d = Document(
                factory_id=factory.id, department_id=dept.id,
                doc_name=name, doc_type="Test Document",
                cert_no=f"CERT-{abs(offset)}",
                issue_date=today - timedelta(days=30),
                expiry_date=today + timedelta(days=offset),
                no_expiry=False, status=STATUS_VALID, is_active=True, created_by=target.id,
            )
            db.session.add(d)
            db.session.flush()
            d.alert_recipients = [target]
        db.session.commit()
        recalculate_all_statuses()

        for name, offset, expected in BUCKETS:
            row = Document.query.filter_by(doc_name=name).first()
            check(f"bucket status {name} ({offset:+d}d) -> {expected}", row.status == expected,
                  f"got {row.status!r}")

    # --- Weekly ------------------------------------------------------------
    print("\n-- Run 1: weekly send_compliance_alert --")
    captured.clear()
    with app.app_context():
        docs = Document.query.filter(Document.doc_name.like(f"{TAG}%")).all()
        send_compliance_alert(documents=docs, licenses=[], checkups=[])
    subjects = [m["subject"] for m in captured]
    for name, offset, expected in BUCKETS:
        hits = sum(1 for s in subjects if name in s)
        should_send = expected in NORMAL_ALERT_STATUSES
        check(f"weekly  {name:28} should_send={should_send}  hits={hits}",
              (hits > 0) == should_send)
    # Each in-scope record now mails BOTH its department-scoped recipient and
    # the global compliance watcher mailbox (one envelope per recipient so each
    # sees only the record that concerns them).
    check("weekly emailed exactly the 90/60/30 buckets (3 records x 2 recipients)",
          len(captured) == 6, f"captured={len(captured)}")

    # --- Emergency run 1 ---------------------------------------------------
    print("\n-- Run 2: emergency send_emergency_alerts --")
    captured.clear()
    with app.app_context():
        docs = Document.query.filter(Document.doc_name.like(f"{TAG}%")).all()
        send_emergency_alerts(documents=docs, licenses=[], checkups=[])
    subjects = [m["subject"] for m in captured]
    for name, offset, expected in BUCKETS:
        hits = sum(1 for s in subjects if name in s)
        should_send = expected in EMERGENCY_STATUSES
        check(f"emerg   {name:28} should_send={should_send}  hits={hits}",
              (hits > 0) == should_send)
    check("emergency emailed exactly the 7-day + Expired buckets (5 records x 2 recipients)",
          len(captured) == 10, f"captured={len(captured)}")

    # --- Emergency anti-spam ----------------------------------------------
    print("\n-- Run 3: emergency re-run (anti-spam) --")
    captured.clear()
    with app.app_context():
        docs = Document.query.filter(Document.doc_name.like(f"{TAG}%")).all()
        send_emergency_alerts(documents=docs, licenses=[], checkups=[])
    check("emergency re-run sends 0 emails (dedup via AlertDispatch)",
          len(captured) == 0, f"captured={len(captured)}")

    # --- Transition triggers a fresh urgent --------------------------------
    print("\n-- Run 4: status transition (7-day -> Expired) --")
    with app.app_context():
        row = Document.query.filter_by(doc_name="ZZ_BUCKET_7_days_1").first()
        row.expiry_date = today - timedelta(days=2)
        db.session.commit()
        recalculate_all_statuses()
    captured.clear()
    with app.app_context():
        docs = Document.query.filter(Document.doc_name.like(f"{TAG}%")).all()
        send_emergency_alerts(documents=docs, licenses=[], checkups=[])
    # 1 new urgent record fans out to its 2 recipients: the department-scoped
    # user and the global compliance watcher.
    check("status change (7-day -> Expired) fires one new urgent record to both recipients",
          len(captured) == 2
          and all("ZZ_BUCKET_7_days_1" in m["subject"] and "Expired" in m["subject"] for m in captured),
          f"captured={[m['subject'] for m in captured]}")

    # --- Standardized email layout ----------------------------------------
    print("\n-- Email layout --")
    if captured:
        body = captured[0]["html"]
        check("urgent email includes Document Type", "Document Type" in body)
        check("urgent email includes Certificate / License No.", "Certificate / License No." in body)
        check("urgent email includes Issue Date", "Issue Date" in body)
        check("urgent email uses 'Alert Status' label (not plain 'Status')",
              "Alert Status" in body and ">Status:<" not in body)
        check("urgent email names the DB-assigned company/factory/department",
              "Ananta" in body and "Ananta Garments Ltd." in body)

    cleanup(app)

    failed = sum(1 for _label, ok in results if not ok)
    print(f"\nALERT BUCKET TEST: {'PASSED' if failed == 0 else 'FAILED'} "
          f"({len(results) - failed}/{len(results)} checks)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
