"""
Weekly compliance alert email job for Windows Task Scheduler.

Sends one Saturday email per record expiring in 90, 60, or 30 days.
Recipients are resolved per record: company, department, optional record
recipients, and the uploader. 7-day and expired records are sent by the
daily status job as emergency alerts.

Schedule: Weekly, Saturday 09:00 Bangladesh Standard Time (UTC+6).

Task Scheduler setup (adjust the install path to your deployment):
    Program/script:  <INSTALL_DIR>\\venv\\Scripts\\python.exe
    Arguments:       scripts\\scheduled_email_alert.py
    Start in:        <INSTALL_DIR>

Exit code 0 = success or nothing to alert about, 1 = failure.
Email success/failure is logged to System Logs (EMAIL_SUCCESS / EMAIL_FAILURE).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.utils import send_compliance_alert, log_event


def main():
    app = create_app()
    with app.app_context():
        log_event("JOB_START", "Weekly compliance alert job started.", username_or_process="scheduler")
        sent, error, count = send_compliance_alert()

        if count == 0:
            log_event("JOB_SUCCESS", "No records needed attention; no email sent.",
                      username_or_process="scheduler")
            print("No records need attention. No email sent.")
            return 0

        if sent:
            log_event("JOB_SUCCESS", f"Weekly alert sent for {count} record(s).",
                      username_or_process="scheduler")
            print(f"Alert email sent successfully for {count} record(s).")
            return 0

        log_event("JOB_FAILURE", f"Weekly alert failed ({count} record(s)): {error}",
                  level="ERROR", username_or_process="scheduler")
        print(f"ERROR: Alert email failed to send: {error}")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"ERROR: Scheduled email alert job crashed: {type(e).__name__}")
        sys.exit(1)
