"""
One-time migration to the Version 1.0 production baseline.

What it does (idempotent / safe to re-run):
  1. Backs up the current database to backups/ before touching anything.
  2. Adds new columns:
        documents.no_expiry, documents.is_active
        document_versions.issue_date, document_versions.expiry_date
        users.failed_login_attempts, users.locked_until
  3. Creates new tables (employees, employee_compliance, health_checkups,
     system_logs) via SQLAlchemy create_all().
  4. Drops obsolete regulatory columns from documents (category_id,
     authority_id, service_id) and their indexes.
  5. Drops obsolete tables: audit_logs, notifications, services,
     regulatory_authorities, service_categories.
  6. Prints a before/after table inventory.

Run:
    venv\\Scripts\\python.exe scripts\\migrate_to_v1.py
"""
import os
import sys
import shutil
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB_PATH = os.path.join(ROOT, "instance", "compliance.db")


def backup_db():
    os.makedirs(os.path.join(ROOT, "backups"), exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dest = os.path.join(ROOT, "backups", f"premigration_v1_{ts}.db")
    # Use SQLite backup API so WAL contents are included.
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    print(f"[backup] database copied to {dest}")


def columns(cur, table):
    return {row[1] for row in cur.execute(f"PRAGMA table_info('{table}')").fetchall()}


def table_exists(cur, table):
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def add_column(cur, table, name, decl):
    if name not in columns(cur, table):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        print(f"[add-col] {table}.{name}")
    else:
        print(f"[skip] {table}.{name} already exists")


def drop_column(cur, table, name):
    if name in columns(cur, table):
        try:
            cur.execute(f"ALTER TABLE {table} DROP COLUMN {name}")
            print(f"[drop-col] {table}.{name}")
        except sqlite3.OperationalError as e:
            print(f"[warn] could not drop {table}.{name}: {e}")
    else:
        print(f"[skip] {table}.{name} not present")


def inventory(cur, label):
    print(f"\n=== {label} ===")
    for (name,) in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall():
        try:
            n = cur.execute(f"SELECT COUNT(*) FROM '{name}'").fetchone()[0]
        except Exception:
            n = "?"
        print(f"  {name:28} {n}")
    print()


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: database not found at {DB_PATH}")
        return 1

    backup_db()

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("PRAGMA foreign_keys=OFF")

    inventory(cur, "BEFORE")

    # 2. New columns on existing tables.
    add_column(cur, "documents", "no_expiry", "BOOLEAN NOT NULL DEFAULT 0")
    add_column(cur, "documents", "is_active", "BOOLEAN NOT NULL DEFAULT 1")
    add_column(cur, "document_versions", "issue_date", "DATE")
    add_column(cur, "document_versions", "expiry_date", "DATE")
    add_column(cur, "users", "failed_login_attempts", "INTEGER NOT NULL DEFAULT 0")
    add_column(cur, "users", "locked_until", "DATETIME")

    # 4. Drop obsolete regulatory indexes + columns from documents.
    for idx in ("ix_documents_category_id", "ix_documents_authority_id", "ix_documents_service_id"):
        try:
            cur.execute(f"DROP INDEX IF EXISTS {idx}")
        except sqlite3.OperationalError as e:
            print(f"[warn] drop index {idx}: {e}")
    for col in ("category_id", "authority_id", "service_id"):
        drop_column(cur, "documents", col)

    # 5. Drop obsolete tables.
    for tbl in ("audit_logs", "notifications", "services",
                "regulatory_authorities", "service_categories"):
        if table_exists(cur, tbl):
            cur.execute(f"DROP TABLE {tbl}")
            print(f"[drop-table] {tbl}")

    con.commit()
    cur.execute("PRAGMA foreign_keys=ON")
    con.commit()

    # 3. Create new tables via SQLAlchemy models.
    os.environ.setdefault("SECRET_KEY", "migration-temporary-key")
    from app import create_app, db
    app = create_app()
    with app.app_context():
        db.create_all()
    print("[create] new tables created via SQLAlchemy (employees, "
          "employee_compliance, health_checkups, system_logs)")

    con2 = sqlite3.connect(DB_PATH)
    inventory(con2.cursor(), "AFTER")
    con2.close()
    con.close()
    print("Migration completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
