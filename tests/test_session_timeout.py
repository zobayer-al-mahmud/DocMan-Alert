"""Inactivity session timeout: server-side enforcement, the timeout endpoint,
the expired login banner, and the XHR 401 path. Runs isolated.

Login and permissions must keep working; only idle sessions expire.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("SECRET_KEY", "session-timeout-test-key")

from _isolated_db import assert_isolated, use_isolated_db
use_isolated_db()

from app import create_app, db
from app.models import User

PASS = "zz-sto-pass-1"
results = []


def check(name, passed, detail=""):
    results.append((name, bool(passed)))
    extra = f" :: {detail}" if detail and not passed else ""
    print(f"  [{'OK' if passed else 'FAIL'}] {name}{extra}")


def main():
    app = create_app()
    assert_isolated(app)
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True
    timeout_min = app.config["INACTIVITY_TIMEOUT_MINUTES"]
    check("inactivity timeout configured to 15 minutes", timeout_min == 15, str(timeout_min))

    with app.app_context():
        u = User.query.filter_by(username="admin").first()
        u.set_password(PASS)
        u.failed_login_attempts = 0
        u.locked_until = None
        db.session.commit()

    def fresh():
        return app.test_client()

    def login(c):
        return c.post("/login", data={"username": "admin", "password": PASS}, follow_redirects=False)

    # 1. Normal login still works
    c = fresh()
    r = login(c)
    check("valid login redirects to dashboard", r.status_code == 302 and "/login" not in r.headers.get("Location", ""),
          f"{r.status_code} {r.headers.get('Location')}")
    r = c.get("/", follow_redirects=False)
    check("authenticated dashboard loads (200)", r.status_code == 200, str(r.status_code))

    # 2. Invalid login shows the error, no redirect to app
    c2 = fresh()
    r = c2.post("/login", data={"username": "admin", "password": "nope"}, follow_redirects=True)
    check("invalid login stays on login with error",
          b"Invalid username or password" in r.data, "error flash missing")

    # 3. Idle beyond the window -> next request redirects to login?expired=1
    c = fresh()
    login(c)
    c.get("/")  # sets last_activity
    with c.session_transaction() as sess:
        sess["last_activity"] = time.time() - (timeout_min * 60 + 30)
    r = c.get("/", follow_redirects=False)
    check("idle session redirects to login", r.status_code == 302, str(r.status_code))
    check("redirect carries expired=1 notice", "expired=1" in r.headers.get("Location", ""),
          r.headers.get("Location", ""))
    # and the session is now cleared -> a follow-up protected request also bounces
    r2 = c.get("/documents/", follow_redirects=False)
    check("after timeout the user is logged out", r2.status_code == 302 and "/login" in r2.headers.get("Location", ""),
          f"{r2.status_code} {r2.headers.get('Location')}")

    # 4. Active use keeps the session alive (no premature expiry)
    c = fresh()
    login(c)
    for _ in range(3):
        r = c.get("/")
        assert r.status_code == 200
    check("repeated activity keeps session authenticated", c.get("/").status_code == 200)

    # 5. XHR after idle gets 401 (not an HTML redirect)
    c = fresh()
    login(c); c.get("/")
    with c.session_transaction() as sess:
        sess["last_activity"] = time.time() - (timeout_min * 60 + 30)
    r = c.get("/", headers={"X-Requested-With": "XMLHttpRequest"})
    check("idle XHR returns 401", r.status_code == 401, str(r.status_code))

    # 6. /session-timeout endpoint clears session + redirects with expired notice
    c = fresh()
    login(c); c.get("/")
    r = c.get("/session-timeout", follow_redirects=False)
    check("/session-timeout redirects to login?expired=1",
          r.status_code == 302 and "expired=1" in r.headers.get("Location", ""),
          r.headers.get("Location", ""))
    r2 = c.get("/", follow_redirects=False)
    check("/session-timeout logged the user out", r2.status_code == 302 and "/login" in r2.headers.get("Location", ""))
    # idempotent when already logged out
    r3 = fresh().get("/session-timeout", follow_redirects=False)
    check("/session-timeout safe when logged out", r3.status_code == 302 and "expired=1" in r3.headers.get("Location", ""))

    # 7. The expired banner renders on the login page
    r = fresh().get("/login?expired=1")
    check("expired banner shown on login", b"session expired due to inactivity" in r.data)
    r = fresh().get("/login")
    check("no expired banner without the flag", b"session expired due to inactivity" not in r.data)

    # 8. The login page carries the new two-column markup + assets
    r = fresh().get("/login")
    check("login uses the two-column brand layout", b"login-brand" in r.data and b"login-form-col" in r.data)
    check("login shows the Sign in heading", b"Sign in" in r.data)
    check("login has the password visibility toggle", b"toggle-pw" in r.data)

    # 9. Authenticated pages include the client timeout script + data attr
    c = fresh(); login(c)
    r = c.get("/")
    check("base layout loads the session-timeout script", b"session-timeout.js" in r.data)
    check("base layout exposes the inactivity window", b'data-inactivity-minutes="15"' in r.data)

    print("\n=== SESSION TIMEOUT TESTS ===")
    failed = [n for n, p in results if not p]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    print("SESSION TIMEOUT TEST:", "PASSED" if not failed else "FAILED")
    if failed:
        print("Failed:", "; ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
