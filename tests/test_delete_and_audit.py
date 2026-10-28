"""Bulk/individual delete permissions + audit logging across the three modules.

Standalone script (no pytest).  venv\\Scripts\\python.exe tests\\test_delete_and_audit.py
"""
import os
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "test-key")

from app import create_app, db
from app.models import (
    Role, User, Factory, Document, Employee, EmployeeCompliance, HealthCheckup,
    Vehicle, VehicleDocument, SystemLog,
)

OK = 0
FAIL = 0


def check(cond, label):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK  ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def _login(client, username):
    with app.app_context():
        u = User.query.filter_by(username=username).first()
        u.set_password("Testpw@2026")
        u.failed_login_attempts = 0
        u.locked_until = None
        u.is_active = True
        db.session.commit()
    client.post("/login", data={"username": username, "password": "Testpw@2026"},
                follow_redirects=True)


def _user_for_role(role_name):
    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        return None
    u = User.query.filter_by(role_id=role.id, is_active=True).first()
    return u


app = create_app()
app.config["WTF_CSRF_ENABLED"] = False


def seed_documents(factory_id, n, prefix):
    ids = []
    for i in range(n):
        d = Document(factory_id=factory_id, doc_name=f"{prefix}-{i}", doc_type="Test",
                     cert_no=f"C{i}", status="Valid", is_active=True)
        db.session.add(d)
        db.session.flush()
        ids.append(d.id)
    db.session.commit()
    return ids


def test_documents_delete():
    print("\n--- Documents: bulk (Super Admin) + individual (Factory Admin) + RBAC ---")
    client = app.test_client()
    with app.app_context():
        fa = _user_for_role("Factory Admin")
        fa_factory = fa.factory_id or Factory.query.first().id

    # Super Admin bulk delete
    with app.app_context():
        ids = seed_documents(fa_factory, 3, "ZZDOCBULK")
    _login(client, "admin")
    r = client.post("/documents/bulk-delete", data={"ids": [str(i) for i in ids]},
                    follow_redirects=True)
    with app.app_context():
        remaining = Document.query.filter(Document.id.in_(ids)).count()
    check(remaining == 0, f"Super Admin bulk-deleted 3 documents (remaining={remaining})")
    with app.app_context():
        logrow = SystemLog.query.filter_by(event_type="RECORD_BULK_DELETED").order_by(SystemLog.id.desc()).first()
    check(logrow is not None and logrow.username_or_process == "admin",
          "RECORD_BULK_DELETED logged for admin")

    # Factory Admin: individual delete OK, bulk-delete endpoint 403
    with app.app_context():
        ids = seed_documents(fa_factory, 2, "ZZDOCFA")
        fa_name = _user_for_role("Factory Admin").username
    _login(client, fa_name)
    r = client.post("/documents/bulk-delete", data={"ids": [str(ids[0])]})
    check(r.status_code == 403, f"Factory Admin bulk-delete = 403 (got {r.status_code})")
    r = client.post(f"/documents/delete/{ids[0]}", data={"confirm_name": "ZZDOCFA-0"},
                    follow_redirects=True)
    with app.app_context():
        gone = db.session.get(Document, ids[0]) is None
    check(gone, "Factory Admin individual-deleted one document")
    with app.app_context():
        logrow = SystemLog.query.filter_by(event_type="RECORD_DELETED").order_by(SystemLog.id.desc()).first()
    check(logrow is not None and logrow.username_or_process == fa_name,
          "RECORD_DELETED logged for Factory Admin")

    # Compliance Viewer: no delete at all
    with app.app_context():
        cv = _user_for_role("Compliance Viewer")
        cv_name = cv.username if cv else None
    if cv_name:
        _login(client, cv_name)
        r = client.post(f"/documents/delete/{ids[1]}", data={"confirm_name": "ZZDOCFA-1"})
        check(r.status_code == 403, f"Compliance Viewer document delete = 403 (got {r.status_code})")

    # cleanup leftovers
    with app.app_context():
        for d in Document.query.filter(Document.doc_name.like("ZZDOC%")).all():
            db.session.delete(d)
        db.session.commit()


def test_vehicles_delete():
    print("\n--- Vehicles: bulk (Super Admin) + individual (Transport Manager) + RBAC ---")
    client = app.test_client()

    def seed_vehicles(n, prefix):
        ids = []
        for i in range(n):
            v = Vehicle()
            v.set_registration(f"ZZ {prefix} {i:04d}")
            v.is_active = True
            db.session.add(v)
            db.session.flush()
            db.session.add(VehicleDocument(vehicle_id=v.id, doc_type="tax_token",
                                           no_expiry=True, status="Valid"))
            ids.append(v.id)
        db.session.commit()
        return ids

    with app.app_context():
        ids = seed_vehicles(3, "BULK")
    _login(client, "admin")
    r = client.post("/vehicles/bulk-delete", data={"ids": [str(i) for i in ids]},
                    follow_redirects=True)
    with app.app_context():
        remaining = Vehicle.query.filter(Vehicle.id.in_(ids)).count()
    check(remaining == 0, f"Super Admin bulk-deleted 3 vehicles (remaining={remaining})")

    with app.app_context():
        ids = seed_vehicles(2, "TM")
        tm = _user_for_role("Transport Manager")
        tm_name = tm.username
        tu = _user_for_role("Transport User")
        tu_name = tu.username if tu else None
        reg0 = db.session.get(Vehicle, ids[0]).registration_no

    _login(client, tm_name)
    r = client.post("/vehicles/bulk-delete", data={"ids": [str(ids[0])]})
    check(r.status_code == 403, f"Transport Manager bulk-delete = 403 (got {r.status_code})")
    r = client.post(f"/vehicles/{ids[0]}/delete", data={"confirm_reg": reg0}, follow_redirects=True)
    with app.app_context():
        gone = db.session.get(Vehicle, ids[0]) is None
    check(gone, "Transport Manager individual-deleted one vehicle")

    if tu_name:
        _login(client, tu_name)
        r = client.post(f"/vehicles/{ids[1]}/delete",
                        data={"confirm_reg": "whatever"})
        check(r.status_code == 403, f"Transport User vehicle delete = 403 (got {r.status_code})")

    with app.app_context():
        for v in Vehicle.query.filter(Vehicle.registration_no.like("ZZ %")).all():
            db.session.delete(v)
        db.session.commit()


