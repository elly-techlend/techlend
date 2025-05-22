from flask import Blueprint, render_template, make_response, abort, url_for, request, flash, redirect, Response
from extensions import db
from flask_login import login_required, current_user
from utils.decorators import roles_required
from flask import session
from utils.logging import log_company_action, log_system_action, log_action
from xhtml2pdf import pisa
import io
import uuid
from models import Borrower, SavingAccount, Loan, LoanRepayment, Branch  # assuming these models exist
from werkzeug.utils import secure_filename
import os, random, string
from forms import AddBorrowerForm  # Your form
from sqlalchemy import or_, extract
from datetime import datetime
borrower_bp = Blueprint('borrowers', __name__)
from flask import request
from sqlalchemy import or_, extract

@borrower_bp.route('/borrowers')
@login_required
@roles_required('Superuser', 'Admin', 'Accountant', 'Branch_Manager', 'Loans_Supervisor')
def view_borrowers():
    branch_id = session.get('active_branch_id')
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '').strip()
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)

    borrowers_query = Borrower.query.filter_by(company_id=current_user.company_id)

    if branch_id:
        borrowers_query = borrowers_query.filter_by(branch_id=branch_id)

    # Search by name or phone
    if search:
        borrowers_query = borrowers_query.filter(
            or_(
                Borrower.name.ilike(f"%{search}%"),
                Borrower.phone.ilike(f"%{search}%")
            )
        )

    # Filter by year and month
    if year:
        borrowers_query = borrowers_query.filter(extract('year', Borrower.created_at) == year)
    if month:
        borrowers_query = borrowers_query.filter(extract('month', Borrower.created_at) == month)

    pagination = borrowers_query.order_by(Borrower.created_at.desc()).paginate(page=page, per_page=10)
    borrowers = pagination.items

    # Preload calculated properties
    for borrower in borrowers:
        _ = borrower.total_paid
        _ = borrower.open_balance

    return render_template('borrowers/view_borrowers.html', borrowers=borrowers, pagination=pagination)

@borrower_bp.route('/borrowers/add', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'Branch_manager', 'Loans_Supervisor')
def add_borrower():
    branch_id = session.get('active_branch_id')  # 👈 Get active branch from session

    if not branch_id:
        flash("No active branch selected. Please select a branch first.", "warning")
        return redirect(url_for('dashboard.index'))

    # Generate unique borrower number
    borrower_number = f"{random.randint(100000, 999999)}{random.choice(string.ascii_uppercase)}"

    form = AddBorrowerForm()
    form.branch_id.data = branch_id  # 👈 Set the active branch as hidden input

    if form.validate_on_submit():
        # Handle photo upload
        photo_filename = None
        if form.photo.data:
            photo_file = form.photo.data
            photo_filename = secure_filename(photo_file.filename)
            photo_path = os.path.join('static/uploads', photo_filename)
            photo_file.save(photo_path)

        # Combine title and name
        title = form.title.data or ''
        raw_name = form.name.data.strip()
        display_name = f"{title} {raw_name}".strip()

        new_borrower = Borrower(
            borrower_id=borrower_number,
            name=display_name,
            title=title,
            gender=form.gender.data,
            date_of_birth=form.date_of_birth.data,
            registration_date=form.registration_date.data,
            place_of_birth=form.place_of_birth.data,
            email=form.email.data,
            phone=form.phone.data,
            address=form.address.data,
            marital_status=form.marital_status.data,
            spouse_name=form.spouse_name.data,
            number_of_children=form.number_of_children.data,
            education=form.education_level.data,
            branch_id=branch_id,  # 👈 Use the active branch directly
            next_of_kin=form.next_of_kin.data,
            photo=photo_filename,
            company_id=current_user.company_id
        )

        db.session.add(new_borrower)
        db.session.commit()

        account_number = f"SAV-{new_borrower.id:05d}"
        existing_account = SavingAccount.query.filter_by(borrower_id=new_borrower.id).first()

        if not existing_account:
            savings_account = SavingAccount(
                borrower_id=new_borrower.id,
                company_id=new_borrower.company_id,
                branch_id=branch_id,  # ✅ Use the same branch
                account_number=account_number,
                balance=0.0,
                date_opened=datetime.utcnow()
            )
            db.session.add(savings_account)
            db.session.commit()

        log_action(f"{current_user.full_name} added a new borrower: {new_borrower.name}")
        flash('Borrower added successfully!', 'success')
        return redirect(url_for('borrowers.view_borrowers'))

    # Display active branch name
    active_branch = Branch.query.get(branch_id)
    active_branch_name = active_branch.name if active_branch else 'N/A'

    return render_template(
        'borrowers/add_borrower.html',
        form=form,
        borrower_number=borrower_number,
        active_branch_name=active_branch_name
    )

