"""Admin: show the platform image an applicant uploaded."""
from flask import abort, send_file

from app.admin import admin_bp
from app.extensions import db
from app.main import platform_image
from app.models import Candidate


@admin_bp.route("/applications/<int:candidate_id>/platform-image")
def application_platform_image(candidate_id):
    candidate = db.session.get(Candidate, candidate_id) or abort(404)
    path = platform_image.resolve(candidate.poster_path)
    if path is None:
        abort(404)
    return send_file(path, mimetype=platform_image.mimetype_for(path.name), max_age=60)
