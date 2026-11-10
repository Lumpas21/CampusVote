import secrets
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from flask import current_app, redirect, request, session, url_for
from flask_login import current_user
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import AuditLog, EmailOTP, utc_now


class InvalidFacePhoto(ValueError):
    pass


class FaceVerificationUnavailable(RuntimeError):
    pass


def safe_next(target):
    if not target:
        return None
    parts = urlparse(target)
    if (
        parts.netloc
        or parts.scheme
        or not target.startswith("/")
        or target.startswith("//")
    ):
        return None
    return target


def record_audit(
    action, *, actor_id=None, entity_type=None, entity_id=None, details=None
):
    db.session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            ip_address=request.remote_addr,
        )
    )


def deliver_email_code(recipient, code):
    mode = current_app.config["EMAIL_DELIVERY"]
    if mode == "console":
        current_app.logger.warning(
            "Development email verification code for %s: %s", recipient, code
        )
        return
    if mode != "smtp":
        raise RuntimeError("EMAIL_DELIVERY must be 'console' or 'smtp'.")

    host = current_app.config["MAIL_HOST"]
    sender = current_app.config["MAIL_FROM"]
    if not host or not sender:
        raise RuntimeError("MAIL_HOST and MAIL_FROM are required for SMTP.")

    message = EmailMessage()
    message["Subject"] = "Your CampusVote verification code"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        f"Your CampusVote verification code is {code}. "
        f"It expires in {current_app.config['OTP_EXPIRY_MINUTES']} minutes."
    )

    with smtplib.SMTP(host, current_app.config["MAIL_PORT"], timeout=15) as smtp:
        smtp.ehlo()
        if current_app.config["MAIL_USE_TLS"]:
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        username = current_app.config["MAIL_USERNAME"]
        if username:
            smtp.login(username, current_app.config["MAIL_PASSWORD"])
        smtp.send_message(message)


def issue_email_code(user):
    now = utc_now()
    EmailOTP.query.filter_by(user_id=user.id, used_at=None).update(
        {"used_at": now}, synchronize_session=False
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    otp = EmailOTP(
        user_id=user.id,
        otp_hash=generate_password_hash(code),
        expires_at=now + timedelta(minutes=current_app.config["OTP_EXPIRY_MINUTES"]),
        attempts=0,
    )
    db.session.add(otp)
    db.session.commit()
    deliver_email_code(user.email, code)


def verify_email_code(user, submitted_code):
    otp = (
        EmailOTP.query.filter_by(user_id=user.id, used_at=None)
        .order_by(EmailOTP.created_at.desc(), EmailOTP.id.desc())
        .first()
    )
    now = utc_now()
    if otp is None or otp.expires_at <= now or otp.attempts >= 5:
        if otp is not None and otp.used_at is None:
            otp.used_at = now
            db.session.commit()
        return False, "expired"
    if not check_password_hash(otp.otp_hash, submitted_code):
        otp.attempts += 1
        if otp.attempts >= 5:
            otp.used_at = now
        db.session.commit()
        return False, "invalid"

    otp.used_at = now
    user.email_verified = True
    record_audit(
        "email_verified",
        actor_id=user.id,
        entity_type="user",
        entity_id=user.id,
    )
    db.session.commit()
    return True, None


def face_storage_directory():
    path = Path(current_app.instance_path) / "face_photos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_face_photo(upload, *, prefix=""):
    if upload is None or not upload.filename:
        raise InvalidFacePhoto("Choose a face photo to continue.")
    try:
        upload.stream.seek(0, 2)
        size = upload.stream.tell()
        upload.stream.seek(0)
    except OSError as exc:
        raise InvalidFacePhoto("The selected photo could not be read.") from exc
    if size > 5 * 1024 * 1024:
        raise InvalidFacePhoto("Choose an image smaller than 5 MB.")

    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:
        raise FaceVerificationUnavailable(
            "Image support is unavailable. Install the project requirements."
        ) from exc

    try:
        image = Image.open(upload.stream)
        if image.format not in {"JPEG", "PNG", "WEBP"}:
            raise InvalidFacePhoto("Use a JPG, PNG, or WebP image.")
        if image.width * image.height > 20_000_000:
            raise InvalidFacePhoto("The image is too large to process.")
        image.verify()
        upload.stream.seek(0)
        image = Image.open(upload.stream)
        image.load()
        image = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise InvalidFacePhoto("The selected file is not a valid supported image.") from exc

    filename = f"{prefix}{uuid4().hex}.jpg"
    destination = face_storage_directory() / filename
    try:
        image.save(destination, format="JPEG", quality=90, optimize=True)
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise FaceVerificationUnavailable(
            "The photo could not be saved. Try another image."
        ) from exc
    return filename


def resolve_face_photo(filename):
    root = face_storage_directory().resolve()
    path = (root / filename).resolve()
    if path.parent != root:
        raise ValueError("Invalid stored face photo path.")
    return path


def compare_face_photos(reference_path, live_path):
    try:
        from deepface import DeepFace
    except ImportError as exc:
        raise FaceVerificationUnavailable(
            "Face matching is not installed. Install the project requirements."
        ) from exc

    result = DeepFace.verify(
        img1_path=str(reference_path),
        img2_path=str(live_path),
        model_name="Facenet512",
        detector_backend="opencv",
        enforce_detection=True,
        anti_spoofing=True,
    )
    return bool(result["verified"])


def require_face_verification(view=None, *, method="GET"):
    protected_method = method.upper()
    if protected_method not in {"GET", "POST"}:
        raise ValueError("Face verification supports GET and POST routes only.")

    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(
                    url_for("auth.login", next=request.full_path.rstrip("?"))
                )
            if current_user.role != "student" or request.method != protected_method:
                return function(*args, **kwargs)

            target = request.full_path.rstrip("?")
            grant = session.get("face_check_grant")
            if (
                grant
                and grant.get("user_id") == current_user.id
                and grant.get("target") == target
                and grant.get("method") == protected_method
                and grant.get("expires_at", 0) >= datetime.now().timestamp()
            ):
                session.pop("face_check_grant", None)
                return function(*args, **kwargs)

            return redirect(
                url_for(
                    "auth.face_check",
                    next=target,
                    method=protected_method,
                )
            )

        return wrapped

    if view is None:
        return decorate
    return decorate(view)


def grant_face_check(user_id, target, method="GET"):
    session["face_check_grant"] = {
        "user_id": user_id,
        "target": target,
        "method": method,
        "expires_at": datetime.now().timestamp() + 120,
    }


def create_live_photo(upload):
    return save_face_photo(upload, prefix="live-")
