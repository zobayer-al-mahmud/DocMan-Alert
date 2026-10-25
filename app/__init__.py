import os
from flask import Flask, request, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail
from flask_wtf import CSRFProtect
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

db = SQLAlchemy()
login_manager = LoginManager()
mail = Mail()
csrf = CSRFProtect()


def create_app():
    app = Flask(__name__, instance_relative_config=True)

    os.makedirs(app.instance_path, exist_ok=True)

    app.config.from_object("config.Config")

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    mail.init_app(app)
    csrf.init_app(app)

    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    @event.listens_for(Engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        # WAL lets readers keep working during a write; busy_timeout makes a
        # write that arrives during another write wait briefly and retry
        # instead of immediately raising "database is locked". WAL reduces
        # (but does not eliminate) write contention.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    from app import models

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(models.User, int(user_id))

    with app.app_context():
        db.create_all()
        from app.schema_upgrade import ensure_alert_schema
        ensure_alert_schema()

    # --- Blueprints ---------------------------------------------------------
    from app.routes.auth import auth_bp
    app.register_blueprint(auth_bp)

    from app.routes.main import main_bp
    app.register_blueprint(main_bp)

    from app.routes.admin import admin_bp
    app.register_blueprint(admin_bp)

    from app.routes.documents import documents_bp
    app.register_blueprint(documents_bp)

    from app.routes.employees import employees_bp
    app.register_blueprint(employees_bp)

    from app.routes.users import users_bp
    app.register_blueprint(users_bp)

    from app.routes.logs import logs_bp
    app.register_blueprint(logs_bp)

    from app.routes.alerts import alerts_bp
    app.register_blueprint(alerts_bp)

    from app.routes.regulatory import regulatory_bp
    app.register_blueprint(regulatory_bp)

    from app.routes.vehicles import vehicles_bp
    app.register_blueprint(vehicles_bp)

    @app.after_request
    def set_security_headers(response):
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "same-origin"
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.context_processor
    def inject_active_page():
        endpoint = request.endpoint or ""
        if endpoint == "main.my_alerts":
            active = "my_alerts"
        elif endpoint.startswith("main."):
            active = "dashboard"
        elif endpoint.startswith("documents."):
            active = "documents"
        elif endpoint.startswith("employees."):
            active = "employees"
        elif endpoint == "vehicles.dashboard":
            # Transport roles reach this via the Dashboard sidebar link; keep
            # that nav item highlighted instead of 'Vehicle Documents'.
            active = "dashboard"
        elif endpoint.startswith("vehicles."):
            active = "vehicles"
        elif endpoint.startswith("regulatory.") and "categor" in endpoint:
            active = "reg_categories"
        elif endpoint.startswith("regulatory.") and "author" in endpoint:
            active = "reg_authorities"
        elif endpoint.startswith("regulatory.") and "service" in endpoint:
            active = "reg_services"
        elif endpoint in ("admin.backups", "admin.download_backup"):
            active = "backups"
        elif endpoint in (
            "admin.list_factories", "admin.add_factory",
            "admin.edit_factory", "admin.delete_factory",
        ):
            active = "factories"
        elif endpoint in (
            "admin.list_departments", "admin.add_department",
            "admin.edit_department", "admin.delete_department",
            "admin.department_alert_recipients",
        ):
            active = "departments"
        elif endpoint == "admin.factory_department_recipients":
            active = "dept_recipients"
        elif endpoint.startswith("admin."):
            active = "administration"
        elif endpoint.startswith("users."):
            active = "users"
        elif endpoint.startswith("logs."):
            active = "logs"
        elif endpoint.startswith("alerts."):
            active = "alert_history"
        else:
            active = ""
        return dict(active_page=active)

    # --- Error handling -----------------------------------------------------
    @app.errorhandler(RequestEntityTooLarge)
    @app.errorhandler(413)
    def handle_file_too_large(e):
        max_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        allowed_types = ", ".join(sorted(app.config["ALLOWED_EXTENSIONS"])).upper()
        return render_template("error_413.html", max_mb=max_mb, allowed_types=allowed_types), 413

    @app.errorhandler(403)
    def handle_forbidden(e):
        return render_template("error_generic.html",
                               code=403, title="Access denied",
                               message="You don't have permission to view this page or it is outside your factory scope."), 403

    @app.errorhandler(404)
    def handle_not_found(e):
        return render_template("error_generic.html",
                               code=404, title="Not found",
                               message="The page or record you requested does not exist."), 404

    @app.errorhandler(Exception)
    def handle_unexpected(e):
        # Let real HTTP errors (403/404/413/...) use their own handlers.
        if isinstance(e, HTTPException):
            return e
        from app.utils import log_event
        try:
            db.session.rollback()
        except Exception:
            pass
        log_event("UNHANDLED_EXCEPTION", type(e).__name__, level="ERROR")
        return render_template("error_generic.html",
                               code=500, title="Something went wrong",
                               message="An unexpected error occurred. Please try again or contact ICT."), 500

    # --- Startup log --------------------------------------------------------
    with app.app_context():
        from app.routes.documents import purge_extract_staging
        from app.utils import log_event
        purge_extract_staging(app.instance_path)
        log_event("APP_START", "Application started.", username_or_process="system")

    return app
