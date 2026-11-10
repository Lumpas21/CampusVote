"""Admin: lists, security rules and admin accounts (super admins only)."""
import os

from email_validator import EmailNotValidError, validate_email
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.admin import admin_bp
from app.auth.services import record_audit
from app.extensions import db
from app.models import College, Program, Section, Setting, User, YearLevel


@admin_bp.before_request
def _super_admin_only_for_settings():
    if request.endpoint and request.endpoint.startswith("admin.") and current_user.is_authenticated:
        name = request.endpoint.split(".", 1)[1]
        if name in SETTINGS_ENDPOINTS and current_user.admin_role != "super_admin":
            abort(403)


SETTINGS_ENDPOINTS = {"settings", "save_security", "add_college", "add_program",
                      "add_section", "add_admin", "toggle_admin"}


def _setting(key, default):
    row = Setting.query.filter_by(key=key).first()
    return row.value if row else default


def _put(key, value):
    row = Setting.query.filter_by(key=key).first()
    if row is None:
        db.session.add(Setting(key=key, value=str(value)))
    else:
        row.value = str(value)


def _done(message, kind="success"):
    flash(message, kind)
    return redirect(url_for("admin.settings"))


@admin_bp.route("/settings")
def settings():
    colleges = [(c, Program.query.filter_by(college_id=c.id).order_by(Program.code).all())
                for c in College.query.order_by(College.code).all()]
    years = [(y, Section.query.filter_by(year_level_id=y.id).order_by(Section.code).all())
             for y in YearLevel.query.order_by(YearLevel.sort_order).all()]
    admins = User.query.filter_by(role="admin").order_by(User.full_name).all()
    return render_template(
        "admin/settings.html", colleges=colleges, years=years, admins=admins,
        otp_minutes=_setting("otp_expiry_minutes", "2"),
        face_attempts=_setting("face_check_attempts", "3"),
        otp_env_override=os.getenv("OTP_EXPIRY_MINUTES", "").strip(),
    )


@admin_bp.route("/settings/security", methods=["POST"])
def save_security():
    try:
        otp = int(request.form.get("otp_expiry_minutes", ""))
        attempts = int(request.form.get("face_check_attempts", ""))
    except ValueError:
        return _done("Enter whole numbers only.", "danger")
    if not 1 <= otp <= 30:
        return _done("OTP expiry must be between 1 and 30 minutes.", "danger")
    if not 1 <= attempts <= 10:
        return _done("Face check attempts must be between 1 and 10.", "danger")
    _put("otp_expiry_minutes", otp)
    _put("face_check_attempts", attempts)
    record_audit("settings_updated", actor_id=current_user.id,
                 details=f"otp_expiry_minutes={otp}, face_check_attempts={attempts}")
    db.session.commit()
    return _done("Security rules saved.")


@admin_bp.route("/settings/college", methods=["POST"])
def add_college():
    code = request.form.get("code", "").strip().upper()
    name = request.form.get("name", "").strip()
    if not code or not name or len(code) > 20 or len(name) > 150:
        return _done("Enter a college code (up to 20 characters) and its full name.", "danger")
    if College.query.filter_by(code=code).first():
        return _done(f"{code} already exists.", "danger")
    db.session.add(College(code=code, name=name))
    record_audit("settings_updated", actor_id=current_user.id, details=f"college added: {code}")
    db.session.commit()
    return _done(f"{name} added.")


@admin_bp.route("/settings/program", methods=["POST"])
def add_program():
    college = db.session.get(College, request.form.get("college_id", type=int) or 0)
    code = request.form.get("code", "").strip()
    name = request.form.get("name", "").strip()
    if college is None or not code or not name or len(code) > 30 or len(name) > 200:
        return _done("Choose a college and enter the program code and name.", "danger")
    if Program.query.filter_by(college_id=college.id, code=code).first():
        return _done(f"{code} already exists in {college.code}.", "danger")
    db.session.add(Program(college_id=college.id, code=code, name=name))
    record_audit("settings_updated", actor_id=current_user.id, details=f"program added: {code}")
    db.session.commit()
    return _done(f"{code} added to {college.code}.")


@admin_bp.route("/settings/section", methods=["POST"])
def add_section():
    year = db.session.get(YearLevel, request.form.get("year_level_id", type=int) or 0)
    code = request.form.get("code", "").strip().upper()
    if year is None or not code or len(code) > 10:
        return _done("Choose the year level and enter a section code like 3C.", "danger")
    if Section.query.filter_by(year_level_id=year.id, code=code).first():
        return _done(f"Section {code} already exists in {year.name}.", "danger")
    db.session.add(Section(year_level_id=year.id, code=code))
    record_audit("settings_updated", actor_id=current_user.id, details=f"section added: {code}")
    db.session.commit()
    return _done(f"Section {code} added to {year.name}.")


@admin_bp.route("/settings/admins", methods=["POST"])
def add_admin():
    name = request.form.get("full_name", "").strip()
    raw_email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("admin_role", "")
    if not name or len(name) > 150:
        return _done("Enter the admin's name.", "danger")
    try:
        email = validate_email(raw_email, check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return _done("Enter a valid email address.", "danger")
    if role not in ("super_admin", "department_admin"):
        return _done("Choose a role.", "danger")
    if len(password) < 10:
        return _done("Use a password with at least 10 characters.", "danger")
    if User.query.filter_by(email=email).first():
        return _done("That email already has an account.", "danger")
    admin = User(full_name=name, email=email, role="admin", admin_role=role, email_verified=True)
    admin.set_password(password)
    db.session.add(admin)
    db.session.flush()
    record_audit("admin_added", actor_id=current_user.id, entity_type="user",
                 entity_id=admin.id, details=f"{name} ({role})")
    db.session.commit()
    return _done(f"{name} can now log in at the admin page.")


@admin_bp.route("/settings/admins/<int:user_id>/toggle", methods=["POST"])
def toggle_admin(user_id):
    admin = db.session.get(User, user_id) or abort(404)
    if admin.role != "admin":
        abort(404)
    if admin.id == current_user.id:
        return _done("You cannot deactivate your own account.", "warning")
    admin.is_locked = not admin.is_locked
    record_audit("admin_deactivated" if admin.is_locked else "admin_reactivated",
                 actor_id=current_user.id, entity_type="user", entity_id=admin.id,
                 details=admin.full_name)
    db.session.commit()
    return _done(f"{admin.full_name} is now {'deactivated' if admin.is_locked else 'active'}.")
