from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from flask import (
    abort, current_app, flash, redirect, render_template, request, send_file,
    session, url_for,
)
from flask_login import login_required, current_user

from app.auth.forms import ChangeEmailForm, ChangePasswordForm
from app.auth.otp import (
    MAX_ATTEMPTS, OTPDeliveryError, check_code, delivery_mode,
    expiry_minutes, issue_otp, mask_email, seconds_until_resend,
)
from app.auth.services import record_audit
from app.election_utils import election_phase
from app.extensions import db
from app.main import main_bp, platform_image
from app.models import (
    AuditLog, Candidate, College, EmailOTP, Election, ElectionPosition,
    Program, Section, User, YearLevel, utc_now,
)

MIN_ESSAY_CHARS = 200      # platform text and "why are you running"
MIN_ESSAY_WORDS = 20       # stops "aaaa..." style filler
HARD_ESSAY_CAP = 20000     # invisible safety cap so one paste cannot flood the database
MAX_FEEDBACK_LEN = 1000
MIN_FEEDBACK_LEN = 5
MAX_PER_HOUR = 5


@main_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return render_template("landing.html")


@main_bp.route("/dashboard")
@login_required
def dashboard():
    applications = _user_applications(current_user.id)
    return render_template("dashboard.html", applications=applications)


def _user_applications(user_id):
    return (
        db.session.query(Candidate, ElectionPosition, Election)
        .join(ElectionPosition, Candidate.election_position_id == ElectionPosition.id)
        .join(Election, ElectionPosition.election_id == Election.id)
        .filter(Candidate.user_id == user_id, Candidate.status != "withdrawn")
        .order_by(Candidate.submitted_at.desc())
        .all()
    )


def _profile_photo_directory():
    path = Path(current_app.instance_path) / "profile_photos"
    path.mkdir(parents=True, exist_ok=True)
    return path


@main_bp.route("/profile/photo", methods=["POST"])
@login_required
def upload_photo():
    photo = request.files.get("photo")
    if photo is None or not photo.filename:
        flash("Choose a profile photo to upload.", "warning")
        return redirect(url_for("main.dashboard"))

    try:
        photo.stream.seek(0, 2)
        size = photo.stream.tell()
        photo.stream.seek(0)
    except OSError:
        flash("The selected photo could not be read.", "danger")
        return redirect(url_for("main.dashboard"))
    if size > 5 * 1024 * 1024:
        flash("Choose an image smaller than 5 MB.", "warning")
        return redirect(url_for("main.dashboard"))

    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        current_app.logger.exception("Pillow is required to process profile photos")
        flash("Photo support is unavailable. Please contact the election committee.", "danger")
        return redirect(url_for("main.dashboard"))

    try:
        image = Image.open(photo.stream)
        if image.format not in {"JPEG", "PNG", "WEBP"}:
            flash("Use a JPG, PNG, or WebP image.", "warning")
            return redirect(url_for("main.dashboard"))
        if image.width * image.height > 20_000_000:
            flash("The image is too large to process.", "warning")
            return redirect(url_for("main.dashboard"))
        image.verify()
        photo.stream.seek(0)
        image = Image.open(photo.stream)
        image.load()
        image = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        flash("The selected file is not a valid supported image.", "warning")
        return redirect(url_for("main.dashboard"))

    filename = f"{uuid4().hex}.jpg"
    destination = _profile_photo_directory() / filename
    try:
        image.save(destination, format="JPEG", quality=90, optimize=True)
    except OSError:
        current_app.logger.exception("Could not save uploaded profile photo")
        flash("The photo could not be saved. Please try again.", "danger")
        return redirect(url_for("main.dashboard"))

    current_user.profile_photo_path = filename
    record_audit(
        "profile_photo_updated", actor_id=current_user.id,
        entity_type="user", entity_id=current_user.id,
    )
    db.session.commit()
    flash("Profile photo updated.", "success")
    return redirect(url_for("main.dashboard"))


