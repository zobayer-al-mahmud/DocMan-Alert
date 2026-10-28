"""Super Admin dashboard -> Vehicle Documents section.

Locks in the calculation contract for vehicle_dashboard_overview() (the helper
behind the dashboard's Vehicle Documents cards, factory-wise table and
needs-attention list) and the template's empty-state branches.

Read-only against whatever data exists (asserts internal invariants and
reconciliation, not hard-coded totals), plus a no-DB render of the empty state.
Standalone script -- no pytest.

    venv\\Scripts\\python.exe tests\\test_dashboard_vehicle_section.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "test-key")

from flask import render_template
from app import create_app
from app.routes.vehicles import vehicle_dashboard_overview, VIEW_ROLES
from app.utils import ATTENTION_STATUSES, STATUS_EXPIRED

OK = 0
FAIL = 0


def check(cond, label):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK  ] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


app = create_app()

with app.test_request_context("/"):
    o = vehicle_dashboard_overview()

    # --- shape ---------------------------------------------------------------
    for key in ("total_vehicles", "compliant_vehicles", "expired_docs",
                "missing_required", "expiring_7", "expiring_30", "expiring_60",
                "expiring_90"):
        check(isinstance(o.get(key), int) and o[key] >= 0, f"card '{key}' is a non-negative int ({o.get(key)})")
    check(isinstance(o["factory_stats"], list), "factory_stats is a list")
    check(isinstance(o["attention"], list), "attention is a list")

    # --- reconciliation: cards vs factory-wise table -------------------------
    fx_expired = sum(r["expired"] for r in o["factory_stats"])
    fx_expiring = sum(r["expiring"] for r in o["factory_stats"])
    fx_total = sum(r["total"] for r in o["factory_stats"])
    check(fx_expired == o["expired_docs"],
          f"sum(factory expired) == expired_docs card ({fx_expired} == {o['expired_docs']})")
    check(o["expiring_7"] + o["expiring_30"] + o["expiring_60"] + o["expiring_90"] == fx_expiring,
          f"7/30/60/90 buckets sum == sum(factory expiring) "
          f"({o['expiring_7']}+{o['expiring_30']}+{o['expiring_60']}+{o['expiring_90']} == {fx_expiring})")
    # each factory row's parts add up to its total
    check(all(r["compliant"] + r["expiring"] + r["expired"] == r["total"] for r in o["factory_stats"]),
          "each factory row: compliant + expiring + expired == total")

    # --- compliant vehicles never exceeds total vehicles ---------------------
    check(0 <= o["compliant_vehicles"] <= o["total_vehicles"],
          f"0 <= compliant_vehicles <= total_vehicles ({o['compliant_vehicles']} / {o['total_vehicles']})")
    check(0 <= o["missing_required"] <= o["total_vehicles"],
          f"0 <= missing_required <= total_vehicles ({o['missing_required']} / {o['total_vehicles']})")

    # --- attention list contract --------------------------------------------
    check(len(o["attention"]) <= 12, f"attention list capped at 12 ({len(o['attention'])})")
    shape_ok = all(
        set(("registration", "owner", "doc_label", "expiry", "status")).issubset(a)
        for a in o["attention"]
    )
    check(shape_ok, "every attention row has registration/owner/doc_label/expiry/status")
    statuses_ok = all(a["status"] in ATTENTION_STATUSES for a in o["attention"])
    check(statuses_ok, "attention rows are only expiring/expired statuses")
    # ordered by expiry ascending (most overdue first)
    exps = [a["expiry"] for a in o["attention"] if a["expiry"] is not None]
    check(exps == sorted(exps), "attention rows ordered by expiry ascending")

# --- RBAC: who may see the section ------------------------------------------
check("Super Admin" in VIEW_ROLES, "Super Admin is allowed to view the vehicle section")
for r in ("Compliance User", "Compliance Viewer"):
    check(r not in VIEW_ROLES, f"{r} is NOT allowed to view the vehicle section")

# --- empty state renders without data (no DB) ------------------------------
with app.test_request_context("/"):
    empty = {
        "total_vehicles": 0, "compliant_vehicles": 0, "expired_docs": 0,
        "missing_required": 0, "expiring_7": 0, "expiring_30": 0,
        "expiring_60": 0, "expiring_90": 0, "factory_stats": [], "attention": [],
    }

    class _Role:
        name = "Super Admin"

    class _User:
        role = _Role()

    html = render_template(
        "dashboard.html", vehicle_overview=empty,
        total_documents=0, valid_count=0, expiring_count=0, expired_count=0,
        bucket_counts={}, factory_stats=[], attention_items=[],
        active_employees=0, emp_license_expiring=0, emp_license_expired=0,
        checkup_expiring=0, checkup_expired=0, active_page="dashboard",
        current_user=_User(),
    )
    check("Vehicle Documents" in html, "empty state still renders the Vehicle Documents heading")
    check("No vehicle documents yet." in html, "factory-table empty state message present")
    check("No vehicle documents need attention." in html, "attention-table empty state message present")

print(f"\n==== {OK} passed, {FAIL} failed ====")
sys.exit(0 if FAIL == 0 else 1)
