"""Test database isolation.

The standalone test scripts in this folder each call ``create_app()``, which
without help binds to the LIVE ``instance/compliance.db``. A destructive test
(notably ``test_vehicles.py``'s DELETE-and-reseed routine) then runs against
production. In October 2026 this wiped the fleet from 112 vehicles down to the
6 fixture rows.

``use_isolated_db()`` guarantees a test process never touches the real DB:

- If ``DATABASE_URL`` already points somewhere other than the production file
  (e.g. ``run_all.py`` set it for the whole suite), it is left alone.
- Otherwise a disposable, consistent *copy* of the production DB is made with
  SQLite's online backup API and ``DATABASE_URL`` is pointed at the copy, so the
  test inherits real seed data (roles, users, factories) but mutates only the
  copy.

Call it BEFORE the first ``create_app()`` so ``config.Config`` reads the env.
``assert_isolated(app)`` is a belt-and-braces check callers can assert on.
"""
import atexit
import os
import shutil
import sqlite3
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROD_DB = os.path.join(ROOT, "instance", "compliance.db")


def _uri_for(path):
    return "sqlite:///" + path.replace("\\", "/")


def production_db_path():
    return os.path.abspath(PROD_DB)


def _is_production_uri(uri):
    if not uri or not uri.startswith("sqlite:///"):
        return False
    path = os.path.abspath(uri[len("sqlite:///"):])
    return path == production_db_path()


def use_isolated_db():
    """Ensure DATABASE_URL points at a disposable copy, not production.

    Returns the filesystem path of the DB the app will use. Safe to call more
    than once; honors an already-isolated DATABASE_URL from the environment.
    """
    existing = os.environ.get("DATABASE_URL")
    if existing and not _is_production_uri(existing):
        # The suite runner (or the caller) already isolated us.
        if existing.startswith("sqlite:///"):
            return os.path.abspath(existing[len("sqlite:///"):])
        return existing

    tmpdir = tempfile.mkdtemp(prefix="docman_testdb_")
    test_db = os.path.join(tmpdir, "compliance_test.db")

    if os.path.exists(PROD_DB):
        # Consistent copy including any WAL contents via the backup API.
        src = sqlite3.connect(PROD_DB)
        try:
            dst = sqlite3.connect(test_db)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    else:
        # No production DB yet (fresh checkout): start from an empty file so
        # create_all() builds the schema.
        open(test_db, "a").close()

    os.environ["DATABASE_URL"] = _uri_for(test_db)

    @atexit.register
    def _cleanup():
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

    return test_db


def assert_isolated(app):
    """Raise if the app is bound to the production database."""
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if _is_production_uri(uri):
        raise RuntimeError(
            "REFUSING TO RUN: the test app is bound to the production database "
            f"({production_db_path()}). Call use_isolated_db() before "
            "create_app(), or set DATABASE_URL to a disposable database."
        )
