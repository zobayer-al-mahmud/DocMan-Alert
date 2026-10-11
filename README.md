# Ananta Companies — DocMan Alert (v1.0)

A simple, secure Flask application for tracking compliance **documents** and
**employee compliance** (licenses and health checkups) across factories. Built
for internal office-LAN use: a single Flask monolith on SQLite (WAL) served by
Waitress, with Windows Task Scheduler for background jobs.

> Design principle: **simple, secure, fast enough, easy to maintain, easy to use.**
> There is intentionally no audit trail, no notification center, no PDF/Excel
> reporting. Government offices and services are maintained in the Regulatory Directory.

---

## 1. What it does

- Secure login with role-based access control (enforced server-side).
- Factory-scoped data isolation.
- Compliance document management with file upload/download.
- Document **renewal** with full **version history** (previous files preserved).
- Automatic expiry **status** calculation (one centralized engine).
- Employee compliance: employee master data, employee **licenses**, and
  **health checkups** (one row per exam).
- Simple dashboard + "needs attention" view (factory-scoped).
- Weekly compliance alert email (90/60/30 days) and a daily emergency alert (7 days and expired).
- Excel **import** for migration/onboarding (Super Admin only).
- Daily backup + controlled restore.
- **System Logs** (technical/operational events) visible to **Super Admin only**.

## 2. Architecture

```
Browser --HTTPS--> Internal Reverse Proxy --> Waitress --> Flask
                                                          |
                 +----------------------------------------+
                 | SQLite (WAL) | Uploaded files | System Logs | SMTP |
                 +----------------------------------------+
Windows Task Scheduler:
  - Daily 09:00 BST  -> status recalculation and emergency alerts (scripts/scheduled_status_recalc.py)
  - Sat   09:00 BST  -> weekly email alert    (scripts/scheduled_email_alert.py)
  - Daily 18:30 BST  -> backup                (scripts/backup.py)
  - At system startup -> serve app            (serve.py via scripts/run_server.bat)
```

All date/time comparisons and schedules use **Bangladesh Standard Time (UTC+6)**.

## 3. Roles

| Role | Factory scope | Capabilities |
|------|---------------|--------------|
| Super Admin | All | Everything incl. companies/factories/users, employee data, import, backup/restore, **System Logs** |
| Administration | All | Documents + employee compliance (no System Logs) |
| Factory Admin | Own factory | Departments, users, documents, employee compliance in own factory |
| Compliance Manager | Own factory | Add/edit/delete documents + employee compliance in own factory |
| Compliance User | Own factory | Add/edit documents + employee compliance in own factory |
| Compliance Viewer | All | Read-only (view/download) |

## 4. Project layout

```
compliance-tracker/
├── app/
│   ├── __init__.py         app factory, blueprints, error handling, startup log
│   ├── models.py           SQLAlchemy models
│   ├── forms.py            WTForms
│   ├── utils.py            status engine, system logging, email alert, auth helpers
│   ├── import_utils.py     Excel import parsing/apply
│   ├── routes/             auth, main (dashboard), documents, employees, users, admin, logs
│   ├── templates/
│   └── static/             css, img, vendor (self-hosted Bootstrap)
├── scripts/
│   ├── scheduled_status_recalc.py
│   ├── scheduled_email_alert.py
│   ├── backup.py / restore.py
│   ├── migrate_to_v1.py    one-time migration to the v1.0 schema
│   └── run_*.bat / setup_task_scheduler.bat
├── tests/                  lightweight standalone tests + run_all.py
├── instance/compliance.db
├── uploads/  backups/
├── config.py  serve.py  run.py  requirements.txt  .env.example
```

## 5. Configuration (environment variables)

Secrets and infrastructure values are read from the environment — never
hard-coded. See [.env.example](.env.example). On Windows set them with `setx`
(then open a new terminal / restart the service):

| Variable | Required | Purpose |
|----------|----------|---------|
| `SECRET_KEY` | **Yes** | Flask session signing (no default; app refuses to start without it) |
| `SMTP_HOST` / `SMTP_PORT` | No | SMTP relay (defaults to LAN relay) |
| `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_SENDER` | No | SMTP credentials and From address (password is a secret; no hard-coded password) |
| `UPLOAD_FOLDER` | No | Where uploaded files are stored |
| `BACKUP_FOLDER` / `BACKUP_MIRROR_FOLDER` | No | Primary + optional mirror backup target |
| `BACKUP_RETENTION_DAYS` | No | Days of backups to keep (default 30) |
| `SESSION_COOKIE_SECURE` | For HTTPS | Set to `1` once served over HTTPS |

## 6. Running

Development:
```
set SECRET_KEY=...        &  venv\Scripts\python.exe run.py
```
Production (Waitress, port 5001):
```
venv\Scripts\python.exe serve.py
```
Put the app behind an internal reverse proxy terminating **HTTPS**, and set
`SESSION_COOKIE_SECURE=1`. Register `scripts\setup_task_scheduler.bat`
(as Administrator) to create all scheduled tasks; it auto-detects the install path.

## 7. Backup & restore

- `scripts/backup.py` — consistent DB snapshot (SQLite backup API, WAL-safe) +
  uploaded files into a timestamped zip; optional mirror copy; retention cleanup;
  logs `BACKUP_SUCCESS` / `BACKUP_FAILURE`.
- `scripts/restore.py` — interactive, requires `YES` confirmation, copies current
  data to a safety folder first, restores, **validates** the DB, logs
  `RESTORE_SUCCESS` / `RESTORE_FAILURE`. See [RESTORE_GUIDE.txt](RESTORE_GUIDE.txt).

> Backup success does not prove recoverability — periodically run a restore
> against a scratch copy to verify.

## 8. Excel import (Super Admin)

Employee Compliance → **Import** → choose a sheet type or a full workbook → upload `.xlsx` → **preview** → confirm.
Sheets can be Employees, Employee Licenses, Medical Licenses, Health Checkups, and Alert Recipients.
Handles Excel serial dates, Bangla numerals, and "Till Now" (stored as no expiry).
Companies, factories, departments, and users must already exist. Duplicate recipient links are skipped.

## 9. Local OCR (optional)

OCR reads PDF, JPG, and PNG files on the server. It does not send documents to a cloud service, and it never changes the original file or saved dates/numbers until a user confirms the suggestion.

Install Tesseract on the Windows server, including English and Bangla:

1. Install Tesseract OCR.
2. Confirm Bangla data is present: `tesseract --list-langs` should list `eng` and `ben`.
3. If `tesseract.exe` is not on PATH, set `TESSERACT_CMD` to its full path.
4. Run `venv\Scripts\python.exe scripts\migrate_ocr.py` once on an existing database.

If Tesseract is missing, the rest of the app still works and OCR shows: "OCR is currently unavailable because the OCR engine is not installed."

Text PDFs are read directly. Scanned PDFs and images use Tesseract `eng+ben`. Search matches both document fields and extracted text.

## 10. Tests

```
venv\Scripts\python.exe tests\run_all.py
```
Covers the status-engine boundaries, route smoke tests, factory isolation /
System-Logs RBAC, and Excel import (parse + idempotent apply).

## 10. Migration to v1.0

`scripts/migrate_to_v1.py` was used once to move an existing database to this
baseline: it backs up first, adds new columns (`no_expiry`, `is_active`,
per-version issue/expiry, persistent login-lockout fields), creates the new
tables (`employees`, `employee_compliance`, `health_checkups`, `system_logs`),
and **drops** the obsolete `audit_logs`, `notifications`, `service_categories`,
`regulatory_authorities`, and `services` tables plus the regulatory columns on
`documents`.
