"""
Add company/department alert recipients, employee file versions, and
emergency-alert dedup. Idempotent. Does not delete existing data.

    venv\\Scripts\\python.exe scripts\\migrate_alert_scope.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "migration-temporary-key")


def main():
    from app import create_app

    create_app()
    print("Alert-scope schema is ready (company/department recipients, file versions, emergency dedup).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
