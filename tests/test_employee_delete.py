"""Delete-employee feature: role gate, typed-name confirmation, cascade.

Covers:
  1. Role gate — only Super Admin and Compliance Manager can open or POST
     to /employees/<id>/delete. Every other role (including Administration,
     Factory Admin, Compliance User, Compliance Viewer, Transport roles)
     is denied by the route decorator.
  2. Factory scope — a Compliance Manager scoped to another factory is
     refused with 403 even though their role is allowed.
  3. Typed-name mismatch leaves the employee intact and re-renders the
     form; typing the exact name deletes the employee and every attached
     license, checkup, file, dispatch, and alert-recipient link.
  4. The confirmation page lists sensible counts (license, checkup, file)
     for an employee that has multiple of each.
  5. The two visible entry points (list page row button and detail page
     header button) appear only for allowed roles, not for others.

Runs isolated via tests/_isolated_db.py; no changes to the real DB.
"""
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("SECRET_KEY", "ec-delete-test-key")
os.environ["COMPLIANCE_SKIP_EMERGENCY"] = "1"

from _isolated_db import assert_isolated, use_isolated_db

use_isolated_db()

from app import create_app, db
from app.models import (
    AlertDispatch, Company, Department, Employee, EmployeeCompliance,
    EmployeeComplianceVersion, Factory, HealthCheckup, HealthCheckupVersion,
    Role, User,
)

