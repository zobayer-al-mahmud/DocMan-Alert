"""Vehicle Document alerts: email content, routing, dedupe, manual send, RBAC.

Covers:
  1. Every alert email (weekly / urgent / manual) names registration, vehicle
     type, owner, company, document type, document number, issue date, expiry,
     days remaining and renewal status. A field that is not stored is printed
     as "Not recorded" rather than left out or guessed.
  2. The scheduled weekly job sends only 90/60/30-day records, the daily job
     sends only 7-day / Expired records, and both skip archived vehicles.
     An emergency for the same record+expiry does not fire again until the
     expiry moves, and a mail-server failure is logged without stamping the
     dedup row.
  3. Manual Send Alert emails only the chosen recipients, writes a history
     row with the record snapshot, requires one of the Transport-allowed
     roles, enforces the authorization server-side, and rejects a POST that
     tries to add an address not on the allowed list.
"""
import os
import re
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("SECRET_KEY", "vehicle-alert-test-key")
os.environ["COMPLIANCE_SKIP_EMERGENCY"] = "1"

from _isolated_db import assert_isolated, use_isolated_db

use_isolated_db()

from app import create_app, db, mail
from app.models import (
    AlertDispatch, AlertEmail, AlertEmailItem, Company, Department, Factory, Role,
    User, Vehicle, VehicleDocument,
)
from app.utils import (
    compute_status, record_snapshot, resolve_vehicle_alert_recipients,
    send_compliance_alert, send_emergency_alerts, send_manual_vehicle_doc_alert,
)

PASS = "zz-ve-pass-1"
results = []


def check(name, passed, detail=""):
    results.append((name, bool(passed)))
    extra = f" :: {detail}" if detail and not passed else ""
    print(f"  [{'OK' if passed else 'FAIL'}] {name}{extra}")


def _text_near(body, label):
    """Return the plain text that follows '<label>:' in an alert HTML body."""
    m = re.search(re.escape(label) + r":</strong>\s*([^<]*)", body)
    return (m.group(1) if m else "").strip()