@borrower_bp.route('/borrowers/<int:borrower_id>')
@login_required
@roles_required('Superuser', 'Admin', 'Accountant', 'Branch_Manager', 'Loans_Supervisor')
def borrower_details(borrower_id):
    branch_id = session.get('active_branch_id')

    # Base query scoped by company and optionally branch
    borrower_query = Borrower.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        borrower_query = borrower_query.filter_by(branch_id=branch_id)

    borrower = borrower_query.filter_by(id=borrower_id).first_or_404()

    # Fetch related loans and savings accounts
    loans = Loan.query.filter_by(borrower_id=borrower.id, company_id=current_user.company_id)
    if branch_id:
        loans = loans.filter_by(branch_id=branch_id)
    loans = loans.order_by(Loan.date.desc()).all()

    savings_accounts = SavingAccount.query.filter_by(borrower_id=borrower.id, company_id=current_user.company_id)
    if branch_id:
        savings_accounts = savings_accounts.filter_by(branch_id=branch_id)
    savings_accounts = savings_accounts.all()

    return render_template(
        'borrowers/borrower_profile.html',
        borrower=borrower,
        loans=loans,
        savings_accounts=savings_accounts
    )

@borrower_bp.route('/borrowers/<int:borrower_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans_Supervisor')
def edit_borrower(borrower_id):
    borrower = Borrower.query.get_or_404(borrower_id)
    form = AddBorrowerForm(obj=borrower)

    # Populate branch choices
    form.branch_id.choices = [(b.id, b.name) for b in Branch.query.all()]

    if form.validate_on_submit():
        title = form.title.data or ''
        raw_name = form.name.data.strip()
        borrower.name = f"{title} {raw_name}".strip()
        borrower.title = title
        borrower.gender = form.gender.data
        borrower.date_of_birth = form.date_of_birth.data
        borrower.registration_date = form.registration_date.data
        borrower.place_of_birth = form.place_of_birth.data

        # Photo upload
        if form.photo.data:
            from flask import current_app
            import os
            from werkzeug.utils import secure_filename

            photo_file = form.photo.data
            photo_filename = secure_filename(photo_file.filename)
            upload_folder = os.path.join(current_app.root_path, 'static', 'uploads')
            os.makedirs(upload_folder, exist_ok=True)
            photo_path = os.path.join(upload_folder, photo_filename)
            photo_file.save(photo_path)
            borrower.photo = photo_filename

        borrower.email = form.email.data
        borrower.phone = form.phone.data
        borrower.address = form.address.data
        borrower.marital_status = form.marital_status.data
        borrower.spouse_name = form.spouse_name.data
        borrower.number_of_children = form.number_of_children.data
        borrower.education = form.education_level.data
        borrower.occupation = form.occupation.data
        borrower.branch_id = form.branch_id.data
        borrower.next_of_kin = form.next_of_kin.data

        db.session.commit()

        log_action(f"{current_user.full_name} updated borrower: {borrower.name}")

        flash('Borrower updated successfully.')
        return redirect(url_for('borrowers.view_borrowers'))

    # Pre-fill form.name without title on GET
    if request.method == 'GET' and borrower.name and borrower.title:
        form.name.data = borrower.name.replace(borrower.title, '').strip()

    return render_template('borrowers/edit_borrower.html', form=form, borrower=borrower)

