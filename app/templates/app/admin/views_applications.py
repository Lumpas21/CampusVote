"""Admin: review officer applications."""
from pathlib import Path

from flask import abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user

from app.admin import admin_bp
from app.auth.services import record_audit
from app.extensions import db
from app.models import Candidate, Election, ElectionPosition, User

STATUSES = ("pending", "approved", "rejected")


@admin_bp.route("/applications")
def applications():
    status = request.args.get("status", "all")
    base = (
        db.session.query(Candidate, ElectionPosition, Election, User)
        .join(ElectionPosition, Candidate.election_position_id == ElectionPosition.id)
        .join(Election, ElectionPosition.election_id == Election.id)
        .join(User, Candidate.user_id == User.id)
        .filter(Candidate.status != "withdrawn")
    )
    counts = {s: 0 for s in STATUSES}
    for state, total in (
        db.session.query(Candidate.status, db.func.count(Candidate.id))
        .filter(Candidate.status != "withdrawn").group_by(Candidate.status).all()
    ):
        counts[state] = total
    query = base.filter(Candidate.status == status) if status in STATUSES else base
    rows = query.order_by(Candidate.submitted_at.desc()).all()

    selected_id = request.args.get("id", type=int)
    selected = next((r for r in rows if r[0].id == selected_id), None) or (rows[0] if rows else None)
    return render_template("admin/applications.html", rows=rows, selected=selected,
                           status=status, counts=counts, total=sum(counts.values()))


@admin_bp.route("/applications/<int:candidate_id>/review", methods=["POST"])
def review_application(candidate_id):
    candidate = db.session.get(Candidate, candidate_id) or abort(404)
    decision = request.form.get("decision")
    reason = request.form.get("reason", "").strip()
    back = url_for("admin.applications", status=request.form.get("status", "all"), id=candidate.id)

    if candidate.status != "pending":
        flash("This application was already reviewed.", "warning")
        return redirect(back)
    if decision not in ("approve", "reject"):
        abort(400)
    if decision == "reject" and not reason:
        flash("Write the reason so the student knows what to fix.", "danger")
        return redirect(back)

    student = db.session.get(User, candidate.user_id)
    position = db.session.get(ElectionPosition, candidate.election_position_id)
    if decision == "approve":
        candidate.status, candidate.reject_reason = "approved", None
    else:
        candidate.status, candidate.reject_reason = "rejected", reason[:1000]
    record_audit(f"application_{'approved' if decision == 'approve' else 'rejected'}",
                 actor_id=current_user.id, entity_type="candidate", entity_id=candidate.id,
                 details=f"{student.full_name} - {position.title}")
    db.session.commit()
    flash(f"Application {'approved' if decision == 'approve' else 'rejected'}.", "success")
    return redirect(back)


@admin_bp.route("/students/<int:user_id>/photo")
def student_photo(user_id):
    """Profile photo of a student, for the review panels."""
    user = db.session.get(User, user_id) or abort(404)
    if not user.profile_photo_path:
        abort(404)
    root = (Path(current_app.instance_path) / "profile_photos").resolve()
    path = (root / user.profile_photo_path).resolve()
    if path.parent != root or not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/jpeg", max_age=300)
