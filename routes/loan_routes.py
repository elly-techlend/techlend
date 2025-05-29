# techlend/routes/loan_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from utils.decorators import roles_required
from forms import LoginForm
from extensions import db
from utils.logging import log_company_action, log_system_action, log_action
from models import Loan, Borrower, LoanRepayment, Collateral
from flask import session
from datetime import datetime, timedelta
from utils import get_company_filter
from routes.cashbook_routes import add_cashbook_entry
from models import CashbookEntry
from utils.branch_filter import filter_by_active_branch
from dateutil.relativedelta import relativedelta
from sqlalchemy import extract, func
import io
from flask import send_file
import pandas as pd
from flask import render_template, make_response, current_app
from xhtml2pdf import pisa
from io import BytesIO
import os
from extensions import csrf
from zoneinfo import ZoneInfo
from utils.time_helpers import today

loan_bp = Blueprint('loan', __name__)

# View all approved loans
from sqlalchemy import extract

@loan_bp.route('/loans')
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans_Officer', 'Loans Supervisor', 'Cashier')
def view_loans():
    branch_id = session.get('active_branch_id')  # Get active branch from session

    query = Loan.query.filter_by(company_id=current_user.company_id, is_archived=False, approval_status='approved')

    # ✅ Apply branch filter only for non-superuser and only if a branch is set
    if not current_user.is_superuser and branch_id:
        query = query.filter_by(branch_id=branch_id)
        print(f"DEBUG - Filtering by branch_id: {branch_id}")
    else:
        print("DEBUG - No branch filter applied (Superuser or no branch selected)")

    # Filters
    search = request.args.get('search', '').strip()
    month = request.args.get('month')
    year = request.args.get('year')

    if search:
        query = query.filter(
            (Loan.loan_id.ilike(f"%{search}%")) | 
            (Loan.borrower_name.ilike(f"%{search}%"))
        )

    if month:
        try:
            month_int = int(month)
            query = query.filter(extract('month', Loan.date) == month_int)
        except ValueError:
            pass

    if year:
        try:
            year_int = int(year)
            query = query.filter(extract('year', Loan.date) == year_int)
        except ValueError:
            pass

    page = request.args.get('page', 1, type=int)
    per_page = 20

    pagination = query.order_by(Loan.date.desc()).paginate(page=page, per_page=per_page)
    loans = pagination.items

    totals_query = query.with_entities(
        func.coalesce(func.sum(Loan.amount_borrowed), 0),
        func.coalesce(func.sum(Loan.processing_fee), 0),
        func.coalesce(func.sum(Loan.total_due), 0),
        func.coalesce(func.sum(Loan.amount_paid), 0),
        func.coalesce(func.sum(Loan.remaining_balance), 0),
    ).first()

    totals = {
        'amount_borrowed': totals_query[0],
        'processing_fee': totals_query[1],
        'total_due': totals_query[2],
        'amount_paid': totals_query[3],
        'remaining_balance': totals_query[4],
    }

    return render_template('loans/view_all_loans.html', loans=loans, totals=totals, pagination=pagination)

@loan_bp.route('/loan/<int:loan_id>')
@login_required
@roles_required('Admin', 'Cashier', 'Loans Supervisor', 'Branch_Manager', 'Accountant', 'Loans_Officer')
def loan_details(loan_id):
    from utils.branch_filter import filter_by_active_branch

    # Build base query for the loan
    query = Loan.query.filter_by(company_id=current_user.company_id)
    query = filter_by_active_branch(query, model=Loan)

    # Now filter by ID
    loan = query.filter_by(id=loan_id).first_or_404()

    # Repayments are tied to loan, so we don't need further filtering
    repayments = (
        LoanRepayment.query
        .filter_by(loan_id=loan.id)
        .order_by(LoanRepayment.date_paid.asc())
        .all()
    )

    return render_template('loans/loan_details.html', loan=loan, repayments=repayments)

