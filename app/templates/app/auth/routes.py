from urllib.parse import urlparse

from flask import current_app, jsonify, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user

from app.auth import auth_bp
from app.auth.forms import (
    ForgotPasswordForm, LoginForm, OTPForm, RegisterForm, ResetPasswordForm,
)
from app.auth import face
from app.auth.otp import (
    MAX_ATTEMPTS, OTPDeliveryError, active_otp, check_code, delivery_mode,
    ensure_active_otp, expiry_minutes, issue_otp, mask_email, seconds_left,
    seconds_until_resend,
)
from app.auth.services import grant_face_check, record_audit
from app.extensions import db
from app.models import EmailOTP, FaceData, Setting, User, utc_now


# ------------------------------------------------------------------ helpers
def safe_next(target):
    """Only allow redirects to paths on this site."""
    if not target:
        return None
    parts = urlparse(target)
    if parts.netloc or parts.scheme or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def pending_user():
    """The not-yet-verified user who is in the middle of registering."""
    uid = session.get("pending_user_id")
    if not uid:
        return None
    user = db.session.get(User, uid)
    if user is None or user.email_verified:
        session.pop("pending_user_id", None)
        return None
    return user


def start_verification(user, send=True):
    session["pending_user_id"] = user.id
    session["otp_attempts"] = 0
    session.pop("dev_code", None)
    if not send:
        return
    try:
        code = issue_otp(user)
        if delivery_mode() == "console":
            session["dev_code"] = code
    except OTPDeliveryError as exc:
        current_app.logger.error("OTP email failed: %s", exc)
        flash(f"We could not send the email: {exc} Press Resend code to try again.", "danger")


def render_verify(user, form):
    otp = active_otp(user)
    return render_template(
        "auth/verify_email.html",
        form=form,
        masked_email=mask_email(user.email),
        expires_in=seconds_left(otp),
        resend_in=seconds_until_resend(user),
        dev_code=session.get("dev_code") if delivery_mode() == "console" else None,
        expiry_minutes=expiry_minutes(),
        email_mode=delivery_mode() == "smtp",
    )


