"""Admin: sign out and go back to the public home page."""
from flask import flash, redirect, url_for
from flask_login import current_user, login_required, logout_user

from app.admin import admin_bp
from app.auth.services import record_audit
from app.extensions import db


@admin_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    record_audit("logout", actor_id=current_user.id, entity_type="user",
                 entity_id=current_user.id, details="admin")
    db.session.commit()
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("main.index"))
