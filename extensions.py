from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

# Database
db = SQLAlchemy()

# Database migrations
migrate = Migrate()

# User authentication
login_manager = LoginManager()

# CSRF protection
csrf = CSRFProtect()