"""Employee Compliance manual alerts: utility, routing, RBAC, forged POST.

Covers:
  1. send_manual_employee_record_alert sends to the chosen users and the
     chosen group mailbox for both license and checkup kinds, stores an
     AlertEmail history row per recipient with the record snapshot, and
     returns a helpful error when nothing valid is selected.
  2. The /employees/license/<id>/send-alert and
     /employees/checkup/<id>/send-alert routes enforce ALERT_ROLES and
     redirect archived records back to the employee page without opening
     the form.
  3. A forged POST that adds a user id outside the eligible list, or a
     group address outside the department's own mailbox, is silently
     dropped: no email, no history row.

Follows tests/test_vehicle_alerts.py conventions. Runs isolated.
"""
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("SECRET_KEY", "ec-alert-test-key")
os.environ["COMPLIANCE_SKIP_EMERGENCY"] = "1"

from _isolated_db import assert_isolated, use_isolated_db

use_isolated_db()

from app import create_app, db, mail
from app.models import (
    AlertEmail, AlertEmailItem, Company, Department, Employee,
    EmployeeCompliance, Factory, HealthCheckup, Role, User,
)
from app.utils import (
    compute_status, send_manual_employee_record_alert,
    manual_employee_record_users,
)

PASS = "zz-ec-pass-1"
results = []


def check(name, passed, detail=""):
    results.append((name, bool(passed)))
    extra = f" :: {detail}" if detail and not passed else ""
    print(f"  [{'OK' if passed else 'FAIL'}] {name}{extra}")


