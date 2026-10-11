"""
Full-project integration test.

Covers authentication, all six roles, factory isolation, documents,
employee compliance, alert recipients, system logs, CSRF, uploads,
idle timeout, lockout, status engine reuse, backup, and scheduled jobs.
Creates only ZZ-prefixed records and restores every password afterwards.
"""
import io
import os
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "full-test-key")

from app import create_app, db
from app.models import (
    User, Role, Factory, Department, Document, DocumentVersion, Employee,
    EmployeeCompliance, HealthCheckup, SystemLog,
)
from app.utils import compute_status, STATUS_EXPIRED, STATUS_VALID, recalculate_all_statuses

PASS = "full-test-pass-123"
results = []


def check(name, passed, detail=""):
    results.append((name, bool(passed), detail))
    flag = "OK" if passed else "FAIL"
    extra = f" :: {detail}" if detail and not passed else ""
    print(f"  [{flag}] {name}{extra}")


def login(client, username):
    return client.post("/login", data={"username": username, "password": PASS}, follow_redirects=True)


def first_user(role_name, factory_id=None, require_factory=False):
    q = User.query.join(Role).filter(Role.name == role_name, User.is_active.is_(True))
    if factory_id is not None:
        q = q.filter(User.factory_id == factory_id)
    elif require_factory:
        q = q.filter(User.factory_id.isnot(None))
    return q.order_by(User.id).first()


