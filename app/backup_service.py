"""Database and uploaded-document backup.

The zip contains a consistent SQLite snapshot and every file under the
upload folder (factory documents, licenses, and health checkups).
"""
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta

from flask import current_app

from app.utils import log_event


def database_path():
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    return uri.replace("sqlite:///", "", 1)


def create_backup_zip(db_path, uploads_path, destination_folder):
    os.makedirs(destination_folder, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    zip_path = os.path.join(destination_folder, f"compliance_backup_{timestamp}.zip")

    tmp_db = os.path.join(tempfile.gettempdir(), f"compliance_snapshot_{timestamp}.db")
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(tmp_db)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(tmp_db, arcname="compliance.db")
            if uploads_path and os.path.isdir(uploads_path):
                for root, _dirs, files in os.walk(uploads_path):
                    for filename in files:
                        full_path = os.path.join(root, filename)
                        arc = os.path.join("uploads", os.path.relpath(full_path, uploads_path))
                        zipf.write(full_path, arcname=arc)
    finally:
        if os.path.exists(tmp_db):
            os.remove(tmp_db)
    return zip_path


def cleanup_old_backups(folder, days):
    if not folder or not os.path.isdir(folder):
        return 0
    cutoff = datetime.now() - timedelta(days=days)
    removed = 0
    for filename in os.listdir(folder):
        if not (filename.startswith("compliance_backup_") and filename.endswith(".zip")):
            continue
        full_path = os.path.join(folder, filename)
        if not os.path.isfile(full_path):
            continue
        if datetime.fromtimestamp(os.path.getmtime(full_path)) < cutoff:
            os.remove(full_path)
            removed += 1
    return removed


def list_backups():
    folder = current_app.config["BACKUP_FOLDER"]
    if not folder or not os.path.isdir(folder):
        return []
    rows = []
    for filename in os.listdir(folder):
        if not (filename.startswith("compliance_backup_") and filename.endswith(".zip")):
            continue
        full_path = os.path.join(folder, filename)
        if not os.path.isfile(full_path):
            continue
        stat = os.stat(full_path)
        rows.append({
            "name": filename,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime),
        })
    rows.sort(key=lambda item: item["modified"], reverse=True)
    return rows


def perform_backup():
    """Create the primary zip, mirror it when configured, and prune old copies.

    Returns (ok, summary). The summary does not include filesystem exception text.
    """
    primary = current_app.config["BACKUP_FOLDER"]
    mirror = current_app.config.get("BACKUP_MIRROR_FOLDER")
    retention = int(current_app.config.get("BACKUP_RETENTION_DAYS") or 30)
    try:
        zip_path = create_backup_zip(database_path(), current_app.config["UPLOAD_FOLDER"], primary)
    except Exception:
        log_event("BACKUP_FAILURE", "Backup failed.", level="ERROR", username_or_process="system")
        return False, "The backup could not be created."

    mirror_note = ""
    if mirror:
        try:
            os.makedirs(mirror, exist_ok=True)
            shutil.copy2(zip_path, os.path.join(mirror, os.path.basename(zip_path)))
            mirror_note = " A mirror copy was saved."
        except Exception:
            mirror_note = " The mirror copy could not be saved."
            log_event("BACKUP_FAILURE", "Mirror copy failed.", level="WARNING", username_or_process="system")

    cleanup_old_backups(primary, retention)
    if mirror:
        cleanup_old_backups(mirror, retention)
    name = os.path.basename(zip_path)
    log_event("BACKUP_SUCCESS", f"Backup created: {name}.{mirror_note}", username_or_process="system")
    return True, f"Backup created: {name}.{mirror_note}"
