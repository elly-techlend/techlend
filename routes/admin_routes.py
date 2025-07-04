from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import User, Role, Company, Branch, Loan, CompanyLog
from flask import flash
from flask import url_for
from werkzeug.security import generate_password_hash
from sqlalchemy.orm import joinedload
from models import db, SystemLog
from utils.decorators import superuser_required, roles_required, admin_or_superuser_required
from sqlalchemy import func
from flask import session
from utils.logging import log_company_action, log_system_action
from extensions import csrf

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def set_password(self, password):
    self.password_hash = generate_password_hash(password)

# Superuser panel to view all companies
@admin_bp.route('/admin/panel')
@login_required
@roles_required('superuser')
def admin_panel():
    from utils.decorators import superuser_required
    from app import db 
    companies = Company.query.all()
    company_data = []

    for company in companies:
        loans = Loan.query.filter_by(company_id=company.id).all()
        total_loans = len(loans)
        total_amount = sum(loan.amount_borrowed for loan in loans)
        company_data.append({
            'id': company.id,
            'name': company.name,
            'email': company.email,
            'created_at': company.created_at,
            'is_active': company.is_active,
        })

    return render_template('admin/company_table_page.html', companies=company_data)

@admin_bp.route('/register-admin', methods=['GET', 'POST'])
@superuser_required
def register_admin():
    from app import db 
    from forms import AdminRegistrationForm
    form = AdminRegistrationForm()
    form.set_company_choices()

    if form.validate_on_submit():
        email = form.email.data.lower()

        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger')
            return render_template('admin/register_admin.html', form=form)

        admin_role = Role.query.filter(func.lower(Role.name) == 'admin').first()
        if not admin_role:
            flash("Admin role not found. Please create it in the system.", "danger")
            return render_template('admin/register_admin.html', form=form)

        new_admin = User(
            full_name=form.full_name.data,
            email=email,
            username=email.split('@')[0],
            password_hash=generate_password_hash(form.password.data),
            is_active=True,
            is_superuser=False,
            company_id=form.company.data
        )
        new_admin.roles = [admin_role]  # Assign the admin role only

        db.session.add(new_admin)
        db.session.commit()

        flash('Admin registered successfully.', 'success')
        return redirect(url_for('admin.superuser_dashboard'))

    return render_template('admin/register_admin.html', form=form)

# admin_routes.py or core_routes.py

@admin_bp.route('/set_active_branch', methods=['POST'])
@login_required
def set_active_branch():
    branch_id = request.form.get('branch_id')
    if branch_id:
        branch = Branch.query.filter_by(id=branch_id, company_id=current_user.company_id).first()
        if branch:
            session['active_branch_id'] = branch.id
    return redirect(request.referrer or url_for('dashboard'))

@admin_bp.route('/manage-staff')
@login_required
@admin_or_superuser_required
def manage_staff():
    from models import User, Branch

    if current_user.is_superuser:
        # Superuser sees everything
        staff = User.query.filter_by(is_superuser=False).all()
        branches = Branch.query.all()
    else:
        # Admin sees only their company's staff and branches
        staff = User.query.filter_by(
            company_id=current_user.company_id,
            is_superuser=False
        ).all()
        branches = Branch.query.filter_by(company_id=current_user.company_id).all()

    return render_template('admin/manage_staff.html', staff=staff, branches=branches)

@csrf.exempt
@admin_bp.route('/staff/<int:user_id>/move', methods=['GET', 'POST'])
@login_required
@admin_or_superuser_required
def move_staff(user_id):
    from app import db
    from models import User, Branch

    user = User.query.get_or_404(user_id)

    # ❗ Restrict access: Admins can only move staff within their own company
    if not current_user.is_superuser and user.company_id != current_user.company_id:
        abort(403)

    # Load branches within the correct company (based on the user being moved)
    branches = Branch.query.filter_by(company_id=user.company_id).all()

    if request.method == 'POST':
        new_branch_id = request.form.get('branch_id')

        if new_branch_id and new_branch_id != str(user.branch_id):
            user.branch_id = int(new_branch_id)
            db.session.commit()
            flash('Staff successfully moved to new branch.', 'success')
        else:
            flash('No branch change detected.', 'info')

        return redirect(url_for('admin.manage_staff'))

    return render_template('admin/move_staff.html', user=user, branches=branches)

