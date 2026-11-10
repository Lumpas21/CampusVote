from flask import Flask

from config import Config
from app.extensions import db, migrate, login_manager, csrf


def create_app():
    app = Flask(__name__, instance_relative_config=True)

    app.config.from_object(Config)

    # Initialize Flask extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Login settings
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."
    login_manager.login_message_category = "warning"

    # Import models so Flask-Migrate can detect database tables.
    from app import models

    # Register CLI commands
    from app.commands import register_commands
    register_commands(app)

    # Register blueprints (pages)
    from app.auth import auth_bp
    from app.admin import admin_bp
    from app.main import main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(main_bp)

    # Template helpers (times are stored in UTC, shown in local time)
    from app.timeutil import local_date, local_dt, local_input
    from app.election_utils import election_phase, phase_label
    app.add_template_filter(local_dt, "local_dt")
    app.add_template_filter(local_date, "local_date")
    app.add_template_filter(local_input, "local_input")
    app.jinja_env.globals["election_phase"] = election_phase
    app.jinja_env.globals["phase_label"] = phase_label

    return app