# ----------------------------------------------------------------- register
@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = RegisterForm()
    if form.validate_on_submit():
        user = User(
            full_name=form.full_name.data.strip(),
            student_id=form.student_id.data.strip(),
            email=form.email.data.strip().lower(),
            program_id=form.program_id.data,
            year_level_id=form.year_level_id.data,
            section_id=form.section_id.data,
            role="student",
            email_verified=False,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()

        start_verification(user)
        return redirect(url_for("auth.verify_email"))

    return render_template("auth/register.html", form=form)


# ------------------------------------------------------------- verify email
@auth_bp.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    user = pending_user()
    if user is None:
        flash("Please log in or register first.", "warning")
        return redirect(url_for("auth.login"))

    form = OTPForm()
    if request.method == "POST":
        if not form.validate_on_submit():
            return render_verify(user, form)

        attempts = session.get("otp_attempts", 0)
        if attempts >= MAX_ATTEMPTS:
            flash("Too many wrong attempts. Please request a new code.", "danger")
            return redirect(url_for("auth.verify_email"))

        result = check_code(user, form.code.data)
        if result == "ok":
            session.pop("pending_user_id", None)
            session.pop("dev_code", None)
            session.pop("otp_attempts", None)
            login_user(user)
            flash("Email verified. One more step.", "success")
            return redirect(url_for("auth.face_setup"))

        if result == "expired":
            flash("That code has expired. Please request a new one.", "danger")
        else:
            session["otp_attempts"] = attempts + 1
            left = MAX_ATTEMPTS - session["otp_attempts"]
            flash(f"Incorrect code. {left} attempt(s) left.", "danger")
        return redirect(url_for("auth.verify_email"))

    return render_verify(user, form)


@auth_bp.route("/resend-code", methods=["POST"])
def resend_code():
    user = pending_user()
    if user is None:
        return redirect(url_for("auth.login"))

    wait = seconds_until_resend(user)
    if wait > 0:
        flash(f"Please wait {wait}s before asking for a new code.", "warning")
    else:
        session["otp_attempts"] = 0
        try:
            code = issue_otp(user)
            if delivery_mode() == "console":
                session["dev_code"] = code
            flash("A new code has been sent.", "success")
        except OTPDeliveryError as exc:
            current_app.logger.error("OTP resend failed: %s", exc)
            flash(f"We could not send the email: {exc}", "danger")
    return redirect(url_for("auth.verify_email"))


@auth_bp.route("/change-email", methods=["POST"])
def change_email():
    """Wrong email? Remove the unverified account so the student can register again."""
    user = pending_user()
    if user is not None:
        EmailOTP.query.filter_by(user_id=user.id).delete()
        db.session.delete(user)
        db.session.commit()
    session.pop("pending_user_id", None)
    session.pop("dev_code", None)
    flash("You can register again with the correct email.", "success")
    return redirect(url_for("auth.register"))


# --------------------------------------------------------------- face setup
def _json_error(message, status=400, **extra):
    return jsonify({"ok": False, "message": message, **extra}), status


def _max_face_attempts():
    setting = Setting.query.filter_by(key="face_check_attempts").first()
    try:
        return max(1, int(setting.value)) if setting else 3
    except ValueError:
        return 3


@auth_bp.route("/face-setup")
@login_required
def face_setup():
    if current_user.role != "student":
        return redirect(url_for("main.dashboard"))
    if current_user.face_status == "enrolled":
        return redirect(url_for("main.dashboard"))
    return render_template("auth/face_setup.html")


@auth_bp.route("/face-setup/save", methods=["POST"])
@login_required
def face_setup_save():
    """Receives the 3 camera snapshots (Center, Left, Right) and saves the face data."""
    if current_user.role != "student":
        return _json_error("Only student accounts can set up face verification.", 403)

    payload = request.get_json(silent=True) or {}
    if payload.get("consent") is not True:
        return _json_error("Please tick the consent box first.")

    try:
        blob = face.build_enrollment(payload.get("frames"))
    except face.FaceError as exc:
        return _json_error(str(exc))
    except face.FaceUnavailable as exc:
        current_app.logger.exception("Face engine unavailable")
        return _json_error(str(exc), 503)

    record = FaceData.query.filter_by(user_id=current_user.id).first()
    if record is None:
        record = FaceData(user_id=current_user.id)
        db.session.add(record)
    record.face_embedding = blob
    record.reference_path = None       # no photo is kept - only the numbers
    record.status = "enrolled"
    record.reviewed_at = utc_now()

    current_user.face_status = "enrolled"
    current_user.face_attempts = 0
    record_audit("face_enrolled", actor_id=current_user.id,
                 entity_type="user", entity_id=current_user.id)
    db.session.commit()

    flash("Face verification is ready. You can now vote securely.", "success")
    return jsonify({"ok": True, "redirect": url_for("main.dashboard")})


@auth_bp.route("/face-enrollment")
@login_required
def face_enrollment():
    """Old address kept so older links do not break."""
    return redirect(url_for("auth.face_setup"))


# --------------------------------------------------------------- face check
@auth_bp.route("/face-check", methods=["GET", "POST"])
@login_required
def face_check():
    """Live face check. On success the student may open the page in ?next= once."""
    if current_user.role != "student":
        return redirect(url_for("main.dashboard"))

    if request.method == "GET":
        next_url = safe_next(request.args.get("next")) or url_for("main.dashboard")
        method = "POST" if request.args.get("method", "GET").upper() == "POST" else "GET"
        return render_template(
            "auth/face_check.html",
            enrolled=current_user.face_status == "enrolled",
            next_url=next_url,
            target_method=method,
        )

    payload = request.get_json(silent=True) or {}
    record = FaceData.query.filter_by(user_id=current_user.id).first()
    if current_user.face_status != "enrolled" or record is None or not record.face_embedding:
        return _json_error("Set up face verification first.", 409,
                           redirect=url_for("auth.face_setup"))
    if current_user.is_locked:
        return _json_error("Your account is locked. Contact the election committee.", 423)

    target = safe_next(payload.get("next")) or url_for("main.dashboard")
    method = "POST" if str(payload.get("method", "GET")).upper() == "POST" else "GET"

    try:
        matched, distance = face.verify_live(record.face_embedding, payload.get("frame"))
    except face.FaceError as exc:
        return _json_error(str(exc))          # not a mismatch - does not count as a failed try
    except face.FaceUnavailable as exc:
        current_app.logger.exception("Face engine unavailable")
        return _json_error(str(exc), 503)

    if matched:
        current_user.face_attempts = 0
        record_audit("face_check_passed", actor_id=current_user.id,
                     entity_type="user", entity_id=current_user.id)
        db.session.commit()
        grant_face_check(current_user.id, target, method)
        return jsonify({"ok": True, "redirect": target})

    limit = _max_face_attempts()
    current_user.face_attempts = (current_user.face_attempts or 0) + 1
    record_audit("face_check_failed", actor_id=current_user.id,
                 entity_type="user", entity_id=current_user.id)
    if current_user.face_attempts >= limit:
        current_user.is_locked = True
        record_audit("account_locked", actor_id=current_user.id,
                     entity_type="user", entity_id=current_user.id, details="too many failed face checks")
        db.session.commit()
        logout_user()
        flash("Your face did not match your registered face too many times. Your account is locked - contact the election committee.", "danger")
        return _json_error("Account locked.", 423, redirect=url_for("auth.login"))
    db.session.commit()
    left = limit - current_user.face_attempts
    return _json_error(f"Face does not match your registered face (from your previous face verification). {left} attempt(s) left.", 401)


# -------------------------------------------------------------------- login
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()

        if user is None or not user.check_password(form.password.data):
            record_audit("login_failed", actor_id=user.id if user else None,
                         entity_type="user", entity_id=user.id if user else None,
                         details="student login")
            db.session.commit()
            flash("Invalid email or password.", "danger")
        elif user.is_locked:
            flash("Your account is locked. Contact the election committee.", "danger")
        elif not user.email_verified:
            session["pending_user_id"] = user.id
            session["otp_attempts"] = 0
            try:
                code = ensure_active_otp(user)
                if code and delivery_mode() == "console":
                    session["dev_code"] = code
            except OTPDeliveryError as exc:
                current_app.logger.error("OTP email failed: %s", exc)
                flash(f"We could not send the email: {exc} Press Resend code to try again.", "danger")
            flash("Please verify your email to continue.", "warning")
            return redirect(url_for("auth.verify_email"))
        else:
            login_user(user)
            record_audit("login", actor_id=user.id, entity_type="user", entity_id=user.id,
                         details=user.role)
            db.session.commit()
            if user.role == "admin":
                return redirect(url_for("admin.dashboard"))
            return redirect(safe_next(request.args.get("next")) or url_for("main.dashboard"))

    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    was_admin = current_user.role == "admin"
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("admin.login" if was_admin else "auth.login"))


