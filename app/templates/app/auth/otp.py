"""Email OTP helpers: create, deliver and check 6-digit verification codes.

HOW THE CODE REACHES THE STUDENT  (set these in your .env file)

    OTP_DELIVERY=smtp      -> the code is sent by REAL EMAIL to the student's
                              own school inbox (e.g. juan@ssct.edu.ph).
                              The code is NOT shown on the web page.
    OTP_DELIVERY=console   -> development only: the code is printed in the
                              terminal and shown on the verify page, because
                              no email server is configured yet.

    MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD, MAIL_FROM
    MAIL_USE_TLS=1         (port 587, STARTTLS)   or   MAIL_USE_SSL=1 (port 465)
    OTP_EXPIRY_MINUTES=2   -> how long a code stays valid (default 2 minutes)
"""
import os
import secrets
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db
from app.models import EmailOTP, Setting, utc_now

RESEND_SECONDS = 30
MAX_ATTEMPTS = 5
DEFAULT_EXPIRY_MINUTES = 2


class OTPDeliveryError(Exception):
    """Raised when the code could not be delivered."""


def _env(*names, default=""):
    """First non-empty environment variable among `names`."""
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return default


def delivery_mode():
    # OTP_DELIVERY is the name used by this project; EMAIL_DELIVERY is accepted too.
    return _env("OTP_DELIVERY", "EMAIL_DELIVERY", default="console").lower()


