from flask import Blueprint, render_template, request
from flask_login import login_required

from app.models import SystemLog
from app.utils import roles_required, to_bst, VALID_SYSTEM_EVENTS

logs_bp = Blueprint("logs", __name__, url_prefix="/system-logs")


@logs_bp.route("/")
@login_required
@roles_required("Super Admin")
def list_logs():
    page = request.args.get("page", 1, type=int)
    level = request.args.get("level", "").strip()
    event_type = request.args.get("event_type", "").strip()

    query = SystemLog.query.order_by(SystemLog.timestamp.desc())
    if level:
        query = query.filter(SystemLog.level == level)
    if event_type:
        query = query.filter(SystemLog.event_type == event_type)

    pagination = query.paginate(page=page, per_page=30, error_out=False)

    return render_template(
        "logs/list.html",
        logs=pagination.items,
        pagination=pagination,
        to_bst=to_bst,
        selected_level=level,
        selected_event_type=event_type,
        event_types=sorted(VALID_SYSTEM_EVENTS),
        levels=["INFO", "WARNING", "ERROR"],
    )