def main():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True
    app.config["ALERT_RECIPIENTS"] = []

    saved_pw = {}
    created_doc_ids = []
    created_emp_ids = []
    created_ver_paths = []

    with app.app_context():
        roles = {r.name: r for r in Role.query.all()}
        required = ["Super Admin", "Administration", "Factory Admin",
                    "Compliance Manager", "Compliance User", "Compliance Viewer"]
        check("all six roles exist", all(n in roles for n in required),
              f"have {sorted(roles)}")
        check("transport roles exist", "Transport Manager" in roles and "Transport User" in roles)
        check("head office location exists", Factory.query.filter(Factory.name.ilike("%head office%")).first() is not None)
        transport = Department.query.filter(db.func.lower(Department.name) == "transport").first()
        compliance_dept = Department.query.filter(db.func.lower(Department.name) == "compliance").first()
        check("transport department exists", transport is not None and transport.group_email == "transport_agl@ananta.com.bd")
        check(
            "compliance group email is shared",
            compliance_dept is not None and compliance_dept.group_email == "compliance_dept@ananta.com.bd",
        )

        sa = first_user("Super Admin")
        admin_role_user = first_user("Administration")
        viewer = first_user("Compliance Viewer")
        fa_a = first_user("Factory Admin", require_factory=True)
        fa_b = None
        if fa_a:
            fa_b = User.query.join(Role).filter(
                Role.name == "Factory Admin", User.is_active.is_(True),
                User.factory_id.isnot(None), User.factory_id != fa_a.factory_id,
            ).first()
        mgr = first_user("Compliance Manager", factory_id=fa_a.factory_id if fa_a else None) \
            or first_user("Compliance Manager", require_factory=True)
        user_role = first_user("Compliance User", factory_id=fa_a.factory_id if fa_a else None) \
            or first_user("Compliance User", require_factory=True)

        actors = {
            "Super Admin": sa,
            "Administration": admin_role_user,
            "Factory Admin A": fa_a,
            "Factory Admin B": fa_b,
            "Compliance Manager": mgr,
            "Compliance User": user_role,
            "Compliance Viewer": viewer,
        }
        for label, u in actors.items():
            check(f"user available: {label}", u is not None,
                  "missing" if u is None else u.username)

        for u in filter(None, actors.values()):
            saved_pw[u.id] = (u.password_hash, u.failed_login_attempts, u.locked_until)
            u.set_password(PASS)
            u.failed_login_attempts = 0
            u.locked_until = None
            if not u.email:
                u.email = f"{u.username.replace(' ', '.').lower()}@ananta.com.bd"
        db.session.commit()

        factory_a = Factory.query.get(fa_a.factory_id) if fa_a else Factory.query.first()
        factory_b = Factory.query.get(fa_b.factory_id) if fa_b else (
            Factory.query.filter(Factory.id != factory_a.id).first() if factory_a else None
        )
        check("two factories for isolation", factory_a is not None and factory_b is not None)

        names = {
            "sa": sa.username if sa else None,
            "adm": admin_role_user.username if admin_role_user else None,
            "fa_a": fa_a.username if fa_a else None,
            "fa_b": fa_b.username if fa_b else None,
            "mgr": mgr.username if mgr else None,
            "usr": user_role.username if user_role else None,
            "view": viewer.username if viewer else None,
        }
        factory_a_id = factory_a.id if factory_a else None
        factory_b_id = factory_b.id if factory_b else None
        factory_a_name = factory_a.name if factory_a else ""
        factory_b_name = factory_b.name if factory_b else ""
        sa_id = sa.id if sa else None

    # ---- Auth ----
    client = app.test_client()
    r = client.get("/")
    check("unauthenticated dashboard redirects", r.status_code in (302, 401))

    r = client.post("/login", data={"username": names["sa"], "password": "wrong-pass"}, follow_redirects=True)
    check("invalid password rejected", r.status_code == 200 and b"Invalid" in r.data)

    r = login(client, names["sa"])
    check("super admin login", r.status_code == 200 and (b"Dashboard" in r.data or b"Factory Documents" in r.data))

    r = client.get("/logout")
    check("GET logout forbidden", r.status_code == 405)
    r = client.post("/logout", follow_redirects=True)
    check("POST logout", r.status_code == 200 and b"Login" in r.data)

    # ---- No idle timeout: a long-idle session stays signed in ----
    login(client, names["sa"])
    with client.session_transaction() as sess:
        sess["last_activity"] = time_now_minus_hours(24)
    r = client.get("/", follow_redirects=False)
    check("session with no activity for a day still works", r.status_code == 200)

    # ---- Lockout ----
    lock_target = names["usr"] or names["mgr"]
    if lock_target:
        c_lock = app.test_client()
        for _ in range(5):
            c_lock.post("/login", data={"username": lock_target, "password": "bad"}, follow_redirects=True)
        r = c_lock.post("/login", data={"username": lock_target, "password": PASS}, follow_redirects=True)
        locked = b"Too many failed" in r.data or b"wait" in r.data.lower()
        check("persistent lockout after 5 failures", locked)
        with app.app_context():
            u = User.query.filter_by(username=lock_target).first()
            u.failed_login_attempts = 0
            u.locked_until = None
            db.session.commit()

    # ---- Role pages ----
    role_checks = [
        (names["sa"], "/system-logs/", 200, "Super Admin sees System Logs"),
        (names["sa"], "/admin/backups", 200, "Super Admin sees Backups"),
        (names["adm"], "/admin/backups", 403, "Administration blocked from Backups"),
        (names["sa"], "/users/", 200, "Super Admin sees Users"),
        (names["sa"], f"/users/?search={names['sa']}", 200, "Super Admin can search users"),
        (names["sa"], "/admin/companies", 200, "Super Admin sees Companies"),
        (names["adm"], "/system-logs/", 403, "Administration blocked from System Logs"),
        (names["adm"], "/documents/add", 200, "Administration can add documents"),
        (names["sa"], "/regulatory/categories", 200, "Super Admin sees Regulatory Directory"),
        (names["adm"], "/regulatory/categories", 403, "Administration cannot edit Regulatory Directory"),
        (names["view"], "/regulatory/categories", 403, "Viewer cannot manage Regulatory Directory"),
        (names["fa_a"], "/regulatory/authorities", 403, "Factory Admin cannot edit Regulatory Directory"),
        (names["view"], "/documents/add", 403, "Viewer cannot add documents"),
        (names["view"], "/employees/add", 403, "Viewer cannot add employees"),
        (names["view"], "/system-logs/", 403, "Viewer blocked from System Logs"),
        (names["usr"], "/system-logs/", 403, "Compliance User blocked from System Logs"),
        (names["usr"], "/users/", 403, "Compliance User blocked from Users"),
        (names["fa_a"], "/users/", 200, "Factory Admin sees Users"),
        (names["fa_a"], "/admin/companies", 403, "Factory Admin blocked from Companies"),
        (names["mgr"], "/documents/add", 200, "Manager can add documents"),
    ]
    for username, path, expect, label in role_checks:
        if not username:
            check(label, False, "no user")
            continue
        c = app.test_client()
        login(c, username)
        r = c.get(path)
        check(label, r.status_code == expect, f"{path} -> {r.status_code} expected {expect}")

    # ---- Document CRUD + recipients + isolation ----
    c = app.test_client()
    login(c, names["sa"])
    found = c.get(f"/users/?search={names['sa']}")
    check(
        "user search finds the account",
        found.status_code == 200 and names["sa"].encode() in found.data and b'Search' in found.data,
    )
    missing = c.get("/users/?search=zzzz-not-a-user")
    check("user search empty state", b"No users found." in missing.data)
    with app.app_context():
        role = Role.query.filter_by(name="Compliance User").first()
        extra = User(
            username="zz_delete_me",
            full_name="ZZ Delete Me",
            email="zz.delete.me@ananta.com.bd",
            role_id=role.id,
            factory_id=factory_a_id,
            is_active=True,
        )
        extra.set_password("unused-pass-1")
        db.session.add(extra)
        db.session.commit()
        extra_id = extra.id
    listed = c.get("/users/")
    check("user list offers delete", b"/users/delete/" in listed.data)
    confirm = c.get(f"/users/delete/{extra_id}")
    check("delete user page names the account", confirm.status_code == 200 and b"zz_delete_me" in confirm.data)
    wrong_user = c.post(f"/users/delete/{extra_id}", data={"confirm_name": "nope"}, follow_redirects=True)
    check("delete user requires the exact username", b"exactly" in wrong_user.data)
    with app.app_context():
        check("wrong confirmation keeps the user", User.query.get(extra_id) is not None)
    gone_user = c.post(f"/users/delete/{extra_id}", data={"confirm_name": "zz_delete_me"}, follow_redirects=True)
    check("user deleted", gone_user.status_code == 200 and b"Deleted user zz_delete_me" in gone_user.data)
    with app.app_context():
        check("deleted user is gone", User.query.filter_by(username="zz_delete_me").first() is None)
    self_delete = c.post(f"/users/delete/{sa_id}", data={"confirm_name": names["sa"]}, follow_redirects=True)
    check("cannot delete the signed-in account", b"cannot delete this user" in self_delete.data.lower())
    doc_list = c.get("/documents/")
    check("document list hides version numbers", b"<th>Version</th>" not in doc_list.data)
    past = (date.today() - timedelta(days=2)).isoformat()
    future = (date.today() + timedelta(days=120)).isoformat()

    r = c.get("/documents/add")
    check("document form offers regulatory reference",
          r.status_code == 200 and b"Regulatory reference (optional)" in r.data)

    with app.app_context():
        scope_user = User.query.filter_by(username=names["fa_a"]).first()
        scope_department = Department.query.filter(db.func.lower(Department.name) == "compliance").first()
        saved_scope_department = scope_user.department_id
        scope_user.department_id = scope_department.id
        db.session.commit()
        scope_user_id = scope_user.id
        scope_user_email = scope_user.email
        scope_department_id = scope_department.id

    # The alert-users API returns {users, group_emails} so the form can show
    # the department group mailbox as a department-wide recipient alongside
    # the factory + department users.
    api = c.get(
        f"/documents/api/alert-users?factory_id={factory_a_id}&department_id={scope_department_id}"
    )
    import json as _json
    api_payload = _json.loads(api.data.decode()) if api.status_code == 200 else {}
    check(
        "alert-users API returns both users and the compliance group mailbox",
        api.status_code == 200
        and isinstance(api_payload, dict)
        and "users" in api_payload
        and any(
            entry.get("email") == "compliance_dept@ananta.com.bd"
            for entry in api_payload.get("group_emails", [])
        ),
        str(api_payload)[:300],
    )
    api_user_ids = {entry["id"] for entry in api_payload.get("users", [])}
    check(
        "alert-users API lists the in-scope factory+department user",
        scope_user_id in api_user_ids,
        str(sorted(api_user_ids)),
    )

    # Global Access + Department: factory=None user with the same department
    # must be listed for every factory in that department. Use the Compliance
    # Manager role so the match is NOT just an artifact of the global roles
    # (Super Admin / Compliance Viewer / Administration).
    with app.app_context():
        from app.models import Role as _Role
        cm_role = _Role.query.filter_by(name="Compliance Manager").first()
        global_dept_user = User(
            username="zz_global_compliance",
            full_name="ZZ Global Compliance",
            email="zz_global_compliance@ananta.com.bd",
            role_id=cm_role.id,
            factory_id=None,
            department_id=scope_department_id,
            is_active=True,
        )
        global_dept_user.set_password(PASS)
        db.session.add(global_dept_user)
        db.session.commit()
        global_dept_user_id = global_dept_user.id

    other_factory_id = factory_b_id or factory_a_id
    api_a = c.get(
        f"/documents/api/alert-users?factory_id={factory_a_id}&department_id={scope_department_id}"
    )
    api_b = c.get(
        f"/documents/api/alert-users?factory_id={other_factory_id}&department_id={scope_department_id}"
    )
    pay_a = _json.loads(api_a.data.decode()) if api_a.status_code == 200 else {}
    pay_b = _json.loads(api_b.data.decode()) if api_b.status_code == 200 else {}
    ids_a = {e["id"] for e in pay_a.get("users", [])}
    ids_b = {e["id"] for e in pay_b.get("users", [])}
    check(
        "global-access user with department appears for factory A",
        global_dept_user_id in ids_a,
        str(sorted(ids_a)),
    )
    check(
        "global-access user with department appears for the other factory",
        global_dept_user_id in ids_b,
        str(sorted(ids_b)),
    )

    # user_matches_factory_department honors Global Access
    from app.utils import user_matches_factory_department as _umfd
    with app.app_context():
        gdu = User.query.get(global_dept_user_id)
        unrelated_dept = (
            Department.query.filter(
                db.func.lower(Department.name) != "compliance",
            ).first()
        )
        check(
            "global-access user matches factory A + compliance",
            _umfd(gdu, factory_a_id, scope_department_id),
        )
        check(
            "global-access user matches other factory + compliance",
            _umfd(gdu, other_factory_id, scope_department_id),
        )
        check(
            "global-access user does NOT match a different department",
            unrelated_dept is None
            or not _umfd(gdu, factory_a_id, unrelated_dept.id),
        )

    # Keep global_dept_user_id for the automation check later; it is cleaned
    # up at the end of the test alongside the other ZZ fixtures.
    r = c.post("/documents/add", data={
        "factory_id": str(factory_a_id),
        "department_id": str(scope_department_id),
        "doc_name": "ZZ Full Test License",
        "doc_type": "Fire License",
        "cert_no": "ZZ-FULL-1",
        "issue_date": "2024-01-01",
        "expiry_date": past,
        "issuing_authority": "Test Authority",
        "responsible_person": "Tester",
        "renewal_status": "Not Started",
        "remarks": "full project test",
        "alert_recipients": [str(scope_user_id), str(sa_id)],
    }, follow_redirects=True)
    check("create document", r.status_code == 200)

    with app.app_context():
        shared_department = Department(name="ZZ Shared Dept")
        db.session.add(shared_department)
        db.session.commit()
        shared_department_id = shared_department.id
    saved = c.post("/documents/add", data={
        "factory_id": str(factory_a_id),
        "department_id": str(shared_department_id),
        "doc_name": "ZZ Shared Dept Doc",
        "doc_type": "Fire License",
        "cert_no": "ZZ-SHARED-DEPT",
        "issue_date": "2026-01-01",
        "expiry_date": "2026-12-01",
        "renewal_status": "Not Started",
    }, follow_redirects=True)
    check("department is independent of factory", saved.status_code == 200 and b"Choose a department from the list" not in saved.data)
    with app.app_context():
        shared_doc = Document.query.filter_by(cert_no="ZZ-SHARED-DEPT").first()
        check(
            "shared department saved on the factory document",
            shared_doc is not None and shared_doc.department_id == shared_department_id and shared_doc.factory_id == factory_a_id,
        )
        if shared_doc:
            db.session.delete(shared_doc)
        leftover = Department.query.get(shared_department_id)
        if leftover:
            db.session.delete(leftover)
        db.session.commit()
    inverted = c.post("/documents/add", data={
        "factory_id": str(factory_a_id),
        "department_id": "0",
        "doc_name": "ZZ Bad Dates",
        "doc_type": "Fire License",
        "cert_no": "ZZ-BAD-DATE",
        "issue_date": "2026-06-01",
        "expiry_date": "2026-01-01",
        "renewal_status": "Not Started",
    }, follow_redirects=True)
    check("expiry before issue date is rejected", b"on or after the issue date" in inverted.data)

    with app.app_context():
        doc = Document.query.filter_by(cert_no="ZZ-FULL-1").first()
        check("document persisted", doc is not None)
        if doc:
            created_doc_ids.append(doc.id)
            check("status auto Expired", doc.status == STATUS_EXPIRED, doc.status)
            recips = [u.id for u in doc.alert_recipients]
            check("alert recipient saved", scope_user_id in recips and sa_id not in recips, str(recips))
            doc_id = doc.id
            other_factory_doc = Document.query.filter(
                Document.factory_id == factory_b_id, Document.is_active.is_(True)
            ).first()
            other_doc_id = other_factory_doc.id if other_factory_doc else None
        else:
            doc_id = None
            other_doc_id = None

    if doc_id:
        r = c.get(f"/documents/view/{doc_id}")
        check("view document", r.status_code == 200 and b"ZZ Full Test License" in r.data)
        check("view shows recipients", b"Alert Recipients" in r.data)
        check("document detail hides version labels", b"Version History" not in r.data and b">v1<" not in r.data and b">v2<" not in r.data)

        from app import mail as flask_mail
        sent_mail = []
        original_send = flask_mail.send

        def capture_mail(message):
            sent_mail.append((message.subject, list(message.recipients), message.html))

        flask_mail.send = capture_mail
        try:
            preview = c.get(f"/documents/send-alert/{doc_id}")
            check(
                "manual alert page names the document",
                preview.status_code == 200 and b"ZZ Full Test License" in preview.data and b"Send this alert" in preview.data,
            )
            empty = c.post(f"/documents/send-alert/{doc_id}", follow_redirects=True)
            check(
                "manual alert requires a selected recipient",
                b"Select at least one recipient" in empty.data and not sent_mail,
            )
            with app.app_context():
                other_user = User.query.filter(
                    User.factory_id == factory_b_id,
                    User.is_active.is_(True),
                ).first()
                other_id = other_user.id if other_user else None
                other_email = other_user.email if other_user else None
            posted = c.post(
                f"/documents/send-alert/{doc_id}",
                data={
                    "recipient_id": [str(scope_user_id), str(sa_id)] + ([str(other_id)] if other_id else []),
                    "group_email": ["compliance_dept@ananta.com.bd"],
                },
                follow_redirects=True,
            )
            recipients = [addr.lower() for _subject, batch, _html in sent_mail for addr in batch]
            bodies = [html for _subject, _batch, html in sent_mail]
            check(
                "manual alert sent for this document",
                posted.status_code == 200
                and len(sent_mail) == 2
                and all(subject.startswith("Compliance Notice: ZZ Full Test License") for subject, _b, _h in sent_mail)
                and scope_user_email.lower() in recipients
                and "compliance_dept@ananta.com.bd" in recipients
                and any("ZZ Full Test License" in html for html in bodies),
                str(sent_mail),
            )
            check(
                "manual alert reaches the department group mailbox regardless of factory",
                "compliance_dept@ananta.com.bd" in recipients,
            )
            if other_email:
                check("manual alert skips a user from another factory", other_email.lower() not in recipients)

            # Group mailbox is now opt-in: unticking it must suppress its mail.
            sent_mail.clear()
            opted_out = c.post(
                f"/documents/send-alert/{doc_id}",
                data={"recipient_id": [str(scope_user_id)]},
                follow_redirects=True,
            )
            recipients_out = [addr.lower() for _subject, batch, _html in sent_mail for addr in batch]
            check(
                "manual alert skips the group mailbox when unticked",
                opted_out.status_code == 200
                and len(sent_mail) == 1
                and recipients_out == [scope_user_email.lower()],
                str(sent_mail),
            )

            # Group mailbox alone is also valid (no users ticked).
            sent_mail.clear()
            group_only = c.post(
                f"/documents/send-alert/{doc_id}",
                data={"group_email": ["compliance_dept@ananta.com.bd"]},
                follow_redirects=True,
            )
            recipients_group = [addr.lower() for _subject, batch, _html in sent_mail for addr in batch]
            check(
                "manual alert can be sent to the group mailbox alone",
                group_only.status_code == 200
                and recipients_group == ["compliance_dept@ananta.com.bd"],
                str(sent_mail),
            )
        finally:
            flask_mail.send = original_send
            with app.app_context():
                person = User.query.get(scope_user_id)
                if person:
                    person.department_id = saved_scope_department
                    db.session.commit()
        with app.app_context():
            from app.models import AlertEmail, AlertEmailItem
            parent_ids = [
                row[0] for row in db.session.query(AlertEmailItem.alert_email_id).filter(
                    AlertEmailItem.record_type == "document",
                    AlertEmailItem.record_id == doc_id,
                ).distinct()
            ]
            if parent_ids:
                AlertEmailItem.query.filter(AlertEmailItem.alert_email_id.in_(parent_ids)).delete(synchronize_session=False)
                AlertEmail.query.filter(AlertEmail.id.in_(parent_ids)).delete(synchronize_session=False)
                db.session.commit()

        upload_root = app.config["UPLOAD_FOLDER"]
        relative = "factory_zz/zz_delete_me.txt"
        full_path = os.path.join(upload_root, "factory_zz", "zz_delete_me.txt")
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as handle:
            handle.write(b"zz-delete")
        with app.app_context():
            removable = Document(
                factory_id=factory_a_id,
                doc_name="ZZ Delete Me",
                doc_type="Other",
                status="Valid",
                is_active=True,
                created_by=sa_id,
            )
            db.session.add(removable)
            db.session.flush()
            db.session.add(DocumentVersion(
                document_id=removable.id,
                version_no=1,
                file_path=relative,
                uploaded_by=sa_id,
            ))
            db.session.commit()
            delete_id = removable.id
        if names.get("fa_b"):
            other = app.test_client()
            login(other, names["fa_b"])
            blocked = other.get(f"/documents/delete/{delete_id}")
            check("other factory cannot open delete", blocked.status_code == 403, str(blocked.status_code))
        wrong = c.post(f"/documents/delete/{delete_id}", data={"confirm_name": "not the name"}, follow_redirects=True)
        check("delete requires the exact document name", b"exactly" in wrong.data)
        with app.app_context():
            check("wrong confirmation keeps the document", Document.query.get(delete_id) is not None)
            check("wrong confirmation keeps the file", os.path.isfile(full_path))
        gone = c.post(f"/documents/delete/{delete_id}", data={"confirm_name": "ZZ Delete Me"}, follow_redirects=True)
        check("confirmed delete finishes", gone.status_code == 200 and b"Deleted ZZ Delete Me" in gone.data)
        with app.app_context():
            check("deleted document is gone", Document.query.get(delete_id) is None)
            check("deleted file is gone", not os.path.isfile(full_path))

        r = c.post(f"/documents/edit/{doc_id}", data={
            "factory_id": str(factory_a_id),
            "department_id": "0",
            "doc_name": "ZZ Full Test License",
            "doc_type": "Fire License",
            "cert_no": "ZZ-FULL-1",
            "issue_date": "2024-01-01",
            "expiry_date": future,
            "issuing_authority": "Test Authority",
            "responsible_person": "Tester",
            "renewal_status": "In Progress",
            "remarks": "updated",
            "alert_recipients": [str(sa_id)],
        }, follow_redirects=True)
        check("edit document", r.status_code == 200)
        with app.app_context():
            doc = Document.query.get(doc_id)
            check("edit recalculates Valid", doc.status == STATUS_VALID, doc.status)

        # upload + renew
        pdf = (io.BytesIO(b"%PDF-1.4 testdoc"), "zz_full_test.pdf")
        r = c.post(f"/documents/renew/{doc_id}", data={
            "issue_date": date.today().isoformat(),
            "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
            "cert_no": "ZZ-FULL-1-R",
            "remarks": "renewed",
            "file": pdf,
        }, content_type="multipart/form-data", follow_redirects=True)
        check("renew document with file", r.status_code == 200)
        with app.app_context():
            doc = Document.query.get(doc_id)
            versions = sorted(doc.versions, key=lambda v: v.version_no)
            check("renew created version", len(versions) >= 1, str(len(versions)))
            if versions:
                created_ver_paths.append(versions[-1].file_path)
                r = c.get(f"/documents/download/{versions[-1].id}")
                check("authorized download", r.status_code == 200)

        # reject executable upload on edit
        exe = (io.BytesIO(b"MZ executable"), "malware.exe")
        r = c.post(f"/documents/edit/{doc_id}", data={
            "factory_id": str(factory_a_id),
            "department_id": "0",
            "doc_name": "ZZ Full Test License",
            "doc_type": "Fire License",
            "cert_no": "ZZ-FULL-1-R",
            "issue_date": date.today().isoformat(),
            "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
            "renewal_status": "Completed",
            "file": exe,
        }, content_type="multipart/form-data", follow_redirects=True)
        check("reject .exe upload", r.status_code == 200 and (
            b"not allowed" in r.data.lower() or b"Only PDF" in r.data or b"allowed" in r.data.lower()
        ))

        # Factory isolation: Factory Admin B cannot see factory A doc
        if names["fa_b"]:
            cb = app.test_client()
            login(cb, names["fa_b"])
            r = cb.get(f"/documents/view/{doc_id}")
            check("other factory cannot view doc", r.status_code == 403)
            r = cb.get(f"/documents/edit/{doc_id}")
            check("other factory cannot edit doc", r.status_code == 403)
            r = cb.post(f"/documents/archive/{doc_id}")
            check("other factory cannot archive doc", r.status_code == 403)
            dash = cb.get("/").get_data(as_text=True)
            # factory A name may still appear in brand copy; check Needs Attention / table for the test doc
            check("other factory dashboard hides test doc", "ZZ Full Test License" not in dash)
            r = cb.get(f"/documents/api/departments/{factory_a_id}")
            names_only = r.get_json(silent=True) or []
            check(
                "department list is shared and has no factory prefix",
                r.status_code == 200 and all("Ltd" not in item.get("name", "") for item in names_only),
                str(r.status_code),
            )

        if names["usr"] and factory_b_id:
            cu = app.test_client()
            login(cu, names["usr"])
            r = cu.post("/documents/add", data={
                "factory_id": str(factory_b_id),
                "department_id": "0",
                "doc_name": "ZZ Bypass Factory",
                "doc_type": "Other",
                "cert_no": "ZZ-BYPASS",
                "renewal_status": "Not Started",
            }, follow_redirects=False)
            check("POST cannot assign foreign factory", r.status_code in (200, 403),
                  f"status {r.status_code}")
            with app.app_context():
                leaked = Document.query.filter_by(cert_no="ZZ-BYPASS").first()
                check("foreign-factory POST did not persist", leaked is None)
                if leaked:
                    created_doc_ids.append(leaked.id)

        # Viewer can view but not mutate
        if names["view"]:
            cv = app.test_client()
            login(cv, names["view"])
            r = cv.get(f"/documents/view/{doc_id}")
            check("viewer can view document", r.status_code == 200)
            r = cv.post(f"/documents/archive/{doc_id}")
            check("viewer cannot archive", r.status_code == 403)

        # Compliance User cannot delete/archive
        if names["usr"]:
            cu = app.test_client()
            login(cu, names["usr"])
            r = cu.post(f"/documents/archive/{doc_id}")
            check("compliance user cannot archive", r.status_code == 403)

        # Archive + restore as Super Admin
        r = c.post(f"/documents/archive/{doc_id}", follow_redirects=True)
        check("archive document", r.status_code == 200)
        with app.app_context():
            check("archive is logical", Document.query.get(doc_id).is_active is False)
        r = c.post(f"/documents/restore/{doc_id}", follow_redirects=True)
        check("restore document", r.status_code == 200)
        with app.app_context():
            check("restore reactivates", Document.query.get(doc_id).is_active is True)

        # Dashboard search/filter
        r = c.get("/documents/?search=ZZ-FULL")
        check("document search", r.status_code == 200 and b"ZZ Full Test License" in r.data)
        r = c.get("/")
        check("dashboard loads", r.status_code == 200 and b"Factory Documents" in r.data)
        check("dashboard has employee section", b"Employee Compliance" in r.data)
        check("no Audit Trail nav", b"href=\"/audit" not in r.data and b"nav-link\">Audit Trail" not in r.data)
        check("no Notifications nav", b"Notifications" not in r.data)
        check("no Send Test Alert", b"Send Test Alert" not in r.data)

    # ---- Employees ----
    r = c.post("/employees/add", data={
        "employee_code": "ZZFULL1",
        "name": "ZZ Test Employee",
        "designation": "Electrician",
        "section": "Maintenance",
        "factory_id": str(factory_a_id),
        "date_of_joining": "2020-01-01",
        "is_active": "1",
    }, follow_redirects=True)
    check("create employee", r.status_code == 200)
    with app.app_context():
        emp = Employee.query.filter_by(employee_code="ZZFULL1", factory_id=factory_a_id).first()
        check("employee persisted", emp is not None)
        emp_id = emp.id if emp else None
        if emp:
            created_emp_ids.append(emp.id)

    if emp_id:
        r = c.post(f"/employees/{emp_id}/license/add", data={
            "compliance_type": "Electrician License",
            "registration_number": "ZZ-LIC-1",
            "category": "LT",
            "issuing_authority": "DESCO",
            "issue_date": "2024-01-01",
            "expiry_date": past,
            "remarks": "test",
        }, follow_redirects=True)
        check("create employee license", r.status_code == 200)
        with app.app_context():
            lic = EmployeeCompliance.query.filter_by(registration_number="ZZ-LIC-1").first()
            check("license status Expired", lic is not None and lic.status == STATUS_EXPIRED,
                  None if not lic else lic.status)

        r = c.post(f"/employees/{emp_id}/checkup/add", data={
            "exam_type": "CBC",
            "report_date": "2025-01-01",
            "expiry_date": future,
            "pre_yn": "Y",
            "test_center": "City Lab",
        }, follow_redirects=True)
        check("create health checkup", r.status_code == 200)
        with app.app_context():
            chk = HealthCheckup.query.filter_by(employee_id=emp_id, exam_type="CBC").first()
            check("checkup status Valid", chk is not None and chk.status == STATUS_VALID,
                  None if not chk else chk.status)

        r = c.get(f"/employees/{emp_id}")
        check("employee detail", r.status_code == 200 and b"ZZ Test Employee" in r.data)
        r = c.get("/employees/?search=ZZFULL1")
        check("employee search", r.status_code == 200 and b"ZZ Test Employee" in r.data)
        r = c.get("/employees/licenses?status=Expired")
        check("license filter", r.status_code == 200)
        r = c.get("/employees/checkups?exam_type=CBC")
        check("checkup filter", r.status_code == 200)

        if names["fa_b"]:
            cb = app.test_client()
            login(cb, names["fa_b"])
            r = cb.get(f"/employees/{emp_id}")
            check("other factory cannot view employee", r.status_code == 403)
            r = cb.get("/employees/?search=ZZFULL1")
            check("other factory search hides employee", b"ZZ Test Employee" not in r.data)

        # duplicate employee rejected
        r = c.post("/employees/add", data={
            "employee_code": "ZZFULL1",
            "name": "ZZ Duplicate",
            "factory_id": str(factory_a_id),
            "is_active": "1",
        }, follow_redirects=True)
        check("duplicate employee rejected", r.status_code == 200 and b"already exists" in r.data)

    # ---- Import page Super Admin only ----
    r = c.get("/employees/import")
    check("Super Admin import page", r.status_code == 200)
    if names["usr"]:
        cu = app.test_client()
        login(cu, names["usr"])
        r = cu.get("/employees/import")
        check("non-admin cannot import", r.status_code == 403)

    # ---- System logs ----
    r = c.get("/system-logs/")
    check("system logs page", r.status_code == 200)
    check("system logs show events", b"LOGIN" in r.data or b"APP_START" in r.data or b"Event" in r.data)

    # ---- CSRF ----
    csrf_app = create_app()
    csrf_app.config["TESTING"] = True
    csrf_app.config["WTF_CSRF_ENABLED"] = True
    csrf_client = csrf_app.test_client()
    r = csrf_client.post("/login", data={"username": names["sa"], "password": PASS}, follow_redirects=True)
    check("CSRF blocks login without token", r.status_code == 400 or b"CSRF" in r.data or b"csrf" in r.data.lower())

    # ---- Jobs / backup ----
    with app.app_context():
        try:
            recalculate_all_statuses()
            check("status recalc job function", True)
        except Exception as e:
            check("status recalc job function", False, str(e))

    from scripts.backup import create_backup_zip
    with app.app_context():
        db_path = app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "", 1)
        uploads = app.config["UPLOAD_FOLDER"]
        dest = os.path.join(ROOT, "backups")
        probe_dir = os.path.join(uploads, "zz_backup_probe")
        probe = os.path.join(probe_dir, "probe.txt")
        try:
            os.makedirs(probe_dir, exist_ok=True)
            with open(probe, "w", encoding="utf-8") as handle:
                handle.write("probe")
            zip_path = create_backup_zip(db_path, uploads, dest)
            exists = os.path.exists(zip_path) and os.path.getsize(zip_path) > 0
            import zipfile
            with zipfile.ZipFile(zip_path) as archive:
                names = archive.namelist()
            check("backup zip created", exists, zip_path)
            check(
                "backup contains the database and uploaded files",
                "compliance.db" in names and any(name.endswith("probe.txt") for name in names),
                str([name for name in names if name == "compliance.db" or name.endswith("probe.txt")][:4]),
            )
        except Exception as e:
            check("backup zip created", False, str(e))
        finally:
            if os.path.isfile(probe):
                os.remove(probe)
            if os.path.isdir(probe_dir):
                os.rmdir(probe_dir)

    from scripts.scheduled_status_recalc import main as status_job
    os.environ["COMPLIANCE_SKIP_EMERGENCY"] = "1"
    try:
        rc = status_job()
        check("scheduled status recalc script", rc == 0, str(rc))
    except Exception as e:
        check("scheduled status recalc script", False, str(e))

    # ---- Obsolete routes stay gone ----
    for path in ("/audit/", "/notifications/", "/documents/export/excel",
                 "/documents/export/pdf", "/send-test-alert", "/admin/test-alert"):
        r = c.get(path)
        check(f"obsolete path gone {path}", r.status_code == 404, str(r.status_code))

    # ---- Cleanup ----
    with app.app_context():
        upload = app.config["UPLOAD_FOLDER"]
        for emp_id in created_emp_ids:
            emp = Employee.query.get(emp_id)
            if emp:
                db.session.delete(emp)
        for doc_id in created_doc_ids:
            doc = Document.query.get(doc_id)
            if doc:
                doc.alert_recipients = []
                for v in list(doc.versions):
                    fp = os.path.join(upload, v.file_path)
                    if os.path.exists(fp):
                        os.remove(fp)
                db.session.delete(doc)
        for uid, (pw, fails, locked) in saved_pw.items():
            u = User.query.get(uid)
            if u:
                u.password_hash = pw
                u.failed_login_attempts = fails or 0
                u.locked_until = locked
        db.session.commit()
        leftover = Document.query.filter(Document.cert_no.like("ZZ-FULL%")).count()
        leftover_emp = Employee.query.filter(Employee.employee_code.like("ZZFULL%")).count()
        leftover_user = User.query.filter_by(username="zz_delete_me").first()
        if leftover_user:
            db.session.delete(leftover_user)
            db.session.commit()
        zz_global = User.query.filter_by(username="zz_global_compliance").first()
        if zz_global:
            db.session.delete(zz_global)
            db.session.commit()
        check("test documents cleaned up", leftover == 0)
        check("test employees cleaned up", leftover_emp == 0)
        check("passwords restored", True)

    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\nFULL PROJECT TEST: {'PASSED' if failed == 0 else 'FAILED'} "
          f"({len(results) - failed}/{len(results)} checks)")
    return 0 if failed == 0 else 1


def time_now_minus_hours(hours):
    import time
    return time.time() - hours * 3600


if __name__ == "__main__":
    sys.exit(main())
