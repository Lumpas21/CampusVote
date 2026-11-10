"""Admin: read-only activity log."""
from flask import render_template, request

from app.admin import admin_bp
from app.extensions import db
from app.models import AuditLog, User

PAGE_SIZE = 40
GROUPS = ("logins", "face", "approvals", "blocked")


def action_kind(action):
    """Colour of the action badge."""
    a = action or ""
    if a.endswith("unlocked"):
        return "ok"
    if any(word in a for word in ("failed", "denied", "locked", "rejected")):
        return "bad"
    if any(word in a for word in ("passed", "submitted", "enrolled", "verified", "unlocked")):
        return "ok"
    if a.startswith(("application_", "election_", "results_", "face_reference", "settings_", "admin_")):
        return "warn"
    return "plain"


@admin_bp.route("/audit")
def audit():
    group = request.args.get("group", "all")
    page = max(1, request.args.get("page", 1, type=int))
    query = db.session.query(AuditLog, User).outerjoin(User, AuditLog.actor_id == User.id)
    if group == "logins":
        query = query.filter(AuditLog.action.in_(("login", "login_failed", "password_reset", "password_changed")))
    elif group == "face":
        query = query.filter(AuditLog.action.like("face\\_%", escape="\\"))
    elif group == "approvals":
        query = query.filter(db.or_(AuditLog.action.like("application\\_%", escape="\\"),
                                    AuditLog.action == "face_reference_reviewed"))
    elif group == "blocked":
        query = query.filter(AuditLog.action.in_(("login_failed", "access_denied", "account_locked")))
    total = query.count()
    rows = (query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all())
    pages = max(1, -(-total // PAGE_SIZE))
    return render_template("admin/audit.html", rows=rows, group=group, page=page, pages=pages,
                           total=total, action_kind=action_kind)
