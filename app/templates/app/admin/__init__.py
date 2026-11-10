from flask import Blueprint

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

from app.admin import routes  # noqa: E402,F401
from app.admin import (  # noqa: E402,F401
    views_applications, views_audit, views_elections, views_platform, views_results, views_session, views_settings, views_voters,
)
