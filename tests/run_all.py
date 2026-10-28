"""
Run the full lightweight test suite (no pytest dependency).

    venv\\Scripts\\python.exe tests\\run_all.py

Each test is a standalone script that exits 0 on success. This runner invokes
them in separate processes so a crash in one does not abort the rest.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TESTS = [
    "test_status_engine.py",
    "test_routes_smoke.py",
    "test_isolation_rbac.py",
    "test_regulatory_rbac.py",
    "test_global_access.py",
    "test_excel_import.py",
    "test_alert_recipients.py",
    "test_alert_scope.py",
    "test_alert_history_rbac.py",
    "test_alert_buckets.py",
    "test_default_group_emails.py",
    "test_production_mail.py",
    "test_full_project.py",
    "test_ocr.py",
    "test_employee_ocr_extract.py",
    "test_vehicles.py",
    "test_vehicle_alerts.py",
    "test_health_merged_import.py",
    "test_delete_and_audit.py",
    "test_license_import.py",
    "test_ec_alert_logic.py",
    "test_employee_alerts.py",
    "test_dashboard_vehicle_section.py",
]


def main():
    env = dict(os.environ)
    env.setdefault("SECRET_KEY", "test-key")

    # Isolate the ENTIRE suite from the live database. Every test script calls
    # create_app(), which binds to instance/compliance.db unless DATABASE_URL
    # says otherwise. We point it at one disposable copy of production so the
    # suite exercises real seed data but can never mutate the real file. This
    # is the systemic guard against the 112->6 vehicle-wipe incident.
    from _isolated_db import use_isolated_db
    test_db = use_isolated_db()
    env["DATABASE_URL"] = os.environ["DATABASE_URL"]
    print(f"[run_all] isolated test database: {test_db}")

    failed = []
    for t in TESTS:
        print(f"\n########## {t} ##########")
        rc = subprocess.call([sys.executable, os.path.join(HERE, t)], env=env)
        if rc != 0:
            failed.append(t)
    print("\n==================== SUMMARY ====================")
    for t in TESTS:
        print(f"  {'FAIL' if t in failed else 'PASS'}  {t}")
    print("=================================================")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
