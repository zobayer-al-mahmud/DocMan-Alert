"""Vehicle fleet management blueprint.

Layer 2 scope: Excel import flow (upload -> preview -> confirm).
Later layers add list, detail, add/edit, archive, and the transport dashboard.
"""
import os
import secrets

from flask import (Blueprint, render_template, redirect, url_for, flash,
                   current_app, request, abort)
from flask_login import login_required, current_user

from app import db
from app.models import Vehicle
from app.utils import roles_required, log_event

vehicles_bp = Blueprint("vehicles", __name__, url_prefix="/vehicles")

# Who may do what. Follows the pattern in app/routes/documents.py.
VIEW_ROLES = (
    "Super Admin", "Administration", "Factory Admin",
    "Compliance Manager", "Compliance User", "Compliance Viewer",
    "Transport Manager", "Transport User",
)
MANAGE_ROLES = (
    "Super Admin", "Administration", "Transport Manager", "Transport User",
)
IMPORT_ROLES = ("Super Admin", "Administration", "Transport Manager")


def _imports_dir():
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], "_vehicle_imports")
    os.makedirs(path, exist_ok=True)
    return path


@vehicles_bp.route("/import", methods=["GET", "POST"])
@login_required
@roles_required(*IMPORT_ROLES)
def import_vehicles():
    """Upload a vehicle workbook. Nothing is written until the next step."""
    from app.vehicle_import import parse_workbook, annotate_existing, preview_counts

    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Please choose an Excel (.xlsx) file.", "danger")
            return redirect(url_for("vehicles.import_vehicles"))
        if not file.filename.lower().endswith(".xlsx"):
            flash("Only .xlsx files are supported for import.", "danger")
            return redirect(url_for("vehicles.import_vehicles"))

        token = secrets.token_hex(16)
        temp_path = os.path.join(_imports_dir(), f"{token}.xlsx")
        file.save(temp_path)

        try:
            rows, errors = parse_workbook(temp_path)
            annotate_existing(rows)
            counts = preview_counts(rows)
        except Exception as exc:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            log_event(
                "XLSX_IMPORT_FAILED",
                f"Vehicle Excel preview failed: {type(exc).__name__}",
                level="ERROR",
            )
            flash("Could not read the Excel file. Use a valid .xlsx workbook "
                  "that matches the expected header row.", "danger")
            return redirect(url_for("vehicles.import_vehicles"))

        return render_template(
            "vehicles/import_preview.html",
            rows=rows,
            errors=errors,
            counts=counts,
            token=token,
            filename=file.filename,
            preview_rows=rows[:60],
        )

    return render_template("vehicles/import.html")


@vehicles_bp.route("/import/confirm", methods=["POST"])
@login_required
@roles_required(*IMPORT_ROLES)
def import_confirm():
    """Apply the previously-previewed workbook. The server re-parses the
    stored file, never trusting posted row data."""
    from app.vehicle_import import (
        parse_workbook, annotate_existing, preview_counts, apply_import,
        record_import_run,
    )

    token = request.form.get("token", "")
    if not token or not token.isalnum() or len(token) != 32:
        flash("Invalid import request.", "danger")
        return redirect(url_for("vehicles.import_vehicles"))

    temp_path = os.path.join(_imports_dir(), f"{token}.xlsx")
    if not os.path.exists(temp_path):
        flash("The uploaded file expired. Please upload again.", "warning")
        return redirect(url_for("vehicles.import_vehicles"))

    log_event("XLSX_IMPORT_STARTED", "Vehicle Excel import started.")
    filename = request.form.get("filename") or os.path.basename(temp_path)
    try:
        rows, errors = parse_workbook(temp_path)
        annotate_existing(rows)
        counts = preview_counts(rows)
        summary = apply_import(rows, user_id=current_user.id)
        record_import_run(filename, current_user.id, counts, summary, status="success")
    except Exception as exc:
        db.session.rollback()
        record_import_run(
            filename, current_user.id, {}, {},
            status="failed", error=f"{type(exc).__name__}: {exc}",
        )
        log_event(
            "XLSX_IMPORT_FAILED",
            f"Vehicle Excel import failed: {type(exc).__name__}",
            level="ERROR",
        )
        flash("Import failed. The file was not applied. Check the file and "
              "try again.", "danger")
        return redirect(url_for("vehicles.import_vehicles"))
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    log_event(
        "XLSX_IMPORT_COMPLETED",
        f"Vehicle Excel import: total {counts.get('total', 0)}, "
        f"new vehicles {summary['new_vehicles']}, "
        f"updated vehicles {summary['updated_vehicles']}, "
        f"new docs {summary['new_docs']}, "
        f"updated docs {summary['updated_docs']}, "
        f"skipped {summary['skipped']}.",
    )
    flash(
        f"Import summary — Rows: {counts.get('total', 0)}. "
        f"New vehicles: {summary['new_vehicles']}. "
        f"Updated vehicles: {summary['updated_vehicles']}. "
        f"New documents: {summary['new_docs']}. "
        f"Updated documents: {summary['updated_docs']}. "
        f"Skipped rows: {summary['skipped']}.",
        "success" if summary["skipped"] == 0 else "warning",
    )
    return redirect(url_for("vehicles.import_vehicles"))
