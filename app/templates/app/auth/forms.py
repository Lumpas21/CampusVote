import os

from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectField, BooleanField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Regexp, ValidationError

from app.extensions import db
from app.models import User, College, Program, YearLevel, Section


def allowed_email_domains():
    """School email domains allowed for students (edit in .env if needed)."""
    raw = os.getenv("ALLOWED_EMAIL_DOMAINS", "snsu.edu.ph,ssct.edu.ph")
    return tuple("@" + d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip())


class RegisterForm(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(max=150)])
    student_id = StringField("Student ID", validators=[
        DataRequired(), Length(max=50),
        Regexp(r"^[A-Za-z0-9\-]+$", message="Letters, numbers and dashes only."),
    ])
    email = StringField("School email", validators=[DataRequired(), Email(), Length(max=150)])

    # The <select> elements are drawn by hand in the template, so choices are not validated here.
    program_id = SelectField("Program", coerce=int, choices=[], validate_choice=False,
                             validators=[DataRequired(message="Select your program.")])
    year_level_id = SelectField("Year level", coerce=int, choices=[], validate_choice=False,
                                validators=[DataRequired(message="Select your year level.")])
    section_id = SelectField("Section", coerce=int, choices=[], validate_choice=False,
                             validators=[DataRequired(message="Select your section.")])

    password = PasswordField("Password", validators=[DataRequired(), Length(min=8)])
    confirm = PasswordField("Confirm password", validators=[
        DataRequired(), EqualTo("password", message="Passwords do not match.")
    ])
    agree = BooleanField(validators=[DataRequired(message="You must agree to continue.")])

    # ---- data for the template -------------------------------------------
    @property
    def program_groups(self):
        groups = []
        for college in College.query.order_by(College.code).all():
            programs = Program.query.filter_by(college_id=college.id).order_by(Program.code).all()
            if programs:
                groups.append((
                    f"{college.code} - {college.name}",
                    [(p.id, f"{p.code}: {p.name}") for p in programs],
                ))
        return groups

    @property
    def year_options(self):
        return [(y.id, y.name) for y in YearLevel.query.order_by(YearLevel.sort_order).all()]

    @property
    def section_options(self):
        return [{"id": s.id, "code": s.code, "year": s.year_level_id}
                for s in Section.query.order_by(Section.code).all()]

    # ---- validation ----------------------------------------------------------
    def validate_email(self, field):
        email = field.data.strip().lower()
        if not email.endswith(allowed_email_domains()):
            shown = " or ".join(allowed_email_domains())
            raise ValidationError(f"Use your school email ({shown}).")
        if User.query.filter_by(email=email).first():
            raise ValidationError("This email is already registered.")

    def validate_student_id(self, field):
        if User.query.filter_by(student_id=field.data.strip()).first():
            raise ValidationError("This student ID is already registered.")

    def validate_program_id(self, field):
        if db.session.get(Program, field.data) is None:
            raise ValidationError("Select a valid program.")

    def validate_year_level_id(self, field):
        if db.session.get(YearLevel, field.data) is None:
            raise ValidationError("Select a valid year level.")

    def validate_section_id(self, field):
        section = db.session.get(Section, field.data)
        if section is None or section.year_level_id != self.year_level_id.data:
            raise ValidationError("Section does not match the selected year level.")


class LoginForm(FlaskForm):
    email = StringField("School email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])


class OTPForm(FlaskForm):
    code = StringField("Code", validators=[
        DataRequired(message="Enter the 6-digit code."),
        Regexp(r"^\d{6}$", message="Enter the 6-digit code."),
    ])


# ------------------------------------------------------- account recovery / settings
def _check_school_email(field, current_user_id=None):
    """Shared rule: must be a school address that no other account uses."""
    email = field.data.strip().lower()
    if not email.endswith(allowed_email_domains()):
        shown = " or ".join(allowed_email_domains())
        raise ValidationError(f"Use your school email ({shown}).")
    other = User.query.filter_by(email=email).first()
    if other is not None and other.id != current_user_id:
        raise ValidationError("This email is already used by another account.")
    return email


class ForgotPasswordForm(FlaskForm):
    email = StringField("School email", validators=[DataRequired(), Email()])


class ResetPasswordForm(FlaskForm):
    code = StringField("Code", validators=[
        DataRequired(message="Enter the 6-digit code."),
        Regexp(r"^\d{6}$", message="Enter the 6-digit code."),
    ])
    password = PasswordField("New password", validators=[DataRequired(), Length(min=8)])
    confirm = PasswordField("Confirm new password", validators=[
        DataRequired(), EqualTo("password", message="Passwords do not match.")
    ])


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    password = PasswordField("New password", validators=[DataRequired(), Length(min=8)])
    confirm = PasswordField("Confirm new password", validators=[
        DataRequired(), EqualTo("password", message="Passwords do not match.")
    ])


class ChangeEmailForm(FlaskForm):
    new_email = StringField("New school email", validators=[DataRequired(), Email(), Length(max=150)])
    password = PasswordField("Current password", validators=[DataRequired()])

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._user = user

    def validate_new_email(self, field):
        _check_school_email(field, current_user_id=self._user.id if self._user else None)
        if self._user and field.data.strip().lower() == self._user.email:
            raise ValidationError("That is already your email.")

    def validate_password(self, field):
        if self._user is None or not self._user.check_password(field.data):
            raise ValidationError("Wrong password.")