def main():
    app = create_app()
    assert_isolated(app)
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True
    app.config["ALERT_RECIPIENTS"] = []

    created = {
        "users": [], "licenses": [], "checkups": [], "employees": [],
        "company": None, "factory": None, "factory_b": None,
        "dept": None, "dept_was_recipients": None, "dept_was_group_email": None,
    }

    with app.app_context():
        roles = {r.name: r for r in Role.query.all()}
        company = Company(name="ZZ EC Company", code="ZZEC")
        db.session.add(company); db.session.flush()
        created["company"] = company.id
        fac = Factory(company_id=company.id, name="ZZ EC Factory", location="A")
        fac_b = Factory(company_id=company.id, name="ZZ EC Factory B", location="B")
        db.session.add_all([fac, fac_b]); db.session.flush()
        created["factory"] = fac.id
        created["factory_b"] = fac_b.id

        dept = Department.query.filter(db.func.lower(Department.name) == "compliance").first()
        if dept is None:
            dept = Department(name="Compliance", group_email="")
            db.session.add(dept); db.session.flush()
        created["dept"] = dept.id
        created["dept_was_recipients"] = [u.id for u in list(dept.alert_recipients)]
        created["dept_was_group_email"] = dept.group_email
        dept.alert_recipients = []
        dept.group_email = "compliance_ec@ananta.com.bd"
        db.session.flush()

        def mk_user(username, role, factory=None, department=None, email=None, active=True):
            u = User(username=username, full_name=username,
                     role_id=roles[role].id,
                     factory_id=factory.id if factory else None,
                     department_id=department.id if department else None,
                     email=email, is_active=active)
            u.set_password(PASS)
            db.session.add(u); db.session.flush()
            created["users"].append(u.id)
            return u

        sa  = mk_user("zz_ec_super", "Super Admin",       department=dept, email="zz.ec.super@ananta.com.bd")
        adm = mk_user("zz_ec_admin", "Administration",    department=dept, email="zz.ec.admin@ananta.com.bd")
        fa  = mk_user("zz_ec_fadm",  "Factory Admin",     factory=fac, department=dept, email="zz.ec.fadm@ananta.com.bd")
        cm  = mk_user("zz_ec_cmgr",  "Compliance Manager", factory=fac, department=dept, email="zz.ec.cmgr@ananta.com.bd")
        cu  = mk_user("zz_ec_cuser", "Compliance User",   factory=fac, department=dept, email="zz.ec.cuser@ananta.com.bd")
        cv  = mk_user("zz_ec_cview", "Compliance Viewer", factory=fac, department=dept, email="zz.ec.cview@ananta.com.bd")
        tm  = mk_user("zz_ec_tmgr",  "Transport Manager", factory=fac, department=dept, email="zz.ec.tmgr@ananta.com.bd")
        tu  = mk_user("zz_ec_tusr",  "Transport User",    factory=fac, department=dept, email="zz.ec.tusr@ananta.com.bd")
        # Users excluded on scope: wrong factory, wrong department, inactive, bad email.
        fa_b = mk_user("zz_ec_fadm_b",  "Factory Admin",    factory=fac_b, department=dept, email="zz.ec.fadm.b@ananta.com.bd")
        cu_nodept = mk_user("zz_ec_cuser_nd", "Compliance User", factory=fac, department=None, email="zz.ec.cuser.nd@ananta.com.bd")
        off = mk_user("zz_ec_off",  "Compliance User",   factory=fac, department=dept, email="zz.ec.off@ananta.com.bd", active=False)
        bad = mk_user("zz_ec_bad",  "Compliance User",   factory=fac, department=dept, email="not-an-email")

        today = date.today()
        emp = Employee(employee_code="ZZECX1", name="Rahim Khan",
                       designation="Electrician", section="Maintenance",
                       factory_id=fac.id, department_id=dept.id,
                       date_of_joining=today - timedelta(days=1000),
                       email="rahim@example.com", receive_alerts=False)
        emp_nodept = Employee(employee_code="ZZECY1", name="Karim Mia",
                              designation="Operator", section="Spin",
                              factory_id=fac.id, department_id=None,
                              date_of_joining=today - timedelta(days=500))
        db.session.add_all([emp, emp_nodept]); db.session.flush()
        created["employees"] = [emp.id, emp_nodept.id]

        lic = EmployeeCompliance(
            employee_id=emp.id, compliance_type="Electrician License",
            registration_number="EL-0001", category="Class B",
            issuing_authority="Govt",
            issue_date=today - timedelta(days=300),
            expiry_date=today + timedelta(days=10),
            no_expiry=False, status=compute_status(today + timedelta(days=10)),
            created_by=sa.id,
        )
        lic_arch = EmployeeCompliance(
            employee_id=emp.id, compliance_type="Boiler License",
            registration_number="BO-OLD", issue_date=today - timedelta(days=800),
            expiry_date=today - timedelta(days=100), status="Expired",
            is_active=False, created_by=sa.id,
        )
        lic_nodept = EmployeeCompliance(
            employee_id=emp_nodept.id, compliance_type="Medical License",
            registration_number="ML-X1", issue_date=today - timedelta(days=200),
            expiry_date=today + timedelta(days=5),
            status=compute_status(today + timedelta(days=5)),
            created_by=sa.id,
        )
        chk = HealthCheckup(
            employee_id=emp.id, exam_type="CBC",
            report_date=today - timedelta(days=100),
            expiry_date=today + timedelta(days=20),
            status=compute_status(today + timedelta(days=20)),
            test_center="City Lab", pre_yn="Y",
            created_by=sa.id,
        )
        db.session.add_all([lic, lic_arch, lic_nodept, chk]); db.session.flush()
        created["licenses"] = [lic.id, lic_arch.id, lic_nodept.id]
        created["checkups"] = [chk.id]
        ids = {
            "emp": emp.id, "emp_nodept": emp_nodept.id,
            "lic": lic.id, "lic_arch": lic_arch.id, "lic_nodept": lic_nodept.id,
            "chk": chk.id,
            "cu_id": cu.id, "tm_id": tm.id, "fa_b_id": fa_b.id,
        }
        db.session.commit()

    def client_for(username):
        c = app.test_client()
        r = c.post("/login", data={"username": username, "password": PASS},
                   follow_redirects=False)
        assert r.status_code == 302, f"login failed for {username}: {r.status_code}"
        return c

    try:
        orig_send = mail.send
        captured = []
        mail.send = lambda m: captured.append({
            "subject": m.subject, "to": list(m.recipients),
            "sender": m.sender, "html": m.html,
        })

        # -------- 1. eligible-user resolver -------------------------------
        print("\n-- 1. Eligible-user resolver --")
        with app.app_context():
            lic_row = db.session.get(EmployeeCompliance, ids["lic"])
            users = manual_employee_record_users(lic_row, "license")
            usernames = sorted(u.username for u in users)
        check("fadm/cmgr/cuser/cview/tmgr/tusr are listed",
              set(usernames) >= {"zz_ec_fadm", "zz_ec_cmgr", "zz_ec_cuser",
                                 "zz_ec_cview", "zz_ec_tmgr", "zz_ec_tusr"},
              str(usernames))
        check("wrong-factory user is not listed",
              "zz_ec_fadm_b" not in usernames, str(usernames))
        check("no-department user is not listed",
              "zz_ec_cuser_nd" not in usernames, str(usernames))
        check("inactive user is not listed", "zz_ec_off" not in usernames)
        check("bad email is listed by scope but will be dropped on send",
              "zz_ec_bad" in usernames)  # scope only; send filters valid_email

        with app.app_context():
            lic_nd = db.session.get(EmployeeCompliance, ids["lic_nodept"])
            nd_users = manual_employee_record_users(lic_nd, "license")
        check("employee without a department: eligible list is empty",
              nd_users == [], str([u.username for u in nd_users]))

        # -------- 2. manual send: user + group --------------------------
        print("\n-- 2. Manual send --")
        captured.clear()
        with app.app_context():
            lic_row = db.session.get(EmployeeCompliance, ids["lic"])
            cm_user = User.query.filter_by(username="zz_ec_cmgr").first()
            sent, err, cnt = send_manual_employee_record_alert(
                lic_row, "license", [cm_user],
                group_emails=["compliance_ec@ananta.com.bd"],
            )
        check("manual send to one user + group mailbox",
              sent and cnt == 2 and err is None, f"sent={sent} cnt={cnt} err={err}")
        check("captured two messages", len(captured) == 2)
        subs = {m["subject"] for m in captured}
        check("subject starts 'Compliance Notice:'",
              all(s.startswith("Compliance Notice:") for s in subs))
        check("subject names the license + employee",
              all("Electrician License" in s and "Rahim Khan" in s for s in subs),
              str(subs))
        for m in captured:
            body = m["html"]
            check(f"body carries license + reg no ({m['to']})",
                  "Electrician License" in body and "EL-0001" in body)
            check(f"body shows company + factory ({m['to']})",
                  "ZZ EC Company" in body and "ZZ EC Factory" in body)

        # Checkup
        captured.clear()
        with app.app_context():
            chk_row = db.session.get(HealthCheckup, ids["chk"])
            cu_user = User.query.filter_by(username="zz_ec_cuser").first()
            sent, err, cnt = send_manual_employee_record_alert(
                chk_row, "checkup", [cu_user], group_emails=[],
            )
        check("checkup manual send to one user, no group",
              sent and cnt == 1 and err is None, f"sent={sent} cnt={cnt} err={err}")
        check("checkup subject names the exam + employee",
              all("CBC" in m["subject"] and "Rahim Khan" in m["subject"] for m in captured),
              str([m["subject"] for m in captured]))

        # No valid recipients -> helpful error
        captured.clear()
        with app.app_context():
            lic_row = db.session.get(EmployeeCompliance, ids["lic"])
            sent, err, cnt = send_manual_employee_record_alert(
                lic_row, "license", [], group_emails=[],
            )
        check("empty send returns an error and sends no mail",
              not sent and cnt == 0 and err and "No valid" in err, err)
        check("no mail captured on empty send", not captured)

        # History row + snapshot
        with app.app_context():
            man_rows = AlertEmail.query.filter_by(alert_type="manual").all()
            items = AlertEmailItem.query.filter(
                AlertEmailItem.alert_email_id.in_([r.id for r in man_rows])
            ).all()
        check("manual send stored history rows", len(man_rows) >= 3)
        check("history has a license item snapshot",
              any(it.record_type == "license" and it.record_id == ids["lic"] for it in items))
        check("history has a checkup item snapshot",
              any(it.record_type == "checkup" and it.record_id == ids["chk"] for it in items))

        # -------- 3. Route-level RBAC + archived + 404 -------------------
        print("\n-- 3. Route-level RBAC --")
        get_lic = f"/employees/license/{ids['lic']}/send-alert"
        get_chk = f"/employees/checkup/{ids['chk']}/send-alert"
        allowed = {"zz_ec_super", "zz_ec_admin", "zz_ec_fadm",
                   "zz_ec_cmgr", "zz_ec_cuser"}
        denied = {"zz_ec_cview": 403, "zz_ec_tmgr": 403, "zz_ec_tusr": 403}
        for user, expected in denied.items():
            r = client_for(user).get(get_lic)
            check(f"{user} cannot open license Send Alert (expects {expected})",
                  r.status_code == expected, str(r.status_code))
            r2 = client_for(user).get(get_chk)
            check(f"{user} cannot open checkup Send Alert (expects {expected})",
                  r2.status_code == expected, str(r2.status_code))
            r3 = client_for(user).post(get_lic, data={"recipient_id": "9999"})
            check(f"{user} POST license Send Alert is denied",
                  r3.status_code == 403, str(r3.status_code))
        for user in allowed:
            r = client_for(user).get(get_lic)
            check(f"{user} can open license Send Alert (expects 200)",
                  r.status_code == 200, str(r.status_code))
            r2 = client_for(user).get(get_chk)
            check(f"{user} can open checkup Send Alert (expects 200)",
                  r2.status_code == 200, str(r2.status_code))

        guest = app.test_client()
        anon = guest.get(get_lic)
        check("anonymous user is redirected to login",
              anon.status_code == 302 and "/login" in anon.headers.get("Location", ""))

        # Factory-scope: Factory Admin from the OTHER factory is blocked by 403.
        r = client_for("zz_ec_fadm_b").get(get_lic)
        check("factory-B Factory Admin: 403 on this license", r.status_code == 403,
              str(r.status_code))

        # Archived license: redirect back, do not render the form.
        r = client_for("zz_ec_super").get(
            f"/employees/license/{ids['lic_arch']}/send-alert", follow_redirects=False
        )
        check("archived license: redirect, no form", r.status_code == 302,
              str(r.status_code))

        # Unknown ids: 404
        for url in (
            "/employees/license/999999/send-alert",
            "/employees/checkup/999999/send-alert",
        ):
            r = client_for("zz_ec_super").get(url)
            check(f"unknown id {url}: 404", r.status_code == 404, str(r.status_code))

        # -------- 4. Forged POST ----------------------------------------
        print("\n-- 4. Forged POST --")
        captured.clear()
        with app.app_context():
            email_before = AlertEmail.query.count()
        r = client_for("zz_ec_cmgr").post(
            get_lic,
            data={
                # cuser is in the eligible list; tmgr too (department filter).
                # Add fa_b (wrong factory) and an invented group address.
                "recipient_id": [str(ids["cu_id"]), str(ids["fa_b_id"])],
                "group_email": ["evil@example.com", "compliance_ec@ananta.com.bd"],
            },
            follow_redirects=False,
        )
        with app.app_context():
            email_after = AlertEmail.query.count()
        delivered = {addr for m in captured for addr in m["to"]}
        check("legitimate cuser was mailed",
              "zz.ec.cuser@ananta.com.bd" in delivered, str(delivered))
        check("legitimate group mailbox was mailed",
              "compliance_ec@ananta.com.bd" in delivered, str(delivered))
        check("forged wrong-factory user id was dropped",
              "zz.ec.fadm.b@ananta.com.bd" not in delivered, str(delivered))
        check("forged group address was dropped",
              "evil@example.com" not in delivered, str(delivered))
        check("response is 302 (post-send redirect)", r.status_code == 302,
              str(r.status_code))
        check("history row count rose by exactly the delivered count",
              email_after - email_before == len(captured),
              f"before={email_before} after={email_after} captured={len(captured)}")

        # Sidebar / detail page: Compliance Viewer sees no Send alert button.
        page_cm = client_for("zz_ec_cmgr").get(f"/employees/{ids['emp']}").get_data(as_text=True)
        check("CM detail page shows Send alert button",
              "send-alert" in page_cm.lower())
        page_cv = client_for("zz_ec_cview").get(f"/employees/{ids['emp']}").get_data(as_text=True)
        check("CV detail page hides Send alert button",
              "send-alert" not in page_cv.lower())

        mail.send = orig_send

    finally:
        with app.app_context():
            try:
                # Only clear history created in this run — identify by subject
                # prefix is unreliable, so rely on item record_id scoping to
                # the records we just created.
                lic_ids = created["licenses"]
                chk_ids = created["checkups"]
                item_q = AlertEmailItem.query.filter(
                    db.or_(
                        db.and_(AlertEmailItem.record_type == "license",
                                AlertEmailItem.record_id.in_(lic_ids)),
                        db.and_(AlertEmailItem.record_type == "checkup",
                                AlertEmailItem.record_id.in_(chk_ids)),
                    )
                )
                email_ids = {it.alert_email_id for it in item_q.all()}
                if email_ids:
                    AlertEmailItem.query.filter(
                        AlertEmailItem.alert_email_id.in_(email_ids)
                    ).delete(synchronize_session=False)
                    AlertEmail.query.filter(AlertEmail.id.in_(email_ids)).delete(
                        synchronize_session=False
                    )
                HealthCheckup.query.filter(HealthCheckup.id.in_(chk_ids)).delete(
                    synchronize_session=False)
                EmployeeCompliance.query.filter(EmployeeCompliance.id.in_(lic_ids)).delete(
                    synchronize_session=False)
                Employee.query.filter(Employee.id.in_(created["employees"])).delete(
                    synchronize_session=False)
                User.query.filter(User.id.in_(created["users"])).delete(
                    synchronize_session=False)
                if created["dept"]:
                    dept = db.session.get(Department, created["dept"])
                    if dept is not None:
                        prev = created.get("dept_was_recipients") or []
                        dept.alert_recipients = (
                            User.query.filter(User.id.in_(prev)).all() if prev else []
                        )
                        dept.group_email = created.get("dept_was_group_email") or ""
                Factory.query.filter(Factory.id.in_(
                    [created["factory"], created["factory_b"]]
                )).delete(synchronize_session=False)
                if created["company"]:
                    Company.query.filter_by(id=created["company"]).delete(
                        synchronize_session=False)
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                print("cleanup warning:", type(exc).__name__, exc)

    print("\n=== EMPLOYEE COMPLIANCE ALERT TESTS ===")
    failed = [name for name, passed in results if not passed]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    print("EC ALERT TEST:", "PASSED" if not failed else "FAILED")
    if failed:
        print("Failed:", "; ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
