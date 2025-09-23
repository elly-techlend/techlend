# techlend/routes/loan_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from utils.decorators import roles_required
from forms import LoginForm
from extensions import db
from utils.logging import log_company_action, log_system_action, log_action
from models import Loan, Borrower, LoanRepayment, Collateral, LedgerEntry, Company
from flask import session
from datetime import datetime, timedelta, date
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
from utils.utils import sum_paid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from weasyprint import HTML, CSS

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

    tab = request.args.get('tab', 'payments')  # default to 'payments'

    # Build base query for the loan
    query = Loan.query.filter_by(company_id=current_user.company_id)
    query = filter_by_active_branch(query, model=Loan)

    # Now filter by ID
    loan = query.filter_by(id=loan_id).first_or_404()

    # Repayments (for payments tab)
    repayments_raw = (
        LoanRepayment.query
        .filter_by(loan_id=loan.id)
        .order_by(LoanRepayment.date_paid.asc())
        .all()
    )
    repayments = []
    for r in repayments_raw:
        total_paid = Decimal(r.principal_paid or 0) + Decimal(r.interest_paid or 0)
        repayments.append({
            'id': r.id,
            'loan_id': r.loan_id,
            'date_paid': r.date_paid,
            'amount_paid': total_paid,
            'principal_paid': r.principal_paid,
            'interest_paid': r.interest_paid,
            'balance_after': r.balance_after,
        })

    # Only fetch ledger entries if tab is 'ledger'
    ledger_entries = []
    if tab == 'ledger':
        ledger_entries = (
            LedgerEntry.query
            .filter_by(loan_id=loan.id)
            .order_by(LedgerEntry.date.asc())
            .all()
        )

    return render_template(
        'loans/loan_details.html',
        loan=loan,
        repayments=repayments,
        ledger_entries=ledger_entries,
        tab=tab,
        now=datetime.utcnow  # ✅ pass current time to template
    )

# edit repayment
@csrf.exempt
@loan_bp.route('/repayment/<int:repayment_id>/edit', methods=['POST'])
@login_required
@roles_required('Admin', 'Loans Supervisor')
def edit_repayment(repayment_id):
    repayment = LoanRepayment.query.get_or_404(repayment_id)
    loan = repayment.loan
    branch_id = loan.branch_id

    try:
        amount_paid = Decimal(request.form.get('amount_paid', 0))
        date_paid = datetime.strptime(request.form.get('date_paid'), '%Y-%m-%d').date()
        cumulative_interest = Decimal(request.form.get('cumulative_interest', 0))

    except (ValueError, TypeError):
        flash("Invalid amount or date.", "danger")
        return redirect(request.referrer or url_for('loan.loan_details', loan_id=loan.id))

    if amount_paid <= 0:
        flash("Amount must be greater than zero.", "warning")
        return redirect(request.referrer or url_for('loan.loan_details', loan_id=loan.id))

    # Remove old repayment, cashbook, and ledger entries
    old_date = repayment.date_paid
    old_amount = repayment.amount_paid

    # Remove cashbook
    CashbookEntry.query.filter_by(
        particulars=f"Loan repayment by {loan.borrower_name}",
        date=old_date
    ).delete()

    # Remove ledger entry
    LedgerEntry.query.filter_by(
        loan_id=loan.id,
        date=old_date,
        particulars='Loan repayment'
    ).delete()

    db.session.delete(repayment)
    db.session.commit()

    # Recalculate total paid (excluding this deleted one)
    previous_repayments = LoanRepayment.query.filter(
        LoanRepayment.loan_id == loan.id
    ).all()

    total_paid_so_far = sum(r.amount_paid for r in previous_repayments)
    principal_paid_so_far = sum(r.principal_paid for r in previous_repayments)
    interest_paid_so_far = sum(r.interest_paid for r in previous_repayments)

    # Recalculate interest due
    interest_due = Decimal(str(loan.amount_borrowed)) * Decimal(str(loan.interest_rate)) / Decimal('100')
    interest_remaining = max(Decimal('0'), interest_due - interest_paid_so_far)

    # Allocate this new amount
    interest_payment = min(amount_paid, interest_remaining)
    remaining_amount = amount_paid - interest_payment

    principal_payment = min(remaining_amount, loan.total_due - total_paid_so_far - interest_payment)
    total_paid = interest_payment + principal_payment

    # Update loan
    loan.amount_paid = total_paid_so_far + total_paid
    loan.remaining_balance = max(Decimal('0'), loan.total_due - loan.amount_paid)
    loan.status = 'Paid' if loan.remaining_balance <= 0 else (
        'In Arrears' if datetime.utcnow().date() > loan.due_date.date() else 'Partially Paid'
    )

    # Create updated repayment
    new_repayment = LoanRepayment(
        loan_id=loan.id,
        branch_id=branch_id,
        amount_paid=total_paid,
        principal_paid=principal_payment,
        interest_paid=interest_payment,
        date_paid=date_paid,
        balance_after=loan.remaining_balance,
        cumulative_interest=cumulative_interest
    )
    db.session.add(new_repayment)

    # Ledger entry
    ledger_entry = LedgerEntry(
        loan_id=loan.id,
        date=date_paid,
        particulars='Loan repayment',
        principal=principal_payment,
        interest=interest_payment,
        principal_balance=max(Decimal('0'), Decimal(str(loan.amount_borrowed)) - (principal_paid_so_far + principal_payment)),
        interest_balance=max(Decimal('0'), interest_due - (interest_paid_so_far + interest_payment)),
        running_balance=loan.remaining_balance
    )
    db.session.add(ledger_entry)

    # Cashbook
    add_cashbook_entry(
        date=date_paid,
        particulars=f"Loan repayment by {loan.borrower_name}",
        debit=Decimal('0'),
        credit=total_paid,
        company_id=current_user.company_id,
        branch_id=branch_id,
        created_by=current_user.id
    )

    # Log action
    log_action(
        f"{current_user.full_name} edited repayment for loan {loan.id} — New Total: {total_paid} "
        f"(Interest: {interest_payment}, Principal: {principal_payment})"
    )

    db.session.commit()
    flash('Repayment updated successfully.', 'success')
    return redirect(url_for('loan.loan_details', loan_id=loan.id))

