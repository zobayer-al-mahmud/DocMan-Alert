from flask import Blueprint, abort, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from app.models import AlertEmail, AlertEmailItem
from app.utils import to_bst

alerts_bp = Blueprint("alerts", __name__, url_prefix="/alert-history")

ALERT_TYPE_LABELS = {
    "weekly": "Weekly expiry",
    "emergency": "Urgent expiry",
    "manual": "Manual alert",
    "test": "Test",
}
ALERT_TYPES = ("weekly", "emergency", "manual", "test")
ALERT_STATUSES = ("sent", "failed")


def _sees_all_factories():
    return current_user.can_access_all_factories()


def _sees_all_alerts():
    """Only Super Admin may see alerts sent to other users."""
    return current_user.role.name == "Super Admin"


def _personal_recipient_filter():
    """Match alert emails addressed to the signed-in user."""
    conditions = [AlertEmail.user_id == current_user.id]
    if current_user.email:
        conditions.append(AlertEmail.recipient_email == current_user.email)
    return or_(*conditions)


def _history_query():
    query = AlertEmail.query.options(selectinload(AlertEmail.items))
    if not _sees_all_alerts():
        query = query.filter(AlertEmail.alert_type != "test")
        query = query.filter(_personal_recipient_filter())
        return query
    return query


def _visible_items(email):
    items = list(email.items or [])
    if _sees_all_alerts():
        return items
    if _sees_all_factories():
        return items
    factory_id = current_user.factory_id
    return [item for item in items if item.factory_id == factory_id]


@alerts_bp.route("/")
@login_required
def list_history():
    alert_type = request.args.get("alert_type", "").strip()
    status = request.args.get("status", "").strip()
    q = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)

    allowed_types = ALERT_TYPES if current_user.role.name == "Super Admin" else ("weekly", "emergency", "manual")
    if alert_type not in allowed_types:
        alert_type = ""
    if status not in ALERT_STATUSES:
        status = ""

    query = _history_query().order_by(AlertEmail.sent_at.desc(), AlertEmail.id.desc())
    if alert_type:
        query = query.filter(AlertEmail.alert_type == alert_type)
    if status:
        query = query.filter(AlertEmail.status == status)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            AlertEmail.recipient_email.ilike(like),
            AlertEmail.recipient_name.ilike(like),
            AlertEmail.subject.ilike(like),
            AlertEmail.items.any(AlertEmailItem.title.ilike(like)),
        ))

    pagination = query.paginate(page=page, per_page=30, error_out=False)
    for email in pagination.items:
        email.visible_items = _visible_items(email)

    return render_template(
        "alerts/history.html",
        emails=pagination.items,
        pagination=pagination,
        to_bst=to_bst,
        type_labels=ALERT_TYPE_LABELS,
        alert_types=allowed_types,
        statuses=ALERT_STATUSES,
        selected_type=alert_type,
        selected_status=status,
        q=q,
    )


@alerts_bp.route("/<int:email_id>")
@login_required
def view_history(email_id):
    email = _history_query().filter(AlertEmail.id == email_id).first()
    if email is None:
        abort(404)
    items = _visible_items(email)
    if email.alert_type != "test" and not items and not _sees_all_factories():
        abort(404)
    return render_template(
        "alerts/history_detail.html",
        email=email,
        items=items,
        to_bst=to_bst,
        type_labels=ALERT_TYPE_LABELS,
    )
