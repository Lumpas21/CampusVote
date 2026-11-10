import json

from flask import abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required, login_user

from app.admin import admin_bp
from app.auth.forms import LoginForm
from app.auth.services import record_audit, resolve_face_photo, safe_next
from app.election_utils import election_phase
from app.extensions import db
from app.models import AuditLog, Candidate, Election, FaceData, User, VoteReceipt, utc_now


def require_admin():
    if current_user.role != "admin":
        abort(403)


@admin_bp.route("/face-reviews")
@login_required
def face_reviews():
    require_admin()
    submissions = (
        db.session.query(FaceData, User)
        .join(User, FaceData.user_id == User.id)
        .filter(FaceData.status == "pending")
        .order_by(FaceData.created_at.asc())
        .all()
    )
    return render_template("admin/face_reviews.html", submissions=submissions)


@admin_bp.route("/face-reviews/<int:user_id>/photo")
@login_required
def face_photo(user_id):
    require_admin()
    face_data = FaceData.query.filter_by(user_id=user_id).first_or_404()
    if face_data.status != "pending" or not face_data.reference_path:
        abort(404)
    path = resolve_face_photo(face_data.reference_path)
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/jpeg", conditional=True)


def review_submission(user_id, status):
    require_admin()
    face_data = FaceData.query.filter_by(user_id=user_id).with_for_update().first_or_404()
    if face_data.status != "pending" or not face_data.reference_path:
        abort(409)

    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    rejected_photo = face_data.reference_path if status == "rejected" else None
    face_data.status = status
    if status == "rejected":
        face_data.reference_path = None
    face_data.reviewed_by = current_user.id
    face_data.reviewed_at = utc_now()
    user.face_status = status
    record_audit(
        "face_reference_reviewed",
        actor_id=current_user.id,
        entity_type="user",
        entity_id=user.id,
        details=json.dumps({"status": status}),
    )
    db.session.commit()
    if rejected_photo:
        try:
            resolve_face_photo(rejected_photo).unlink(missing_ok=True)
        except OSError:
            current_app.logger.exception("Unable to remove a rejected face photo.")


@admin_bp.route("/face-reviews/<int:user_id>/approve", methods=["POST"])
@login_required
def approve_face(user_id):
    review_submission(user_id, "approved")
    flash("Face photo approved.", "success")
    return redirect(url_for("admin.face_reviews"))


@admin_bp.route("/face-reviews/<int:user_id>/reject", methods=["POST"])
@login_required
def reject_face(user_id):
    review_submission(user_id, "rejected")
    flash("Face photo rejected. The student can submit a new photo.", "warning")
    return redirect(url_for("admin.face_reviews"))


# ------------------------------------------------------------------ guard
@admin_bp.before_request
def guard():
    """Every /admin page needs an admin account. Only the login page is open."""
    if request.endpoint == "admin.login":
        return None
    if not current_user.is_authenticated:
        return redirect(url_for("admin.login", next=request.full_path.rstrip("?")))
    if current_user.role != "admin":
        record_audit("access_denied", actor_id=current_user.id,
                     entity_type="user", entity_id=current_user.id, details=request.path)
        db.session.commit()
        abort(403)
    return None


@admin_bp.context_processor
def admin_context():
    """Numbers shown in the sidebar."""
    try:
        pending = Candidate.query.filter_by(status="pending").count()
    except Exception:  # never let the sidebar break a page
        pending = 0
    return {"pending_applications": pending}


# ------------------------------------------------------------------ login
@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated and current_user.role == "admin":
        return redirect(url_for("admin.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        valid = user is not None and user.role == "admin" and user.check_password(form.password.data)
        if not valid:
            record_audit("login_failed", actor_id=user.id if user and user.role == "admin" else None,
                         details="admin portal")
            db.session.commit()
            flash("Invalid email or password.", "danger")
        elif user.is_locked:
            flash("This admin account is deactivated. Ask a super admin to reactivate it.", "danger")
        else:
            login_user(user)
            record_audit("login", actor_id=user.id, entity_type="user", entity_id=user.id, details="admin")
            db.session.commit()
            return redirect(safe_next(request.args.get("next")) or url_for("admin.dashboard"))
    return render_template("admin/login.html", form=form)


# -------------------------------------------------------------- dashboard
@admin_bp.route("/")
def dashboard():
    students = User.query.filter_by(role="student")
    stats = {
        "students": students.count(),
        "enrolled": students.filter(User.face_status.in_(("enrolled", "approved"))).count(),
        "locked": students.filter(User.is_locked.is_(True)).count(),
        "pending_apps": Candidate.query.filter_by(status="pending").count(),
        "voters": db.session.query(db.func.count(db.func.distinct(VoteReceipt.voter_id))).scalar() or 0,
    }
    now = utc_now()
    live = []
    for e in Election.query.filter(Election.status != "draft").order_by(Election.voting_start.asc()).all():
        phase = election_phase(e, now)
        if phase != "closed":
            live.append((e, phase))
    drafts = Election.query.filter_by(status="draft").count()
    recent = (
        db.session.query(AuditLog, User)
        .outerjoin(User, AuditLog.actor_id == User.id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(6).all()
    )
    return render_template("admin/dashboard.html", stats=stats, live=live[:6],
                           drafts=drafts, recent=recent)
