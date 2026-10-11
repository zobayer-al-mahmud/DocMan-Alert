import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "smoke-test-key")

from app import create_app, db
from app.models import User, Role, Document, Factory

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False

with app.app_context():
    doc = Document.query.first()
    doc_id = doc.id
    doc_factory = doc.factory_id
    # Find a factory-scoped user (Compliance User/Manager/Factory Admin) in a DIFFERENT factory
    other = (User.query.join(Role).filter(
        Role.name.in_(["Compliance User", "Compliance Manager", "Factory Admin"]),
        User.factory_id.isnot(None),
        User.factory_id != doc_factory,
        User.is_active.is_(True),
    ).first())
    sa = User.query.join(Role).filter(Role.name == "Super Admin").first()
    saved = {}
    for u in filter(None, [other, sa]):
        saved[u.id] = u.password_hash
        u.set_password("smoke-temp-pass-123")
    db.session.commit()
    other_name = other.username if other else None
    other_factory = other.factory_id if other else None
    sa_name = sa.username if sa else None

print(f"doc_id={doc_id} in factory {doc_factory}; cross-factory user={other_name} (factory {other_factory})")

def login(c, username):
    return c.post("/login", data={"username": username, "password": "smoke-temp-pass-123"}, follow_redirects=True)

results = []
# Super Admin can view the document
c = app.test_client()
login(c, sa_name)
r = c.get(f"/documents/view/{doc_id}")
results.append(("SuperAdmin view doc", r.status_code, "expect 200"))
r = c.get(f"/documents/edit/{doc_id}")
results.append(("SuperAdmin edit doc", r.status_code, "expect 200"))
r = c.get(f"/documents/renew/{doc_id}")
results.append(("SuperAdmin renew doc", r.status_code, "expect 200"))

# Cross-factory user must be BLOCKED (403) on another factory's doc
if other_name:
    c2 = app.test_client()
    login(c2, other_name)
    r = c2.get(f"/documents/view/{doc_id}")
    results.append(("CrossFactory view doc (isolation)", r.status_code, "expect 403"))
    r = c2.get(f"/documents/edit/{doc_id}")
    results.append(("CrossFactory edit doc (isolation)", r.status_code, "expect 403"))
    # system logs must be blocked for non-super-admin
    r = c2.get("/system-logs/")
    results.append(("CrossFactory system-logs (RBAC)", r.status_code, "expect 403"))

print("\n=== RESULTS ===")
ok = True
for name, code, expect in results:
    exp_code = int(expect.split()[-1])
    flag = "OK" if code == exp_code else "FAIL"
    if flag == "FAIL": ok = False
    print(f"  [{flag}] {name} -> {code} ({expect})")

with app.app_context():
    for uid, h in saved.items():
        u = User.query.get(uid); u.password_hash = h
    db.session.commit()

print("\nISOLATION/RBAC TEST:", "PASSED" if ok else "FAILED")
sys.exit(0 if ok else 1)