@loan_bp.route('/borrower/<int:borrower_id>')
@login_required
@roles_required('Admin', 'Loans Supervisor', 'Branch_Manager', 'Loans_Officer')
def view_by_borrower(borrower_id):
    from utils.branch_filter import filter_by_active_branch

    # Filter borrower by company and active branch
    borrower_query = Borrower.query.filter_by(company_id=current_user.company_id)
    borrower_query = filter_by_active_branch(borrower_query, model=Borrower)
    borrower = borrower_query.filter_by(id=borrower_id).first_or_404()

    # Get borrower's loans
    loans = Loan.query.filter_by(borrower_id=borrower_id).all()

    return render_template('loans/view_by_borrower.html', borrower=borrower, loans=loans)

# Add a loan
@csrf.exempt
@loan_bp.route('/loan/add', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'Loans Officer', 'Branch_Manager', 'Loans_Supervisor')
def add_loan():
    branch_id = session.get('active_branch_id')

    if request.method == 'POST':
        borrower_id = request.form.get('borrower_id')
        borrower = Borrower.query.filter_by(id=borrower_id, company_id=current_user.company_id).first()

        if borrower and branch_id and borrower.branch_id != branch_id:
            flash("This borrower does not belong to the current branch.", "danger")
            return redirect(url_for('loan.add_loan'))

        if not borrower:
            flash("Invalid borrower selected.", "danger")
            return redirect(url_for('loan.add_loan'))

        try:
            amount_borrowed = float(request.form['amount_borrowed'])
            processing_fee = float(request.form.get('processing_fee', 0))
            interest_rate = float(request.form.get('interest', 20.0))
            amount_paid = float(request.form.get('amount_paid', 0))
            collateral_note = request.form.get('collateral')
            loan_duration = int(request.form['loan_duration_value'])  # ✅ correct name
            loan_duration_unit = request.form.get('loan_duration_unit') or 'months'
            loan_date = datetime.strptime(request.form['date'], '%Y-%m-%d')
        except (ValueError, KeyError) as e:
            flash(f"Invalid input: {str(e)}", "danger")
            return redirect(url_for('loan.add_loan'))

        # Calculate due date based on unit
        if loan_duration_unit == 'days':
            due_date = loan_date + timedelta(days=loan_duration)
        elif loan_duration_unit == 'weeks':
            due_date = loan_date + timedelta(weeks=loan_duration)
        elif loan_duration_unit == 'years':
            due_date = loan_date + relativedelta(years=loan_duration)
        else:
            due_date = loan_date + relativedelta(months=loan_duration)

        total_due = amount_borrowed + (amount_borrowed * (interest_rate / 100))
        remaining_balance = total_due - amount_paid

        # Generate unique loan_id
        last_loan = Loan.query.filter_by(company_id=current_user.company_id).order_by(Loan.id.desc()).first()
        last_number = 0
        if last_loan and last_loan.loan_id:
            try:
                last_number = int(last_loan.loan_id.split("-")[-1][1:])
            except (ValueError, IndexError):
                pass

        loan_id = f"C{current_user.company_id}-T{last_number + 1:05d}"

        loan = Loan(
            loan_id=loan_id,
            borrower_id=borrower.id,
            borrower_name=borrower.name,
            phone_number=borrower.phone,
            amount_borrowed=amount_borrowed,
            processing_fee=processing_fee,
            interest_rate=interest_rate,
            total_due=total_due,
            amount_paid=amount_paid,
            remaining_balance=remaining_balance,
            loan_duration_value=loan_duration,
            loan_duration_unit=loan_duration_unit,
            collateral=collateral_note,
            date=loan_date,
            due_date=due_date,
            status='Paid' if remaining_balance == 0 else 'Pending',
            approval_status='pending',
            company_id=current_user.company_id,
            created_by=current_user.id,
            branch_id=branch_id
        )

        db.session.add(loan)

        # Handle collateral
        item_name = request.form.get('collateral_item_name')
        if item_name:
            collateral_entry = Collateral(
                borrower_id=borrower.id,
                item_name=item_name,
                model=request.form.get('collateral_model'),
                serial_number=request.form.get('collateral_serial_number'),
                status=request.form.get('collateral_status'),
                condition=request.form.get('collateral_condition')
            )
            db.session.add(collateral_entry)

        # Cashbook entries
        if processing_fee > 0:
            db.session.add(CashbookEntry(
                date=loan_date,
                particulars=f"Processing fee received from {borrower.name}",
                debit=0.0,
                credit=processing_fee,
                balance=0.0,
                company_id=current_user.company_id,
                branch_id=branch_id,
                created_by=current_user.id
            ))

        if loan.approval_status == 'approved':
            db.session.add(CashbookEntry(
                date=loan_date,
                particulars=f"Loan disbursed to {borrower.name}",
                debit=amount_borrowed,
                credit=0.0,
                balance=0.0,
                company_id=current_user.company_id,
                branch_id=branch_id,
                created_by=current_user.id
            ))

        db.session.commit()

        log_action(f"{current_user.full_name} created loan {loan.loan_id} for {borrower.name} (Amount: {amount_borrowed})")
        flash(f'Loan {loan_id} added successfully.', 'success')
        return redirect(url_for('loan.pending_loans'))

    borrowers = Borrower.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        borrowers = borrowers.filter_by(branch_id=branch_id)
    borrowers = borrowers.all()

    return render_template('loans/add_loan.html', borrowers=borrowers, current_date=datetime.today().strftime('%Y-%m-%d'))