def expiry_minutes():
    """.env (OTP_EXPIRY_MINUTES) wins, then the admin setting, then 2 minutes."""
    raw = os.getenv("OTP_EXPIRY_MINUTES", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    setting = Setting.query.filter_by(key="otp_expiry_minutes").first()
    if setting:
        try:
            return max(1, int(setting.value))
        except ValueError:
            pass
    return DEFAULT_EXPIRY_MINUTES


def mask_email(email):
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def latest_otp(user, only_unused=True):
    query = EmailOTP.query.filter_by(user_id=user.id)
    if only_unused:
        query = query.filter(EmailOTP.used_at.is_(None))
    return query.order_by(EmailOTP.id.desc()).first()


def active_otp(user):
    """Latest unused code that has not expired yet (or None)."""
    otp = latest_otp(user)
    if otp and otp.expires_at > utc_now():
        return otp
    return None


def seconds_left(otp):
    if otp is None:
        return 0
    return max(0, int((otp.expires_at - utc_now()).total_seconds()))


def seconds_until_resend(user):
    last = latest_otp(user, only_unused=False)
    if last is None:
        return 0
    wait = (last.created_at + timedelta(seconds=RESEND_SECONDS)) - utc_now()
    return max(0, int(wait.total_seconds()) + 1)


def issue_otp(user, to_address=None, purpose="verify"):
    """Create a new code (cancelling older ones) and deliver it.

    to_address: send to this address instead of user.email (used when a student
                changes to a new school email and must prove they own it).
    purpose:    "verify" (new account), "reset" (forgot password) or "change_email".
    """
    now = utc_now()
    EmailOTP.query.filter(
        EmailOTP.user_id == user.id, EmailOTP.used_at.is_(None)
    ).update({"used_at": now})

    code = f"{secrets.randbelow(10 ** 6):06d}"
    db.session.add(EmailOTP(
        user_id=user.id,
        otp_hash=generate_password_hash(code),
        expires_at=now + timedelta(minutes=expiry_minutes()),
    ))
    db.session.commit()

    deliver(user, code, to_address=to_address, purpose=purpose)
    return code


def ensure_active_otp(user):
    """Send a fresh code only if the user has no valid one."""
    if active_otp(user) is None:
        return issue_otp(user)
    return None


def check_code(user, code):
    """Return 'ok', 'invalid' or 'expired'."""
    otp = active_otp(user)
    if otp is None:
        return "expired"
    if not check_password_hash(otp.otp_hash, code):
        return "invalid"
    otp.used_at = utc_now()
    user.email_verified = True
    db.session.commit()
    return "ok"


# ---------------------------------------------------------------- delivery
def deliver(user, code, to_address=None, purpose="verify"):
    mode = delivery_mode()
    target = to_address or user.email
    if mode == "smtp":
        send_code_email(target, code, user.full_name, purpose=purpose)
    else:
        banner = "=" * 56
        print(f"\n{banner}\n  CampusVote DEV OTP ({purpose}) for {target}: {code}\n"
              f"  (OTP_DELIVERY=console - not for production)\n{banner}\n", flush=True)
        current_app.logger.info("DEV OTP (%s) for %s: %s", purpose, target, code)


def _mail_settings():
    server = _env("MAIL_SERVER", "MAIL_HOST")
    username = _env("MAIL_USERNAME")
    # Gmail shows app passwords as "abcd efgh ijkl mnop" - spaces are not part of it.
    password = _env("MAIL_PASSWORD").replace(" ", "")
    if not server or not username or not password:
        raise OTPDeliveryError(
            "Email is not configured. Set MAIL_SERVER, MAIL_USERNAME and "
            "MAIL_PASSWORD in your .env file."
        )
    use_ssl = _env("MAIL_USE_SSL", default="0") == "1"
    return {
        "server": server,
        "port": int(_env("MAIL_PORT", default="465" if use_ssl else "587")),
        "username": username,
        "password": password,
        "use_ssl": use_ssl,
        "use_tls": _env("MAIL_USE_TLS", default="1") == "1",
        "sender": _env("MAIL_FROM", default=username),
    }


_PURPOSE_TEXT = {
    # purpose: (subject suffix, short heading, sentence in the email, "ignore" sentence)
    "verify": ("is your CampusVote verification code", "Email verification",
               "use this code to verify your school email:",
               "If you did not create a CampusVote account, ignore this email."),
    "reset": ("is your CampusVote password reset code", "Password reset",
              "use this code to reset your CampusVote password:",
              "If you did not ask to reset your password, ignore this email. Your password has not changed."),
    "change_email": ("is your CampusVote email change code", "Confirm your new email",
                     "use this code to confirm this as your new school email:",
                     "If you did not ask to change your CampusVote email, ignore this email."),
}


def send_code_email(to_address, code, full_name=None, purpose="verify"):
    """Send the verification code to `to_address` by SMTP."""
    subject_tail, heading, sentence, ignore_line = _PURPOSE_TEXT.get(purpose, _PURPOSE_TEXT["verify"])
    cfg = _mail_settings()
    minutes = expiry_minutes()
    unit = "minute" if minutes == 1 else "minutes"
    name = (full_name or "student").split(" ")[0]

    msg = EmailMessage()
    msg["Subject"] = f"{code} {subject_tail}"
    msg["From"] = formataddr(("CampusVote", cfg["sender"].split("<")[-1].strip(" >")))
    msg["To"] = to_address
    msg.set_content(
        f"Hello {name},\n\n"
        f"Your CampusVote code is: {code}\n\n"
        f"It expires in {minutes} {unit}. Do not share this code with anyone.\n"
        f"{ignore_line}\n"
    )
    msg.add_alternative(f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:480px;margin:auto;padding:24px;
            background:#0f1e18;color:#e8f3ec;border-radius:14px">
  <h2 style="margin:0 0 4px;color:#f5a623">CampusVote</h2>
  <p style="margin:0 0 18px;color:#9db0a5">{heading}</p>
  <p>Hello {name}, {sentence}</p>
  <p style="font-size:34px;letter-spacing:10px;font-weight:700;text-align:center;
            background:#0b1410;border:1px solid #23382e;border-radius:12px;padding:16px;margin:18px 0">{code}</p>
  <p style="color:#f5a623">This code expires in {minutes} {unit}.</p>
  <p style="color:#9db0a5;font-size:13px">Do not share this code with anyone. {ignore_line}</p>
</div>""", subtype="html")

    try:
        if cfg["use_ssl"]:
            smtp = smtplib.SMTP_SSL(cfg["server"], cfg["port"], timeout=20,
                                    context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(cfg["server"], cfg["port"], timeout=20)
        with smtp:
            smtp.ehlo()
            if not cfg["use_ssl"] and cfg["use_tls"]:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            smtp.login(cfg["username"], cfg["password"])
            smtp.send_message(msg, from_addr=cfg["sender"].split("<")[-1].strip(" >"),
                              to_addrs=[to_address])
    except smtplib.SMTPAuthenticationError as exc:
        current_app.logger.error("SMTP login failed: %s", exc)
        raise OTPDeliveryError("The email server rejected the username/password.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        current_app.logger.error("SMTP send failed: %s", exc)
        raise OTPDeliveryError(str(exc)) from exc
