"""Compliance Audit Tracker: parser, model, RBAC, CRUD, filtering, dashboard,
status engine, and alerts (manual + auto + upcoming reminders).

Self-contained: builds its own .xlsx fixture and its own users/roles. Runs
isolated via tests/_isolated_db.py.
"""
import os
import sys
import tempfile
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("SECRET_KEY", "audit-test-key")
os.environ["COMPLIANCE_SKIP_EMERGENCY"] = "1"

from openpyxl import Workbook

from _isolated_db import assert_isolated, use_isolated_db
use_isolated_db()

from app import create_app, db, mail
from app.models import (
    Audit, AlertDispatch, AlertEmail, AlertEmailItem, Company, Department,
    Factory, Role, User,
)
from app.utils import (
    resolve_audit_alert_recipients, send_manual_audit_alert,
    send_compliance_alert, send_emergency_alerts, send_upcoming_audit_reminders,
    recalculate_all_statuses, record_snapshot,
)

PASS = "zz-aud-pass-1"
results = []


def check(name, passed, detail=""):
    results.append((name, bool(passed)))
    extra = f" :: {detail}" if detail and not passed else ""
    print(f"  [{'OK' if passed else 'FAIL'}] {name}{extra}")


def _make_fixture(path, agl, asl):
    """Write a 2-row-merged-header Audit workbook mirroring the real file."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Audit"
    ws["A1"] = "Ananta Garments Ltd & Ananta"
    ws["A3"] = "Date"; ws["C3"] = "Audit Name"; ws["D3"] = "Audited By"
    ws["E3"] = "Auditor Name"; ws["F3"] = "Buyer"; ws["G3"] = "ID"
    ws["H3"] = "Audit Type"; ws["I3"] = "Announcement Type"; ws["J3"] = "Monitoring Type"
    ws["K3"] = "Factory"; ws["L3"] = "Rating"; ws["M3"] = "Validity"; ws["N3"] = "Cost"
    ws["A4"] = "Start"; ws["B4"] = "End"
    today = date.today()
    rows = [
        # start, end, name, firm, auditor, buyer, id, type, ann, mon, factory, rating, validity, cost
        (today - timedelta(days=400), today - timedelta(days=400), "GSV", "ITS", "Mr A", "Costco", "G1",
         "Security", "Announced", "Annual", "AGL", "0.9", today - timedelta(days=10), "TK 90,000"),   # expired validity
        (today - timedelta(days=100), today - timedelta(days=99), "BSCI", "LRQA", "Mr B", "Walmart", "B1",
         "Social", "Semi-Announced", "2 Years", "AGL & ASL", "B", today + timedelta(days=20), "TK 278,706"),  # expiring 30
        (today - timedelta(days=30), today - timedelta(days=30), "Internal Audit", "Indochine", "Mr C", "", "",
         "Social", "Announced", "Initial", "ASL", "", None, ""),   # no validity
        (today + timedelta(days=5), today + timedelta(days=6), "Next Technical", "LF", "Mr D", "Next", "N1",
         "Technical", "Announced", "Periodic", "Both", "84% (B)", today + timedelta(days=400), ""),  # upcoming + valid
        (today + timedelta(days=40), today + timedelta(days=40), "Future SA8000", "SGS", "Mr E", "", "",
         "Certification", "Announced", "3 Years", "ABM AJL AGL PWPL ASL", "", today + timedelta(days=800), ""),  # upcoming (outside 14d)
    ]
    r = 5
    for row in rows:
        for ci, val in enumerate(row):
            ws.cell(row=r, column=ci + 1, value=val)
        r += 1
    # a blank separator row + a stray-column-only row (should be ignored)
    r += 1
    ws.cell(row=r, column=18, value="Environment")  # col R only -> ignored
    wb.save(path)


def main():
    app = create_app()
    assert_isolated(app)
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True
    app.config["ALERT_RECIPIENTS"] = []

    created = {"users": [], "company": None, "factory": None, "factory_b": None,
               "dept": None, "dept_group_was": None}

    with app.app_context():
        roles = {r.name: r for r in Role.query.all()}
        company = Company(name="ZZ AUD Co", code="ZZAUD")
        db.session.add(company); db.session.flush()
        created["company"] = company.id
        # Reuse real factory names so the tokenizer maps AGL/ASL correctly.
        agl = Factory.query.filter(Factory.name.ilike("%Ananta Garments%")).first()
        asl = Factory.query.filter(Factory.name.ilike("%Ananta Sportswear%")).first()
        created["factory"] = agl.id
        created["factory_b"] = asl.id

        dept = Department.query.filter(db.func.lower(Department.name) == "compliance").first()
        if dept is None:
            dept = Department(name="Compliance", group_email="")
            db.session.add(dept); db.session.flush()
        created["dept"] = dept.id
        created["dept_group_was"] = dept.group_email
        dept.group_email = "compliance_grp@ananta.com.bd"
        db.session.flush()

        def mk_user(username, role, factory=None, email=None, active=True):
            u = User(username=username, full_name=username, role_id=roles[role].id,
                     factory_id=factory.id if factory else None,
                     department_id=dept.id, email=email, is_active=active)
            u.set_password(PASS)
            db.session.add(u); db.session.flush()
            created["users"].append(u.id)
            return u

        sa  = mk_user("zz_aud_super", "Super Admin", email="zz.aud.super@ananta.com.bd")
        cm  = mk_user("zz_aud_cmgr", "Compliance Manager", factory=agl, email="zz.aud.cmgr@ananta.com.bd")
        cu  = mk_user("zz_aud_cuser", "Compliance User", factory=agl, email="zz.aud.cuser@ananta.com.bd")
        cv  = mk_user("zz_aud_cview", "Compliance Viewer", email="zz.aud.cview@ananta.com.bd")
        adm = mk_user("zz_aud_admin", "Administration", email="zz.aud.admin@ananta.com.bd")
        fa  = mk_user("zz_aud_fadm", "Factory Admin", factory=agl, email="zz.aud.fadm@ananta.com.bd")
        tm  = mk_user("zz_aud_tmgr", "Transport Manager", factory=agl, email="zz.aud.tmgr@ananta.com.bd")
        cm_b = mk_user("zz_aud_cmgr_b", "Compliance Manager", factory=asl, email="zz.aud.cmgr.b@ananta.com.bd")
        db.session.commit()
        agl_id, asl_id = agl.id, asl.id

    tmp = tempfile.mkdtemp(prefix="aud_fixture_")
    fixture = os.path.join(tmp, "audit.xlsx")
    with app.app_context():
        agl = db.session.get(Factory, agl_id); asl = db.session.get(Factory, asl_id)
        _make_fixture(fixture, agl, asl)

    def client_for(username):
        c = app.test_client()
        r = c.post("/login", data={"username": username, "password": PASS}, follow_redirects=False)
        assert r.status_code == 302, f"login failed {username}: {r.status_code}"
        return c

    try:
        # ---------- 1. Parser ----------------------------------------------
        print("\n-- 1. Parser --")
        from app.audit_import import parse_workbook, annotate_existing, preview_counts, apply_import, resolve_factory_ids
        with app.app_context():
            rows, errors = parse_workbook(fixture)
            check("parser found 5 audit rows", len(rows) == 5, f"got {len(rows)}")
            annotate_existing(rows)
            counts = preview_counts(rows)
            check("preview all new", counts["new"] == 5 and counts["updated"] == 0, str(counts))
            check("4 rows carry validity", counts["with_validity"] == 4, str(counts))
            # factory tokenizer
            check("AGL & ASL -> both factories",
                  resolve_factory_ids("AGL & ASL") == {agl_id, asl_id})
            check("ABM AJL AGL PWPL ASL -> 5 factories",
                  len(resolve_factory_ids("ABM AJL AGL PWPL ASL")) == 5)
            summary = apply_import(rows, user_id=1)
            check("apply created 5", summary["created"] == 5, str(summary))
            # idempotent
            rows2, _ = parse_workbook(fixture); annotate_existing(rows2)
            summary2 = apply_import(rows2, user_id=1)
            check("re-import updates, no dupes", summary2["created"] == 0 and Audit.query.count() == 5,
                  f"{summary2} total={Audit.query.count()}")

        # ---------- 2. Status engine ---------------------------------------
        print("\n-- 2. Status engine --")
        with app.app_context():
            recalculate_all_statuses()
            by_name = {a.audit_name: a for a in Audit.query.all()}
            check("expired validity -> Expired", by_name["GSV"].status == "Expired", by_name["GSV"].status)
            check("validity +20d -> Expiring in 30 Days",
                  by_name["BSCI"].status == "Expiring in 30 Days", by_name["BSCI"].status)
            check("no validity -> No Validity",
                  by_name["Internal Audit"].status == "No Validity", by_name["Internal Audit"].status)
            check("validity +400d -> Valid", by_name["Next Technical"].status == "Valid",
                  by_name["Next Technical"].status)

        # ---------- 3. RBAC -------------------------------------------------
        print("\n-- 3. RBAC --")
        with app.app_context():
            any_id = Audit.query.first().id
        view_ok = ("zz_aud_super", "zz_aud_cmgr", "zz_aud_cuser", "zz_aud_cview")
        view_no = ("zz_aud_admin", "zz_aud_fadm", "zz_aud_tmgr")
        for u in view_ok:
            r = client_for(u).get("/audits/")
            check(f"{u} can list audits (200)", r.status_code == 200, str(r.status_code))
        for u in view_no:
            r = client_for(u).get("/audits/")
            check(f"{u} denied audit list (403)", r.status_code == 403, str(r.status_code))
            r2 = client_for(u).get(f"/audits/{any_id}")
            check(f"{u} denied audit detail (403)", r2.status_code == 403, str(r2.status_code))
        # manage gate: cuser can add, cview cannot
        check("Compliance User can open add form (200)",
              client_for("zz_aud_cuser").get("/audits/add").status_code == 200)
        check("Compliance Viewer cannot open add form (403)",
              client_for("zz_aud_cview").get("/audits/add").status_code == 403)
        # delete gate: cmgr yes, cuser no
        check("Compliance Manager can open delete (200)",
              client_for("zz_aud_cmgr").get(f"/audits/{any_id}/delete").status_code == 200)
        check("Compliance User cannot open delete (403)",
              client_for("zz_aud_cuser").get(f"/audits/{any_id}/delete").status_code == 403)
        # import gate: cmgr yes, cuser no
        check("Compliance Manager can open import (200)",
              client_for("zz_aud_cmgr").get("/audits/import").status_code == 200)
        check("Compliance User cannot open import (403)",
              client_for("zz_aud_cuser").get("/audits/import").status_code == 403)
        # anonymous
        anon = app.test_client().get("/audits/")
        check("anonymous redirected to login",
              anon.status_code == 302 and "/login" in anon.headers.get("Location", ""))

        # ---------- 4. CRUD via routes -------------------------------------
        print("\n-- 4. CRUD --")
        c = client_for("zz_aud_cmgr")
        r = c.post("/audits/add", data={
            "date_start": "2026-03-01", "date_end": "2026-03-02",
            "audit_name": "ZZ Manual Audit", "audited_by": "ZZ Firm",
            "audit_type": "Social", "announcement_type": "Announced",
            "monitoring_type": "Annual", "factory_raw": "AGL",
            "factory_ids": [str(agl_id)], "validity_date": "2027-03-01",
            "rating": "A", "cost": "TK 1,000",
        }, follow_redirects=False)
        check("add audit redirects (302)", r.status_code == 302, str(r.status_code))
        with app.app_context():
            made = Audit.query.filter_by(audit_name="ZZ Manual Audit").first()
            check("audit persisted", made is not None)
            check("factory link applied", made and [f.id for f in made.factories] == [agl_id])
            check("status from validity = Valid", made and made.status == "Valid", made.status if made else "-")
            made_id = made.id
        # edit
        r = c.post(f"/audits/{made_id}/edit", data={
            "date_start": "2026-03-01", "audit_name": "ZZ Manual Audit (edited)",
            "audit_type": "Technical", "announcement_type": "Unannounced",
            "factory_raw": "AGL & ASL", "factory_ids": [str(agl_id), str(asl_id)],
            "validity_date": "",
        }, follow_redirects=False)
        check("edit redirects (302)", r.status_code == 302, str(r.status_code))
        with app.app_context():
            made = db.session.get(Audit, made_id)
            check("edit changed name", made.audit_name == "ZZ Manual Audit (edited)")
            check("edit cleared validity -> No Validity", made.status == "No Validity", made.status)
            check("edit set two factories", len(made.factories) == 2)
        # archive + delete
        c.post(f"/audits/{made_id}/archive")
        with app.app_context():
            check("archive set is_active False", db.session.get(Audit, made_id).is_active is False)
        r = c.post(f"/audits/{made_id}/delete", data={"confirm_name": "wrong"}, follow_redirects=False)
        check("delete wrong name re-renders (200)", r.status_code == 200, str(r.status_code))
        with app.app_context():
            check("audit still there after wrong confirm", db.session.get(Audit, made_id) is not None)
        r = c.post(f"/audits/{made_id}/delete", data={"confirm_name": "ZZ Manual Audit (edited)"},
                   follow_redirects=False)
        check("delete correct name redirects (302)", r.status_code == 302)
        with app.app_context():
            check("audit deleted", db.session.get(Audit, made_id) is None)

        # ---------- 5. Factory filtering + scope ---------------------------
        print("\n-- 5. Filtering + scope --")
        c = client_for("zz_aud_super")
        html = c.get(f"/audits/?factory_id={asl_id}").get_data(as_text=True)
        # BSCI (AGL & ASL) and Internal Audit (ASL) link ASL; GSV (AGL only) does not.
        check("factory filter shows ASL-linked audit", "BSCI" in html and "Internal Audit" in html)
        check("factory filter hides AGL-only audit", "GSV" not in html)
        # status filter
        html = c.get("/audits/?status=Expired").get_data(as_text=True)
        check("status filter Expired shows GSV", "GSV" in html and "BSCI" not in html)
        # search
        html = c.get("/audits/?search=walmart").get_data(as_text=True)
        check("search by buyer finds BSCI", "BSCI" in html)
        # scope: factory-B compliance manager (ASL) sees ASL audits but not AGL-only GSV
        html = client_for("zz_aud_cmgr_b").get("/audits/").get_data(as_text=True)
        check("ASL manager sees ASL-linked BSCI", "BSCI" in html)
        check("ASL manager does not see AGL-only GSV", "GSV" not in html)

        # ---------- 6. Recipients + manual alert --------------------------
        print("\n-- 6. Alerts --")
        with app.app_context():
            gsv = Audit.query.filter_by(audit_name="GSV").first()  # AGL
            allowed = resolve_audit_alert_recipients(gsv)
            emails = {r["email"].lower() for r in allowed}
        check("recipients include AGL compliance manager", "zz.aud.cmgr@ananta.com.bd" in emails, str(emails))
        check("recipients include compliance group mailbox", "compliance_grp@ananta.com.bd" in emails, str(emails))
        check("recipients include global compliance viewer", "zz.aud.cview@ananta.com.bd" in emails, str(emails))
        check("recipients exclude ASL-only manager for AGL audit",
              "zz.aud.cmgr.b@ananta.com.bd" not in emails, str(emails))
        check("recipients exclude non-compliance admin", "zz.aud.admin@ananta.com.bd" not in emails)

        orig_send = mail.send
        captured = []
        mail.send = lambda m: captured.append({"subject": m.subject, "to": list(m.recipients), "html": m.html})

        with app.app_context():
            gsv = Audit.query.filter_by(audit_name="GSV").first()
            cm_user = User.query.filter_by(username="zz_aud_cmgr").first()
            sent, err, cnt = send_manual_audit_alert(gsv, [cm_user], group_emails=["compliance_grp@ananta.com.bd"])
        check("manual send to user + group (2)", sent and cnt == 2 and err is None, f"{sent} {cnt} {err}")
        check("manual subject names the audit", all("GSV" in m["subject"] for m in captured), str([m["subject"] for m in captured]))
        check("manual body carries audit fields",
              all("Audited By" in m["html"] and "Validity" in m["html"] for m in captured))
        with app.app_context():
            man = AlertEmail.query.filter_by(alert_type="manual").count()
            items = AlertEmailItem.query.filter_by(record_type="audit").count()
        check("manual history rows stored", man >= 2)
        check("history has audit item snapshot", items >= 1)

        # auto weekly: BSCI (expiring 30) should be picked; GSV (expired) should NOT
        captured.clear()
        with app.app_context():
            sent, err, cnt = send_compliance_alert()
        weekly_subjects = " ".join(m["subject"] for m in captured)
        check("weekly alert includes expiring audit (BSCI)", "BSCI" in weekly_subjects, weekly_subjects[:200])
        check("weekly alert excludes expired audit (GSV)", "GSV" not in weekly_subjects)

        # auto emergency: GSV (expired) picked; dedup on second run
        captured.clear()
        with app.app_context():
            sent, err, cnt = send_emergency_alerts()
        emerg_subjects = " ".join(m["subject"] for m in captured)
        check("emergency includes expired audit (GSV)", "GSV" in emerg_subjects, emerg_subjects[:200])
        captured.clear()
        with app.app_context():
            sent2, _, cnt2 = send_emergency_alerts()
        check("emergency dedup: second run no audit re-send",
              not any("GSV" in m["subject"] for m in captured), str([m["subject"] for m in captured]))

        # upcoming reminder: "Next Technical" (start +5d) in window; "Future SA8000" (+40d) out
        captured.clear()
        with app.app_context():
            sent, err, cnt = send_upcoming_audit_reminders()
        up_subjects = " ".join(m["subject"] for m in captured)
        check("upcoming reminder includes +5d audit", "Next Technical" in up_subjects, up_subjects[:200])
        check("upcoming reminder excludes +40d audit", "Future SA8000" not in up_subjects)
        captured.clear()
        with app.app_context():
            send_upcoming_audit_reminders()
        check("upcoming reminder dedup: second run silent",
              not any("Next Technical" in m["subject"] for m in captured))

        mail.send = orig_send

        # ---------- 7. Manual-alert route RBAC + forged POST --------------
        print("\n-- 7. Alert route --")
        with app.app_context():
            gsv_id = Audit.query.filter_by(audit_name="GSV").first().id
        check("Compliance User can open send-alert (200)",
              client_for("zz_aud_cuser").get(f"/audits/{gsv_id}/send-alert").status_code == 200)
        check("Compliance Viewer cannot open send-alert (403)",
              client_for("zz_aud_cview").get(f"/audits/{gsv_id}/send-alert").status_code == 403)
        # forged POST: a non-recipient user id + bogus group address are dropped
        orig_send = mail.send
        captured = []
        mail.send = lambda m: captured.append({"to": list(m.recipients)})
        with app.app_context():
            bad_uid = User.query.filter_by(username="zz_aud_admin").first().id
        client_for("zz_aud_cmgr").post(f"/audits/{gsv_id}/send-alert", data={
            "recipient_id": [str(bad_uid)],
            "group_email": ["evil@example.com", "compliance_grp@ananta.com.bd"],
        }, follow_redirects=False)
        delivered = {a for m in captured for a in m["to"]}
        check("forged non-recipient dropped", "zz.aud.admin@ananta.com.bd" not in delivered, str(delivered))
        check("forged group dropped", "evil@example.com" not in delivered, str(delivered))
        check("legit group delivered", "compliance_grp@ananta.com.bd" in delivered, str(delivered))
        mail.send = orig_send

        # ---------- 8. Import via route (preview + confirm) ---------------
        print("\n-- 8. Import route --")
        with app.app_context():
            Audit.query.delete(); db.session.commit()
        c = client_for("zz_aud_cmgr")
        with open(fixture, "rb") as f:
            r = c.post("/audits/import", data={"file": (f, "audit.xlsx")},
                       content_type="multipart/form-data")
        check("import preview renders (200)", r.status_code == 200 and b"Confirm Import" in r.data)
        # extract token
        import re as _re
        m = _re.search(rb'name="token" value="([0-9a-f]{32})"', r.data)
        check("preview issued a token", m is not None)
        if m:
            token = m.group(1).decode()
            r = c.post("/audits/import/confirm", data={"token": token, "filename": "audit.xlsx"},
                       follow_redirects=True)
            check("confirm succeeds", r.status_code == 200)
            with app.app_context():
                check("import route created 5 audits", Audit.query.count() == 5, str(Audit.query.count()))

    finally:
        with app.app_context():
            try:
                AlertEmailItem.query.filter_by(record_type="audit").delete(synchronize_session=False)
                ids = [e.id for e in AlertEmail.query.filter(AlertEmail.subject.like("%Audit%")).all()]
                # best-effort cleanup of test audits + deps
                AlertDispatch.query.filter_by(record_type="audit").delete(synchronize_session=False)
                for a in Audit.query.all():
                    a.factories = []; a.alert_recipients = []
                Audit.query.delete(synchronize_session=False)
                User.query.filter(User.id.in_(created["users"])).delete(synchronize_session=False)
                if created["dept"]:
                    d = db.session.get(Department, created["dept"])
                    if d:
                        d.group_email = created.get("dept_group_was")
                if created["company"]:
                    Company.query.filter_by(id=created["company"]).delete(synchronize_session=False)
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                print("cleanup warning:", type(exc).__name__, exc)

    print("\n=== AUDIT TRACKER TESTS ===")
    failed = [n for n, p in results if not p]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    print("AUDIT TEST:", "PASSED" if not failed else "FAILED")
    if failed:
        print("Failed:", "; ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