def test_employee_compliance_delete():
    print("\n--- Employee Compliance: license/checkup bulk + individual + RBAC ---")
    client = app.test_client()
    with app.app_context():
        factory = Factory.query.first()
        emp = Employee(employee_code="ZZDELTEST", name="Delete Test",
                       factory_id=factory.id, is_active=True)
        db.session.add(emp)
        db.session.flush()
        lic_ids, chk_ids = [], []
        for i in range(3):
            lic = EmployeeCompliance(employee_id=emp.id, compliance_type=f"Lic{i}",
                                     status="Valid", is_active=True)
            db.session.add(lic)
            db.session.flush()
            lic_ids.append(lic.id)
        for i in range(2):
            chk = HealthCheckup(employee_id=emp.id, exam_type=f"Exam{i}",
                                status="Valid", is_active=True)
            db.session.add(chk)
            db.session.flush()
            chk_ids.append(chk.id)
        db.session.commit()
        emp_id = emp.id
        fa_name = _user_for_role("Factory Admin").username

    # Super Admin bulk-delete 2 licenses
    _login(client, "admin")
    r = client.post(f"/employees/{emp_id}/licenses/bulk-delete",
                    data={"ids": [str(lic_ids[0]), str(lic_ids[1])]}, follow_redirects=True)
    with app.app_context():
        remaining = EmployeeCompliance.query.filter(EmployeeCompliance.id.in_(lic_ids)).count()
    check(remaining == 1, f"Super Admin bulk-deleted 2 licenses (remaining={remaining})")

    # Factory Admin: bulk 403, individual delete OK
    _login(client, fa_name)
    r = client.post(f"/employees/{emp_id}/licenses/bulk-delete", data={"ids": [str(lic_ids[2])]})
    check(r.status_code == 403, f"Factory Admin license bulk-delete = 403 (got {r.status_code})")
    r = client.post(f"/employees/license/{lic_ids[2]}/delete", follow_redirects=True)
    with app.app_context():
        gone = db.session.get(EmployeeCompliance, lic_ids[2]) is None
    check(gone, "Factory Admin individual-deleted one license")

    # Super Admin bulk-delete checkups
    _login(client, "admin")
    r = client.post(f"/employees/{emp_id}/checkups/bulk-delete",
                    data={"ids": [str(c) for c in chk_ids]}, follow_redirects=True)
    with app.app_context():
        remaining = HealthCheckup.query.filter(HealthCheckup.id.in_(chk_ids)).count()
    check(remaining == 0, f"Super Admin bulk-deleted all checkups (remaining={remaining})")

    with app.app_context():
        logged = SystemLog.query.filter(
            SystemLog.event_type.in_(["RECORD_DELETED", "RECORD_BULK_DELETED"]),
            SystemLog.message.like("%Employee License%"),
        ).count()
    check(logged >= 1, "license deletions were audit-logged")

    with app.app_context():
        e = db.session.get(Employee, emp_id)
        if e:
            db.session.delete(e)
            db.session.commit()


def test_logout_logged():
    print("\n--- logout audit ---")
    client = app.test_client()
    _login(client, "admin")
    client.post("/logout", follow_redirects=True)
    with app.app_context():
        row = SystemLog.query.filter_by(event_type="LOGOUT").order_by(SystemLog.id.desc()).first()
    check(row is not None, "LOGOUT event recorded")


def test_checkbox_visibility():
    print("\n--- bulk checkboxes only render for Super Admin ---")
    client = app.test_client()
    _login(client, "admin")
    r = client.get("/documents/")
    check(b'id="chkAll"' in r.data, "Super Admin sees bulk checkboxes on Documents")
    with app.app_context():
        fa_name = _user_for_role("Factory Admin").username
    _login(client, fa_name)
    r = client.get("/documents/")
    check(b'id="chkAll"' not in r.data, "Factory Admin does NOT see bulk checkboxes on Documents")


def main():
    test_documents_delete()
    test_vehicles_delete()
    test_employee_compliance_delete()
    test_logout_logged()
    test_checkbox_visibility()
    print(f"\n==== {OK} passed, {FAIL} failed ====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
