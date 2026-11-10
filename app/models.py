from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db


def utc_now():
    """Return the current UTC time as a timezone-naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class College(db.Model):
    __tablename__ = "colleges"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)

    programs = db.relationship("Program", backref="college", lazy=True)


class Program(db.Model):
    __tablename__ = "programs"

    id = db.Column(db.Integer, primary_key=True)
    college_id = db.Column(
        db.Integer, db.ForeignKey("colleges.id"), nullable=False
    )
    code = db.Column(db.String(30), nullable=False)
    name = db.Column(db.String(200), nullable=False)

    __table_args__ = (
        db.UniqueConstraint("college_id", "code"),
    )

    users = db.relationship("User", backref="program", lazy=True)


class YearLevel(db.Model):
    __tablename__ = "year_levels"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False)

    sections = db.relationship("Section", backref="year_level", lazy=True)


class Section(db.Model):
    __tablename__ = "sections"

    id = db.Column(db.Integer, primary_key=True)
    year_level_id = db.Column(
        db.Integer, db.ForeignKey("year_levels.id"), nullable=False
    )
    code = db.Column(db.String(10), nullable=False)

    __table_args__ = (
        db.UniqueConstraint("year_level_id", "code"),
    )

    users = db.relationship("User", backref="section", lazy=True)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    student_id = db.Column(db.String(50), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)

    role = db.Column(db.String(20), nullable=False, default="student")
    admin_role = db.Column(
        db.String(30), nullable=True
    )  # super_admin / department_admin

    program_id = db.Column(db.Integer, db.ForeignKey("programs.id"))
    year_level_id = db.Column(db.Integer, db.ForeignKey("year_levels.id"))
    section_id = db.Column(db.Integer, db.ForeignKey("sections.id"))

    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    face_status = db.Column(db.String(30), default="not_enrolled")
    face_attempts = db.Column(db.Integer, default=0, nullable=False)
    profile_photo_path = db.Column(db.String(255), nullable=True)
    is_locked = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(
        db.DateTime(timezone=False), default=utc_now, nullable=False
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class EmailOTP(db.Model):
    __tablename__ = "email_otps"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    otp_hash = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=False), nullable=False)
    used_at = db.Column(db.DateTime(timezone=False))
    attempts = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=False), default=utc_now, nullable=False
    )


class FaceData(db.Model):
    __tablename__ = "face_data"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False
    )
    face_embedding = db.Column(db.LargeBinary, nullable=True)
    reference_path = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default="pending", nullable=False)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime(timezone=False))
    created_at = db.Column(
        db.DateTime(timezone=False), default=utc_now, nullable=False
    )


class Election(db.Model):
    __tablename__ = "elections"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(30), nullable=False)

    # Readable scope description, such as CCIS, BSIT, or 1A.
    scope_label = db.Column(db.String(100), nullable=True)

    scope_college_id = db.Column(
        db.Integer, db.ForeignKey("colleges.id"), nullable=True
    )
    scope_program_id = db.Column(
        db.Integer, db.ForeignKey("programs.id"), nullable=True
    )
    scope_section_id = db.Column(
        db.Integer, db.ForeignKey("sections.id"), nullable=True
    )

    scope_college = db.relationship(
        "College", foreign_keys=[scope_college_id]
    )
    scope_program = db.relationship(
        "Program", foreign_keys=[scope_program_id]
    )
    scope_section = db.relationship(
        "Section", foreign_keys=[scope_section_id]
    )

    @property
    def scope(self):
        """Backward-compatible display alias for older templates."""
        return self.scope_label

    candidacy_start = db.Column(db.DateTime(timezone=False))
    candidacy_end = db.Column(db.DateTime(timezone=False))
    voting_start = db.Column(db.DateTime(timezone=False))
    voting_end = db.Column(db.DateTime(timezone=False))

    status = db.Column(db.String(30), default="draft", nullable=False)
    rules = db.Column(db.Text)
    created_at = db.Column(
        db.DateTime(timezone=False), default=utc_now, nullable=False
    )


class ElectionPosition(db.Model):
    __tablename__ = "election_positions"

    id = db.Column(db.Integer, primary_key=True)
    election_id = db.Column(
        db.Integer, db.ForeignKey("elections.id"), nullable=False
    )
    title = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, default=0)


class Candidate(db.Model):
    __tablename__ = "candidates"

    id = db.Column(db.Integer, primary_key=True)
    election_position_id = db.Column(
        db.Integer, db.ForeignKey("election_positions.id"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )

    status = db.Column(db.String(30), default="pending", nullable=False)
    photo_path = db.Column(db.String(255))
    poster_path = db.Column(db.String(255))
    platform = db.Column(db.Text)
    reason = db.Column(db.Text, nullable=True)
    party_group = db.Column(db.String(100), nullable=True)
    reject_reason = db.Column(db.Text, nullable=True)

    submitted_at = db.Column(
        db.DateTime(timezone=False), default=utc_now, nullable=False
    )

    __table_args__ = (
        db.UniqueConstraint("election_position_id", "user_id"),
    )


class Vote(db.Model):
    """One chosen candidate (or an abstain) for one position.

    There is deliberately NO voter column here. Who voted is kept in VoteReceipt,
    and what was chosen is kept here, so a ballot cannot be traced to a student.
    """
    __tablename__ = "votes"

    id = db.Column(db.Integer, primary_key=True)
    election_position_id = db.Column(
        db.Integer, db.ForeignKey("election_positions.id"), nullable=False
    )

    # NULL candidate_id means the voter abstained for this position.
    candidate_id = db.Column(
        db.Integer, db.ForeignKey("candidates.id"), nullable=True
    )

    # Rounded to the hour so it cannot be matched to a receipt time.
    cast_at = db.Column(
        db.DateTime(timezone=False), default=utc_now, nullable=False
    )


class VoteReceipt(db.Model):
    """Records THAT a student voted in an election (never what they chose)."""
    __tablename__ = "vote_receipts"

    id = db.Column(db.Integer, primary_key=True)
    voter_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )
    election_id = db.Column(
        db.Integer, db.ForeignKey("elections.id"), nullable=False
    )
    receipt_code = db.Column(db.String(40), unique=True, nullable=True)
    receipt_hash = db.Column(db.String(255), nullable=False)
    issued_at = db.Column(
        db.DateTime(timezone=False), default=utc_now, nullable=False
    )

    __table_args__ = (
        db.UniqueConstraint("voter_id", "election_id"),
    )


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(100), nullable=False)
    entity_type = db.Column(db.String(100))
    entity_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    created_at = db.Column(
        db.DateTime(timezone=False), default=utc_now, nullable=False
    )


class Setting(db.Model):
    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.String(255), nullable=False)


from app.extensions import login_manager


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))