# Pending loans
@loan_bp.route('/pending-loans')
@login_required
def pending_loans():
    branch_id = session.get('active_branch_id')
    query = Loan.query.filter_by(company_id=current_user.company_id, approval_status='pending')

    if branch_id:
        query = query.filter_by(branch_id=branch_id)

    loans = query.order_by(Loan.date.desc()).all()
    return render_template('loans/pending_loans.html', loans=loans)

# Rejected loans
@loan_bp.route('/rejected-loans')
@login_required
def rejected_loans():
    branch_id = session.get('active_branch_id')
    query = Loan.query.filter_by(company_id=current_user.company_id, approval_status='rejected')

    if branch_id:
        query = query.filter_by(branch_id=branch_id)

    loans = query.order_by(Loan.date.desc()).all()
    return render_template('loans/rejected_loans.html', loans=loans)

@loan_bp.route('/loans-in-arrears')
@login_required
def loans_in_arrears():
    branch_id = session.get('active_branch_id')
    today = datetime.today().date()

    query = Loan.query.filter(
        Loan.company_id == current_user.company_id,
        Loan.approval_status == 'approved',
        Loan.due_date < today,
        Loan.remaining_balance > 0
    )
    if branch_id:
        query = query.filter_by(branch_id=branch_id)

    raw_loans = query.order_by(Loan.due_date.asc()).all()

    enriched_loans = []

    # Initialize totals
    total_amount = 0
    total_principal_arrears = 0
    total_interest_arrears = 0
    total_penalty_arrears = 0
    total_total_arrears = 0
    total_days_overdue = 0

    for loan in raw_loans:
        # Interest due
        interest_due = loan.amount_borrowed * loan.interest_rate / 100

        # Total paid = original due - remaining balance
        total_paid = loan.amount_borrowed + interest_due - loan.remaining_balance

        # Split payment into interest and principal
        interest_paid = min(total_paid, interest_due)
        principal_paid = max(0, total_paid - interest_paid)

        # Arrears
        interest_arrears = max(0, interest_due - interest_paid)
        principal_arrears = loan.remaining_balance  # direct from loan

        # Penalty
        penalty_arrears = 0
        if loan.due_date and today > loan.due_date.date():
            months_overdue = (today.year - loan.due_date.year) * 12 + (today.month - loan.due_date.month)
            months_overdue = max(1, months_overdue)  # at least 1 month if overdue
            penalty_arrears = principal_arrears * loan.interest_rate / 100 * months_overdue

        total_arrears = principal_arrears + interest_arrears + penalty_arrears

        # Last repayment
        last_repayment = (
            LoanRepayment.query.filter_by(loan_id=loan.id)
            .order_by(LoanRepayment.date_paid.desc())
            .first()
        )

        due_date = loan.due_date.date() if isinstance(loan.due_date, datetime) else loan.due_date
        days_overdue = (today - due_date).days if due_date else 0

        enriched_loans.append({
            'loan_id': loan.id,
            'name': loan.borrower_name,
            'phone': loan.borrower.phone if loan.borrower else '',
            'amount': loan.amount_borrowed,
            'date': loan.date,
            'principal_arrears': principal_arrears,
            'interest_arrears': interest_arrears,
            'penalty_arrears': penalty_arrears,
            'total_arrears': total_arrears,
            'days': days_overdue,
            'last_repayment': last_repayment.date_paid if last_repayment else 'N/A',
        })

        # Totals
        total_amount += loan.amount_borrowed
        total_principal_arrears += principal_arrears
        total_interest_arrears += interest_arrears
        total_penalty_arrears += penalty_arrears
        total_total_arrears += total_arrears

    totals = {
        'amount': total_amount,
        'principal_arrears': total_principal_arrears,
        'interest_arrears': total_interest_arrears,
        'penalty_arrears': total_penalty_arrears,
        'total_arrears': total_total_arrears,
    }

    return render_template('loans/loans_in_arrears.html', loans=enriched_loans, totals=totals)

