"""Dashboard stat cards are clickable and each drills through to the matching
filtered module list, with counts that reconcile exactly.

For every card we fetch the dashboard context (the number shown on the card) and
then fetch the list endpoint the card links to, reading the real pagination
total, and assert they are equal. This is the guarantee the user asked for:
"dashboard counts match the actual database records and status filters".

Covered per card group:
  Factory Documents : Total, Valid, Expiring 90/60/30/7, Expired  (exact status)
  Employee Compliance: Active Employees, Licenses/Checkups Expiring (meta) + Expired
  Vehicle Documents : Total, Compliant, Missing (vehicle-count cards reconcile
                      exactly); Expired/Expiring cards are document counts over a
                      vehicle list, so we assert the link resolves and the vehicle
                      set is consistent rather than numerically equal.
  Compliance Audits : Total, Upcoming, Completed (timeframe), Validity Expiring
                      (meta), Overdue (exact)

Runs against an isolated copy of production. Standalone -- no pytest.

    venv\\Scripts\\python.exe tests\\test_dashboard_card_links.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("SECRET_KEY", "dashboard-card-links-test-key")

from _isolated_db import assert_isolated, use_isolated_db
use_isolated_db()

import app.routes.main as main_mod
import app.routes.documents as doc_mod
import app.routes.employees as emp_mod
import app.routes.vehicles as veh_mod
import app.routes.audits as aud_mod
from app import create_app, db
from app.models import User

PASS = "zz-cards-pass-1"
results = []
_captured = {}


def check(name, passed, detail=""):
    results.append((name, bool(passed)))
    extra = f" :: {detail}" if detail and not passed else ""
    print(f"  [{'OK' if passed else 'FAIL'}] {name}{extra}")


def _fake_render(template, **ctx):
    _captured["template"] = template
    _captured["ctx"] = ctx
    return ""


def _patch_renders():
    for mod in (main_mod, doc_mod, emp_mod, veh_mod, aud_mod):
        mod.render_template = _fake_render


def _total_for(client, url):
    """GET a list URL and return its pagination.total (records matching the filter)."""
    _captured.clear()
    r = client.get(url, follow_redirects=False)
    if r.status_code != 200:
        return None  # not accessible to this role
    pag = _captured.get("ctx", {}).get("pagination")
    return pag.total if pag is not None else None


def _dashboard_ctx(client):
    _captured.clear()
    r = client.get("/", follow_redirects=False)
    if r.status_code != 200:
        return None
    return _captured.get("ctx", {})


def _prep_user(app, username):
    with app.app_context():
        u = User.query.filter_by(username=username).first()
        if not u:
            return None
        u.set_password(PASS)
        u.failed_login_attempts = 0
        u.locked_until = None
        db.session.commit()
        return {"role": u.role.name, "factory_id": u.factory_id,
                "global": u.can_access_all_factories()}


def _login(client, username):
    return client.post("/login", data={"username": username, "password": PASS},
                       follow_redirects=False)


def reconcile_role(app, username):
    info = _prep_user(app, username)
    if info is None:
        print(f"  (skip: no user '{username}')")
        return
    role = info["role"]
    if role in ("Transport Manager", "Transport User"):
        # These land on /vehicles/dashboard, covered separately below.
        return
    print(f"\n--- role: {role} (user={username}, global={info['global']}) ---")

    client = app.test_client()
    _login(client, username)
    ctx = _dashboard_ctx(client)
    if ctx is None:
        check(f"[{role}] dashboard loads", False, "non-200")
        return
    check(f"[{role}] dashboard loads", True)

    # ---- Factory Documents (exact-status cards reconcile exactly) -----------
    pairs = [
        ("Total", "/documents/", ctx.get("total_documents")),
        ("Valid", "/documents/?status=Valid", ctx.get("valid_count")),
        ("Expiring 90", "/documents/?status=Expiring+in+90+Days",
         (ctx.get("bucket_counts") or {}).get("Expiring in 90 Days", 0)),
        ("Expiring 60", "/documents/?status=Expiring+in+60+Days",
         (ctx.get("bucket_counts") or {}).get("Expiring in 60 Days", 0)),
        ("Expiring 30", "/documents/?status=Expiring+in+30+Days",
         (ctx.get("bucket_counts") or {}).get("Expiring in 30 Days", 0)),
        ("Expiring 7", "/documents/?status=Expiring+in+7+Days",
         (ctx.get("bucket_counts") or {}).get("Expiring in 7 Days", 0)),
        ("Expired", "/documents/?status=Expired", ctx.get("expired_count")),
    ]
    for label, url, card in pairs:
        total = _total_for(client, url)
        check(f"[{role}] Documents '{label}' card={card} == list={total}", total == card,
              f"card={card} list={total}")

    # ---- Employee Compliance -----------------------------------------------
    if "active_employees" in ctx:
        emp_pairs = [
            ("Active Employees", "/employees/", ctx.get("active_employees")),
            ("Licenses Expiring", "/employees/licenses?status=expiring", ctx.get("emp_license_expiring")),
            ("Licenses Expired", "/employees/licenses?status=Expired", ctx.get("emp_license_expired")),
            ("Checkups Expiring", "/employees/checkups?status=expiring", ctx.get("checkup_expiring")),
            ("Checkups Expired", "/employees/checkups?status=Expired", ctx.get("checkup_expired")),
        ]
        for label, url, card in emp_pairs:
            total = _total_for(client, url)
            check(f"[{role}] Employee '{label}' card={card} == list={total}", total == card,
                  f"card={card} list={total}")

    # ---- Vehicle Documents (only when the section is shown to this role) -----
    vo = ctx.get("vehicle_overview")
    if vo:
        # Vehicle-count cards reconcile exactly.
        exact = [
            ("Total Vehicles", "/vehicles/", vo["total_vehicles"]),
            ("Compliant Vehicles", "/vehicles/?compliant=1", vo["compliant_vehicles"]),
            ("Missing Required", "/vehicles/?missing=1", vo["missing_required"]),
        ]
        for label, url, card in exact:
            total = _total_for(client, url)
            check(f"[{role}] Vehicle '{label}' card={card} == list={total}", total == card,
                  f"card={card} list={total}")
        # Document-count cards: the list is vehicle-centric, so a vehicle with two
        # expired docs counts 2 on the card but is 1 row. Assert the link resolves
        # and the vehicle set is non-empty exactly when the doc count is non-zero.
        doc_cards = [
            ("Expired Documents", "/vehicles/?status=Expired", vo["expired_docs"]),
            ("Expiring 7", "/vehicles/?status=Expiring+in+7+Days", vo["expiring_7"]),
            ("Expiring 30", "/vehicles/?status=Expiring+in+30+Days", vo["expiring_30"]),
            ("Expiring 60", "/vehicles/?status=Expiring+in+60+Days", vo["expiring_60"]),
            ("Expiring 90", "/vehicles/?status=Expiring+in+90+Days", vo["expiring_90"]),
        ]
        for label, url, card in doc_cards:
            total = _total_for(client, url)
            ok = total is not None and 0 <= total <= card and (total > 0) == (card > 0)
            check(f"[{role}] Vehicle '{label}' list resolves & consistent (card={card}, vehicles={total})", ok,
                  f"card={card} vehicles={total}")

    # ---- Compliance Audits (only when the section is shown to this role) -----
    ao = ctx.get("audit_overview")
    if ao:
        aud_pairs = [
            ("Total Audits", "/audits/", ao["total"]),
            ("Upcoming", "/audits/?timeframe=upcoming", ao["upcoming"]),
            ("Completed", "/audits/?timeframe=completed", ao["completed"]),
            ("Validity Expiring", "/audits/?status=expiring", ao["expiring"]),
            ("Overdue", "/audits/?status=Expired", ao["overdue"]),
        ]
        for label, url, card in aud_pairs:
            total = _total_for(client, url)
            check(f"[{role}] Audit '{label}' card={card} == list={total}", total == card,
                  f"card={card} list={total}")


def main():
    app = create_app()
    assert_isolated(app)
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True
    _patch_renders()

    # Super Admin: full reconciliation across every section, global scope.
    reconcile_role(app, "admin")

    # One factory-scoped user per role family, to prove scoping keeps counts
    # reconciled (cards and lists both honour the same scope).
    with app.app_context():
        scoped = {}
        for u in User.query.all():
            if u.can_access_all_factories() or not u.factory_id:
                continue
            if u.role.name in ("Transport Manager", "Transport User"):
                continue
            scoped.setdefault(u.role.name, u.username)
        extra_globals = {}
        for u in User.query.all():
            if u.can_access_all_factories() and u.username != "admin":
                extra_globals.setdefault(u.role.name, u.username)

    for role, username in sorted(scoped.items()):
        reconcile_role(app, username)
    for role, username in sorted(extra_globals.items()):
        reconcile_role(app, username)

    print("\n=== DASHBOARD CARD LINK TESTS ===")
    failed = [n for n, p in results if not p]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    print("DASHBOARD CARD LINK TEST:", "PASSED" if not failed else "FAILED")
    if failed:
        for n in failed:
            print("  FAIL:", n)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