@csrf.exempt
@admin_bp.route('/delete-staff/<int:user_id>', methods=['POST'])
@login_required
@admin_or_superuser_required
def delete_staff(user_id):
    from app import db 
    user = User.query.get_or_404(user_id)

    db.session.delete(user)
    db.session.commit()

    flash(f"Staff member {user.username} has been permanently deleted.", "success")
    return redirect(url_for('admin.manage_staff'))

@admin_bp.route('/view-companies')
@login_required
@superuser_required
def view_companies():
    from models import Company
    
    page = request.args.get('page', 1, type=int)
    pagination = Company.query.order_by(Company.name).paginate(page=page, per_page=20)
    companies = pagination.items
    
    return render_template('admin/company_table_page.html', companies=companies, pagination=pagination)

@admin_bp.route('/company/<int:company_id>')
@login_required
@superuser_required
def view_company_details(company_id):
    from models import Company, Branch, User

    company = Company.query.get_or_404(company_id)
    total_branches = Branch.query.filter_by(company_id=company.id).count()
    admin_user = User.query.filter_by(company_id=company.id, is_admin=True).first()

    return render_template('admin/view_company_details.html',
                           company=company,
                           total_branches=total_branches,
                           admin_user=admin_user)

@csrf.exempt
@admin_bp.route('/system-logs')
@login_required
@superuser_required
def system_logs():
    logs = SystemLog.query.order_by(SystemLog.created_at.desc()).limit(100).all()
    return render_template('superuser/system_logs.html', logs=logs)

@csrf.exempt
@admin_bp.route('/system-logs/clear', methods=['POST'])
@login_required
@superuser_required
def clear_system_logs():
    SystemLog.query.delete()
    db.session.commit()
    flash('System logs cleared.', 'success')
    return redirect(url_for('admin.system_logs'))

@admin_bp.route('/super-settings')
@login_required
@superuser_required
def super_settings():
    # Some critical config settings or toggles
    return render_template('admin/super_settings.html')

# List all companies
@admin_bp.route('/dashboard')
@login_required
@superuser_required
def superuser_dashboard():
    companies = Company.query.order_by(Company.created_at.desc()).all()    
    company_data = []
    for company in companies:
        loans = Loan.query.filter_by(company_id=company.id).all()
        total_loans = len(loans)
        total_amount = sum(loan.amount_borrowed for loan in loans)

        company_data.append({
            'id': company.id,
            'name': company.name,
            'email': company.email,
            'is_active': company.is_active,
            'created_at': company.created_at,
            'total_loans': total_loans,
            'total_amount': total_amount,
        })

    page = request.args.get('page', 1, type=int)
    pagination = Company.query.paginate(page=page, per_page=10)
    company_data = pagination.items

    return render_template('admin/company_table_page.html', companies=company_data, pagination=pagination)


# View a single company
@admin_bp.route('/company/<int:company_id>/view')
@login_required
@superuser_required
def view_company(company_id):
    from models import Company, Branch
    company = Company.query.get_or_404(company_id)

    # Count branches for this company
    total_branches = Branch.query.filter_by(company_id=company.id).count()

    # Get the first admin user for this company
    admin_user = (
        User.query
        .options(joinedload(User.roles))
        .filter(User.company_id == company.id)
        .filter(User.roles.any(Role.name == 'admin'))
        .first()
    )

    return render_template(
        'admin/view_company_details.html',
        company=company,
        total_branches=total_branches,
        admin_user=admin_user
    )