@csrf.exempt
@loan_bp.route('/loans/<int:loan_id>/add_cumulative_interest', methods=['POST'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans Supervisor')
def add_cumulative_interest(loan_id):
    loan = Loan.query.get_or_404(loan_id)

    # Validate amount
    try:
        amount = Decimal(request.form.get('amount', '0'))
        if amount <= 0:
            flash('Cumulative interest must be greater than 0.', 'warning')
            return redirect(url_for('loan.loan_details', loan_id=loan_id))
    except (ValueError, TypeError, InvalidOperation):
        flash('Invalid cumulative interest amount.', 'danger')
        return redirect(url_for('loan.loan_details', loan_id=loan_id))

    # Parse date
    try:
        date_applied_str = request.form.get('date_applied')
        date_applied = datetime.strptime(date_applied_str, '%Y-%m-%d').date() if date_applied_str else datetime.today().date()
    except ValueError:
        flash('Invalid date format.', 'danger')
        return redirect(url_for('loan.loan_details', loan_id=loan_id))

    particulars = request.form.get('particulars') or 'Cumulative Interest'

    # Get the last ledger entry
    last_entry = LedgerEntry.query.filter_by(loan_id=loan.id).order_by(LedgerEntry.date.desc(), LedgerEntry.id.desc()).first()

    # Safe fallback values
    previous_principal_balance = (
        last_entry.principal_balance if last_entry and last_entry.principal_balance is not None else Decimal('0.00')
    )
    previous_interest_balance = (
        last_entry.interest_balance if last_entry and last_entry.interest_balance is not None else Decimal('0.00')
    )
    previous_cumulative_interest_balance = (
        last_entry.cumulative_interest_balance if last_entry and last_entry.cumulative_interest_balance is not None else Decimal('0.00')
    )
    previous_running_balance = (
        last_entry.running_balance if last_entry and last_entry.running_balance is not None else Decimal('0.00')
    )
    # 1. Ledger Entry
    ledger = LedgerEntry(
        loan_id=loan.id,
        date=date_applied,
        particulars=particulars,
        principal=Decimal('0.00'),
        interest=Decimal('0.00'),
        cumulative_interest=amount,
        principal_balance=previous_principal_balance,
        interest_balance=previous_interest_balance,
        cumulative_interest_balance=previous_cumulative_interest_balance + amount,
        running_balance=previous_running_balance + amount
    )

    # 2. Update Loan
    loan.remaining_balance += amount
    loan.total_due += amount
    loan.cumulative_interest = (loan.cumulative_interest or Decimal('0.00')) + amount

    # 3. Repayment Entry (non-cash, just a record)
    repayment = LoanRepayment(
        loan_id=loan.id,
        amount_paid=Decimal('0.00'),
        principal_paid=Decimal('0.00'),
        interest_paid=Decimal('0.00'),
        cumulative_interest=amount,
        balance_after=loan.remaining_balance,
        date_paid=date_applied
    )

    # Save all
    db.session.add(ledger)
    db.session.add(repayment)
    db.session.commit()

    flash('Cumulative interest added successfully.', 'success')
    return redirect(url_for('loan.loan_details', loan_id=loan_id))

# delete repayment
@csrf.exempt
@loan_bp.route('/repayment/<int:repayment_id>/delete', methods=['POST'])
@login_required
@roles_required('Admin')
def delete_repayment(repayment_id):
    repayment = LoanRepayment.query.get_or_404(repayment_id)
    loan = Loan.query.get_or_404(repayment.loan_id)

    # Delete corresponding ledger entry (loan repayment or cumulative interest)
    if repayment.cumulative_interest > 0:
        # Cumulative interest ledger
        LedgerEntry.query.filter_by(
            loan_id=loan.id,
            date=repayment.date_paid,
            particulars='Cumulative interest entry'
        ).delete()
    else:
        # Regular repayment ledger
        LedgerEntry.query.filter_by(
            loan_id=loan.id,
            date=repayment.date_paid,
            particulars='Loan repayment'
        ).delete()

    # Remove cashbook entry
    CashbookEntry.query.filter_by(
        date=repayment.date_paid,
        credit=repayment.amount_paid,
        particulars=f"Loan repayment by {loan.borrower_name}"
    ).delete()

    # Delete the repayment record
    db.session.delete(repayment)
    db.session.commit()

    # Recalculate loan payment stats
    repayments = LoanRepayment.query.filter_by(loan_id=loan.id).order_by(LoanRepayment.date_paid).all()
    total_paid = sum(r.amount_paid for r in repayments)
    total_principal_paid = sum(r.principal_paid for r in repayments)
    total_interest_paid = sum(r.interest_paid for r in repayments)
    total_cumulative_interest = sum((r.cumulative_interest or Decimal('0.00')) for r in repayments)

    # Recalculate interest due
    original_interest = Decimal(str(loan.amount_borrowed)) * Decimal(str(loan.interest_rate)) / Decimal('100')
    total_interest_due = original_interest + total_cumulative_interest

    # Update loan
    loan.amount_paid = total_paid
    loan.remaining_balance = max(Decimal('0'), loan.total_due - total_paid)
    loan.status = 'Paid' if loan.remaining_balance <= 0 else (
        'In Arrears' if datetime.utcnow().date() > loan.due_date.date() else 'Partially Paid'
    )
    db.session.commit()

    # Rebuild all loan-related ledger entries
    LedgerEntry.query.filter_by(loan_id=loan.id).delete()

    principal_balance = loan.amount_borrowed
    interest_balance = original_interest + total_cumulative_interest
    running_balance = loan.total_due

    for r in repayments:
        if (r.cumulative_interest or Decimal('0.00')) > 0:
            entry = LedgerEntry(
                loan_id=loan.id,
                date=r.date_paid,
                particulars='Cumulative interest entry',
                interest=r.cumulative_interest,
                principal=0,
                principal_balance=principal_balance,
                interest_balance=interest_balance,
                running_balance=running_balance
            )
            db.session.add(entry)
            interest_balance -= r.cumulative_interest
            running_balance -= r.cumulative_interest
        else:
            entry = LedgerEntry(
                loan_id=loan.id,
                date=r.date_paid,
                particulars='Loan repayment',
                interest=r.interest_paid,
                principal=r.principal_paid,
                principal_balance=max(Decimal('0'), principal_balance - r.principal_paid),
                interest_balance=max(Decimal('0'), interest_balance - r.interest_paid),
                running_balance=max(Decimal('0'), running_balance - r.amount_paid)
            )
            db.session.add(entry)
            principal_balance -= r.principal_paid
            interest_balance -= r.interest_paid
            running_balance -= r.amount_paid

    db.session.commit()

    log_action(
        f"{current_user.full_name} deleted a repayment of {repayment.amount_paid} for loan {loan.id} "
        f"(Borrower: {loan.borrower_name})"
    )

    flash('Repayment deleted and records updated successfully.', 'success')
    return redirect(url_for('loan.loan_details', loan_id=loan.id))

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
        db.session.flush()

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

        # Ledger: Loan Application Submitted
        db.session.add(LedgerEntry(
            loan_id=loan.id,
            date=loan_date,
            particulars='Loan Application',
            principal=loan.amount_borrowed,
            interest=loan.total_interest,          # total_interest includes cumulative interest now
            principal_balance=loan.amount_borrowed,
            interest_balance=loan.total_interest,
            running_balance=loan.total_due
        ))

        if loan.approval_status == 'approved':
            # Cashbook: actual loan disbursement (money given out)
            db.session.add(CashbookEntry(
                date=loan_date,
                particulars=f"Loan disbursed to {borrower.name}",
                debit=loan.amount_borrowed,
                credit=0.0,
                balance=0.0,
                company_id=current_user.company_id,
                branch_id=branch_id,
                created_by=current_user.id
            ))

            # Ledger: Loan Disbursement (principal only)
            db.session.add(LedgerEntry(
                loan_id=loan.id,
                date=loan_date,
                particulars='Loan Disbursement',
                principal=loan.amount_borrowed,
                interest=0.0,  # No interest disbursed
                principal_balance=loan.amount_borrowed,
                interest_balance=0.0,
                penalty_balance=0.0,
                running_balance=loan.amount_borrowed
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

from math import ceil

@loan_bp.route('/loans-in-arrears')
@login_required
def loans_in_arrears():
    branch_id = session.get('active_branch_id')
    today = date.today()

    query = Loan.query.filter(
        Loan.company_id == current_user.company_id,
        Loan.status == 'Partially Paid',
        Loan.due_date < datetime.combine(today, datetime.min.time()),
        Loan.remaining_balance > 0
    )

    if branch_id:
        query = query.filter_by(branch_id=branch_id)

    loans = query.order_by(Loan.due_date.asc()).all()
    enriched_loans = []

    total_amount = Decimal('0')
    total_original_balance = Decimal('0')
    total_penalty = Decimal('0')
    total_total_arrears = Decimal('0')

    for loan in loans:
        remaining_balance = Decimal(loan.remaining_balance or 0)
        penalty = Decimal(loan.cumulative_interest or 0)
        original_balance = remaining_balance - penalty  # ❗ Key Line

        disbursed_amount = Decimal(loan.amount_borrowed or 0)
        total_arrears = original_balance + penalty  # Or just remaining_balance

        days_overdue = (today - loan.due_date.date()).days

        last_repayment = db.session.query(LoanRepayment.date_paid)\
            .filter_by(loan_id=loan.id)\
            .order_by(LoanRepayment.date_paid.desc())\
            .first()

        enriched_loans.append({
            'loan_id': loan.id,
            'loan_code': loan.loan_id,
            'name': loan.borrower_name,
            'phone': loan.borrower.phone,
            'amount_borrowed': disbursed_amount,
            'disbursement_date': loan.date,
            'balance': original_balance,  # This shows pre-penalty balance
            'penalty': penalty,
            'total_arrears': total_arrears,
            'days': days_overdue,
            'last_repayment': last_repayment[0] if last_repayment else None
        })

        total_amount += disbursed_amount
        total_original_balance += original_balance
        total_penalty += penalty
        total_total_arrears += total_arrears

    totals = {
        'amount': total_amount,
        'original_balance': total_original_balance,
        'penalty': total_penalty,
        'total_arrears': total_total_arrears
    }

    return render_template('loans/loans_in_arrears.html', loans=enriched_loans, totals=totals)

@csrf.exempt
@loan_bp.route('/loan/<int:loan_id>/approve', methods=['POST'])
@login_required
@roles_required('Admin', 'Loans_Supervisor')
def approve_loan(loan_id):
    loan = Loan.query.filter_by(id=loan_id, company_id=current_user.company_id).first_or_404()

    if loan.approval_status != 'pending':
        flash("Loan is already processed or not pending.", "warning")
        return redirect(url_for('loan.pending_loans'))

    # Calculate total interest for records
    total_interest = loan.amount_borrowed * (Decimal(loan.interest_rate) / Decimal(100))

    # Mark loan as approved and disbursed
    loan.approval_status = 'approved'
    loan.status = 'Disbursed'
    loan.total_due = loan.amount_borrowed + total_interest
    loan.remaining_balance = loan.remaining_balance

    # Ledger Entry: Loan Approved
    ledger_approved = LedgerEntry(
        loan_id=loan.id,
        date=loan.date,
        particulars='Loan Approved',
        principal=loan.amount_borrowed,
        interest=total_interest,
        principal_balance=loan.amount_borrowed,
        interest_balance=total_interest,
        running_balance=loan.amount_borrowed + total_interest
    )
    db.session.add(ledger_approved)

    # Ledger Entry: Loan Disbursed
    ledger_disbursed = LedgerEntry(
        loan_id=loan.id,
        date=loan.date,
        particulars='Loan Disbursed',
        principal=loan.amount_borrowed,
        interest=0.0,
        principal_balance=loan.amount_borrowed,
        interest_balance=0,
        running_balance=loan.amount_borrowed + total_interest
    )
    db.session.add(ledger_disbursed)

    # Cashbook Entry: Loan disbursement
    add_cashbook_entry(
        date=loan.date,
        particulars=f"Loan disbursed to {loan.borrower.name}",
        debit=Decimal(str(loan.amount_borrowed)),
        credit=Decimal('0'),
        company_id=loan.company_id,
        branch_id=loan.branch_id,
        created_by=current_user.id
    )

    # Log action
    log_action(
        f"{current_user.full_name} approved and disbursed loan {loan.loan_id} "
        f"to {loan.borrower.name}. Amount: {loan.amount_borrowed}, Interest: {total_interest}"
    )

    db.session.commit()
    flash(f"Loan {loan.loan_id} approved and disbursed successfully.", "success")
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
        amount = Decimal(request.form['amount_paid'])
        repayment_date_str = request.form.get('repayment_date')
        repayment_date = datetime.strptime(repayment_date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError, KeyError):
        flash('Invalid repayment amount or date.', 'danger')
        return redirect(url_for('loan.loan_details', loan_id=loan.id))

    if amount <= 0:
        flash('Repayment amount must be greater than zero.', 'warning')
        return redirect(url_for('loan.loan_details', loan_id=loan.id))

    # ---- Step 1: Allocate to cumulative interest ----
    if loan.cumulative_interest is None:
        loan.cumulative_interest = Decimal('0.00')

    cumulative_interest_due = loan.cumulative_interest
    cumulative_interest_payment = min(amount, cumulative_interest_due)
    amount -= cumulative_interest_payment
    loan.cumulative_interest -= cumulative_interest_payment

    # ---- Step 2: Standard interest calculation ----
    standard_interest_due = loan.total_interest
    interest_paid_so_far = Decimal(db.session.query(
        func.coalesce(func.sum(LoanRepayment.interest_paid), 0)
    ).filter_by(loan_id=loan.id).scalar())

    standard_interest_remaining = max(Decimal('0'), standard_interest_due - interest_paid_so_far)
    interest_payment = min(amount, standard_interest_remaining)
    amount -= interest_payment

    # ---- Step 3: Principal ----
    principal_payment = min(amount, loan.remaining_balance)
    amount -= principal_payment

    # ---- Final calculation ----
    total_paid = cumulative_interest_payment + interest_payment + principal_payment

    # Update loan payment status
    loan.amount_paid += total_paid
    loan.remaining_balance = max(Decimal('0.00'), loan.total_due - loan.amount_paid)
    loan.status = 'Paid' if loan.remaining_balance <= 0 else 'Partially Paid'

    # Create repayment record
    repayment = LoanRepayment(
        loan_id=loan.id,
        branch_id=branch_id,
        amount_paid=total_paid,
        cumulative_interest=cumulative_interest_payment,
        interest_paid=interest_payment,
        principal_paid=principal_payment,
        date_paid=repayment_date,
        balance_after=loan.remaining_balance
    )
    db.session.add(repayment)

    # Update ledger
    total_principal_paid = Decimal(db.session.query(
        func.coalesce(func.sum(LoanRepayment.principal_paid), 0)
    ).filter_by(loan_id=loan.id).scalar())

    total_interest_paid = Decimal(db.session.query(
        func.coalesce(func.sum(LoanRepayment.interest_paid), 0)
    ).filter_by(loan_id=loan.id).scalar())

    ledger_entry = LedgerEntry(
        loan_id=loan.id,
        date=repayment_date,
        particulars="Loan repayment",
        principal=principal_payment,
        interest=interest_payment,
        cumulative_interest=cumulative_interest_payment,
        principal_balance=max(Decimal('0.00'), loan.amount_borrowed - total_principal_paid),
        interest_balance=max(Decimal('0.00'), standard_interest_due - total_interest_paid + loan.cumulative_interest),
        cumulative_interest_balance=max(Decimal('0.00'), loan.cumulative_interest),
        running_balance=loan.remaining_balance
    )
    db.session.add(ledger_entry)

    # Add cashbook entry
    add_cashbook_entry(
        date=repayment_date,
        particulars=f"Loan repayment by {loan.borrower_name}",
        debit=Decimal('0'),
        credit=total_paid,
        company_id=current_user.company_id,
        branch_id=branch_id,
        created_by=current_user.id
    )

    # Log action
    log_action(
        f"{current_user.full_name} made a repayment of {total_paid} for loan {loan.id} "
        f"(Borrower: {loan.borrower_name}) — Cumulative Interest: {cumulative_interest_payment}, "
        f"Standard Interest: {interest_payment}, Principal: {principal_payment}"
    )

    db.session.commit()
    flash('Repayment recorded successfully.', 'success')
    return redirect(url_for('loan.loan_details', loan_id=loan.id))

@loan_bp.route('/loan/<int:loan_id>/ledger')
@login_required
def loan_ledger(loan_id):
    loan = Loan.query.filter_by(id=loan_id, company_id=current_user.company_id).first_or_404()

    ledger_entries = LedgerEntry.query.filter_by(loan_id=loan.id).order_by(LedgerEntry.date, LedgerEntry.id).all()

    return render_template('loans/ledger.html', loan=loan, ledger_entries=ledger_entries)

@loan_bp.route('/loans/<loan_id>/ledger/pdf')
@login_required
def generate_ledger_pdf(loan_id):
    # Fetch loan and ledger data
    loan = Loan.query.filter_by(loan_id=loan_id, company_id=current_user.company_id).first_or_404()
    ledger_entries = LedgerEntry.query.filter_by(loan_id=loan.id).order_by(LedgerEntry.date, LedgerEntry.id).all()

    # Fetch company info for current user's company
    company = Company.query.get(current_user.company_id)

    # Render HTML with company
    rendered_html = render_template(
        'loans/ledger_pdf.html',
        loan=loan,
        ledger_entries=ledger_entries,
        company=company
    )
    
    # Generate PDF
    pdf_io = BytesIO()
    HTML(string=rendered_html, base_url='').write_pdf(
        pdf_io,
        stylesheets=[
            CSS(string='''
                @page { size: A4; margin: 1cm; }
                body { font-family: Arial, sans-serif; font-size: 12px; }
                .text-center { text-align: center; }
                .logo { width: 80px; }
                table { border-collapse: collapse; width: 100%; }
                th, td { border: 1px solid #ccc; padding: 5px; }
                th { background-color: #f2f2f2; }
            ''')
        ]
    )
    
    # Send PDF as response
    pdf_io.seek(0)
    response = make_response(pdf_io.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=ledger_{loan.loan_id}.pdf'
    return response

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
    company = loan.company  # Assuming Loan has a 'company' relationship

    # Prepare data
    context = {
        'loan': loan,
        'repayments': loan.repayments,
        'company': company,
        'logo_path': None
    }

    # Check for company logo
    if company.logo_url:
        logo_file_path = os.path.join(current_app.root_path, company.logo_url.lstrip("/"))
        if os.path.exists(logo_file_path):
            context['logo_path'] = company.logo_url

    # Render HTML
    html = render_template('loans/export_loan_pdf.html', **context)

    # Create PDF
    pdf = BytesIO()
    pisa_status = pisa.CreatePDF(
        src=html,
        dest=pdf,
        link_callback=link_callback
    )

    if pisa_status.err:
        return "PDF generation failed", 500

    pdf.seek(0)
    return send_file(pdf, as_attachment=True,
                     download_name=f"loan_{loan.loan_id}_details.pdf",
                     mimetype='application/pdf')

# Helper function to resolve static files for xhtml2pdf
def link_callback(uri, rel):
    """Resolve static file paths for xhtml2pdf."""
    if uri.startswith('/static/'):
        path = os.path.join(current_app.root_path, uri.lstrip('/'))
    else:
        path = uri
    if not os.path.isfile(path):
        raise Exception(f'Missing file: {path}')
    return path
