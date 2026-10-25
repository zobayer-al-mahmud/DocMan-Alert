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
TESTS = [
    "test_status_engine.py",
    "test_routes_smoke.py",
    "test_isolation_rbac.py",
    "test_regulatory_rbac.py",
    "test_global_access.py",
    "test_excel_import.py",
    "test_alert_recipients.py",
    "test_alert_scope.py",
    "test_alert_buckets.py",
    "test_default_group_emails.py",
    "test_production_mail.py",
    "test_full_project.py",
    "test_ocr.py",
    "test_employee_ocr_extract.py",
    "test_vehicles.py",
]


def main():
    env = dict(os.environ)
    env.setdefault("SECRET_KEY", "test-key")
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
