"""
Daily document/employee-compliance status recalculation for Windows Task Scheduler.

Recomputes status for factory documents, employee licenses, and health
checkups using the centralized status engine, then sends emergency alerts for
records that are expired or inside 7 days. Statuses stay accurate even on
days nobody opens the app. Emergency emails are not sent again for the same
record, status, and expiry date.

Schedule: Daily 09:00 Bangladesh Standard Time (UTC+6).

Task Scheduler setup (adjust the install path to your deployment):
    Program/script:  <INSTALL_DIR>\\venv\\Scripts\\python.exe
    Arguments:       scripts\\scheduled_status_recalc.py
    Start in:        <INSTALL_DIR>

Exit code 0 = success, 1 = failure.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.utils import recalculate_all_statuses, send_emergency_alerts, log_event


def main():
    app = create_app()
    with app.app_context():
        log_event("JOB_START", "Status recalculation job started.", username_or_process="scheduler")
        try:
            recalculate_all_statuses()
            # Tests set this so a full-database run cannot email production
            # recipients or stamp emergency dedup rows.
            if os.environ.get("COMPLIANCE_SKIP_EMERGENCY") == "1":
                sent, error, count = False, None, 0
            else:
                sent, error, count = send_emergency_alerts()
        except Exception as e:
            log_event("JOB_FAILURE", f"Status recalculation failed: {type(e).__name__}",
                      level="ERROR", username_or_process="scheduler")
            raise
        if error and not sent:
            log_event("JOB_FAILURE", "Emergency alert failed after status recalculation.",
                      level="ERROR", username_or_process="scheduler")
            print("ERROR: Statuses were recalculated, but the emergency alert could not be sent.")
            return 1
        log_event(
            "JOB_SUCCESS",
            f"Status recalculation completed. Emergency alerts sent for {count} record threshold(s).",
            username_or_process="scheduler",
        )
    print("Status recalculation completed successfully.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"ERROR: Status recalculation failed: {type(e).__name__}")
        sys.exit(1)
