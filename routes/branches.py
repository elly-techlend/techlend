from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from extensions import db
from models import Branch, Borrower
from datetime import datetime
from flask import abort
from utils.decorators import superuser_required, roles_required, admin_or_superuser_required
from utils.logging import log_company_action, log_system_action, log_action
from extensions import csrf
from flask import session

branches = Blueprint('branches', __name__)

@csrf.exempt
@branches.route('/add', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'Superuser')
def add_branch():
    # ✅ Only allow Admins or Superusers to access this
    if not current_user.is_admin:
        abort(403)

    if request.method == 'POST':
        name = request.form.get('name')
        location = request.form.get('location')
        address = request.form.get('address')
        phone_number = request.form.get('phone_number')

        # ✅ Validate input
        if not name:
            flash('Branch name is required.', 'danger')
            return render_template('branches/add_branch.html')

        # ✅ Check for duplicate branch name within the same company
        existing_branch = Branch.query.filter_by(name=name, company_id=current_user.company.id).first()
        if existing_branch:
            flash('A branch with this name already exists for your company.', 'danger')
            return render_template('branches/add_branch.html')

        # ✅ Create and save new branch
        new_branch = Branch(
            name=name,
            location=location,
            address=address,
            phone_number=phone_number,
            company_id=current_user.company.id
        )

        db.session.add(new_branch)
        db.session.commit()

        # ✅ SET the active branch in session
        session['active_branch_id'] = new_branch.id
        session['active_branch_name'] = new_branch.name

        # ✅ Fix typo in log
        log_action(f"{current_user.full_name} added a new branch: {new_branch.name}")

        flash('Branch added successfully!', 'success')
        return redirect(url_for('dashboard.index'))

    return render_template('branches/add_branch.html')

@branches.route('/list')
@login_required
@roles_required('Admin', 'Accountant', 'Loans_Supervisor')
def list_branches():

    branches_list = Branch.query.filter_by(
        company_id=current_user.company_id
    ).filter(Branch.deleted_at.is_(None)).all()

    return render_template('branches/list_branches.html', branches=branches_list)

@branches.route('/update/<int:branch_id>', methods=['GET', 'POST'])
@login_required
@roles_required('Admin')
def update_branch(branch_id):
    if not current_user.is_admin:
        abort(403)

    # Only get non-deleted branches for this company
    branch = Branch.query.filter_by(
        id=branch_id,
        company_id=current_user.company_id
    ).filter(Branch.deleted_at.is_(None)).first_or_404()

    if request.method == 'POST':
        name = request.form.get('name')
        location = request.form.get('location')
        address = request.form.get('address')
        phone_number = request.form.get('phone_number')

        if not name:
            flash('Branch name is required.', 'danger')
            return render_template('branches/update_branch.html', branch=branch)

        if not all([location, address, phone_number]):
            flash('All fields are required.', 'danger')
            return render_template('branches/update_branch.html', branch=branch)

        existing_branch = Branch.query.filter_by(
            name=name,
            company_id=current_user.company_id
        ).filter(Branch.id != branch.id, Branch.deleted_at.is_(None)).first()

        if existing_branch:
            flash('A branch with this name already exists.', 'danger')
            return render_template('branches/update_branch.html', branch=branch)

        # Save updates
        branch.name = name
        branch.location = location
        branch.address = address
        branch.phone_number = phone_number

        db.session.commit()
        flash('Branch updated successfully.', 'success')
        return redirect(url_for('branches.list_branches'))

    return render_template('branches/update_branch.html', branch=branch)

@csrf.exempt
@branches.route('/delete/<int:branch_id>', methods=['POST'])
@login_required
@roles_required('Admin')
def delete_branch(branch_id):
    if not current_user.is_admin:
        abort(403)

    branch = Branch.query.get_or_404(branch_id)

    if branch.company_id != current_user.company.id or branch.deleted_at:
        flash('Unauthorized operation.', 'danger')
        return redirect(url_for('branches.list_branches'))

    branch.deleted_at = datetime.utcnow()
    db.session.commit()
    log_action(f"{current_user.full_name} deleted branch: {branch.name}")

    flash('Branch deleted successfully!', 'success')
    return redirect(url_for('branches.list_branches'))

@branches.route('/view/<int:branch_id>')
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans_Supervisor')
def view_branch(branch_id):
    if not current_user.is_admin:
        abort(403)

    branch = Branch.query.get_or_404(branch_id)

    if branch.company_id != current_user.company_id or branch.deleted_at:
        flash('You do not have permission to view this branch.', 'danger')
        return redirect(url_for('branches.list_branches'))

    return render_template('branches/view_branch.html', branch=branch)

@branches.route('/toggle-status/<int:branch_id>', methods=['POST'])
@login_required
@roles_required('Admin')
def toggle_branch_status(branch_id):
    if not current_user.is_admin:
        abort(403)

    # Ensure we only fetch non-deleted branches for this company
    branch = Branch.query.filter_by(
        id=branch_id,
        company_id=current_user.company_id
    ).filter(Branch.deleted_at.is_(None)).first_or_404()

    branch.is_active = not branch.is_active
    db.session.commit()

    status = 'activated' if branch.is_active else 'deactivated'
    flash(f"Branch {status} successfully.", 'info')

    return redirect(url_for('branches.list_branches'))