@main_bp.route("/profile/photo")
@login_required
def profile_photo():
    filename = request.args.get("v", "")
    if not filename or filename != current_user.profile_photo_path:
        abort(404)
    root = _profile_photo_directory().resolve()
    path = (root / filename).resolve()
    if path.parent != root or not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/jpeg", max_age=300)


def _profile_edit_context(values, errors):
    groups = []
    for college in College.query.order_by(College.code).all():
        programs = (
            Program.query.filter_by(college_id=college.id)
            .order_by(Program.code).all()
        )
        if programs:
            groups.append((
                f"{college.code} - {college.name}",
                [(program.id, f"{program.code}: {program.name}") for program in programs],
            ))
    return dict(
        values=values,
        errors=errors,
        groups=groups,
        years=YearLevel.query.order_by(YearLevel.sort_order).all(),
        sections=Section.query.order_by(Section.code).all(),
    )


@main_bp.route("/profile/edit", methods=["GET", "POST"])
@login_required
def edit_profile():
    values = {
        "full_name": current_user.full_name or "",
        "program_id": current_user.program_id,
        "year_level_id": current_user.year_level_id,
        "section_id": current_user.section_id,
    }
    errors = {}
    if request.method == "POST":
        values = {
            "full_name": (request.form.get("full_name") or "").strip(),
            "program_id": request.form.get("program_id", type=int),
            "year_level_id": request.form.get("year_level_id", type=int),
            "section_id": request.form.get("section_id", type=int),
        }
        name = values["full_name"]
        if not name:
            errors["full_name"] = "Enter your full name."
        elif len(name) > 150:
            errors["full_name"] = "Keep your name under 150 characters."

        program = db.session.get(Program, values["program_id"]) if values["program_id"] else None
        year = db.session.get(YearLevel, values["year_level_id"]) if values["year_level_id"] else None
        section = db.session.get(Section, values["section_id"]) if values["section_id"] else None
        if program is None:
            errors["program_id"] = "Select a valid program."
        if year is None:
            errors["year_level_id"] = "Select a valid year level."
        if section is None or (year and section.year_level_id != year.id):
            errors["section_id"] = "Select a section for the selected year level."

        if not errors:
            current_user.full_name = name
            current_user.program_id = program.id
            current_user.year_level_id = year.id
            current_user.section_id = section.id
            record_audit(
                "profile_updated", actor_id=current_user.id,
                entity_type="user", entity_id=current_user.id,
            )
            db.session.commit()
            flash("Profile updated.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("profile_edit.html", **_profile_edit_context(values, errors))


@main_bp.route("/settings")
@login_required
def settings():
    return render_template(
        "settings.html",
        password_form=ChangePasswordForm(),
        email_form=ChangeEmailForm(user=current_user),
    )


@main_bp.route("/settings/password", methods=["POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("Current password is incorrect.")
        else:
            current_user.set_password(form.password.data)
            record_audit(
                "password_changed", actor_id=current_user.id,
                entity_type="user", entity_id=current_user.id,
            )
            db.session.commit()
            flash("Password updated.", "success")
            return redirect(url_for("main.settings"))

    flash("Please correct the password form and try again.", "warning")
    return render_template(
        "settings.html", password_form=form,
        email_form=ChangeEmailForm(user=current_user),
    )


@main_bp.route("/settings/email", methods=["POST"])
@login_required
def change_email():
    form = ChangeEmailForm(user=current_user)
    if not form.validate_on_submit():
        flash("Please correct the email form and try again.", "warning")
        return render_template(
            "settings.html", password_form=ChangePasswordForm(), email_form=form,
        )

    new_email = form.new_email.data.strip().lower()
    session["pending_new_email"] = new_email
    session["email_change_attempts"] = 0
    session.pop("email_change_dev_code", None)
    try:
        code = issue_otp(current_user, to_address=new_email, purpose="change_email")
        if delivery_mode() == "console":
            session["email_change_dev_code"] = code
    except OTPDeliveryError as exc:
        current_app.logger.error("Email-change OTP delivery failed: %s", exc)
        flash(f"We could not send the email: {exc}", "danger")
        return redirect(url_for("main.settings"))
    flash("A confirmation code was sent to your new email address.", "success")
    return redirect(url_for("main.settings_verify_email"))


@main_bp.route("/settings/email/verify", methods=["GET", "POST"])
@login_required
def settings_verify_email():
    new_email = session.get("pending_new_email")
    if not new_email:
        flash("Start an email change from Settings first.", "warning")
        return redirect(url_for("main.settings"))

    attempts = session.get("email_change_attempts", 0)
    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        if not code.isdigit() or len(code) != 6:
            flash("Enter the 6-digit code.", "warning")
        elif attempts >= MAX_ATTEMPTS:
            flash("Too many incorrect attempts. Request a new code.", "danger")
        else:
            result = check_code(current_user, code)
            if result == "ok":
                if User.query.filter(
                    User.email == new_email, User.id != current_user.id
                ).first():
                    flash("That email is already used by another account.", "danger")
                    return redirect(url_for("main.cancel_email_change"))
                current_user.email = new_email
                record_audit(
                    "email_changed", actor_id=current_user.id,
                    entity_type="user", entity_id=current_user.id,
                    details=new_email,
                )
                db.session.commit()
                for key in ("pending_new_email", "email_change_attempts", "email_change_dev_code"):
                    session.pop(key, None)
                flash("Your school email has been updated.", "success")
                return redirect(url_for("main.settings"))
            session["email_change_attempts"] = attempts + 1
            flash(
                "That code has expired. Request a new one."
                if result == "expired" else "The code is incorrect.",
                "danger",
            )
            attempts = session["email_change_attempts"]

    return render_template(
        "settings_verify_email.html",
        masked_email=mask_email(new_email),
        email_mode=delivery_mode() == "smtp",
        expiry_minutes=expiry_minutes(),
        resend_in=seconds_until_resend(current_user),
        dev_code=(
            session.get("email_change_dev_code")
            if delivery_mode() == "console" else None
        ),
    )


@main_bp.route("/settings/email/cancel", methods=["POST"])
@login_required
def cancel_email_change():
    EmailOTP.query.filter_by(user_id=current_user.id, used_at=None).update(
        {"used_at": utc_now()}
    )
    db.session.commit()
    for key in ("pending_new_email", "email_change_attempts", "email_change_dev_code"):
        session.pop(key, None)
    flash("Email change canceled.", "success")
    return redirect(url_for("main.settings"))


@main_bp.route("/settings/email/resend", methods=["POST"])
@login_required
def resend_new_email_code():
    new_email = session.get("pending_new_email")
    if not new_email:
        flash("Start an email change from Settings first.", "warning")
        return redirect(url_for("main.settings"))
    wait = seconds_until_resend(current_user)
    if wait:
        flash(f"Please wait {wait}s before requesting another code.", "warning")
    else:
        session["email_change_attempts"] = 0
        session.pop("email_change_dev_code", None)
        try:
            code = issue_otp(
                current_user, to_address=new_email, purpose="change_email",
            )
            if delivery_mode() == "console":
                session["email_change_dev_code"] = code
            flash("A new confirmation code was sent.", "success")
        except OTPDeliveryError as exc:
            current_app.logger.error("Email-change OTP resend failed: %s", exc)
            flash(f"We could not send the email: {exc}", "danger")
    return redirect(url_for("main.settings_verify_email"))


def _available_elections(user):
    now = utc_now()
    elections = []
    for election in Election.query.filter_by(status="published").order_by(
        Election.candidacy_end, Election.id
    ).all():
        if election_phase(election, now) != "candidacy":
            continue
        if election.scope_college_id and (
            user.program is None
            or user.program.college_id != election.scope_college_id
        ):
            continue
        if election.scope_program_id and user.program_id != election.scope_program_id:
            continue
        if election.scope_section_id and user.section_id != election.scope_section_id:
            continue
        elections.append(election)
    return elections


@main_bp.route("/apply", methods=["GET", "POST"])
@login_required
def apply_officer():
    applications = _user_applications(current_user.id)
    problems = []
    if current_user.role != "student":
        problems.append(("Only student accounts can apply as an officer.", None))
    if not current_user.email_verified:
        problems.append(("Verify your school email first.", url_for("main.settings")))
    if not current_user.full_name or not current_user.program_id or not current_user.year_level_id or not current_user.section_id:
        problems.append(("Complete your personal information.", url_for("main.edit_profile")))
    if not current_user.profile_photo_path:
        problems.append(("Upload a profile photo.", url_for("main.dashboard")))

    elections = _available_elections(current_user) if not problems else []
    positions = {
        election.id: ElectionPosition.query.filter_by(election_id=election.id)
        .order_by(ElectionPosition.sort_order, ElectionPosition.id).all()
        for election in elections
    }
    values = {
        "election_id": request.form.get("election_id", type=int),
        "position_id": request.form.get("position_id", type=int),
        "party_group": (request.form.get("party_group") or "").strip(),
        "platform": (request.form.get("platform") or "").strip(),
        "reason": (request.form.get("reason") or "").strip(),
        "agree": request.form.get("agree") == "on",
    }
    errors = {}
    if request.method == "POST" and not problems:
        if values["election_id"] not in {e.id for e in elections}:
            errors["election_id"] = "Choose an election that is accepting applications."
        selected_positions = positions.get(values["election_id"], [])
        if values["position_id"] not in {p.id for p in selected_positions}:
            errors["position_id"] = "Choose a position for the selected election."
        if len(values["party_group"]) > 100:
            errors["party_group"] = "Keep the party or group under 100 characters."
        for key, label in (("platform", "platform"), ("reason", "answer")):
            text = values[key]
            if len(text) < MIN_ESSAY_CHARS:
                errors[key] = (
                    f"Write at least {MIN_ESSAY_CHARS} characters for your {label} "
                    f"(you have {len(text)})."
                )
            elif len(text.split()) < MIN_ESSAY_WORDS:
                errors[key] = f"Please write full sentences for your {label}."
            elif len(text) > HARD_ESSAY_CAP:
                errors[key] = "That is too long. Please shorten it."
        platform_file = request.files.get("platform_image")
        if not values["agree"]:
            errors["agree"] = "Confirm the statement before applying."

        if not errors:
            existing = Candidate.query.filter_by(
                user_id=current_user.id,
                election_position_id=values["position_id"],
            ).first()
            if existing and existing.status != "withdrawn":
                errors["position_id"] = "You already have an application for this position."
            else:
                new_image = None
                if platform_file and platform_file.filename:
                    new_image, image_error = platform_image.save(platform_file)
                    if image_error:
                        errors["platform_image"] = image_error
                if not errors:
                    old_image = None
                    if existing:
                        candidate = existing
                        old_image = candidate.poster_path
                        candidate.status = "pending"
                        candidate.reject_reason = None
                        candidate.submitted_at = utc_now()
                    else:
                        candidate = Candidate(
                            user_id=current_user.id,
                            election_position_id=values["position_id"],
                            status="pending",
                        )
                        db.session.add(candidate)
                    candidate.photo_path = current_user.profile_photo_path
                    candidate.party_group = values["party_group"] or None
                    candidate.platform = values["platform"]
                    candidate.reason = values["reason"]
                    candidate.poster_path = new_image if new_image else (
                        old_image if existing else None
                    )
                    record_audit(
                        "application_submitted", actor_id=current_user.id,
                        entity_type="candidate",
                        details=f"position_id={values['position_id']}",
                    )
                    try:
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                        if new_image:
                            platform_image.delete(new_image)
                        raise
                    if new_image and old_image and old_image != new_image:
                        platform_image.delete(old_image)
                    flash("Your officer application was submitted for review.", "success")
                    return redirect(url_for("main.apply_officer"))

    return render_template(
        "apply.html", applications=applications, problems=problems,
        elections=elections, positions=positions, values=values, errors=errors,
    )


@main_bp.route("/apply/<int:candidate_id>/platform-image")
@login_required
def platform_image_file(candidate_id):
    """Platform picture: the applicant sees their own; everyone signed in sees approved ones."""
    candidate = db.session.get(Candidate, candidate_id) or abort(404)
    if candidate.user_id != current_user.id and candidate.status != "approved":
        abort(404)
    path = platform_image.resolve(candidate.poster_path)
    if path is None:
        abort(404)
    return send_file(path, mimetype=platform_image.mimetype_for(path.name), max_age=300)


@main_bp.route("/apply/<int:candidate_id>/withdraw", methods=["POST"])
@login_required
def withdraw_application(candidate_id):
    candidate = db.session.get(Candidate, candidate_id) or abort(404)
    if candidate.user_id != current_user.id:
        abort(403)
    if candidate.status != "pending":
        flash("Only pending applications can be withdrawn.", "warning")
        return redirect(url_for("main.apply_officer"))
    candidate.status = "withdrawn"
    record_audit(
        "application_withdrawn", actor_id=current_user.id,
        entity_type="candidate", entity_id=candidate.id,
    )
    db.session.commit()
    flash("Your application was withdrawn.", "success")
    return redirect(url_for("main.apply_officer"))


@main_bp.route("/elections")
@login_required
def elections():
    rows = Election.query.filter_by(status="published").order_by(
        Election.voting_start, Election.id
    ).all()
    visible = []
    for election in rows:
        if election.scope_college_id and (
            current_user.program is None
            or current_user.program.college_id != election.scope_college_id
        ):
            continue
        if election.scope_program_id and current_user.program_id != election.scope_program_id:
            continue
        if election.scope_section_id and current_user.section_id != election.scope_section_id:
            continue
        visible.append(election)
    return render_template("elections.html", elections=visible)


@main_bp.route("/feedback", methods=["GET", "POST"])
def feedback():
    """Send feedback to the Student Council.

    Guests (not registered / not logged in) are sent to the registration screen first.
    Their message is kept in the session so it is waiting for them after they sign up.
    Messages are stored in audit_logs (action "feedback.submitted") so no new table is needed;
    the future admin panel can list them from there.
    """
    if request.method == "POST":
        message = (request.form.get("message") or "").strip()

        if not current_user.is_authenticated:
            if message:
                session["feedback_draft"] = message[:MAX_FEEDBACK_LEN]
            flash("Please register first to send feedback. Already registered? Log in.", "warning")
            return redirect(url_for("auth.register"))

        if len(message) < MIN_FEEDBACK_LEN:
            flash("Please write a little more before sending.", "warning")
            return render_template("feedback.html", draft=message)
        if len(message) > MAX_FEEDBACK_LEN:
            flash(f"Your message is too long (maximum {MAX_FEEDBACK_LEN} characters).", "warning")
            return render_template("feedback.html", draft=message[:MAX_FEEDBACK_LEN])

        since = utc_now() - timedelta(hours=1)
        recent = AuditLog.query.filter(
            AuditLog.actor_id == current_user.id,
            AuditLog.action == "feedback.submitted",
            AuditLog.created_at >= since,
        ).count()
        if recent >= MAX_PER_HOUR:
            flash("You have sent several messages already. Please try again later.", "warning")
            return render_template("feedback.html", draft=message)

        db.session.add(AuditLog(
            actor_id=current_user.id, action="feedback.submitted", entity_type="user",
            entity_id=current_user.id, details=message, ip_address=request.remote_addr,
        ))
        db.session.commit()
        session.pop("feedback_draft", None)
        flash("Thank you! Your feedback was sent to the Student Council.", "success")
        return redirect(url_for("main.feedback"))

    if not current_user.is_authenticated:
        return redirect(url_for("main.index") + "#contact")
    return render_template("feedback.html", draft=session.get("feedback_draft", ""))
