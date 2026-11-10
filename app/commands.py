from flask import current_app
from werkzeug.security import generate_password_hash
import click

from app.extensions import db
from app.models import (
    College,
    Program,
    YearLevel,
    Section,
    User,
    Setting,
)


COLLEGES = [
    ("CCIS", "College of Computing and Information Sciences"),
    ("CTE", "College of Teacher Education"),
    ("COE", "College of Engineering"),
    ("CBT", "College of Business and Technology"),
    ("CAS", "College of Arts and Sciences"),
]


PROGRAMS = [
    ("CCIS", "BSCS", "Bachelor of Science in Computer Science"),
    ("CCIS", "BSIS", "Bachelor of Science in Information System"),
    ("CCIS", "BSIT", "Bachelor of Science in Information Technology"),

    ("CTE", "BTVTEd-FSM", "Bachelor of Technical-Vocational Teacher Education Major in Food and Services Management"),
    ("CTE", "BSEd-Eng", "Bachelor of Secondary Education Major in English"),
    ("CTE", "BSEd-Fil", "Bachelor of Secondary Education Major in Filipino"),
    ("CTE", "BSEd-Math", "Bachelor of Secondary Education Major in Mathematics"),
    ("CTE", "BSEd-Sci", "Bachelor of Secondary Education Major in Sciences"),
    ("CTE", "BEEd", "Bachelor of Elementary Education"),
    ("CTE", "BPEd", "Bachelor of Physical Education"),
    ("CTE", "BECEd", "Bachelor of Early Childhood Education"),

    ("COE", "BSCE", "Bachelor of Science in Civil Engineering"),
    ("COE", "BSEE", "Bachelor of Science in Electrical Engineering"),
    ("COE", "BSECE", "Bachelor of Science in Electronics Engineering"),
    ("COE", "BSCoE", "Bachelor of Science in Computer Engineering"),
    ("COE", "BAET", "Bachelor in Automotive Engineering Technology"),
    ("COE", "BEET", "Bachelor in Electrical Engineering Technology"),
    ("COE", "BEXET", "Bachelor in Electronics Engineering Technology"),
    ("COE", "BMET-MT", "Bachelor in Mechanical Engineering Technology with concentration in Mechanical Technology"),
    ("COE", "BMET-RAC", "Bachelor in Mechanical Engineering Technology with concentration in Refrigeration and Air-Conditioning Technology"),
    ("COE", "BMET-WAFT", "Bachelor in Mechanical Engineering Technology with concentration in Welding and Fabrication Technology"),

    ("CBT", "BSHM", "Bachelor of Science in Hospitality Management"),
    ("CBT", "BSTM", "Bachelor of Science in Tourism Management"),
    ("CBT", "BIndTech-ELXT", "Bachelor in Industrial Technology Major in Electronics Technology"),
    ("CBT", "BIndTech-ELT", "Bachelor in Industrial Technology Major in Electrical Technology"),
    ("CBT", "BIndTech-WAFT", "Bachelor in Industrial Technology Major in Welding and Fabrication Technology"),
    ("CBT", "BIndTech-AT", "Bachelor in Industrial Technology Major in Automotive Technology"),
    ("CBT", "BIndTech-HVACR", "Bachelor in Industrial Technology Major in Heating, Ventilation, Air Conditioning, and Refrigeration Technology"),
    ("CBT", "BIndTech-MT", "Bachelor in Industrial Technology Major in Mechanical Technology"),
    ("CBT", "BIndTech-ADT", "Bachelor in Industrial Technology Major in Architectural Drafting Technology"),

    ("CAS", "BSES", "Bachelor of Science in Environmental Science"),
    ("CAS", "BSMath", "Bachelor of Science in Mathematics"),
    ("CAS", "AB-EL", "Bachelor of Arts in English Language"),
    ("CAS", "BHS", "Bachelor in Human Services"),
]


YEAR_LEVELS = [
    ("1st Year", 1),
    ("2nd Year", 2),
    ("3rd Year", 3),
    ("4th Year", 4),
]


