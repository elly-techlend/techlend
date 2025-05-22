from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate  # Import Flask-Migrate
from flask_wtf import CSRFProtect
# extensions.py
from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect()

# Shared extension instances

# Database
db = SQLAlchemy()

# Login manager
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
csrf = CSRFProtect()

# Flask-Migrate
migrate = Migrate()  # Initialize Migrate instance
