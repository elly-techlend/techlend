from flask import Blueprint, render_template, request, redirect, url_for, flash,current_app
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db, login_manager
from models import User, Company, Branch, Role, generate_reset_token
from forms import LoginForm, CSRFOnlyForm
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from flask import session
import os
from werkzeug.utils import secure_filename
from extensions import csrf
from email_utils import send_reset_email

auth_bp = Blueprint('auth', __name__)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

def verify_reset_token(token):
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        email = serializer.loads(token, salt='password-reset-salt', max_age=3600)
    except SignatureExpired:
        return None
    except BadSignature:
        return None
    return email

@auth_bp.route('/register-company', methods=['GET', 'POST'])
@login_required
def register_company():
    if not current_user.is_superuser:
        abort(403)

    csrf_form = CSRFOnlyForm()

    if csrf_form.validate_on_submit():
        name = request.form['name'].strip()
        email = request.form['email'].strip()
        phone = request.form.get('phone', '').strip()
        address = request.form.get('address', '').strip()
        logo = request.files.get('logo')

        if not name or not email:
            flash('Company name and email are required.', 'danger')
            return redirect(url_for('auth.register_company'))

        if Company.query.filter_by(name=name).first():
            flash('A company with this name already exists.', 'danger')
            return redirect(url_for('auth.register_company'))

        # ✅ Save logo if provided
        logo_url = None
        if logo and allowed_file(logo.filename):
            filename = secure_filename(logo.filename)
            upload_dir = os.path.join(current_app.root_path, current_app.config['UPLOAD_FOLDER'])
            os.makedirs(upload_dir, exist_ok=True)
            logo_path = os.path.join(upload_dir, filename)
            logo.save(logo_path)
            logo_url = f"/{current_app.config['UPLOAD_FOLDER']}/{filename}"
        elif logo:
            flash('Invalid logo file type.', 'danger')
            return redirect(request.url)

        # ✅ Create company
        company = Company(name=name, email=email, phone=phone, address=address, logo_url=logo_url)
        db.session.add(company)
        db.session.commit()

        # ✅ Create default branch
        default_branch = Branch(
            name="Main Branch",
            location=address,
            address=address,
            phone_number=phone,
            company_id=company.id
        )
        db.session.add(default_branch)
        db.session.commit()

        # ✅ Set active branch in session
        session['active_branch_id'] = default_branch.id
        session['active_branch_name'] = default_branch.name

        log_action(f"{current_user.full_name} registered company '{company.name}' with default branch '{default_branch.name}'")

        flash('Company registered successfully with a default branch set as active!', 'success')
        return redirect(url_for('borrowers.add_borrower'))

    elif request.method == 'POST':
        flash('CSRF token missing or invalid.', 'danger')
        return redirect(request.url)

    return render_template('admin/register_company.html', csrf_form=csrf_form)

