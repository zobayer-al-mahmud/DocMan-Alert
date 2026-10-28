"""Employee Compliance (license + health checkup) expiry alert logic.

Controlled test data proves, end to end, that:
  * status/buckets are derived from the DB expiry date (mutually exclusive
    7 / 30 / 60 / 90 rules),
  * the automatic WEEKLY send includes only 90/60/30 records and skips Valid,
    7-day and Expired,
  * the automatic EMERGENCY send includes only 7-day and Expired records and
    is de-duplicated (AlertDispatch) so the same record+status is not re-sent,
  * the configured Compliance Group mailbox is a recipient for both licenses
    and checkups, with no manual selection,
  * delivery results are logged (AlertEmail history rows),
  * the alert email content carries the DB expiry date.

Standalone script (no pytest).
    venv\\Scripts\\python.exe tests\\test_ec_alert_logic.py
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
    AlertDispatch, AlertEmail, AlertEmailItem, Employee, EmployeeCompliance,
    Factory, HealthCheckup,
)
from app.utils import (
    compute_status, recalculate_all_statuses, record_snapshot,
    resolve_alert_recipients, send_compliance_alert, send_emergency_alerts,
    STATUS_60, STATUS_30, STATUS_7, STATUS_EXPIRED, STATUS_VALID,
)

COMPLIANCE_GROUP = "compliance_dept@ananta.com.bd"
TAG = "ZZECALERT"
OK = 0
FAIL = 0


def check(cond, label, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK  ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f"  :: {extra}" if extra else ""))


def cleanup(app):
    with app.app_context():
        for emp in Employee.query.filter(Employee.employee_code.like(f"{TAG}%")).all():
            lic_ids = [l.id for l in emp.compliances]
            chk_ids = [c.id for c in emp.health_checkups]
            for kind, ids in (("license", lic_ids), ("checkup", chk_ids)):
                if not ids:
                    continue
                AlertDispatch.query.filter(AlertDispatch.record_type == kind,
                                           AlertDispatch.record_id.in_(ids)).delete(synchronize_session=False)
                parents = [r[0] for r in db.session.query(AlertEmailItem.alert_email_id).filter(
                    AlertEmailItem.record_type == kind, AlertEmailItem.record_id.in_(ids)).distinct()]
                if parents:
                    AlertEmailItem.query.filter(AlertEmailItem.alert_email_id.in_(parents)).delete(synchronize_session=False)
                    AlertEmail.query.filter(AlertEmail.id.in_(parents)).delete(synchronize_session=False)
            # Deleting the employee cascades to its licenses and checkups.
            db.session.delete(emp)
        db.session.commit()


def captured_for(captured, label):
    """Union of recipient addresses across every captured message that
    mentions `label` in its subject or body."""
    addrs = set()
    for m in captured:
        if label in (m["subject"] or "") or label in (m["html"] or ""):
            addrs.update(m["to"])
    return addrs


app = create_app()
app.config["TESTING"] = True
cleanup(app)

today = date.today()
captured = []
flask_mail.send = lambda m: captured.append(
    {"subject": m.subject, "to": [a.lower() for a in m.recipients], "html": m.html or ""})

with app.app_context():
    factory = Factory.query.order_by(Factory.id).first()
    emp = Employee(employee_code=f"{TAG}001", name=f"{TAG} PERSON",
                   factory_id=factory.id, is_active=True)
    db.session.add(emp)
    db.session.flush()

    def lic(label, offset):
        r = EmployeeCompliance(employee_id=emp.id, compliance_type=label,
                               expiry_date=today + timedelta(days=offset), no_expiry=False,
                               is_active=True)
        db.session.add(r); db.session.flush(); return r

    def chk(label, offset):
        r = HealthCheckup(employee_id=emp.id, exam_type=label,
                          expiry_date=today + timedelta(days=offset), no_expiry=False,
                          is_active=True)
        db.session.add(r); db.session.flush(); return r

    L60 = lic(f"{TAG}-L60", 45)      # 31-60 -> STATUS_60 (weekly)
    L7 = lic(f"{TAG}-L7", 3)         # 0-7   -> STATUS_7  (emergency)
    LV = lic(f"{TAG}-LVALID", 300)   # Valid (never)
    C30 = chk(f"{TAG}-C30", 20)      # 8-30  -> STATUS_30 (weekly)
    CX = chk(f"{TAG}-CEXP", -5)      # past  -> Expired   (emergency)
    db.session.commit()
    recalculate_all_statuses()

    ids = {"L60": L60.id, "L7": L7.id, "LV": LV.id, "C30": C30.id, "CX": CX.id}
    labels = {k: v for k, v in [("L60", f"{TAG}-L60"), ("L7", f"{TAG}-L7"),
                                ("LV", f"{TAG}-LVALID"), ("C30", f"{TAG}-C30"), ("CX", f"{TAG}-CEXP")]}

    # ---- Part 2: status derived from DB expiry (mutually exclusive buckets) --
    print("\n--- expiry -> status (from DB expiry date) ---")
    def st(rid):
        r = db.session.get(EmployeeCompliance, rid) or db.session.get(HealthCheckup, rid)
        return compute_status(r.expiry_date, r.no_expiry)
    check(st(ids["L60"]) == STATUS_60, f"license +45d -> {STATUS_60}", st(ids["L60"]))
    check(st(ids["L7"]) == STATUS_7, f"license +3d -> {STATUS_7}", st(ids["L7"]))
    check(st(ids["LV"]) == STATUS_VALID, f"license +300d -> {STATUS_VALID}", st(ids["LV"]))
    check(st(ids["C30"]) == STATUS_30, f"checkup +20d -> {STATUS_30}", st(ids["C30"]))
    check(st(ids["CX"]) == STATUS_EXPIRED, f"checkup -5d -> {STATUS_EXPIRED}", st(ids["CX"]))

    # ---- recipient selection: compliance group, no manual choice ----
    print("\n--- recipient selection (compliance group, automatic) ---")
    for key in ("L60", "C30"):
        rec = db.session.get(EmployeeCompliance, ids[key]) or db.session.get(HealthCheckup, ids[key])
        kind = "license" if key.startswith("L") else "checkup"
        addrs = {r["email"].lower() for r in resolve_alert_recipients(rec, kind)}
        check(COMPLIANCE_GROUP in addrs, f"{kind} recipients include compliance group", str(addrs))

    # ---- Part 3a: automatic WEEKLY send ----
    print("\n--- automatic weekly send (90/60/30 only) ---")
    captured.clear()
    lic_list = [db.session.get(EmployeeCompliance, ids[k]) for k in ("L60", "L7", "LV")]
    chk_list = [db.session.get(HealthCheckup, ids[k]) for k in ("C30", "CX")]
    sent, err, total = send_compliance_alert(licenses=lic_list, checkups=chk_list)
    check(COMPLIANCE_GROUP in captured_for(captured, labels["L60"]), "weekly: 60-day license alerted to compliance group")
    check(COMPLIANCE_GROUP in captured_for(captured, labels["C30"]), "weekly: 30-day checkup alerted to compliance group")
    check(not captured_for(captured, labels["L7"]), "weekly: 7-day license held back")
    check(not captured_for(captured, labels["CX"]), "weekly: expired checkup held back")
    check(not captured_for(captured, labels["LV"]), "weekly: valid license not alerted")

    # ---- delivery logged + email content carries DB expiry ----
    print("\n--- logging + email content ---")
    logged = AlertEmailItem.query.filter(
        AlertEmailItem.record_type.in_(["license", "checkup"]),
        AlertEmailItem.record_id.in_([ids["L60"], ids["C30"]])).count()
    check(logged >= 2, "weekly deliveries logged as AlertEmail history rows", f"items={logged}")
    snap = record_snapshot(db.session.get(EmployeeCompliance, ids["L60"]), "license")
    check(snap["expiry"] == (today + timedelta(days=45)), "snapshot expiry == DB expiry", str(snap["expiry"]))
    exp_str = (today + timedelta(days=45)).strftime("%d-%b-%Y")
    html_hit = any(labels["L60"] in m["html"] and exp_str in m["html"] for m in captured)
    check(html_hit, f"weekly email body shows DB expiry {exp_str}")

    # ---- Part 3b: automatic EMERGENCY send + dedup ----
    print("\n--- automatic emergency send (7-day / expired only) + dedup ---")
    captured.clear()
    lic_list = [db.session.get(EmployeeCompliance, ids[k]) for k in ("L60", "L7", "LV")]
    chk_list = [db.session.get(HealthCheckup, ids[k]) for k in ("C30", "CX")]
    sent, err, newly = send_emergency_alerts(licenses=lic_list, checkups=chk_list)
    check(COMPLIANCE_GROUP in captured_for(captured, labels["L7"]), "emergency: 7-day license alerted to compliance group")
    check(COMPLIANCE_GROUP in captured_for(captured, labels["CX"]), "emergency: expired checkup alerted to compliance group")
    check(not captured_for(captured, labels["L60"]), "emergency: 60-day license not alerted")
    check(not captured_for(captured, labels["C30"]), "emergency: 30-day checkup not alerted")
    check(newly > 0, "emergency reported newly-alerted records", str(newly))

    captured.clear()
    lic_list = [db.session.get(EmployeeCompliance, ids[k]) for k in ("L60", "L7", "LV")]
    chk_list = [db.session.get(HealthCheckup, ids[k]) for k in ("C30", "CX")]
    sent2, err2, newly2 = send_emergency_alerts(licenses=lic_list, checkups=chk_list)
    check(newly2 == 0 and not captured, "emergency dedup: same record+status not re-sent", f"newly2={newly2} captured={len(captured)}")

cleanup(app)
print(f"\n==== {OK} passed, {FAIL} failed ====")
sys.exit(0 if FAIL == 0 else 1)
