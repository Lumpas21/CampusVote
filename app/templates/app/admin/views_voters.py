"""Admin: students, face status and who has voted (never who they voted for)."""
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.admin import admin_bp
from app.auth.services import record_audit
from app.extensions import db
from app.models import FaceData, User, VoteReceipt

PAGE_SIZE = 20
ENROLLED = ("enrolled", "approved")
FILTERS = ("face_pending", "locked", "not_enrolled")


def face_state(user):
    """(label, css kind) for the Face column."""
    if user.is_locked:
        return "Locked", "bad"
    if user.face_status in ENROLLED:
        return "Enrolled", "ok"
    if user.face_status == "pending":
        return "Pending", "warn"
    return "Not enrolled", "mute"


@admin_bp.route("/voters")
def voters():
    flt = request.args.get("filter", "all")
    q = request.args.get("q", "").strip()
    page = max(1, request.args.get("page", 1, type=int))

    query = User.query.filter_by(role="student")
    if flt == "face_pending":
        query = query.filter(User.face_status == "pending")
    elif flt == "locked":
        query = query.filter(User.is_locked.is_(True))
    elif flt == "not_enrolled":
        query = query.filter(db.or_(User.face_status.is_(None),
                                    User.face_status.notin_(ENROLLED + ("pending",))))
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(User.full_name.ilike(like), User.email.ilike(like),
                                    User.student_id.ilike(like)))

    total = query.count()
    users = query.order_by(User.full_name).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    voted = {row[0] for row in db.session.query(VoteReceipt.voter_id)
             .filter(VoteReceipt.voter_id.in_([u.id for u in users] or [0])).distinct()}

    selected_id = request.args.get("id", type=int)
    selected = next((u for u in users if u.id == selected_id), None) or (users[0] if users else None)
    face_record = FaceData.query.filter_by(user_id=selected.id).first() if selected else None
    pages = max(1, -(-total // PAGE_SIZE))
    return render_template("admin/voters.html", users=users, voted=voted, selected=selected,
                           face_record=face_record, flt=flt, q=q, page=page, pages=pages,
                           total=total, face_state=face_state)


def _back():
    return redirect(url_for("admin.voters", filter=request.form.get("filter", "all"),
                            q=request.form.get("q", ""), id=request.form.get("id")))


@admin_bp.route("/voters/<int:user_id>/unlock", methods=["POST"])
def unlock_voter(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if user.role != "student":
        abort(404)
    user.is_locked = False
    user.face_attempts = 0
    record_audit("account_unlocked", actor_id=current_user.id, entity_type="user",
                 entity_id=user.id, details=user.full_name)
    db.session.commit()
    flash(f"{user.full_name} can log in again.", "success")
    return _back()


@admin_bp.route("/voters/<int:user_id>/reset-face", methods=["POST"])
def reset_voter_face(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if user.role != "student":
        abort(404)
    FaceData.query.filter_by(user_id=user.id).delete()
    user.face_status = "not_enrolled"
    user.face_attempts = 0
    record_audit("face_reset", actor_id=current_user.id, entity_type="user",
                 entity_id=user.id, details=user.full_name)
    db.session.commit()
    flash(f"{user.full_name} must set up face verification again.", "success")
    return _back()
