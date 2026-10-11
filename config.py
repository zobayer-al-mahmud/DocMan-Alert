import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _require_env(name):
    """Return a required environment variable or refuse to start.

    Used only for genuine secrets that must never have a hard-coded default
    (SECRET_KEY). Everything else may fall back to a safe LAN default.
    """
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is not set. "
            f"Set it with: setx {name} \"your-value-here\" "
            f"then open a NEW terminal/service session and try again."
        )
    return value


def _env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name, default=None):
    """Comma/semicolon-separated list from the environment -> list[str]."""
    raw = os.environ.get(name)
    if not raw:
        return list(default or [])
    return [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]


class Config:
    # Genuine secret: no fallback on purpose. If SECRET_KEY isn't set the app
    # refuses to start rather than silently run with a weak, public default.
    SECRET_KEY = _require_env("SECRET_KEY")

    # --- Database -----------------------------------------------------------
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(BASE_DIR, "instance", "compliance.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- File storage -------------------------------------------------------
    # Keep configurable so uploads can live outside the app folder in prod.
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or os.path.join(BASE_DIR, "app", "uploads")
    ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "docx", "xlsx"}
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB max file size

    # --- Backups ------------------------------------------------------------
    # Primary backup target and optional second (mirror) target. Both
    # configurable; never hard-code the mirror path in business logic.
    BACKUP_FOLDER = os.environ.get("BACKUP_FOLDER") or os.path.join(BASE_DIR, "backups")
    BACKUP_MIRROR_FOLDER = os.environ.get("BACKUP_MIRROR_FOLDER") or None
    BACKUP_RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS") or 30)

    # --- Mail (SMTP) --------------------------------------------------------
    # Infra values default to the office LAN relay but can be overridden by
    # environment. MAIL_PASSWORD is a secret and has no hard-coded default.
    MAIL_SERVER = os.environ.get("SMTP_HOST") or "192.168.10.6"
    MAIL_PORT = int(os.environ.get("SMTP_PORT") or 25)
    MAIL_USE_TLS = _env_bool("SMTP_USE_TLS", default=False)
    MAIL_USE_SSL = _env_bool("SMTP_USE_SSL", default=False)
    MAIL_USERNAME = os.environ.get("SMTP_USERNAME") or "document_tracker@ananta.com.bd"
    MAIL_PASSWORD = os.environ.get("SMTP_PASSWORD") or os.environ.get("MAIL_PASSWORD")
    # From address for compliance alerts. SMTP login can stay a different mailbox.
    MAIL_DEFAULT_SENDER = os.environ.get("SMTP_SENDER") or "document_tracker@ananta.com.bd"

    # Factory concern mailboxes that are not a department group.
    # Head Office still uses the named administration addresses.
    # Compliance and Transport group mailboxes are stored on the department.
    PRODUCTION_ALERT_ROUTES = (
        (("HO",), "arifkhan@ananta.com.bd"),
        (("HO",), "badhon_admin@ananta.com.bd"),
    )
    DEPARTMENT_GROUP_EMAILS = {
        "compliance": "compliance_dept@ananta.com.bd",
        "transport": "transport_agl@ananta.com.bd",
    }

    # Group mailboxes that must be copied on EVERY document alert, regardless of
    # the document's factory or department. Department-specific group mailboxes
    # (e.g. Transport's `transport_agl@`) stay on the Department row and keep
    # firing only when the record belongs to that department. The compliance
    # office is a global watcher by policy, so it lives here instead.
    ALWAYS_INCLUDE_GROUP_EMAILS = _env_list(
        "ALWAYS_INCLUDE_GROUP_EMAILS",
        default=["compliance_dept@ananta.com.bd"],
    )

    # --- Session / cookie security -----------------------------------------
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Set SESSION_COOKIE_SECURE=1 once the app is served over HTTPS (behind the
    # internal reverse proxy). Defaults to off so first-run LAN HTTP still works.
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", default=False)

    # --- Local OCR (Tesseract). No cloud OCR. ------------------------------
    OCR_ENABLED = _env_bool("OCR_ENABLED", default=True)
    _default_tesseract = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    TESSERACT_CMD = os.environ.get("TESSERACT_CMD") or (
        _default_tesseract if os.path.exists(_default_tesseract) else ""
    )
    # This Tesseract build expects TESSDATA_PREFIX to be the folder that
    # contains eng.traineddata and ben.traineddata.
    _project_tessdata = os.path.join(BASE_DIR, "tessdata")
    TESSDATA_PREFIX = os.environ.get("TESSDATA_PREFIX") or (
        _project_tessdata if os.path.isdir(_project_tessdata) else ""
    )
    OCR_LANGUAGES = os.environ.get("OCR_LANGUAGES") or "eng+ben"
    OCR_MAX_FILE_MB = int(os.environ.get("OCR_MAX_FILE_MB") or 10)
    OCR_MAX_PDF_PAGES = int(os.environ.get("OCR_MAX_PDF_PAGES") or 50)
    OCR_TIMEOUT_SECONDS = int(os.environ.get("OCR_TIMEOUT_SECONDS") or 120)