# Edit company - page (you’ll create the template)
@csrf.exempt
@admin_bp.route('/admin/company/<int:company_id>/edit', methods=['GET', 'POST'])
@login_required
@superuser_required
def edit_company(company_id):
    from forms import CompanyForm
    company = Company.query.get_or_404(company_id)
    form = CompanyForm(obj=company)

    if form.validate_on_submit():
        company.name = form.name.data
        company.email = form.email.data
        company.is_active = form.is_active.data
        db.session.commit()
        flash('Company updated successfully!', 'success')
        return redirect(url_for('admin.superuser_dashboard'))

    return render_template('admin/edit_company.html', form=form, company=company)

# Suspend company
@csrf.exempt
@admin_bp.route('/company/<int:company_id>/suspend')
@login_required
@superuser_required
def suspend_company(company_id):
    company = Company.query.get_or_404(company_id)
    company.is_active = False
    db.session.commit()
    flash(f"{company.name} has been suspended.", "warning")
    return redirect(url_for('admin.view_company_details', company_id=company.id))  # Use actual route function name

# Activate company
@csrf.exempt
@admin_bp.route('/company/<int:company_id>/activate')
@login_required
@superuser_required
def activate_company(company_id):
    company = Company.query.get_or_404(company_id)
    company.is_active = True
    db.session.commit()
    flash(f"{company.name} is now active.", "success")
    return redirect(url_for('admin.view_company_details', company_id=company.id))

# Deactivate company (same as suspend - can be merged)
@csrf.exempt
@admin_bp.route('/company/<int:company_id>/deactivate')
@login_required
@superuser_required
def deactivate_company(company_id):
    company = Company.query.get_or_404(company_id)
    company.is_active = False
    db.session.commit()
    flash(f"{company.name} has been suspended.", "warning")
    return redirect(url_for('admin.view_company_details', company_id=company.id))

# Delete company
@csrf.exempt
@admin_bp.route('/delete-company/<int:company_id>', methods=['GET', 'POST'])
@login_required
@superuser_required
def delete_company(company_id):
    company = Company.query.get_or_404(company_id)

    if request.method == 'POST':
        password = request.form.get('password')

        # ✅ Ensure superuser password is verified
        if not current_user.check_password(password):
            flash("Incorrect password. Deletion aborted.", "danger")
            return redirect(url_for('admin.delete_company', company_id=company.id))

        try:
            # ✅ Optional: use cascade delete if already defined in relationships
            User.query.filter_by(company_id=company.id).delete()
            Branch.query.filter_by(company_id=company.id).delete()

            db.session.delete(company)
            db.session.commit()

            flash("Company and all related data deleted.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"An error occurred during deletion: {str(e)}", "danger")

        return redirect(url_for('admin.view_companies'))  # 👈 Ensure this route exists

    return render_template('admin/confirm_delete_company.html', company=company)


@admin_bp.route('/notifications')
@login_required
@roles_required('Admin')
def company_notifications():
    branch_id = session.get('active_branch_id')
    
    logs_query = CompanyLog.query.filter_by(company_id=current_user.company_id)

    if branch_id:
        logs_query = logs_query.filter_by(branch_id=branch_id)

    logs = logs_query.order_by(CompanyLog.created_at.desc()).all()

    return render_template('admin/company_notifications.html', logs=logs)

@csrf.exempt
@admin_bp.route('/notifications/clear', methods=['POST'])
@login_required
@roles_required('Admin')
def clear_company_notifications():
    logs = CompanyLog.query.filter_by(company_id=current_user.company_id).all()
    count = len(logs)
    
    CompanyLog.query.filter_by(company_id=current_user.company_id).delete()
    db.session.commit()
    
    flash('Company notifications cleared.', 'success')
    print(f"Company ID: {current_user.company_id} - Logs found: {count}")
    
    return redirect(url_for('admin.company_notifications'))