@csrf.exempt
@loan_bp.route('/loan/<int:loan_id>/approve', methods=['POST'])
@login_required
@roles_required('Admin', 'Loans_Supervisor')
def approve_loan(loan_id):
    loan = Loan.query.filter_by(id=loan_id, company_id=current_user.company_id).first_or_404()
    
    if loan.approval_status != 'pending':
        flash("Loan is already processed.", "warning")
        return redirect(url_for('loan.pending_loans'))

    loan.approval_status = 'approved'

    # Add cashbook entry on approval
    cashbook_entry = CashbookEntry(
        date=loan.date,
        particulars=f"Loan disbursed to {loan.borrower.name}",
        debit=loan.amount_borrowed,
        credit=0.0,
        balance=0.0,
        company_id=loan.company_id,
        branch_id=loan.branch_id,
        created_by=current_user.id
    )
    db.session.add(cashbook_entry)

    db.session.commit()
    log_action(f"{current_user.full_name} approved loan {loan.loan_id}")
    flash(f"Loan {loan.loan_id} approved and disbursed.", "success")
    return redirect(url_for('loan.pending_loans'))

@csrf.exempt
@loan_bp.route('/loan/<int:loan_id>/reject', methods=['POST'])
@login_required
@roles_required('Admin', 'Loans_Supervisor')
def reject_loan(loan_id):
    loan = Loan.query.filter_by(id=loan_id, company_id=current_user.company_id).first_or_404()
    if loan.approval_status != 'pending':
        flash("Loan is already processed.", "warning")
        return redirect(url_for('loan.pending_loans'))

    loan.approval_status = 'rejected'
    db.session.commit()
    log_action(f"{current_user.full_name} rejected loan {loan.loan_id}")
    flash(f"Loan {loan.loan_id} rejected.", "danger")
    return redirect(url_for('loan.pending_loans'))

