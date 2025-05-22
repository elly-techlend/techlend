# techlend/utils.py
from datetime import datetime
from functools import wraps
from flask_login import current_user
from flask import abort
from extensions import db

def log_activity(user_id, action):
    """Logs user activity in the database."""
    log = ActivityLog(user_id=user_id, action=action, timestamp=datetime.utcnow())
    db.session.add(log)
    db.session.commit()

def send_notification(user_id, message):
    """Saves a notification for a user."""
    notification = Notification(user_id=user_id, message=message, timestamp=datetime.utcnow())
    db.session.add(notification)
    db.session.commit()

def superuser_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("You need to be logged in first.", "danger")
            return redirect(url_for('auth.login'))  # Adjust according to your login route

        if not current_user.is_superuser:
            flash("You need superuser access to perform this action.", "danger")
            return redirect(url_for('admin.dashboard'))  # Redirect to an appropriate page for non-superusers
        
        return f(*args, **kwargs)

    return decorated_function

def role_required(role_name):
    @wraps
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role != role_name:
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def get_company_filter(model):
    """Returns a query filter based on the user's company or superuser access."""
    if current_user.is_superuser:
        return model.query  # Superuser sees all
    return model.query.filter_by(company_id=current_user.company_id)