@borrower_bp.route('/borrowers/<int:borrower_id>/delete', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans_Supervisor')
def delete_borrower(borrower_id):
    branch_id = session.get('active_branch_id')  # 👈 Get active branch from session

    # Filter loans by company (and branch if applicable)
    loan_query = Loan.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        loan_query = loan_query.filter_by(branch_id=branch_id)

    borrower = Borrower.query.get_or_404(borrower_id)
    
    # Optional: Archive or check for related loans before deletion

    db.session.delete(borrower)
    db.session.commit()

    # ✅ Log the deletion (now safely using the new log_action)
    log_action(f"{current_user.full_name} deleted borrower: {borrower.name}")

    flash('Borrower deleted successfully.')
    return redirect(url_for('borrowers.view_borrowers'))


@borrower_bp.route('/borrowers/<int:borrower_id>/download_pdf')
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans_Supervisor',)
def download_borrower_pdf(borrower_id):
    branch_id = session.get('active_branch_id')  # 👈 Get active branch from session

    # Filter loans by company (and branch if applicable)
    loan_query = Loan.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        loan_query = loan_query.filter_by(branch_id=branch_id)

    borrower = Borrower.query.get_or_404(borrower_id)
    loans = Loan.query.filter_by(borrower_id=borrower_id).all()
    rendered = render_template('borrowers/pdf_template.html', borrower=borrower, loans=loans)
    pdf = io.BytesIO()
    pisa.CreatePDF(io.BytesIO(rendered.encode('utf-8')), dest=pdf)
    pdf.seek(0)
    return Response(pdf.read(), content_type='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename=borrower_{borrower.id}.pdf'})

# Other existing routes (view_groups, add_group, etc.) remain unchanged

@borrower_bp.route('/groups')
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans_Officer', 'Loans_Supervisor',)
def view_groups():
    branch_id = session.get('active_branch_id')  # 👈 Get active branch from session

    # Filter loans by company (and branch if applicable)
    loan_query = Loan.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        loan_query = loan_query.filter_by(branch_id=branch_id)

    return render_template('borrowers/view_groups.html')

@borrower_bp.route('/groups/add', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans_Supervisor',)
def add_group():
    branch_id = session.get('active_branch_id')  # 👈 Get active branch from session

    # Filter loans by company (and branch if applicable)
    loan_query = Loan.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        loan_query = loan_query.filter_by(branch_id=branch_id)

    if request.method == 'POST':
        # Add group logic here
        pass
    return render_template('borrowers/add_group.html')

@borrower_bp.route('/borrowers/sms', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans_Supervisor',)
def send_sms():
    branch_id = session.get('active_branch_id')  # 👈 Get active branch from session

    # Filter loans by company (and branch if applicable)
    loan_query = Loan.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        loan_query = loan_query.filter_by(branch_id=branch_id)

    if request.method == 'POST':
        # Send SMS logic
        pass
    return render_template('borrowers/send_sms.html')

@borrower_bp.route('/borrowers/email', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans_Supervisor',)
def send_email():
    branch_id = session.get('active_branch_id')  # 👈 Get active branch from session

    # Filter loans by company (and branch if applicable)
    loan_query = Loan.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        loan_query = loan_query.filter_by(branch_id=branch_id)

    if request.method == 'POST':
        # Send email logic
        pass
    return render_template('borrowers/send_email.html')

@borrower_bp.route('/borrowers/invite', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans_Supervisor',)
def invite_borrowers():
    branch_id = session.get('active_branch_id')  # 👈 Get active branch from session

    # Filter loans by company (and branch if applicable)
    loan_query = Loan.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        loan_query = loan_query.filter_by(branch_id=branch_id)

    if request.method == 'POST':
        # Invite borrowers logic
        pass
    return render_template('borrowers/invite_borrowers.html')

