"""
Controlled restore for ICT / Super Admin.

Safety model (see Restore Guide):
  1. Requires explicit YES confirmation.
  2. Copies the CURRENT database + uploads into a timestamped safety folder
     BEFORE overwriting anything.
  3. Restores the database and uploaded files from the chosen backup zip.
  4. Validates the restored database (opens it and checks core tables exist).
  5. Logs RESTORE_SUCCESS / RESTORE_FAILURE to System Logs.

Run interactively:
    venv\\Scripts\\python.exe scripts\\restore.py

Backup success alone does NOT prove recoverability - run this restore against
a scratch copy periodically to verify backups actually restore.
"""
import os
import sys
import shutil
import sqlite3
import zipfile
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import create_app
from app.utils import log_event

CORE_TABLES = {"companies", "factories", "departments", "roles", "users",
               "documents", "document_versions", "employees",
               "employee_compliance", "health_checkups", "system_logs"}


def list_backups(folder):
    if not os.path.exists(folder):
        return []
    backups = [f for f in os.listdir(folder)
               if f.startswith("compliance_backup_") and f.endswith(".zip")]
    backups.sort(reverse=True)
    return backups


def validate_db(db_path):
    con = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        con.close()
    missing = CORE_TABLES - names
    if missing:
        raise RuntimeError(f"Restored database is missing core tables: {sorted(missing)}")


def restore_from_zip(app, folder, zip_filename, db_path, uploads_path):
    zip_path = os.path.join(folder, zip_filename)
    if not os.path.exists(zip_path):
        print(f"ERROR: Backup file not found: {zip_path}")
        return 1

    confirm = input(
        f"\nWARNING: This REPLACES the current database and uploaded files with the "
        f"backup '{zip_filename}'.\nThe current data is copied to a safety folder first.\n"
        f"Type YES to continue: ")
    if confirm.strip() != "YES":
        print("Restore cancelled.")
        return 1

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safety_folder = os.path.join(ROOT, f"pre_restore_safety_{ts}")
    os.makedirs(safety_folder, exist_ok=True)
    if os.path.exists(db_path):
        shutil.copy2(db_path, os.path.join(safety_folder, "compliance.db"))
    if os.path.exists(uploads_path):
        shutil.copytree(uploads_path, os.path.join(safety_folder, "uploads"))
    print(f"Current data copied to safety folder: {safety_folder}")

    temp_dir = os.path.join(ROOT, f"_restore_temp_{ts}")
    try:
        with zipfile.ZipFile(zip_path, "r") as zipf:
            zipf.extractall(temp_dir)

        restored_db = os.path.join(temp_dir, "compliance.db")
        if not os.path.exists(restored_db):
            raise RuntimeError("Backup zip does not contain compliance.db")
        validate_db(restored_db)

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        # Remove stale WAL/SHM so the restored DB is authoritative.
        for ext in ("-wal", "-shm"):
            p = db_path + ext
            if os.path.exists(p):
                os.remove(p)
        shutil.copy2(restored_db, db_path)

        restored_uploads = os.path.join(temp_dir, "uploads")
        if os.path.exists(restored_uploads):
            if os.path.exists(uploads_path):
                shutil.rmtree(uploads_path)
            shutil.copytree(restored_uploads, uploads_path)

        validate_db(db_path)
    except Exception as e:
        with app.app_context():
            log_event("RESTORE_FAILURE", f"Restore from {zip_filename} failed: {e}",
                      level="ERROR", username_or_process="ict")
        print(f"ERROR: Restore failed: {e}")
        print(f"Your previous data is safe in: {safety_folder}")
        return 1
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    with app.app_context():
        log_event("RESTORE_SUCCESS", f"Restored from {zip_filename}.", username_or_process="ict")
    print("\nRestore completed and validated successfully.")
    print(f"Previous data kept in: {safety_folder}")
    return 0


def main():
    app = create_app()
    folder = app.config["BACKUP_FOLDER"]
    db_path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "", 1)
    uploads_path = app.config["UPLOAD_FOLDER"]

    print("Available backups (most recent first):\n")
    backups = list_backups(folder)
    if not backups:
        print("No backups found.")
        return 1
    for i, b in enumerate(backups, 1):
        print(f"{i}. {b}")

    choice = input("\nEnter the number of the backup to restore (Enter to cancel): ")
    if not choice.strip().isdigit():
        print("Restore cancelled.")
        return 1
    index = int(choice.strip()) - 1
    if not (0 <= index < len(backups)):
        print("Invalid selection.")
        return 1
    return restore_from_zip(app, folder, backups[index], db_path, uploads_path)


if __name__ == "__main__":
    sys.exit(main())