# Edit a loan
@csrf.exempt
@loan_bp.route('/loan/<int:loan_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans Supervisor')
def edit_loan(loan_id):
    branch_id = session.get('active_branch_id')  # Get active branch from session

    loan = get_company_filter(Loan).filter_by(id=loan_id).first_or_404()

    if branch_id and loan.branch_id != branch_id:
        flash("You are not allowed to edit loans from another branch.", "danger")
        return redirect(url_for('loan.view_loans'))

    if request.method == 'POST':
        try:
            loan.borrower_name = request.form['borrower_name']
            loan.phone_number = request.form['phone_number']
            loan.amount_borrowed = float(request.form['amount_borrowed'])
            loan.processing_fee = float(request.form['processing_fee'])
            loan.interest_rate = float(request.form['interest_rate'])  # 👈 Allow editing interest rate

            # Total due recalculated
            loan.total_due = loan.amount_borrowed + (loan.amount_borrowed * (loan.interest_rate / 100))

            loan.amount_paid = float(request.form['amount_paid'])
            loan.remaining_balance = loan.total_due - loan.amount_paid
            loan.date = datetime.strptime(request.form['date'], '%Y-%m-%d')
            loan.collateral = request.form['collateral']

            # ⬇️ New: Handle loan duration
            loan.duration_value = int(request.form['loan_duration_value'])
            loan.duration_unit = request.form['loan_duration_unit']

            # Update loan status
            loan.status = 'Paid' if loan.remaining_balance <= 0 else 'Pending'

            db.session.commit()
            log_action(f"{current_user.full_name} edited loan {loan.loan_id} for {loan.borrower_name}")
            flash('Loan updated successfully.', 'success')
            return redirect(url_for('loan.view_loans'))
        except Exception as e:
            flash(f"Error updating loan: {e}", 'danger')
            return redirect(url_for('loan.edit_loan', loan_id=loan_id))

    return render_template('loans/edit_loan.html', loan=loan)

# Delete loan
@csrf.exempt
@loan_bp.route('/loan/<int:loan_id>/delete', methods=['POST'])
@login_required
@roles_required('Admin', 'Loans Supervisor', 'Branch_Manager')
def delete_loan(loan_id):
    branch_id = session.get('active_branch_id')  # 👈 Get active branch from session

    loan = get_company_filter(Loan).filter_by(id=loan_id).first_or_404()

    # 🔐 Enforce branch-level protection
    if branch_id and loan.branch_id != branch_id:
        flash("You are not allowed to delete loans from another branch.", "danger")
        return redirect(url_for('loan.view_loans'))

    db.session.delete(loan)
    db.session.commit()

    log_action(f"{current_user.full_name} deleted loan {loan.loan_id} for {loan.borrower_name}")

    flash('Loan deleted successfully.', 'success')
    return redirect(url_for('loan.view_loans'))

# Archive loan
@csrf.exempt
@loan_bp.route('/loan/<int:loan_id>/archive')
@login_required
@roles_required('Admin', 'Loans Supervisor', 'Branch_Manager')
def archive_loan(loan_id):
    branch_id = session.get('active_branch_id')

    loan = get_company_filter(Loan).filter_by(id=loan_id).first_or_404()

    # Enforce branch-level access
    if branch_id and loan.branch_id != branch_id:
        flash("You are not allowed to archive loans from another branch.", "danger")
        return redirect(url_for('loan.view_loans'))

    loan.is_archived = True  # Mark loan as archived
    db.session.commit()

    log_action(f"{current_user.full_name} archived loan {loan.loan_id} for {loan.borrower_name}")
    flash(f'Loan {loan.loan_id} has been archived.', 'info')
    return redirect(url_for('loan.view_loans'))

