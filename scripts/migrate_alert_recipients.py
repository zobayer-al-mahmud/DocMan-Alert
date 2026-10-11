"""
Add users.email and the document_alert_recipients table.

Idempotent. Also fills empty emails with a demo address so existing users
can be selected as document alert recipients immediately.

    venv\\Scripts\\python.exe scripts\\migrate_alert_recipients.py
"""
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB_PATH = os.path.join(ROOT, "instance", "compliance.db")


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: database not found at {DB_PATH}")
        return 1

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cols = {row[1] for row in cur.execute("PRAGMA table_info(users)").fetchall()}
    if "email" not in cols:
        cur.execute("ALTER TABLE users ADD COLUMN email VARCHAR(255)")
        print("[add-col] users.email")
    else:
        print("[skip] users.email already exists")
    con.commit()
    con.close()

    os.environ.setdefault("SECRET_KEY", "migration-temporary-key")
    from app import create_app, db
    from app.models import User

    app = create_app()
    with app.app_context():
        db.create_all()
        filled = 0
        for user in User.query.all():
            if user.email:
                continue
            local = re.sub(r"[^a-z0-9]+", ".", (user.username or "").lower()).strip(".")
            if not local:
                continue
            user.email = f"{local}@ananta.com.bd"
            filled += 1
        db.session.commit()
        print(f"[seed] filled {filled} missing user email(s)")
        print("[create] document_alert_recipients ready")
    print("Migration completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