# ---------------------------------------------------------- forgot password
GENERIC_RESET_NOTE = "If that email has a CampusVote account, we sent a 6-digit code to it."


def _send_reset_code(user):
    """Email a reset code unless one was just sent. Returns False if delivery failed."""
    if seconds_until_resend(user) > 0:
        return True                      # code already on its way; do not spam the inbox
    try:
        code = issue_otp(user, purpose="reset")
        if delivery_mode() == "console":
            session["dev_code"] = code
    except OTPDeliveryError as exc:
        current_app.logger.error("Reset email failed: %s", exc)
        flash(f"We could not send the email: {exc}", "danger")
        return False
    return True


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.settings"))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        session["reset_email"] = email
        session["reset_attempts"] = 0
        session.pop("dev_code", None)

        user = User.query.filter_by(email=email).first()
        if user is not None and not _send_reset_code(user):
            return redirect(url_for("auth.forgot_password"))
        flash(GENERIC_RESET_NOTE, "success")      # same message whether or not the email exists
        return redirect(url_for("auth.reset_password"))

    return render_template("auth/forgot_password.html", form=form)


@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.settings"))
    email = session.get("reset_email")
    if not email:
        flash("Enter your school email first.", "warning")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        attempts = session.get("reset_attempts", 0)
        if attempts >= MAX_ATTEMPTS:
            flash("Too many wrong attempts. Request a new code.", "danger")
            return redirect(url_for("auth.forgot_password"))

        user = User.query.filter_by(email=email).first()
        result = check_code(user, form.code.data) if user is not None else "invalid"
        if result == "ok":
            user.set_password(form.password.data)
            record_audit("password_reset", actor_id=user.id,
                         entity_type="user", entity_id=user.id)
            db.session.commit()
            for key in ("reset_email", "reset_attempts", "dev_code"):
                session.pop(key, None)
            flash("Password updated. Log in with your new password.", "success")
            return redirect(url_for("auth.login"))

        session["reset_attempts"] = attempts + 1
        if result == "expired":
            flash("That code has expired. Request a new one.", "danger")
        else:
            left = MAX_ATTEMPTS - session["reset_attempts"]
            flash(f"Incorrect code. {left} attempt(s) left.", "danger")
        return redirect(url_for("auth.reset_password"))

    return render_template(
        "auth/reset_password.html",
        form=form,
        masked_email=mask_email(email),
        expiry_minutes=expiry_minutes(),
        email_mode=delivery_mode() == "smtp",
        resend_in=0,
        dev_code=session.get("dev_code") if delivery_mode() == "console" else None,
    )


@auth_bp.route("/reset-password/resend", methods=["POST"])
def resend_reset_code():
    email = session.get("reset_email")
    if not email or current_user.is_authenticated:
        return redirect(url_for("auth.forgot_password"))
    user = User.query.filter_by(email=email).first()
    if user is not None:
        wait = seconds_until_resend(user)
        if wait > 0:
            flash(f"Please wait {wait}s before asking for a new code.", "warning")
            return redirect(url_for("auth.reset_password"))
        session["reset_attempts"] = 0
        if not _send_reset_code(user):
            return redirect(url_for("auth.reset_password"))
    flash(GENERIC_RESET_NOTE, "success")
    return redirect(url_for("auth.reset_password"))
