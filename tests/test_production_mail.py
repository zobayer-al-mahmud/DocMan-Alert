"""Production concern mailboxes and department group mail resolution.

The Super Admin Test Alert feature has been removed from the application;
this suite now only covers the production routing helpers that are shared by
weekly, emergency, and manual alerts.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SECRET_KEY", "prod-mail-test-key")

from app import create_app
from app.utils import department_group_emails, factory_concern_codes, production_alert_emails


class _Factory:
    def __init__(self, name):
        self.name = name


def main():
    app = create_app()
    app.config["TESTING"] = True
    results = []

    def check(name, passed, detail=""):
        results.append((name, bool(passed)))
        extra = f" :: {detail}" if detail and not passed else ""
        print(f"  [{'OK' if passed else 'FAIL'}] {name}{extra}")

    with app.app_context():
        agl = _Factory("Ananta Garments Ltd.")
        asl = _Factory("Ananta Sportswear Ltd.")
        ajl = _Factory("Ananta Jeanswear Ltd.")
        pwpl = _Factory("Paradise Washing Plant Ltd.")
        abm = _Factory("ABM Fashions Ltd.")
        ho = _Factory("Head Office")
        other = _Factory("Thread And Thread Industries Ltd.")

        check("AGL code", "AGL" in factory_concern_codes(agl))
        check("ABM code from leading token", "ABM" in factory_concern_codes(abm))
        check("PWPL code", "PWPL" in factory_concern_codes(pwpl))
        check("HO code", "HO" in factory_concern_codes(ho))

        class _Dept:
            def __init__(self, name, group_email):
                self.name = name
                self.group_email = group_email

        check(
            "compliance group mailbox",
            department_group_emails(_Dept("Compliance", "compliance_dept@ananta.com.bd"))
            == ["compliance_dept@ananta.com.bd"],
        )
        # Transport docs now receive BOTH the transport group mailbox AND the
        # always-on compliance watcher. Order is deterministic: the record's
        # own department email first, then always-on addresses.
        check(
            "transport group mailbox (plus the global compliance watcher)",
            department_group_emails(_Dept("Transport", "transport_agl@ananta.com.bd"))
            == ["transport_agl@ananta.com.bd", "compliance_dept@ananta.com.bd"],
        )
        check("factory mail no longer assigns the compliance group", "compliance_dept@ananta.com.bd" not in production_alert_emails(agl))
        check("factory mail no longer assigns the transport group", "transport_agl@ananta.com.bd" not in production_alert_emails(asl))
        check("AJL does not get a factory transport mailbox", "transport_agl@ananta.com.bd" not in production_alert_emails(ajl))
        ho_mail = production_alert_emails(ho)
        check("HO compliance mailbox", "arifkhan@ananta.com.bd" in ho_mail, str(ho_mail))
        check("HO administration mailbox", "badhon_admin@ananta.com.bd" in ho_mail)
        check("Ananta Head Office uses the HO mailboxes", "arifkhan@ananta.com.bd" in production_alert_emails(_Factory("Ananta Head Office")))
        check("unlisted factory has no production mailbox", production_alert_emails(other) == [])

    failed = [name for name, passed in results if not passed]
    print("PRODUCTION MAIL TEST:", "PASSED" if not failed else "FAILED")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
