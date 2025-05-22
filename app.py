from dotenv import load_dotenv
import os

load_dotenv()

from flask import Flask, redirect, url_for, render_template, request
from extensions import db, login_manager, csrf
from config import Config
from flask_migrate import Migrate
from sqlalchemy import event
from sqlalchemy.engine import Engine
from flask_login import current_user
from werkzeug.security import generate_password_hash
import click
from flask.cli import with_appcontext
from itsdangerous import URLSafeTimedSerializer

# Initialize migrate object
migrate = Migrate()

# Role-based Access Control (RBAC) decorator
def role_required(role):
    def decorator(func):
        def wrapper(*args, **kwargs):
            if current_user.role != role and not current_user.is_superuser:
                return redirect(url_for('dashboard.index'))  # Redirect non-admins
            return func(*args, **kwargs)
        wrapper.__name__ = func.__name__
        return wrapper
    return decorator

def create_app():
    app = Flask(__name__, static_url_path='/static', static_folder='static')
    app.config.from_object(Config)

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)

    csrf.init_app(app)
    # Load user model
    from models import User
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # ✅ Inject csrf_form into all templates
    from forms import CSRFOnlyForm
    @app.context_processor
    def inject_csrf_form():
        return {'csrf_form': CSRFOnlyForm()}

    # Register blueprints
    from routes.auth_routes import auth_bp 
    from routes.loan_routes import loan_bp
    from routes.dashboard_routes import dashboard_bp
    from routes.admin_routes import admin_bp
    from routes.public_routes import public_bp
    from routes.borrower_routes import borrower_bp 
    from routes.repayment_routes import repayment_bp
    from routes.saving_routes import savings_blueprint
    from routes.branches import branches  # Import blueprint here
    from routes.cashbook_routes import cashbook_bp
    from routes.settings_routes import settings_bp
    from routes.expenses_routes import expenses_bp
    from routes.collateral_routes import collateral_bp
    from routes.other_income_routes import other_income_bp
    from routes.bank_routes import bank_bp
    from routes.cashflow_routes import cashflow_bp

    # Register blueprints in the app
    app.register_blueprint(auth_bp)
    app.register_blueprint(loan_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(borrower_bp)
    app.register_blueprint(repayment_bp)
    app.register_blueprint(savings_blueprint, url_prefix='/savings')
    app.register_blueprint(cashbook_bp, url_prefix='/cashbook')
    app.register_blueprint(branches, url_prefix='/branches')
    app.register_blueprint(settings_bp)
    app.register_blueprint(expenses_bp)
    app.register_blueprint(collateral_bp)
    app.register_blueprint(other_income_bp)
    app.register_blueprint(bank_bp, url_prefix='/bank')
    app.register_blueprint(cashflow_bp)

    # Redirect users to dashboard if logged in
    @app.before_request
    def ensure_redirect_for_logged_in_users():
        if current_user.is_authenticated:
            if request.endpoint == 'public.index':
                return redirect(url_for('dashboard.index'))

    @app.before_request
    def ensure_tenant_access():
        if request.path.startswith('/static'):
            return
    def generate_reset_token(email):
        s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        return s.dumps(email, salt='password-reset-salt')

    def verify_reset_token(token, expiration=3600):
        s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
        try:
           email = s.loads(token, salt='password-reset-salt', max_age=expiration)
        except Exception:
            return None
        return email

        public_paths = ['/', '/landing', '/login', '/register']
        if request.path in public_paths:
            return

        if current_user.is_authenticated:
            if current_user.is_superuser or getattr(current_user, 'company_id', None):
                return
            return redirect(url_for('auth.login'))

        return redirect(url_for('auth.login'))

    @app.template_filter('currency')
    def currency_format(value):
        try:
            return "{:,.0f}".format(value)
        except (ValueError, TypeError):
            return value

    app.role_required = role_required

    # Register the CLI command
    app.cli.add_command(create_superuser)
    app.cli.add_command(seed_roles)

    return app


# CLI Command to create a superuser
@click.command("create-superuser")
@with_appcontext
def create_superuser():
    from models import User  # ✅ Import inside the function
    from extensions import db  # ✅ Also import db here

    username = "superuser"
    email = "super@techlend.com"
    # Strong password example: 16 chars, mix of upper, lower, digits, special
    password = "S3cur3P@ssw0rd!2025"

    if User.query.filter_by(email=email).first():
        click.echo("Superuser already exists.")
        return

    user = User(
        username=username,
        email=email,
        full_name="Super Admin",
        password_hash=generate_password_hash(password),
        is_superuser=True,
        is_active=True,
        company_id=1  # Change if necessary
    )
    db.session.add(user)
    db.session.commit()
    click.echo(f"✅ Superuser created successfully! Email: {email}, Password: {password}")

# CLI Command to seed default roles
@click.command("seed-roles")
@with_appcontext
def seed_roles():
    from models import Role
    from extensions import db

    default_roles = [
        "Admin",
        "Loans Supervisor",
        "Branch Manager",
        "Loans Officer",
        "Cashier",
        "Accountant"
    ]

    created_count = 0
    for role_name in default_roles:
        if not Role.query.filter_by(name=role_name).first():
            role = Role(name=role_name)
            db.session.add(role)
            created_count += 1

    db.session.commit()
    click.echo(f"✅ Seeded {created_count} new role(s).")

# Ensure app is available to Flask CLI
app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
