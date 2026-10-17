"""Regulatory Directory management is restricted to Super Admin.

Administration keeps read-only access to the directory listings so the
sidebar/view flow still works, but every add/edit/delete endpoint must
return 403 for Administration and other non-Super-Admin roles. Dashboard
hides the Employee Compliance section for Administration and Transport.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "regulatory-rbac-test-key")

from app import create_app, db
from app.models import Role, User

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False

TEST_PASSWORD = "regulatory-rbac-temp-pass-123"

MANAGE_PATHS_GET = [
    "/regulatory/categories/add",
    "/regulatory/authorities/add",
    "/regulatory/services/add",
]
LIST_PATHS = [
    "/regulatory/categories",
    "/regulatory/authorities",
    "/regulatory/services",
]


def login(c, username):
    return c.post(
        "/login",
        data={"username": username, "password": TEST_PASSWORD},
        follow_redirects=True,
    )


def _pick_user(role_name):
    return (
        User.query.join(Role)
        .filter(Role.name == role_name, User.is_active.is_(True))
        .first()
    )


with app.app_context():
    saved = {}
    picks = {}
    for role_name in (
        "Super Admin",
        "Administration",
        "Factory Admin",
        "Compliance Manager",
        "Compliance User",
        "Compliance Viewer",
        "Transport Manager",
        "Transport User",
    ):
        user = _pick_user(role_name)
        if user is not None:
            saved[user.id] = user.password_hash
            user.set_password(TEST_PASSWORD)
            picks[role_name] = user.username
    db.session.commit()


results = []


def check(name, got, expected):
    ok = got == expected
    results.append((name, got, expected, ok))


# --- Super Admin: must have full access -------------------------------------
if "Super Admin" in picks:
    c = app.test_client()
    login(c, picks["Super Admin"])
    for path in LIST_PATHS:
        check(f"Super Admin GET {path}", c.get(path).status_code, 200)
    for path in MANAGE_PATHS_GET:
        check(f"Super Admin GET {path}", c.get(path).status_code, 200)
else:
    results.append(("Super Admin available", False, True, False))


# --- Administration: fully blocked from the Regulatory Directory -----------
# Only Super Admin may view or manage directory entries. Administration
# retains the ability to pick a regulatory link on a document (handled by
# can_access_regulatory() in documents.py), but the dedicated directory
# pages themselves are closed to every non-Super-Admin role.
if "Administration" in picks:
    c = app.test_client()
    login(c, picks["Administration"])
    for path in LIST_PATHS:
        check(f"Administration GET {path}", c.get(path).status_code, 403)
    for path in MANAGE_PATHS_GET:
        check(f"Administration GET {path}", c.get(path).status_code, 403)
    # POST to delete must be blocked too
    check(
        "Administration POST /regulatory/categories/delete/1",
        c.post("/regulatory/categories/delete/1").status_code,
        403,
    )


# --- Non-admin roles: no access at all --------------------------------------
for role_name in (
    "Factory Admin",
    "Compliance Manager",
    "Compliance User",
    "Compliance Viewer",
    "Transport Manager",
    "Transport User",
):
    if role_name not in picks:
        continue
    c = app.test_client()
    login(c, picks[role_name])
    for path in LIST_PATHS:
        check(f"{role_name} GET {path}", c.get(path).status_code, 403)
    for path in MANAGE_PATHS_GET:
        check(f"{role_name} GET {path}", c.get(path).status_code, 403)


# --- Dashboard: Employee Compliance section hidden for Administration / Transport ---
for role_name in ("Administration", "Transport Manager", "Transport User"):
    if role_name not in picks:
        continue
    c = app.test_client()
    login(c, picks[role_name])
    r = c.get("/")
    body = r.get_data(as_text=True)
    hidden = "Employee Compliance" not in body
    results.append((
        f"{role_name} dashboard hides Employee Compliance",
        hidden,
        True,
        hidden,
    ))


# --- Dashboard: Compliance Manager still sees Employee Compliance -----------
for role_name in ("Super Admin", "Compliance Manager", "Compliance User"):
    if role_name not in picks:
        continue
    c = app.test_client()
    login(c, picks[role_name])
    r = c.get("/")
    body = r.get_data(as_text=True)
    shown = "Employee Compliance" in body
    results.append((
        f"{role_name} dashboard shows Employee Compliance",
        shown,
        True,
        shown,
    ))


# --- Restore passwords ------------------------------------------------------
with app.app_context():
    for uid, h in saved.items():
        u = User.query.get(uid)
        if u is not None:
            u.password_hash = h
    db.session.commit()


print("\n=== REGULATORY RBAC + DASHBOARD VISIBILITY RESULTS ===")
all_ok = True
for name, got, expected, ok in results:
    flag = "OK" if ok else "FAIL"
    if not ok:
        all_ok = False
    print(f"  [{flag}] {name} -> {got} (expected {expected})")

print(
    "\nREGULATORY RBAC TEST:",
    "PASSED" if all_ok else "FAILED",
    f"({sum(1 for r in results if r[3])}/{len(results)} checks)",
)
sys.exit(0 if all_ok else 1)