PASS = "zz-ec-del-pass-1"
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

    created = {
        "users": [], "employees": [], "licenses": [], "checkups": [],
        "versions": [], "files": [], "dispatches": [],
        "company": None, "factory": None, "factory_b": None, "dept": None,
    }

    with app.app_context():
        roles = {r.name: r for r in Role.query.all()}
        company = Company(name="ZZ EC DEL Company", code="ZZECD")
        db.session.add(company); db.session.flush()
        created["company"] = company.id
        fac = Factory(company_id=company.id, name="ZZ EC DEL Factory", location="A")
        fac_b = Factory(company_id=company.id, name="ZZ EC DEL Factory B", location="B")
        db.session.add_all([fac, fac_b]); db.session.flush()
        created["factory"] = fac.id
        created["factory_b"] = fac_b.id

        dept = Department.query.filter(db.func.lower(Department.name) == "compliance").first()
        if dept is None:
            dept = Department(name="Compliance", group_email="")
            db.session.add(dept); db.session.flush()
        created["dept"] = dept.id

        def mk_user(username, role, factory=None):
            u = User(username=username, full_name=username,
                     role_id=roles[role].id,
                     factory_id=factory.id if factory else None,
                     department_id=dept.id,
                     email=f"{username}@ananta.com.bd", is_active=True)
            u.set_password(PASS)
            db.session.add(u); db.session.flush()
            created["users"].append(u.id)
            return u

        sa  = mk_user("zz_ecd_super", "Super Admin")
        cm  = mk_user("zz_ecd_cmgr",  "Compliance Manager", factory=fac)
        cm_b = mk_user("zz_ecd_cmgr_b", "Compliance Manager", factory=fac_b)
        adm = mk_user("zz_ecd_admin", "Administration")
        fa  = mk_user("zz_ecd_fadm",  "Factory Admin", factory=fac)
        cu  = mk_user("zz_ecd_cuser", "Compliance User", factory=fac)
        cv  = mk_user("zz_ecd_cview", "Compliance Viewer", factory=fac)
        tm  = mk_user("zz_ecd_tmgr",  "Transport Manager", factory=fac)
        tu  = mk_user("zz_ecd_tusr",  "Transport User", factory=fac)
        db.session.commit()

        # Build a target employee with 2 licenses (1 archived), 2 checkups,
        # several file paths, and real on-disk files + alert dispatch rows,
        # so the cascade has something to clean up.
        upload_root = app.config["UPLOAD_FOLDER"]
        subfolder = f"employees/factory_{fac.id}"
        os.makedirs(os.path.join(upload_root, subfolder), exist_ok=True)

        def mk_file(name):
            rel = f"{subfolder}/{name}"
            full = os.path.join(upload_root, rel)
            with open(full, "w", encoding="utf-8") as f:
                f.write("fixture bytes for " + name)
            created["files"].append(full)
            return rel

        emp = Employee(employee_code="ZZECD001", name="Target Employee",
                       designation="Operator", section="QA",
                       factory_id=fac.id, department_id=dept.id,
                       date_of_joining=date.today() - timedelta(days=200))
        emp_other = Employee(employee_code="ZZECD002", name="Other Employee",
                             designation="Operator", section="QA",
                             factory_id=fac.id, department_id=dept.id,
                             date_of_joining=date.today() - timedelta(days=200))
        db.session.add_all([emp, emp_other]); db.session.flush()
        created["employees"] = [emp.id, emp_other.id]

        today = date.today()
        lic1 = EmployeeCompliance(
            employee_id=emp.id, compliance_type="Electrician License",
            registration_number="EL-D-1", issue_date=today - timedelta(days=300),
            expiry_date=today + timedelta(days=10),
            status="Expiring in 30 Days", created_by=sa.id,
            file_path=mk_file("lic1_v1.pdf"),
        )
        lic2 = EmployeeCompliance(
            employee_id=emp.id, compliance_type="Boiler License",
            registration_number="BO-D-1", issue_date=today - timedelta(days=800),
            expiry_date=today - timedelta(days=50),
            status="Expired", created_by=sa.id, is_active=False,  # archived
            file_path=mk_file("lic2_v1.pdf"),
        )
        chk1 = HealthCheckup(
            employee_id=emp.id, exam_type="CBC",
            report_date=today - timedelta(days=50),
            expiry_date=today + timedelta(days=30),
            status="Expiring in 30 Days", created_by=sa.id,
            file_path=mk_file("chk1_v1.pdf"),
        )
        chk2 = HealthCheckup(
            employee_id=emp.id, exam_type="Chest X-Ray",
            report_date=today - timedelta(days=100),
            expiry_date=today + timedelta(days=90),
            status="Expiring in 90 Days", created_by=sa.id,
            file_path=mk_file("chk2_v1.pdf"),
        )
        # Give the other (control) employee one license + file; it must survive.
        lic_other = EmployeeCompliance(
            employee_id=emp_other.id, compliance_type="Electrician License",
            registration_number="EL-X", issue_date=today - timedelta(days=200),
            expiry_date=today + timedelta(days=100), status="Valid",
            created_by=sa.id, file_path=mk_file("other_lic.pdf"),
        )
        db.session.add_all([lic1, lic2, chk1, chk2, lic_other]); db.session.flush()
        created["licenses"] = [lic1.id, lic2.id, lic_other.id]
        created["checkups"] = [chk1.id, chk2.id]

        # Previous-version rows + files for lic1 and chk1, to exercise cleanup.
        ver_lic1 = EmployeeComplianceVersion(
            compliance_id=lic1.id, uploaded_by=sa.id, version_no=1,
            file_path=mk_file("lic1_v1_prior.pdf"),
        )
        ver_chk1 = HealthCheckupVersion(
            checkup_id=chk1.id, uploaded_by=sa.id, version_no=1,
            file_path=mk_file("chk1_v1_prior.pdf"),
        )
        db.session.add_all([ver_lic1, ver_chk1]); db.session.flush()
        created["versions"] = [ver_lic1.id, ver_chk1.id]

        # Alert dispatches for both licenses and both checkups.
        for rt, rid, stage in (
            ("license", lic1.id, "weekly"), ("license", lic2.id, "weekly"),
            ("checkup", chk1.id, "urgent"), ("checkup", chk2.id, "weekly"),
            ("license", lic_other.id, "weekly"),  # control
        ):
            d = AlertDispatch(record_type=rt, record_id=rid, alert_stage=stage,
                              expiry_key="2026-11-01")
            db.session.add(d); db.session.flush()
            created["dispatches"].append(d.id)

        # Hook the record-level M2M alert_recipients so the cascade must clear them.
        lic1.alert_recipients = [sa, cm]
        chk1.alert_recipients = [sa]
        db.session.commit()

        emp_id = emp.id
        emp_other_id = emp_other.id
        emp_name = emp.name
        lic_ids = [lic1.id, lic2.id]
        chk_ids = [chk1.id, chk2.id]
        lic_other_id = lic_other.id
        file_paths = list(created["files"])

    def client_for(username):
        c = app.test_client()
        r = c.post("/login", data={"username": username, "password": PASS},
                   follow_redirects=False)
        assert r.status_code == 302, f"login failed for {username}: {r.status_code}"
        return c

    try:
        # ---------- 1. Role gate -----------------------------------------
        print("\n-- 1. Role gate --")
        url = f"/employees/{emp_id}/delete"
        for user in ("zz_ecd_super", "zz_ecd_cmgr"):
            r = client_for(user).get(url)
            check(f"{user}: GET /delete returns 200",
                  r.status_code == 200, str(r.status_code))
        for user in ("zz_ecd_admin", "zz_ecd_fadm", "zz_ecd_cuser",
                     "zz_ecd_cview", "zz_ecd_tmgr", "zz_ecd_tusr"):
            r = client_for(user).get(url)
            check(f"{user}: GET /delete returns 403",
                  r.status_code == 403, str(r.status_code))
            r2 = client_for(user).post(url, data={"confirm_name": emp_name})
            check(f"{user}: POST /delete returns 403",
                  r2.status_code == 403, str(r2.status_code))

        guest = app.test_client()
        anon = guest.get(url)
        check("anonymous user is redirected to login",
              anon.status_code == 302 and "/login" in anon.headers.get("Location", ""))

        # ---------- 2. Factory scope -------------------------------------
        print("\n-- 2. Factory scope --")
        r = client_for("zz_ecd_cmgr_b").get(url)
        check("factory-B Compliance Manager: 403 on factory-A employee",
              r.status_code == 403, str(r.status_code))

        # ---------- 3. Visible entry points ------------------------------
        print("\n-- 3. Button visibility --")
        detail_url = f"/employees/{emp_id}"
        # Scope the list query to the target factory so the row is on page 1
        # even for Super Admin, who otherwise sees every employee in the DB.
        with app.app_context():
            fac_id = created["factory"]
        list_url = f"/employees/?factory_id={fac_id}"
        for user in ("zz_ecd_super", "zz_ecd_cmgr"):
            html = client_for(user).get(detail_url).get_data(as_text=True)
            check(f"{user}: Detail page shows 'Delete Employee' button",
                  f"/employees/{emp_id}/delete" in html)
            html_list = client_for(user).get(list_url).get_data(as_text=True)
            check(f"{user}: List page shows a per-row Delete button",
                  f"/employees/{emp_id}/delete" in html_list)
        for user in ("zz_ecd_admin", "zz_ecd_fadm", "zz_ecd_cuser",
                     "zz_ecd_cview", "zz_ecd_tmgr", "zz_ecd_tusr"):
            html = client_for(user).get(detail_url).get_data(as_text=True)
            check(f"{user}: Detail page hides 'Delete Employee' button",
                  f"/employees/{emp_id}/delete" not in html)
            html_list = client_for(user).get(list_url).get_data(as_text=True)
            check(f"{user}: List page hides per-row Delete button",
                  f"/employees/{emp_id}/delete" not in html_list)

        # ---------- 4. Typed-name mismatch keeps the employee intact ----
        print("\n-- 4. Typed-name mismatch --")
        r = client_for("zz_ecd_super").post(url, data={"confirm_name": "wrong"},
                                             follow_redirects=False)
        check("mismatched name re-renders the form (200), does not delete",
              r.status_code == 200, str(r.status_code))
        with app.app_context():
            still_there = db.session.get(Employee, emp_id)
            lic_rows = EmployeeCompliance.query.filter(EmployeeCompliance.id.in_(lic_ids)).count()
            chk_rows = HealthCheckup.query.filter(HealthCheckup.id.in_(chk_ids)).count()
        check("employee row still exists", still_there is not None)
        check("licenses still exist", lic_rows == 2)
        check("checkups still exist", chk_rows == 2)
        for p in file_paths:
            # Only check files that belong to the target employee.
            if "other_lic" in p:
                continue
            check(f"file still on disk: {os.path.basename(p)}",
                  os.path.exists(p), p)

        # Confirmation page shows totals + types the user's name
        page = client_for("zz_ecd_super").get(url).get_data(as_text=True)
        check("confirmation page names the employee", emp_name in page)
        check("confirmation page shows license count 2",
              "<strong>2</strong> licenses" in page)
        check("confirmation page shows checkup count 2",
              "<strong>2</strong> health checkups" in page)
        # 4 current files (lic1 + lic2 + chk1 + chk2) + 2 version files = 6
        check("confirmation page shows file count 6",
              "<strong>6</strong> stored files" in page, page[page.find("stored file") - 60:page.find("stored file") + 60])

        # ---------- 5. Successful delete + cascade ----------------------
        print("\n-- 5. Successful delete --")
        r = client_for("zz_ecd_super").post(url, data={"confirm_name": emp_name},
                                              follow_redirects=False)
        check("exact name match returns 302 (redirect to list)",
              r.status_code == 302 and "/employees" in r.headers.get("Location", ""),
              f"status={r.status_code} loc={r.headers.get('Location')}")

        with app.app_context():
            emp_after = db.session.get(Employee, emp_id)
            lic_after = EmployeeCompliance.query.filter(
                EmployeeCompliance.id.in_(lic_ids)).count()
            chk_after = HealthCheckup.query.filter(
                HealthCheckup.id.in_(chk_ids)).count()
            ver_lic_after = EmployeeComplianceVersion.query.filter(
                EmployeeComplianceVersion.compliance_id.in_(lic_ids)).count()
            ver_chk_after = HealthCheckupVersion.query.filter(
                HealthCheckupVersion.checkup_id.in_(chk_ids)).count()
            disp_target_after = AlertDispatch.query.filter(
                db.or_(
                    db.and_(AlertDispatch.record_type == "license",
                            AlertDispatch.record_id.in_(lic_ids)),
                    db.and_(AlertDispatch.record_type == "checkup",
                            AlertDispatch.record_id.in_(chk_ids)),
                )
            ).count()
            # Control employee + their artifacts must still exist.
            other_after = db.session.get(Employee, emp_other_id)
            other_lic_after = db.session.get(EmployeeCompliance, lic_other_id)
            other_disp = AlertDispatch.query.filter_by(
                record_type="license", record_id=lic_other_id).count()
        check("employee row gone", emp_after is None)
        check("licenses gone", lic_after == 0)
        check("checkups gone", chk_after == 0)
        check("license version rows gone", ver_lic_after == 0)
        check("checkup version rows gone", ver_chk_after == 0)
        check("alert dispatches for target gone", disp_target_after == 0)
        check("control employee still exists", other_after is not None)
        check("control employee's license still exists", other_lic_after is not None)
        check("control employee's dispatch still exists", other_disp == 1)

        for p in file_paths:
            if "other_lic" in p:
                check(f"control file still on disk: {os.path.basename(p)}",
                      os.path.exists(p), p)
            else:
                check(f"target file removed from disk: {os.path.basename(p)}",
                      not os.path.exists(p), p)

        # Delete a non-existent id: 404 for allowed roles too
        r = client_for("zz_ecd_super").get(f"/employees/999999/delete")
        check("unknown id: GET returns 404", r.status_code == 404, str(r.status_code))

    finally:
        with app.app_context():
            try:
                # Any employee left (if a check path aborted early) + control
                EmployeeComplianceVersion.query.filter(
                    EmployeeComplianceVersion.compliance_id.in_(created["licenses"])
                ).delete(synchronize_session=False)
                HealthCheckupVersion.query.filter(
                    HealthCheckupVersion.checkup_id.in_(created["checkups"])
                ).delete(synchronize_session=False)
                EmployeeCompliance.query.filter(
                    EmployeeCompliance.id.in_(created["licenses"])
                ).delete(synchronize_session=False)
                HealthCheckup.query.filter(
                    HealthCheckup.id.in_(created["checkups"])
                ).delete(synchronize_session=False)
                AlertDispatch.query.filter(
                    AlertDispatch.id.in_(created["dispatches"])
                ).delete(synchronize_session=False)
                Employee.query.filter(
                    Employee.id.in_(created["employees"])
                ).delete(synchronize_session=False)
                User.query.filter(User.id.in_(created["users"])).delete(
                    synchronize_session=False)
                Factory.query.filter(Factory.id.in_(
                    [created["factory"], created["factory_b"]]
                )).delete(synchronize_session=False)
                if created["company"]:
                    Company.query.filter_by(id=created["company"]).delete(
                        synchronize_session=False)
                db.session.commit()
                for p in created["files"]:
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                    except OSError:
                        pass
            except Exception as exc:
                db.session.rollback()
                print("cleanup warning:", type(exc).__name__, exc)

    print("\n=== EMPLOYEE DELETE TESTS ===")
    failed = [name for name, passed in results if not passed]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    print("EC DELETE TEST:", "PASSED" if not failed else "FAILED")
    if failed:
        print("Failed:", "; ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