def register_commands(app):

    @app.cli.command("seed-db")
    def seed_db():
        """Add initial college, program, year, section, and settings records."""

        for code, name in COLLEGES:
            college = College.query.filter_by(code=code).first()

            if college is None:
                db.session.add(
                    College(code=code, name=name)
                )

        db.session.flush()

        for college_code, program_code, program_name in PROGRAMS:
            college = College.query.filter_by(
                code=college_code
            ).first()

            program = Program.query.filter_by(
                college_id=college.id,
                code=program_code
            ).first()

            if program is None:
                db.session.add(
                    Program(
                        college_id=college.id,
                        code=program_code,
                        name=program_name
                    )
                )

        for name, sort_order in YEAR_LEVELS:
            year = YearLevel.query.filter_by(
                name=name
            ).first()

            if year is None:
                db.session.add(
                    YearLevel(
                        name=name,
                        sort_order=sort_order
                    )
                )

        db.session.flush()

        for year_number in range(1, 5):

            if year_number == 1:
                suffix = "st"
            elif year_number == 2:
                suffix = "nd"
            elif year_number == 3:
                suffix = "rd"
            else:
                suffix = "th"

            year_name = f"{year_number}{suffix} Year"

            year = YearLevel.query.filter_by(
                name=year_name
            ).first()

            for letter in "ABCD":
                section_code = f"{year_number}{letter}"
                section = Section.query.filter_by(
                    year_level_id=year.id,
                    code=section_code
                ).first()

                if section is None:
                    db.session.add(
                        Section(
                            year_level_id=year.id,
                            code=section_code
                        )
                    )

        # Add default settings without overwriting existing values.
        default_settings = {
            "otp_expiry_minutes": "2",
            "face_check_attempts": "3",
        }

        for setting_key, setting_value in default_settings.items():

            setting = Setting.query.filter_by(
                key=setting_key
            ).first()

            if setting is None:
                db.session.add(
                    Setting(
                        key=setting_key,
                        value=setting_value
                    )
                )

        db.session.commit()

        click.echo("Database seed completed.")
        click.echo(f"Colleges: {College.query.count()}")
        click.echo(f"Programs: {Program.query.count()}")
        click.echo(f"Year levels: {YearLevel.query.count()}")
        click.echo(f"Sections: {Section.query.count()}")
        click.echo(f"Settings: {Setting.query.count()}")

    @app.cli.command("create-admin")
    def create_admin():
        """Create or update the initial CampusVote administrator."""

        admin_email = "campusvoteadmin@ssct.edu.ph"

        admin_password = current_app.config.get(
            "ADMIN_SEED_PASSWORD"
        )

        if not admin_password:
            raise click.ClickException(
                "ADMIN_SEED_PASSWORD is missing from your .env file."
            )

        existing_admin = User.query.filter_by(
            email=admin_email
        ).first()

        if existing_admin:

            # Keep the existing password.
            existing_admin.full_name = "Election Committee"
            existing_admin.role = "admin"
            existing_admin.admin_role = "super_admin"
            existing_admin.email_verified = True

            db.session.commit()

            click.echo(
                "Existing administrator updated successfully."
            )
            return

        admin = User(
            full_name="Election Committee",
            email=admin_email,
            role="admin",
            admin_role="super_admin",
            email_verified=True,
            password_hash=generate_password_hash(admin_password),
        )

        db.session.add(admin)
        db.session.commit()

        click.echo(
            "Administrator account created successfully."
        )

    @app.cli.command("test-email")
    @click.argument("to_address")
    def test_email(to_address):
        """Send a sample verification code to TO_ADDRESS to check your .env email settings."""
        from app.auth.otp import (
            OTPDeliveryError, delivery_mode, expiry_minutes, send_code_email,
        )

        click.echo(f"OTP_DELIVERY = {delivery_mode()}")
        if delivery_mode() != "smtp":
            click.echo("Set OTP_DELIVERY=smtp in .env first (console mode never sends email).")
            return
        try:
            send_code_email(to_address, "123456", "Test")
        except OTPDeliveryError as exc:
            click.echo(f"FAILED: {exc}")
            return
        click.echo(f"Sent! Check {to_address} (and Spam). Codes expire after {expiry_minutes()} minute(s).")

    @app.cli.command("face-selftest")
    def face_selftest():
        """Load the face models (downloads them once) and report if face verification can run."""
        import numpy as np
        from app.auth import face

        click.echo(f"Model={face.MODEL_NAME}  Detector={face.DETECTOR}  "
                   f"Anti-spoof={'on' if face.ANTI_SPOOF else 'off'}  Threshold={face.threshold()}")
        click.echo("Loading models... (the first run downloads them, needs internet)")
        blank = np.full((480, 640, 3), 127, dtype=np.uint8)
        try:
            face.embed(blank)
        except face.FaceError:
            click.echo("OK - the face engine works (it correctly found no face in a blank picture).")
        except face.FaceUnavailable as exc:
            click.echo(f"NOT READY: {exc}")
            if exc.__cause__:
                click.echo(f"Details: {exc.__cause__!r}")
        else:
            click.echo("Unexpected: a face was reported in a blank picture.")