# Restore archived loan
@csrf.exempt
@loan_bp.route('/loan/<int:loan_id>/restore', methods=['POST'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans Supervisor')
def restore_loan(loan_id):
    branch_id = session.get('active_branch_id')  # 👈 Get active branch from session

    loan = get_company_filter(Loan).filter_by(id=loan_id, is_archived=True).first_or_404()

    # Enforce branch access
    if branch_id and loan.branch_id != branch_id:
        flash("You are not allowed to restore loans from another branch.", "danger")
        return redirect(url_for('loan.archived_loans'))

    loan.is_archived = False
    db.session.commit()

    log_action(f"{current_user.full_name} restored loan {loan.loan_id} for {loan.borrower_name}")
    flash('Loan restored successfully.', 'success')
    return redirect(url_for('loan.archived_loans'))

# View archived loans
@loan_bp.route('/loans/archived')
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans Supervisor')
def archived_loans():
    branch_id = session.get('active_branch_id')
    loan_query = get_company_filter(Loan).filter_by(is_archived=True)
    
    if branch_id:
        loan_query = loan_query.filter_by(branch_id=branch_id)
    
    loans = loan_query.order_by(Loan.date.desc()).all()

    return render_template('loans/archived_loans.html', archived_loans=loans)

# Make payment
@csrf.exempt
@loan_bp.route('/loan/<int:loan_id>/repay', methods=['POST'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans_Officer', 'Loans Supervisor', 'Cashier')
def repay_loan(loan_id):
    branch_id = session.get('active_branch_id')

    loan_query = get_company_filter(Loan)
    if branch_id:
        loan_query = loan_query.filter_by(branch_id=branch_id)

    loan = loan_query.filter_by(id=loan_id).first_or_404()

    try:
        amount = float(request.form['amount_paid'])
    except (ValueError, KeyError):
        flash('Invalid repayment amount.', 'danger')
        return redirect(url_for('loan.loan_details', loan_id=loan.id))

    if amount <= 0:
        flash('Repayment amount must be greater than zero.', 'warning')
        return redirect(url_for('loan.loan_details', loan_id=loan.id))

    # 📅 Get date from form input
    repayment_date_str = request.form.get('repayment_date')
    try:
        repayment_date = datetime.strptime(repayment_date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        flash('Invalid repayment date.', 'danger')
        return redirect(url_for('loan.loan_details', loan_id=loan.id))

    # 💰 Update loan balances
    loan.amount_paid += amount
    loan.remaining_balance = loan.total_due - loan.amount_paid

    if loan.remaining_balance <= 0:
        loan.remaining_balance = 0
        loan.status = 'Paid'
    else:
        loan.status = 'Partially Paid'

    # 💾 Save repayment
    repayment = LoanRepayment(
        loan_id=loan.id,
        amount_paid=amount,
        date_paid=repayment_date,
        balance_after=loan.remaining_balance
    )

    db.session.add(repayment)
    db.session.commit()

    # 🧾 Record in cashbook
    add_cashbook_entry(
        date=repayment_date,
        particulars=f"Loan repayment by {loan.borrower_name}",
        debit=0,
        credit=amount,
        company_id=current_user.company_id,
        branch_id=branch_id,
        created_by=current_user.id
    )

    # 🧠 Log action
    log_action(f"{current_user.full_name} made a repayment of {amount} for loan {loan.loan_id} (borrower: {loan.borrower_name})")

    flash('Repayment recorded successfully.', 'success')
    return redirect(url_for('loan.loan_details', loan_id=loan.id))

# View repayment history
@csrf.exempt
@loan_bp.route('/loan/<int:loan_id>/repayments')
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans_Officer', 'Loans_Supervisor')
def view_repayments(loan_id):
    branch_id = session.get('active_branch_id')

    loan_query = get_company_filter(Loan)
    if branch_id:
        loan_query = loan_query.filter_by(branch_id=branch_id)

    loan = loan_query.filter_by(id=loan_id).first_or_404()

    repayments = (
        LoanRepayment.query
        .filter_by(loan_id=loan.id)
        .order_by(LoanRepayment.date_paid.desc())
        .all()
    )

    total_repaid = sum(r.amount_paid for r in repayments)
    remaining_balance = None
    if loan.total_due is not None:
        remaining_balance = loan.total_due - total_repaid

    return render_template(
        'loans/repayments.html',
        loan=loan,
        repayments=repayments,
        total_repaid=total_repaid,
        remaining_balance=remaining_balance
    )

@loan_bp.route('/export-loans/<file_type>')
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager' 'Loans_Officer', 'Loans Supervisor',)
def export_loans(file_type):
    query = get_company_filter(Loan).order_by(Loan.date.desc())

    search = request.args.get('search', '').strip()
    month = request.args.get('month')
    year = request.args.get('year')

    if search:
        query = query.filter((Loan.loan_id.ilike(f"%{search}%")) | (Loan.borrower_name.ilike(f"%{search}%")))
    if month:
        query = query.filter(func.strftime('%m', Loan.date) == month)
    if year:
        query = query.filter(func.strftime('%Y', Loan.date) == year)

    loans = query.all()

    # Prepare data
    data = [{
        'Loan ID': loan.loan_id,
        'Borrower': loan.borrower_name,
        'Date': loan.date.strftime('%Y-%m-%d'),
        'Amount Borrowed': loan.amount_borrowed,
        'Processing Fee': loan.processing_fee,
        'Total Due': loan.total_due,
        'Amount Paid': loan.amount_paid,
        'Remaining Balance': loan.remaining_balance,
        'Status': loan.status
    } for loan in loans]

    # Add totals row
    totals = {
        'Loan ID': 'TOTAL',
        'Borrower': '',
        'Date': '',
        'Amount Borrowed': sum(loan.amount_borrowed or 0 for loan in loans),
        'Processing Fee': sum(loan.processing_fee or 0 for loan in loans),
        'Total Due': sum(loan.total_due or 0 for loan in loans),
        'Amount Paid': sum(loan.amount_paid or 0 for loan in loans),
        'Remaining Balance': sum(loan.remaining_balance or 0 for loan in loans),
        'Status': ''
    }
    data.append(totals)

    df = pd.DataFrame(data)

    if file_type == 'excel':
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Loans')
        output.seek(0)
        return send_file(output, download_name='loans.xlsx', as_attachment=True)

    elif file_type == 'pdf':
        from xhtml2pdf import pisa
        from flask import render_template_string

        html = render_template_string("""
            <h2>Loan Export Report</h2>
            <table border="1" cellspacing="0" cellpadding="5">
                <tr>
                    {% for column in df.columns %}
                        <th>{{ column }}</th>
                    {% endfor %}
                </tr>
                {% for row in df.values %}
                    <tr>
                        {% for cell in row %}
                            <td>{{ cell }}</td>
                        {% endfor %}
                    </tr>
                {% endfor %}
            </table>
        """, df=df)

        pdf = io.BytesIO()
        pisa.CreatePDF(io.StringIO(html), dest=pdf)
        pdf.seek(0)
        return send_file(pdf, download_name="loans.pdf", as_attachment=True)

    return "Unsupported file type", 400


@loan_bp.route('/loan/<int:loan_id>/export_pdf')
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans_Officer', 'Loans Supervisor')
def export_loan_pdf(loan_id):
    loan = Loan.query.get_or_404(loan_id)
    company = loan.company  # Assuming Loan has a company relationship

    # Prepare context for template
    context = {
        'loan': loan,
        'repayments': loan.repayments,
        'company': company,
        'logo_path': None
    }

    # Compute absolute logo file path for template usage
    if company.logo_url:
        # logo_url example: "/static/logos/company123.png"
        logo_file_path = os.path.join(current_app.root_path, company.logo_url.lstrip("/"))
        if os.path.exists(logo_file_path):
            # pass relative path for <img src="{{ logo_path }}">
            context['logo_path'] = company.logo_url
        else:
            context['logo_path'] = None

    # Render HTML template to string
    html = render_template('export_repayments_pdf.html', **context)

    # Convert HTML to PDF
    pdf = BytesIO()
    pisa_status = pisa.CreatePDF(
        src=html,
        dest=pdf,
        link_callback=link_callback  # for resolving static files
    )
    if pisa_status.err:
        return f'We had some errors: {pisa_status.err}', 500

    pdf.seek(0)
    return send_file(pdf, as_attachment=True,
                     download_name=f'loan_{loan.loan_id}_details.pdf',
                     mimetype='application/pdf')


# Helper function to resolve static files for xhtml2pdf
def link_callback(uri, rel):
    """
    Convert HTML URIs to absolute system paths so xhtml2pdf can access those resources
    """
    if uri.startswith('/static/'):
        path = os.path.join(current_app.root_path, uri.lstrip('/'))
    else:
        path = uri
    if not os.path.isfile(path):
        raise Exception('Media URI must start with /static/')
    return path
