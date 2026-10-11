"""
Daily backup job for Windows Task Scheduler.

Backs up the SQLite database and every uploaded document file.
Schedule: Daily 18:30 Bangladesh Standard Time (UTC+6).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.backup_service import cleanup_old_backups, create_backup_zip, perform_backup
from app.utils import log_event

__all__ = ["create_backup_zip", "cleanup_old_backups", "perform_backup"]


def main():
    app = create_app()
    with app.app_context():
        log_event("JOB_START", "Backup job started.", username_or_process="scheduler")
        ok, summary = perform_backup()
        print(summary)
        return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: Backup job crashed: {type(exc).__name__}")
        sys.exit(1)
