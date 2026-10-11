import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "smoke-test-key")

from app import create_app, db
from app.models import User, Role

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False
app.config["TESTING"] = True

print("=== URL MAP (app endpoints) ===")
for rule in sorted(app.url_map.iter_rules(), key=lambda r: str(r)):
    if rule.endpoint == "static":
        continue
    print(f"  {rule.endpoint:40} {','.join(sorted(rule.methods - {'HEAD','OPTIONS'})):12} {rule}")

# sanity: no obsolete endpoints
bad = [r.endpoint for r in app.url_map.iter_rules()
       if r.endpoint.split('.')[0] in ("audit", "notifications")]
print("\nObsolete endpoints present:", bad or "NONE")

with app.app_context():
    sa_role = Role.query.filter_by(name="Super Admin").first()
    user = User.query.filter_by(role_id=sa_role.id).first()
    username = user.username
    original_hash = user.password_hash
    user.set_password("smoke-temp-pass-123")
    db.session.commit()

client = app.test_client()
results = []
try:
    # unauthenticated dashboard -> redirect to login
    r = client.get("/", follow_redirects=False)
    results.append(("GET / (unauth)", r.status_code))

    r = client.get("/login")
    results.append(("GET /login", r.status_code))

    r = client.post("/login", data={"username": username, "password": "smoke-temp-pass-123"},
                    follow_redirects=True)
    results.append(("POST /login", r.status_code))

    for path in ["/", "/documents/", "/employees/", "/employees/licenses",
                 "/employees/checkups", "/employees/add", "/documents/add",
                 "/system-logs/", "/alert-history/", "/users/", "/admin/companies", "/admin/factories",
                 "/admin/departments", "/admin/backups", "/regulatory/categories",
                 "/regulatory/authorities", "/regulatory/services"]:
        r = client.get(path)
        results.append((f"GET {path}", r.status_code))

    # logout is POST-only now
    r = client.get("/logout")
    results.append(("GET /logout (should be 405)", r.status_code))
    r = client.post("/logout", follow_redirects=True)
    results.append(("POST /logout", r.status_code))
finally:
    with app.app_context():
        u = User.query.filter_by(username=username).first()
        u.password_hash = original_hash
        db.session.commit()

print("\n=== ROUTE RESULTS ===")
ok = True
for name, code in results:
    flag = "OK" if code in (200, 302, 405) else "FAIL"
    if flag == "FAIL":
        ok = False
    print(f"  [{flag}] {name} -> {code}")

print("\nSMOKE TEST:", "PASSED" if ok else "FAILED")
sys.exit(0 if ok else 1)