def main():
    app = create_app()
    assert_isolated(app)
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True
    app.config["ALERT_RECIPIENTS"] = []

    created = {"users": [], "vehicles": [], "docs": [], "emails": [],
               "dispatches": [], "factory": None, "factory_b": None,
               "company": None, "transport_dept": None, "dept_was_recipients": None}

    with app.app_context():
        roles = {r.name: r for r in Role.query.all()}
        company = Company(name="ZZ VE Company", code="ZZVE")
        db.session.add(company)
        db.session.flush()
        created["company"] = company.id
        fac = Factory(company_id=company.id, name="ZZ VE Factory", location="A")
        fac_b = Factory(company_id=company.id, name="ZZ VE Factory B", location="B")
        db.session.add_all([fac, fac_b])
        db.session.flush()
        created["factory"] = fac.id
        created["factory_b"] = fac_b.id

        transport = (Department.query.filter(db.func.lower(Department.name) == "transport").first()
                     or Department(name="Transport", group_email="transport_agl@ananta.com.bd"))
        if transport.id is None:
            db.session.add(transport)
            db.session.flush()
        created["transport_dept"] = transport.id
        # Snapshot the department's recipient list so tests do not leak into prod.
        created["dept_was_recipients"] = [u.id for u in list(transport.alert_recipients)]
        transport.alert_recipients = []
        db.session.flush()

        def mk_user(username, role, factory=None, email=None, active=True):
            u = User(username=username, full_name=username,
                     role_id=roles[role].id,
                     factory_id=factory.id if factory else None,
                     email=email, is_active=active)
            u.set_password(PASS)
            db.session.add(u)
            db.session.flush()
            created["users"].append(u.id)
            return u

        tm = mk_user("zz_ve_tmgr", "Transport Manager", email="zz.ve.tmgr@ananta.com.bd")
        tu = mk_user("zz_ve_tusr", "Transport User", email="zz.ve.tusr@ananta.com.bd")
        tu_b = mk_user("zz_ve_tusr_b", "Transport User", factory=fac_b,
                       email="zz.ve.tusr.b@ananta.com.bd")
        fa = mk_user("zz_ve_fadm", "Factory Admin", factory=fac,
                     email="zz.ve.fadm@ananta.com.bd")
        cm = mk_user("zz_ve_cmgr", "Compliance Manager", factory=fac,
                     email="zz.ve.cmgr@ananta.com.bd")
        adm = mk_user("zz_ve_admin", "Administration", email="zz.ve.admin@ananta.com.bd")
        sa = mk_user("zz_ve_super", "Super Admin", email="zz.ve.super@ananta.com.bd")
        cu = mk_user("zz_ve_cuser", "Compliance User", factory=fac,
                     email="zz.ve.cuser@ananta.com.bd")
        cv = mk_user("zz_ve_cview", "Compliance Viewer", email="zz.ve.cview@ananta.com.bd")
        inactive = mk_user("zz_ve_offline", "Transport User", email="zz.ve.off@ananta.com.bd",
                           active=False)
        bad_email = mk_user("zz_ve_nomail", "Transport User", email="not-an-email")
        dept_recip = mk_user("zz_ve_dept", "Compliance User", factory=fac,
                             email="zz.ve.dept@ananta.com.bd")
        transport.alert_recipients.append(dept_recip)
        db.session.flush()

        today = date.today()

        def mk_vehicle(reg, factory, type_=None, owner=None, office=None, active=True,
                       creator=None):
            v = Vehicle(registration_no=reg, reg_no_normalized=reg.upper(),
                        vehicle_type=type_, factory_id=factory.id if factory else None,
                        company_id=company.id, owner_name_raw=owner, office_label=office,
                        is_active=active, created_by=creator.id if creator else tm.id)
            db.session.add(v)
            db.session.flush()
            created["vehicles"].append(v.id)
            return v

        def mk_doc(vehicle, doc_type, days_until_expiry, number="", issue=None, note="",
                   renewal="Not Started", active=True, no_expiry=False):
            expiry = None if no_expiry else today + timedelta(days=days_until_expiry)
            d = VehicleDocument(
                vehicle_id=vehicle.id, doc_type=doc_type, doc_number=number,
                issue_date=issue, expiry_date=expiry, no_expiry=no_expiry,
                note=note, status=compute_status(expiry, no_expiry),
                renewal_status=renewal, is_active=active, created_by=tm.id,
            )
            db.session.add(d)
            db.session.flush()
            created["docs"].append(d.id)
            return d

        v_weekly = mk_vehicle("ZZ-WEEKLY-01", fac, type_="Covered Van",
                              owner="ZZ VE Factory", office="Dhaka")
        d_weekly = mk_doc(v_weekly, "tax_token", 25, number="TT-0001",
                          issue=today - timedelta(days=340),
                          renewal="In Progress")
        v_urgent = mk_vehicle("ZZ-URGENT-01", fac, type_="Pickup",
                              owner="ZZ VE Factory", office="Dhaka")
        d_urgent = mk_doc(v_urgent, "fitness", -2, number="FIT-002",
                          issue=today - timedelta(days=400))
        v_bare = mk_vehicle("ZZ-BARE-01", fac, type_=None, owner=None, office=None)
        # 20 days -> weekly bucket (not an emergency), used to prove missing
        # fields print as "Not recorded".
        d_bare = mk_doc(v_bare, "route_permit", 20, number="", issue=None)
        v_personal = mk_vehicle("ZZ-OWN-01", None, type_="Private Car",
                                owner="Mr Smith", office=None)
        d_personal = mk_doc(v_personal, "tax_token", -1, number="TT-999")
        v_archived = mk_vehicle("ZZ-ARCH-01", fac, type_="Bus", active=False)
        d_archived_expired = mk_doc(v_archived, "tax_token", -5, number="TT-ARCH")
        v_valid = mk_vehicle("ZZ-VALID-01", fac, type_="Van")
        d_valid = mk_doc(v_valid, "tax_token", 400, number="TT-VAL")
        v_smartcard = mk_vehicle("ZZ-SMART-01", fac, type_="Van")
        d_smartcard = mk_doc(v_smartcard, "blue_book", 0, number="BB-SC",
                             no_expiry=True, note="Smart Card received")

        db.session.commit()
        ids = {
            "v_weekly": v_weekly.id, "d_weekly": d_weekly.id,
            "v_urgent": v_urgent.id, "d_urgent": d_urgent.id,
            "v_bare": v_bare.id, "d_bare": d_bare.id,
            "v_personal": v_personal.id, "d_personal": d_personal.id,
            "v_archived": v_archived.id, "d_archived": d_archived_expired.id,
            "v_valid": v_valid.id, "d_valid": d_valid.id,
            "v_smart": v_smartcard.id, "d_smart": d_smartcard.id,
        }

    def client_for(username):
        c = app.test_client()
        r = c.post("/login", data={"username": username, "password": PASS},
                   follow_redirects=False)
        assert r.status_code == 302, f"login failed for {username}: {r.status_code}"
        return c

    try:
        orig_send = mail.send
        captured = []

        def _capture(message):
            captured.append({
                "subject": message.subject,
                "to": list(message.recipients),
                "sender": message.sender,
                "html": message.html,
            })

        mail.send = _capture

        with app.app_context():
            weekly_doc = db.session.get(VehicleDocument, ids["d_weekly"])
            # ---------- 1. Email content ----------------------------------
            print("\n-- 1. Email content --")
            snap = record_snapshot(weekly_doc, "vehicle")
            check("snapshot kind is 'vehicle'", snap["kind"] == "vehicle")
            check("snapshot carries registration",
                  snap["reg_no"] == "ZZ-WEEKLY-01", snap.get("reg_no"))
            check("snapshot carries vehicle type",
                  snap["vehicle_type"] == "Covered Van", snap.get("vehicle_type"))
            check("snapshot carries owner", snap["owner"] == "ZZ VE Factory",
                  snap.get("owner"))
            check("snapshot carries factory + company",
                  snap["factory"] == "ZZ VE Factory" and snap["company"] == "ZZ VE Company")
            check("snapshot carries doc number", snap["certificate"] == "TT-0001")
            check("snapshot carries renewal status",
                  snap["renewal_status"] == "In Progress")

        captured.clear()
        with app.app_context():
            weekly_doc = db.session.get(VehicleDocument, ids["d_weekly"])
            sent, err, cnt = send_compliance_alert(vehicles=[weekly_doc])
        check("weekly send succeeds", sent and cnt == 1 and err is None,
              f"sent={sent} cnt={cnt} err={err}")
        check("at least one weekly email captured", len(captured) >= 1)
        weekly_body = captured[0]["html"]
        for label, expected in (
            ("Registration No.", "ZZ-WEEKLY-01"),
            ("Vehicle Type", "Covered Van"),
            ("Owner", "ZZ VE Factory"),
            ("Company", "ZZ VE Company"),
            ("Factory", "ZZ VE Factory"),
            ("Office", "Dhaka"),
            ("Department", "Transport"),
            ("Document Type", "Tax Token"),
            ("Document No.", "TT-0001"),
            ("Renewal Status", "In Progress"),
        ):
            got = _text_near(weekly_body, label)
            check(f"weekly email shows {label}: {expected}", got == expected, got)
        check("weekly email shows the issue date (dd-Mon-yyyy)",
              bool(re.match(r"\d{2}-[A-Za-z]{3}-\d{4}", _text_near(weekly_body, "Issue Date"))))
        check("weekly email shows the expiry date",
              bool(re.match(r"\d{2}-[A-Za-z]{3}-\d{4}", _text_near(weekly_body, "Expiry Date"))))
        remaining = _text_near(weekly_body, "Days Remaining")
        check("weekly email shows days remaining", "Day" in remaining, remaining)
        check("weekly email shows alert status",
              "Expiring" in _text_near(weekly_body, "Alert Status"))
        check("weekly subject names the vehicle",
              "ZZ-WEEKLY-01" in captured[0]["subject"]
              and "Compliance Alert" in captured[0]["subject"])

        # Vehicle with missing information prints "Not recorded" everywhere,
        # not blanks and not invented values.
        captured.clear()
        with app.app_context():
            bare_doc = db.session.get(VehicleDocument, ids["d_bare"])
            send_compliance_alert(vehicles=[bare_doc])
        bare_body = next(m["html"] for m in captured
                         if "ZZ-BARE-01" in m["html"])
        for label in ("Vehicle Type", "Document No.", "Issue Date"):
            value = _text_near(bare_body, label)
            check(f"missing {label} prints 'Not recorded'",
                  value == "Not recorded", value)
        # Owner falls back to the factory name when a mapped vehicle has no
        # raw owner — this is a feature, not an invented value: factory name
        # is the real owner of a company-mapped vehicle.
        check("missing Owner on a mapped vehicle shows the factory name",
              _text_near(bare_body, "Owner") == "ZZ VE Factory",
              _text_near(bare_body, "Owner"))

        # Personal vehicle: company must come from Vehicle.company_id when no
        # factory is attached.
        captured.clear()
        with app.app_context():
            personal_doc = db.session.get(VehicleDocument, ids["d_personal"])
            send_emergency_alerts(vehicles=[personal_doc])
        personal_body = next((m["html"] for m in captured
                              if "ZZ-OWN-01" in m["html"]), "")
        check("personal vehicle: company falls back to Vehicle.company_id",
              _text_near(personal_body, "Company") == "ZZ VE Company",
              _text_near(personal_body, "Company"))
        check("personal vehicle: factory is 'Not recorded' not '-'",
              _text_near(personal_body, "Factory") == "Not recorded",
              _text_near(personal_body, "Factory"))
        check("personal vehicle: owner is the raw name",
              _text_near(personal_body, "Owner") == "Mr Smith",
              _text_near(personal_body, "Owner"))
        check("expired urgent: Days Remaining says Expired",
              _text_near(personal_body, "Days Remaining") == "Expired",
              _text_near(personal_body, "Days Remaining"))

        # Blue Book "no expiry" note: expiry label is "No expiry", status Valid.
        with app.app_context():
            smart_doc = db.session.get(VehicleDocument, ids["d_smart"])
            smart_snap = record_snapshot(smart_doc, "vehicle")
        check("no-expiry doc status is Valid", smart_snap["status"] == "Valid")
        check("no-expiry doc days is None", smart_snap["days"] is None)

        # ---------- 2. Automatic alerts (routing, dedupe, failure) --------
        print("\n-- 2. Automatic alerts --")
        with app.app_context():
            AlertEmail.query.delete()
            AlertEmailItem.query.delete()
            AlertDispatch.query.delete()
            db.session.commit()

        captured.clear()
        with app.app_context():
            weekly_doc = db.session.get(VehicleDocument, ids["d_weekly"])
            urgent_doc = db.session.get(VehicleDocument, ids["d_urgent"])
            valid_doc = db.session.get(VehicleDocument, ids["d_valid"])
            # Explicit lists; weekly must drop a Valid record and leave an urgent
            # record to the emergency job.
            sent, err, cnt = send_compliance_alert(
                vehicles=[weekly_doc, urgent_doc, valid_doc]
            )
        check("weekly keeps the 90/60/30 record and drops the rest",
              cnt == 1 and sent, f"cnt={cnt} err={err}")
        weekly_subjects = {m["subject"] for m in captured}
        check("weekly subject is only the 90/60/30 record",
              all("ZZ-WEEKLY-01" in s and "ZZ-URGENT-01" not in s
                  and "ZZ-VALID-01" not in s for s in weekly_subjects))
        weekly_recipients = sorted({addr for m in captured for addr in m["to"]})
        for addr in ("zz.ve.tmgr@ananta.com.bd", "zz.ve.tusr@ananta.com.bd",
                     "zz.ve.dept@ananta.com.bd", "transport_agl@ananta.com.bd"):
            check(f"weekly email reaches {addr}", addr in weekly_recipients,
                  str(weekly_recipients))
        for addr in ("zz.ve.off@ananta.com.bd", "not-an-email",
                     "zz.ve.cuser@ananta.com.bd", "zz.ve.cview@ananta.com.bd",
                     "zz.ve.fadm@ananta.com.bd", "zz.ve.super@ananta.com.bd"):
            check(f"weekly email does not reach {addr}",
                  addr not in weekly_recipients, str(weekly_recipients))
        check("factory-B user does not receive factory-A alert",
              "zz.ve.tusr.b@ananta.com.bd" not in weekly_recipients)

        captured.clear()
        with app.app_context():
            urgent_doc = db.session.get(VehicleDocument, ids["d_urgent"])
            personal_doc = db.session.get(VehicleDocument, ids["d_personal"])
            arch_doc = db.session.get(VehicleDocument, ids["d_archived"])
            valid_doc = db.session.get(VehicleDocument, ids["d_valid"])
            sent, err, cnt = send_emergency_alerts(
                vehicles=[urgent_doc, personal_doc, arch_doc, valid_doc]
            )
        check("emergency sends the two urgent records, skips valid + archived",
              cnt == 2 and sent, f"cnt={cnt} err={err}")
        urg_subjects = [m["subject"] for m in captured]
        check("every emergency subject is URGENT:",
              all(s.startswith("URGENT:") for s in urg_subjects))
        check("archived vehicle never alerts",
              not any("ZZ-ARCH-01" in m["html"] for m in captured))
        check("Valid vehicle never alerts",
              not any("ZZ-VALID-01" in m["html"] for m in captured))

        # Dedupe: the same records on a second run must send zero mail.
        captured.clear()
        with app.app_context():
            urgent_doc = db.session.get(VehicleDocument, ids["d_urgent"])
            personal_doc = db.session.get(VehicleDocument, ids["d_personal"])
            sent2, _, cnt2 = send_emergency_alerts(vehicles=[urgent_doc, personal_doc])
        check("dedupe: second emergency run sends nothing",
              not sent2 and cnt2 == 0 and not captured, f"cnt={cnt2} captured={len(captured)}")

        # Renewal (new expiry) alerts again.
        with app.app_context():
            urgent_doc = db.session.get(VehicleDocument, ids["d_urgent"])
            urgent_doc.expiry_date = today + timedelta(days=1)
            urgent_doc.status = compute_status(urgent_doc.expiry_date)
            db.session.commit()
        captured.clear()
        with app.app_context():
            urgent_doc = db.session.get(VehicleDocument, ids["d_urgent"])
            sent3, _, cnt3 = send_emergency_alerts(vehicles=[urgent_doc])
        check("new expiry re-alerts after a renewal", sent3 and cnt3 == 1,
              f"cnt={cnt3}")

        # Failure: SMTP raises. Dedup row must NOT be stamped, failed row stored.
        def boom(message):
            raise RuntimeError("smtp down")

        with app.app_context():
            # A new document with no dedup row yet.
            v = db.session.get(Vehicle, ids["v_weekly"])
            sa_id = db.session.query(User.id).filter_by(username="zz_ve_super").scalar()
            boom_doc = VehicleDocument(
                vehicle_id=v.id, doc_type="route_permit", doc_number="RP-BOOM",
                issue_date=today - timedelta(days=360),
                expiry_date=today - timedelta(days=1),
                status="Expired", is_active=True, created_by=sa_id,
            )
            db.session.add(boom_doc)
            db.session.flush()
            created["docs"].append(boom_doc.id)
            boom_id = boom_doc.id
            db.session.commit()
        mail.send = boom
        with app.app_context():
            boom_doc = db.session.get(VehicleDocument, boom_id)
            sent4, err4, cnt4 = send_emergency_alerts(vehicles=[boom_doc])
        check("SMTP failure returns sent=False and names the error",
              not sent4 and cnt4 == 0 and err4 and "RuntimeError" in err4,
              f"sent={sent4} err={err4}")
        mail.send = _capture
        with app.app_context():
            # Dedup row must NOT exist; a failed AlertEmail row MUST.
            dispatch = AlertDispatch.query.filter_by(
                record_type="vehicle", record_id=boom_id).count()
            failed = AlertEmail.query.filter_by(alert_type="emergency",
                                                 status="failed").count()
        check("failed send does not stamp the dedup row", dispatch == 0,
              f"dispatch rows={dispatch}")
        check("failed send stores a failed AlertEmail row", failed >= 1,
              f"failed rows={failed}")

        # ---------- 3. Manual alerts (RBAC + content + history) ----------
        print("\n-- 3. Manual alerts --")
        with app.app_context():
            urgent_doc = db.session.get(VehicleDocument, ids["d_urgent"])
            allowed = resolve_vehicle_alert_recipients(urgent_doc)
            allowed_emails = {row["email"].lower() for row in allowed}
            tm_user = User.query.filter_by(username="zz_ve_tmgr").first()
            cu_user = User.query.filter_by(username="zz_ve_cuser").first()
            tm_id, cu_id = tm_user.id, cu_user.id
        check("allowed list has the Transport Manager",
              "zz.ve.tmgr@ananta.com.bd" in allowed_emails)
        check("allowed list has the dept recipient",
              "zz.ve.dept@ananta.com.bd" in allowed_emails)
        check("allowed list has the transport group mailbox",
              "transport_agl@ananta.com.bd" in allowed_emails)
        check("allowed list excludes other roles",
              "zz.ve.cuser@ananta.com.bd" not in allowed_emails
              and "zz.ve.fadm@ananta.com.bd" not in allowed_emails)

        captured.clear()
        with app.app_context():
            urgent_doc = db.session.get(VehicleDocument, ids["d_urgent"])
            tm_user = User.query.filter_by(username="zz_ve_tmgr").first()
            sent, err, cnt = send_manual_vehicle_doc_alert(
                urgent_doc, [tm_user],
                group_emails=["transport_agl@ananta.com.bd"],
            )
        check("manual send to one user + one group mailbox",
              sent and cnt == 2 and err is None, f"sent={sent} cnt={cnt} err={err}")
        check("manual send captured both messages", len(captured) == 2)
        check("manual subject starts 'Compliance Notice:'",
              all(m["subject"].startswith("Compliance Notice:") for m in captured))
        for m in captured:
            check(f"manual email names the vehicle in subject ({m['to']})",
                  "ZZ-URGENT-01" in m["subject"])
            check(f"manual email body carries all fields ({m['to']})",
                  "Registration No." in m["html"] and "Document Type" in m["html"]
                  and "Expiry Date" in m["html"] and "Renewal Status" in m["html"])
        with app.app_context():
            man_rows = AlertEmail.query.filter_by(alert_type="manual").all()
            manual_items = AlertEmailItem.query.filter(
                AlertEmailItem.alert_email_id.in_([r.id for r in man_rows])
            ).all()
        check("manual send stores a history row per recipient", len(man_rows) >= 2)
        check("manual history now includes a vehicle item snapshot",
              any(it.record_type == "vehicle" and it.record_id == ids["d_urgent"]
                  for it in manual_items))

        # Vehicle with no valid recipients -> helpful error, no mail attempt.
        captured.clear()
        with app.app_context():
            bare_doc = db.session.get(VehicleDocument, ids["d_bare"])
            sent, err, cnt = send_manual_vehicle_doc_alert(bare_doc, [], group_emails=[])
        check("manual with no recipients returns an error",
              not sent and cnt == 0 and err and "No valid" in err, err)
        check("manual with no recipients sends no mail", not captured)

        # ---- route-level authorization ----
        allowed_route_roles = {"zz_ve_super", "zz_ve_admin", "zz_ve_fadm",
                               "zz_ve_cmgr", "zz_ve_tmgr"}
        denied_roles = {"zz_ve_tusr": 403, "zz_ve_cuser": 403, "zz_ve_cview": 403}

        get_url = f"/vehicles/{ids['v_urgent']}/docs/fitness/send-alert"
        for user, expected in denied_roles.items():
            r = client_for(user).get(get_url)
            check(f"{user} cannot open Send Alert (expects {expected})",
                  r.status_code == expected, str(r.status_code))
            # Also POST: must be blocked at the role gate, not just the form.
            r2 = client_for(user).post(get_url, data={"recipient_id": "9999"})
            check(f"{user} POST Send Alert is denied", r2.status_code == 403,
                  str(r2.status_code))

        for user in allowed_route_roles:
            r = client_for(user).get(get_url)
            check(f"{user} can open Send Alert (expects 200)",
                  r.status_code == 200, str(r.status_code))

        guest = app.test_client()
        anon = guest.get(get_url)
        check("anonymous user is redirected to login",
              anon.status_code == 302 and "/login" in anon.headers.get("Location", ""))

        # Non-existent vehicle / archived vehicle / missing doc.
        tm_client = client_for("zz_ve_tmgr")
        r = tm_client.get(f"/vehicles/{ids['v_archived']}/docs/tax_token/send-alert",
                          follow_redirects=False)
        check("archived vehicle: route redirects back (does not open form)",
              r.status_code == 302, str(r.status_code))
        r = tm_client.get(f"/vehicles/{ids['v_valid']}/docs/fitness/send-alert",
                          follow_redirects=False)
        check("missing doc type: redirect with flash message",
              r.status_code == 302, str(r.status_code))
        r = tm_client.get(f"/vehicles/{ids['v_urgent']}/docs/not_a_type/send-alert")
        check("invalid doc_type: 404", r.status_code == 404, str(r.status_code))
        r = tm_client.get("/vehicles/999999/docs/tax_token/send-alert")
        check("unknown vehicle id: 404", r.status_code == 404, str(r.status_code))

        # ---- Forged POST: outside the allowed list must be ignored ----
        with app.app_context():
            outsider = User.query.filter_by(username="zz_ve_cuser").first()
            outsider_id = outsider.id
        captured.clear()
        with app.app_context():
            email_before = AlertEmail.query.count()
        r = client_for("zz_ve_tmgr").post(
            get_url,
            data={
                "recipient_id": [str(outsider_id)],
                "group_email": ["evil@example.com", "transport_agl@ananta.com.bd"],
            },
            follow_redirects=False,
        )
        with app.app_context():
            email_after = AlertEmail.query.count()
        check("forged user id (outside allowed list) sends no mail",
              not any(m["to"] == ["zz.ve.cuser@ananta.com.bd"] for m in captured),
              str([m["to"] for m in captured]))
        check("forged group address is rejected",
              not any("evil@example.com" in m["to"] for m in captured),
              str([m["to"] for m in captured]))
        # A legitimate group address plus the ignored items: either the form
        # falls back to its own "pick at least one" flash, or it sends only
        # to the legitimate address. Either way, no evil@ delivery.
        check("response is a 200 (re-render) or 302 (post-send redirect)",
              r.status_code in (200, 302), str(r.status_code))
        check("history has at most one new row (the legitimate group mailbox)",
              email_after - email_before <= 1,
              f"before={email_before} after={email_after}")
        if captured:
            delivered = {addr for m in captured for addr in m["to"]}
            check("only the authorized group address was delivered",
                  delivered.issubset({"transport_agl@ananta.com.bd"}), str(delivered))

        # Sidebar: Transport User sees Vehicle Documents but not Send Alert.
        with app.app_context():
            d_url = f"/vehicles/{ids['v_urgent']}"
        page = client_for("zz_ve_tusr").get(d_url).get_data(as_text=True)
        check("Transport User detail page hides Send Alert button",
              "send-alert" not in page.lower())
        page_mgr = client_for("zz_ve_tmgr").get(d_url).get_data(as_text=True)
        check("Transport Manager detail page shows Send Alert",
              "send-alert" in page_mgr.lower())

        mail.send = orig_send

    finally:
        with app.app_context():
            try:
                AlertEmailItem.query.delete()
                AlertEmail.query.delete()
                AlertDispatch.query.delete()
                VehicleDocument.query.filter(VehicleDocument.id.in_(created["docs"])).delete(
                    synchronize_session=False)
                Vehicle.query.filter(Vehicle.id.in_(created["vehicles"])).delete(
                    synchronize_session=False)
                if created["transport_dept"]:
                    dept = db.session.get(Department, created["transport_dept"])
                    if dept is not None:
                        prev = created.get("dept_was_recipients") or []
                        dept.alert_recipients = [u for u in (
                            User.query.filter(User.id.in_(prev)).all() if prev else []
                        )]
                User.query.filter(User.id.in_(created["users"])).delete(
                    synchronize_session=False)
                Factory.query.filter(Factory.id.in_(
                    [created["factory"], created["factory_b"]]
                )).delete(synchronize_session=False)
                if created["company"]:
                    Company.query.filter_by(id=created["company"]).delete(
                        synchronize_session=False)
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                print("cleanup warning:", type(exc).__name__, exc)

    print("\n=== VEHICLE ALERT TESTS ===")
    failed = [name for name, passed in results if not passed]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    print("VEHICLE ALERT TEST:", "PASSED" if not failed else "FAILED")
    if failed:
        print("Failed:", "; ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
