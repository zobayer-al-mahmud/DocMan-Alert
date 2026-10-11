"""Create the ocr_results table without touching existing documents.

    venv\\Scripts\\python.exe scripts\\migrate_ocr.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "migration-temporary-key")

from app import create_app, db


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
    print("ocr_results table is ready. Existing documents were not modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())