@csrf.exempt
@auth_bp.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    superuser_exists = User.query.filter_by(is_superuser=True).first() is not None

    # Access control: only superuser or Admin can register users
    if not current_user.is_superuser and not current_user.has_role('Admin'):
        abort(403)

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        full_name = request.form.get('full_name', '').strip()
        role_name = request.form.get('role', 'Staff').strip()
        branch_id = request.form.get('branch_id')
        is_superuser = request.form.get('is_superuser') == 'on'

        # Company logic
        if current_user.is_superuser:
            company_id = request.form.get('company_id')
        else:
            company_id = str(current_user.company_id)  # Ensure it's a string

        # Validation: Check required fields
        required_fields = [username, email, password, confirm_password, full_name, branch_id]
        if current_user.is_superuser:
            required_fields.append(company_id)

        if not all(required_fields):
            flash('All fields are required.', 'danger')
            return redirect(url_for('auth.register'))

        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('auth.register'))

        if role_name.lower() == 'superuser':
            flash('Invalid role selection.', 'danger')
            return redirect(url_for('auth.register'))

        if is_superuser and not current_user.is_superuser:
            flash('Only a superuser can assign superuser access.', 'danger')
            return redirect(url_for('auth.register'))

        # Check for duplicate email
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('This email is already registered. Please use a different email.', 'danger')
            return redirect(url_for('auth.register'))

        # Get role object
        role = Role.query.filter_by(name=role_name).first()
        if not role:
            flash('Selected role does not exist in the system.', 'danger')
            return redirect(url_for('auth.register'))

        # Create new user
        new_user = User(
            username=username,
            email=email,
            full_name=full_name,
            company_id=company_id,
            branch_id=branch_id,
            is_superuser=is_superuser
        )
        new_user.set_password(password)
        new_user.roles.append(role)

        db.session.add(new_user)
        db.session.commit()

        flash('User registered successfully!', 'success')
        return redirect(url_for('auth.login'))

    # GET request
    companies = Company.query.all() if current_user.is_superuser else []
    roles = Role.query.all()

    # Branch logic
    if current_user.is_superuser:
        all_branches = Branch.query.all()
        branches_data = {}
        for company in companies:
            branches_data[company.id] = [
                {"id": branch.id, "name": branch.name}
                for branch in company.branches
            ]
        branches = []  # Will be filled by JS after company select
    else:
        branches = Branch.query.filter_by(company_id=current_user.company_id).all()
        branches_data = {
            current_user.company_id: [
                {"id": branch.id, "name": branch.name} for branch in branches
            ]
        }

    return render_template(
        'auth/register.html',
        companies=companies,
        roles=roles,
        branches=branches,
        branches_data=branches_data
    )

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        username_or_email = form.username_or_email.data.strip()
        password = form.password.data

        from sqlalchemy.orm import joinedload
        user = User.query.options(joinedload(User.roles), joinedload(User.company)).filter(
            (User.username == username_or_email) | (User.email == username_or_email)
        ).first()

        if user and user.is_active and check_password_hash(user.password_hash, password):
            # 🚫 Company suspension check
            if not user.is_superuser and user.company and not user.company.is_active:
                flash("Your company account has been suspended. Please contact support.", "danger")
                return redirect(url_for('auth.login'))

            login_user(user)

            # ✅ Branch session assignment logic
            if not user.is_superuser:
                if user.branch_id:
                    # Use user's assigned branch
                    session['active_branch_id'] = user.branch_id
                    session['active_branch_name'] = user.branch.name
                else:
                    # Auto-assign if company has only one branch
                    branches = Branch.query.filter_by(company_id=user.company_id).all()
                    if len(branches) == 1:
                        session['active_branch_id'] = branches[0].id
                        session['active_branch_name'] = branches[0].name
                    else:
                        session['active_branch_id'] = None
                        session['active_branch_name'] = None
            else:
                session['active_branch_id'] = None
                session['active_branch_name'] = None

            flash('Logged in successfully.', 'success')
            next_page = request.args.get('next')
            return redirect(next_page) if next_page and is_safe_url(next_page) else redirect(url_for('dashboard.index'))
        else:
            flash('Invalid username or password.', 'danger')

    return render_template('auth/login.html', form=form)

@login_required
def after_login_redirect():
    # Get roles as names
    role_names = [r.name for r in current_user.roles]

    if 'admin' in role_names:
        if current_user.default_branch_id:
            session['active_branch_id'] = current_user.default_branch_id
        else:
            # Auto-pick branch if company has only one
            branches = Branch.query.filter_by(company_id=current_user.company_id).all()
            if len(branches) == 1:
                session['active_branch_id'] = branches[0].id
    elif any(role in role_names for role in ['branch_manager', 'loans_officer', 'cashier', 'accountant']):
        session['active_branch_id'] = current_user.branch_id

    return redirect(url_for('dashboard'))

@csrf.exempt
@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email').strip()
        user = User.query.filter_by(email=email).first()
        if user:
            token = generate_reset_token(email)

            # ✅ Use the unified send_reset_email
            send_reset_email(user.email, token)

        flash(
            'If an account with that email exists, a reset link has been sent.',
            'info'
        )
        return redirect(url_for('auth.login'))

    return render_template('auth/forgot_password.html')

@csrf.exempt
@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password_token(token):
    email = verify_reset_token(token)
    if not email:
        flash('The password reset link is invalid or has expired.', 'danger')
        return redirect(url_for('auth.forgot_password'))

    user = User.query.filter_by(email=email).first_or_404()

    if request.method == 'POST':
        new_password = request.form.get('new_password')
        if not new_password:
            flash('Password is required.', 'warning')
            return redirect(request.url)

        user.set_password(new_password)  # Make sure your User model has this method
        db.session.commit()
        flash('Password reset successfully. You can now log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/reset_password.html', token=token)

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('auth.login'))
