# utils/decorators.py
from functools import wraps
from flask import redirect, url_for, flash
from flask_login import current_user

# ✅ Superuser only
def superuser_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_superuser:
            flash("Access denied: Superuser only.", "danger")
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

# ✅ Role-based access (for systems with multiple roles per user)
def roles_required(*required_roles):
    normalized_required = [r.replace('_', ' ').lower() for r in required_roles]

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                flash("Please log in to continue.", "warning")
                return redirect(url_for('auth.login'))

            user_roles = [r.name.lower() for r in current_user.roles]
            if not current_user.is_superuser and not any(r in user_roles for r in normalized_required):
                flash("Access denied: You do not have the required role.", "danger")
                return redirect(url_for('dashboard.index'))

            return func(*args, **kwargs)
        return wrapper
    return decorator

# ✅ Admin or Superuser access
def admin_or_superuser_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Please log in to continue.", "warning")
            return redirect(url_for('auth.login'))

        user_roles = [role.name.lower() for role in current_user.roles]

        if not (current_user.is_superuser or 'admin' in user_roles):
            flash("Access denied: Admins or Superuser only.", "danger")
            return redirect(url_for('dashboard.index'))

        return func(*args, **kwargs)
    return wrapper
