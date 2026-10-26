"""Alert History + My Alerts: role access, filters, scoping, direct URLs.

Runs against a disposable copy of the database (see _isolated_db.py).
"""
import os
import re
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("SECRET_KEY", "alert-history-test-key")
os.environ["COMPLIANCE_SKIP_EMERGENCY"] = "1"

from _isolated_db import assert_isolated, use_isolated_db

use_isolated_db()

from app import create_app, db
from app.models import (
    AlertEmail, AlertEmailItem, Company, Document, Factory, Role, User,
)
from app.utils import compute_status

PASS = "zz-ah-pass-1"
ROLES = [
    "Super Admin", "Administration", "Factory Admin", "Compliance Manager",
    "Compliance User", "Compliance Viewer", "Transport Manager", "Transport User",
]
MY_ALERTS_ROLES = ("Super Admin", "Administration")
results = []


def check(name, passed, detail=""):
    results.append((name, bool(passed)))
    extra = f" :: {detail}" if detail and not passed else ""
    print(f"  [{'OK' if passed else 'FAIL'}] {name}{extra}")


def slug(role):
    return role.lower().replace(" ", "_")


def main():
    app = create_app()
    assert_isolated(app)
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True

    created = {"users": [], "emails": [], "docs": [], "factories": [], "company": None}

    with app.app_context():
        roles = {r.name: r for r in Role.query.all()}
        company = Company(name="ZZ AH Company", code="ZZAH")
        db.session.add(company)
        db.session.flush()
        created["company"] = company.id
        fac_a = Factory(company_id=company.id, name="ZZ AH Factory A", location="A")
        fac_b = Factory(company_id=company.id, name="ZZ AH Factory B", location="B")
        db.session.add_all([fac_a, fac_b])
        db.session.flush()
        created["factories"] = [fac_a.id, fac_b.id]

        users = {}
        for role in ROLES:
            # Super Admin / Administration are global; the rest are tied to factory A.
            factory = None if role in ("Super Admin", "Administration") else fac_a
            u = User(
                username=f"zz_ah_{slug(role)}", full_name=f"ZZ {role}",
                role_id=roles[role].id, factory_id=factory.id if factory else None,
                email=f"zz.ah.{slug(role)}@ananta.com.bd", is_active=True,
            )
            u.set_password(PASS)
            db.session.add(u)
            db.session.flush()
            users[role] = u
            created["users"].append(u.id)

        def add_email(user, alert_type, status, subject, items=(), email=None, link_user=True):
            row = AlertEmail(
                sent_at=datetime.utcnow(), alert_type=alert_type, subject=subject,
                sender="document_tracker@ananta.com.bd",
                recipient_email=email or user.email, recipient_name=user.full_name,
                user_id=user.id if link_user else None, status=status,
                error_detail="RuntimeError" if status == "failed" else None,
                record_count=len(items),
            )
            for title, factory in items:
                row.items.append(AlertEmailItem(
                    record_type="document", record_id=0, title=title,
                    factory_id=factory.id, factory_name=factory.name,
                    record_status="Expired", sources="Uploader",
                ))
            db.session.add(row)
            db.session.flush()
            created["emails"].append(row.id)
            return row

        mail_ids = {}
        for role, u in users.items():
            tag = slug(role)
            if role == "Super Admin":
                items = [(f"ZZDOC-{tag}", fac_a)]
            else:
                items = [(f"ZZDOC-{tag}", fac_a)]
            mail_ids[(role, "weekly")] = add_email(u, "weekly", "sent", f"ZZ weekly {tag}", items).id
            mail_ids[(role, "emergency")] = add_email(u, "emergency", "failed", f"ZZ urgent {tag}", items).id
            mail_ids[(role, "manual")] = add_email(u, "manual", "sent", f"ZZ manual {tag}").id
            mail_ids[(role, "test")] = add_email(u, "test", "sent", f"ZZ test {tag}").id

        # Address stored in different case and without a user_id link.
        mail_ids["case"] = add_email(
            users["Compliance User"], "weekly", "sent", "ZZ case match",
            [("ZZDOC-case", fac_a)], email="ZZ.AH.COMPLIANCE_USER@ANANTA.COM.BD", link_user=False,
        ).id
        # Addressed to the factory-A user but names only a factory-B record.
        mail_ids["xfactory"] = add_email(
            users["Compliance User"], "weekly", "sent", "ZZ other factory only",
            [("ZZDOC-xfactory", fac_b)],
        ).id
        # Literal-wildcard probe subjects (both addressed to the same user).
        mail_ids["pct"] = add_email(users["Compliance Viewer"], "weekly", "sent", "ZZ 100% done").id
        mail_ids["plain"] = add_email(users["Compliance Viewer"], "weekly", "sent", "ZZ 100 plain").id

        # A record the Super Admin is responsible for, for My Alerts content.
        sa = users["Super Admin"]
        today = date.today()
        expired = Document(
            factory_id=fac_a.id, created_by=sa.id, doc_name="ZZ AH Expired Doc",
            doc_type="Other", cert_no="ZZ-AH-1", expiry_date=today - timedelta(days=3),
            status=compute_status(today - timedelta(days=3)), is_active=True,
        )
        valid = Document(
            factory_id=fac_a.id, created_by=sa.id, doc_name="ZZ AH Valid Doc",
            doc_type="Other", cert_no="ZZ-AH-2", expiry_date=today + timedelta(days=400),
            status=compute_status(today + timedelta(days=400)), is_active=True,
        )
        db.session.add_all([expired, valid])
        db.session.flush()
        created["docs"] = [expired.id, valid.id]
        db.session.commit()

    def client_for(role):
        c = app.test_client()
        r = c.post("/login", data={"username": f"zz_ah_{slug(role)}", "password": PASS},
                   follow_redirects=False)
        assert r.status_code == 302, f"login failed for {role}: {r.status_code}"
        return c

    def subjects(resp):
        return set(re.findall(r"ZZ (?:weekly|urgent|manual|test|case match|other factory only|100% done|100 plain)[^<]*", resp.get_data(as_text=True)))

    try:
        clients = {role: client_for(role) for role in ROLES}
        non_sa = [r for r in ROLES if r != "Super Admin"]

        # ---- Alert History: list, per role ------------------------------------
        print("\n-- Alert History: list access and scoping --")
        for role in ROLES:
            tag = slug(role)
            resp = clients[role].get("/alert-history/")
            body = resp.get_data(as_text=True)
            if role == "Super Admin":
                # Super Admin sees the whole table (30 per page), so read every page.
                page = 2
                while "page-link" in body and page <= 20:
                    more = clients[role].get(f"/alert-history/?page={page}").get_data(as_text=True)
                    body += more
                    page += 1
            check(f"{role}: /alert-history/ loads", resp.status_code == 200, str(resp.status_code))
            check(f"{role}: sees own weekly alert", f"ZZ weekly {tag}" in body)
            check(f"{role}: sees own urgent (failed) alert", f"ZZ urgent {tag}" in body)
            check(f"{role}: sees own manual alert", f"ZZ manual {tag}" in body)
            others = [slug(r) for r in ROLES if r != role]
            if role == "Super Admin":
                check("Super Admin: sees everyone's alerts",
                      all(f"ZZ weekly {o}" in body for o in others))
                check("Super Admin: sees test alerts", f"ZZ test {tag}" in body)
            else:
                leaked = [o for o in others if f"ZZ weekly {o}" in body or f"ZZ manual {o}" in body]
                check(f"{role}: sees nobody else's alerts", not leaked, str(leaked))
                check(f"{role}: test alerts hidden", "ZZ test" not in body)

        # ---- Filters ----------------------------------------------------------
        print("\n-- Alert History: filters --")
        for role in ROLES:
            c = clients[role]
            tag = slug(role)
            for kind, expect_hit, expect_miss in (
                ("weekly", f"ZZ weekly {tag}", f"ZZ urgent {tag}"),
                ("emergency", f"ZZ urgent {tag}", f"ZZ weekly {tag}"),
                ("manual", f"ZZ manual {tag}", f"ZZ weekly {tag}"),
            ):
                body = c.get(f"/alert-history/?alert_type={kind}").get_data(as_text=True)
                check(f"{role}: type={kind} keeps matches only",
                      expect_hit in body and expect_miss not in body)
            sent = c.get("/alert-history/?status=sent").get_data(as_text=True)
            failed = c.get("/alert-history/?status=failed").get_data(as_text=True)
            check(f"{role}: result=Sent shows sent, hides failed",
                  f"ZZ weekly {tag}" in sent and f"ZZ urgent {tag}" not in sent)
            check(f"{role}: result=Failed shows failed, hides sent",
                  f"ZZ urgent {tag}" in failed and f"ZZ weekly {tag}" not in failed)
            combo = c.get(
                f"/alert-history/?alert_type=emergency&status=sent&q=ZZ urgent {tag}"
            ).get_data(as_text=True)
            check(f"{role}: type+result combine (urgent+sent is empty)",
                  "No alert emails" in combo and "/alert-history/" in combo
                  and "<tbody>" in combo and "ZZ urgent" not in combo.split("<tbody>")[1])
            by_recipient = c.get(f"/alert-history/?q=zz.ah.{tag}@").get_data(as_text=True)
            check(f"{role}: search by recipient email", f"ZZ weekly {tag}" in by_recipient)
            by_subject = c.get(f"/alert-history/?q=urgent {tag}").get_data(as_text=True)
            check(f"{role}: search by subject",
                  f"ZZ urgent {tag}" in by_subject and f"ZZ weekly {tag}" not in by_subject)
            by_doc = c.get(f"/alert-history/?q=ZZDOC-{tag}").get_data(as_text=True)
            check(f"{role}: search by document title", f"ZZ weekly {tag}" in by_doc
                  and f"ZZ manual {tag}" not in by_doc)
            by_name = c.get(f"/alert-history/?q=ZZ {role}").get_data(as_text=True)
            check(f"{role}: search by recipient name", f"ZZ weekly {tag}" in by_name)

        sa_c = clients["Super Admin"]
        sa_test = sa_c.get("/alert-history/?alert_type=test").get_data(as_text=True)
        check("Super Admin: type=test lists test alerts only",
              "ZZ test super_admin" in sa_test and "ZZ weekly super_admin" not in sa_test)
        for role in non_sa:
            body = clients[role].get("/alert-history/?alert_type=test").get_data(as_text=True)
            check(f"{role}: forged type=test still hides test alerts", "ZZ test" not in body)

        pct = clients["Compliance Viewer"].get("/alert-history/?q=%25").get_data(as_text=True)
        check("search '%' is literal (matches only the % subject)",
              "ZZ 100% done" in pct and "ZZ 100 plain" not in pct)
        und = clients["Compliance Viewer"].get("/alert-history/?q=100_").get_data(as_text=True)
        check("search '_' is literal (no single-char wildcard)",
              "ZZ 100 plain" not in und and "ZZ 100% done" not in und)
        junk = clients["Compliance User"].get("/alert-history/?alert_type=%27%3Bdrop&status=bogus&page=abc")
        check("invalid filter values and page are ignored", junk.status_code == 200, str(junk.status_code))
        far = clients["Compliance User"].get("/alert-history/?page=9999")
        check("out-of-range page renders an empty list", far.status_code == 200, str(far.status_code))
        neg = clients["Compliance User"].get("/alert-history/?page=-3")
        check("negative page does not error", neg.status_code == 200, str(neg.status_code))
        sqli = clients["Compliance User"].get("/alert-history/?q=%27+OR+1%3D1--")
        check("quote in search does not error or widen results",
              sqli.status_code == 200 and "ZZ weekly super_admin" not in sqli.get_data(as_text=True))

        cu_body = clients["Compliance User"].get("/alert-history/").get_data(as_text=True)
        check("email address matched case-insensitively", "ZZ case match" in cu_body)

        # ---- Dropdown options -------------------------------------------------
        print("\n-- Alert History: dropdown options --")
        for role in ROLES:
            body = clients[role].get("/alert-history/").get_data(as_text=True)
            opts = re.findall(r"<option value=\"([^\"]*)\"", body)
            expected = ["", "weekly", "emergency", "manual"]
            if role == "Super Admin":
                expected.append("test")
            expected += ["", "sent", "failed"]
            check(f"{role}: filter dropdown options", opts == expected, str(opts))
            check(f"{role}: option labels",
                  all(label in body for label in ("All types", "Weekly expiry", "Urgent expiry",
                                                  "Manual alert", "All results", "Sent", "Failed")))
        sa_sel = sa_c.get("/alert-history/?alert_type=manual&status=failed").get_data(as_text=True)
        check("selected filters stay selected after submit",
              re.search(r'value="manual"\s+selected', sa_sel) is not None
              and re.search(r'value="failed"\s+selected', sa_sel) is not None)

        # ---- Detail / direct URL ---------------------------------------------
        print("\n-- Alert History: detail pages and direct URLs --")
        for role in ROLES:
            c = clients[role]
            tag = slug(role)
            own = c.get(f"/alert-history/{mail_ids[(role, 'weekly')]}")
            check(f"{role}: own alert detail opens",
                  own.status_code == 200 and f"zz.ah.{tag}@ananta.com.bd" in own.get_data(as_text=True)
                  and f"ZZDOC-{tag}" in own.get_data(as_text=True), str(own.status_code))
            failed = c.get(f"/alert-history/{mail_ids[(role, 'emergency')]}")
            check(f"{role}: failed alert detail shows the error type",
                  failed.status_code == 200 and "RuntimeError" in failed.get_data(as_text=True))
            manual = c.get(f"/alert-history/{mail_ids[(role, 'manual')]}")
            check(f"{role}: alert with no records still opens (list/detail agree)",
                  manual.status_code == 200, str(manual.status_code))
            test_mail = c.get(f"/alert-history/{mail_ids[(role, 'test')]}")
            expected_code = 200 if role == "Super Admin" else 404
            check(f"{role}: own test alert detail -> {expected_code}",
                  test_mail.status_code == expected_code, str(test_mail.status_code))
            victim = "Compliance User" if role != "Compliance User" else "Compliance Viewer"
            other = c.get(f"/alert-history/{mail_ids[(victim, 'weekly')]}")
            expected_code = 200 if role == "Super Admin" else 404
            check(f"{role}: someone else's alert detail -> {expected_code}",
                  other.status_code == expected_code, str(other.status_code))
            missing = c.get("/alert-history/99999999")
            check(f"{role}: unknown alert id -> 404 page", missing.status_code == 404
                  and "Not found" in missing.get_data(as_text=True))
            badid = c.get("/alert-history/abc")
            check(f"{role}: non-numeric alert id -> 404", badid.status_code == 404)

        xf = clients["Compliance User"].get(f"/alert-history/{mail_ids['xfactory']}")
        check("factory-scoped user cannot open an email naming only another factory", xf.status_code == 404,
              str(xf.status_code))
        xf_list = clients["Compliance User"].get("/alert-history/").get_data(as_text=True)
        check("other-factory record title is not shown in the list", "ZZDOC-xfactory" not in xf_list)
        xf_sa = sa_c.get(f"/alert-history/{mail_ids['xfactory']}")
        check("Super Admin can open the cross-factory email", xf_sa.status_code == 200)

        guest = app.test_client()
        for path in ("/alert-history/", f"/alert-history/{mail_ids[('Super Admin', 'weekly')]}",
                     "/my-alerts", "/my-alerts?show=all"):
            r = guest.get(path)
            check(f"anonymous {path} -> login redirect",
                  r.status_code == 302 and "/login" in r.headers.get("Location", ""), str(r.status_code))
        check("alert history is read-only (POST not allowed)",
              clients["Super Admin"].post("/alert-history/").status_code == 405)

        # ---- My Alerts --------------------------------------------------------
        print("\n-- My Alert Responsibilities: Super Admin + Administration --")
        for role in ROLES:
            for path in ("/my-alerts", "/my-alerts?show=all", "/my-alerts/"):
                r = clients[role].get(path)
                if path == "/my-alerts/":
                    # Trailing slash is not a registered route variant.
                    check(f"{role}: {path} is never served",
                          r.status_code in (404, 308) or (role != "Super Admin" and r.status_code == 403),
                          str(r.status_code))
                    continue
                if role in MY_ALERTS_ROLES:
                    check(f"{role}: {path} -> 200",
                          r.status_code == 200 and "My Alert Responsibilities" in r.get_data(as_text=True),
                          str(r.status_code))
                else:
                    body = r.get_data(as_text=True)
                    check(f"{role}: {path} -> 403",
                          r.status_code == 403 and "Access denied" in body
                          and "My Alert Responsibilities" not in body, str(r.status_code))
        check("My Alerts rejects POST", clients["Super Admin"].post("/my-alerts").status_code == 405)

        attention = sa_c.get("/my-alerts").get_data(as_text=True)
        full = sa_c.get("/my-alerts?show=all").get_data(as_text=True)
        check("My Alerts default shows the expired record", "ZZ AH Expired Doc" in attention)
        check("My Alerts default hides a valid record", "ZZ AH Valid Doc" not in attention)
        check("My Alerts 'Show all' includes the valid record",
              "ZZ AH Valid Doc" in full and "ZZ AH Expired Doc" in full)
        check("My Alerts shows why (Uploader) and the expired badge",
              "Uploader" in attention and "Expired" in attention)
        check("My Alerts shows company and factory",
              "ZZ AH Company" in attention and "ZZ AH Factory A" in attention)
        check("My Alerts 'Needs attention' link visible in show-all view",
              "Needs attention" in full)
        check("My Alerts junk show= value falls back to attention view",
              "ZZ AH Valid Doc" not in sa_c.get("/my-alerts?show=%27x").get_data(as_text=True))

        # ---- Navigation -------------------------------------------------------
        print("\n-- Navigation --")
        for role in ROLES:
            body = clients[role].get("/alert-history/").get_data(as_text=True)
            has_my = 'href="/my-alerts"' in body
            check(f"{role}: sidebar 'My Alerts' link {'shown' if role in MY_ALERTS_ROLES else 'hidden'}",
                  has_my == (role in MY_ALERTS_ROLES))
            check(f"{role}: sidebar 'Alert History' link shown", 'href="/alert-history/"' in body)
            active = re.search(r'href="/alert-history/" class="nav-link ([^"]*)"', body)
            check(f"{role}: 'Alert History' highlighted on its own page",
                  bool(active) and "active" in active.group(1))
        body = sa_c.get("/my-alerts").get_data(as_text=True)
        active = re.search(r'href="/my-alerts" class="nav-link ([^"]*)"', body)
        check("Super Admin: 'My Alerts' highlighted on its own page",
              bool(active) and "active" in active.group(1))
        dash = clients["Administration"].get("/", follow_redirects=True).get_data(as_text=True)
        check("Administration dashboard has the My Alerts link", 'href="/my-alerts"' in dash)

    finally:
        with app.app_context():
            try:
                AlertEmailItem.query.filter(AlertEmailItem.alert_email_id.in_(created["emails"])).delete(
                    synchronize_session=False)
                AlertEmail.query.filter(AlertEmail.id.in_(created["emails"])).delete(
                    synchronize_session=False)
                Document.query.filter(Document.id.in_(created["docs"])).delete(synchronize_session=False)
                User.query.filter(User.id.in_(created["users"])).delete(synchronize_session=False)
                Factory.query.filter(Factory.id.in_(created["factories"])).delete(synchronize_session=False)
                if created["company"]:
                    Company.query.filter_by(id=created["company"]).delete(synchronize_session=False)
                db.session.commit()
            except Exception as exc:  # cleanup is best effort on a throwaway DB
                db.session.rollback()
                print("cleanup warning:", type(exc).__name__, exc)

    print("\n=== ALERT HISTORY / MY ALERTS RBAC TESTS ===")
    failed = [name for name, passed in results if not passed]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    print("ALERT HISTORY RBAC TEST:", "PASSED" if not failed else "FAILED")
    if failed:
        print("Failed:", "; ".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
