# techlend/routes/loan_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from utils.decorators import roles_required
from forms import VoucherForm
from extensions import db
from utils.logging import log_company_action, log_system_action, log_action
from models import Loan, Borrower, LoanRepayment, Collateral, LedgerEntry, Company, Voucher
from flask import session
from datetime import datetime, timedelta, date
from utils import get_company_filter
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
from cashbook_helpers import ledger_to_cashbook, recalculate_balances
from routes.cashbook_routes import add_cashbook_entry

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

@loan_bp.route('/loans/search', methods=['GET'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans Supervisor', 'Loans_Officer')
def search_loans():
    query = request.args.get('q', '').strip()
    results = []

    try:
        if query:
            # cast integer ID to string for ilike
            results = Loan.query.join(Borrower).filter(
                Loan.status == "Approved",
                (db.cast(Loan.id, db.String).ilike(f"%{query}%")) |
                (Borrower.name.ilike(f"%{query}%")) |
                (Borrower.phone.ilike(f"%{query}%"))
            ).all()
    except Exception as e:
        print("Loan search error:", e)
        return jsonify([])  # return empty JSON to prevent JS crash

    # return JSON
    return jsonify([
        {
            "id": loan.id,
            "borrower": loan.borrower.name,
            "phone": loan.borrower.phone,
            "amount": loan.amount_borrowed,
            "due_date": loan.due_date.strftime("%Y-%m-%d") if loan.due_date else "N/A"
        } for loan in results
    ])

from decimal import Decimal
def recalc_repayment_balances(loan_id):
    from decimal import Decimal
    from models import Loan, LedgerEntry
    from extensions import db

    loan = Loan.query.get(loan_id)
    if not loan:
        return

    # 🔹 INITIAL BALANCES
    principal_balance = Decimal(loan.amount_borrowed or 0)
    interest_balance = (
        Decimal(loan.amount_borrowed or 0)
        * Decimal(loan.interest_rate or 0)
        / Decimal('100')
    )
    cumulative_interest_balance = Decimal('0.00')
    total_paid = Decimal('0.00')

    # 🔹 FETCH LEDGER IN ORDER
    entries = (
        LedgerEntry.query
        .filter_by(loan_id=loan.id)
        .order_by(LedgerEntry.date.asc(), LedgerEntry.id.asc())
        .all()
    )

    for entry in entries:
        p = (entry.particulars or "").lower().strip()

        # 🔒 IMMUTABLE ENTRIES
        if p in ('loan application', 'loan approved', 'loan disbursed'):
            entry.principal = Decimal(loan.amount_borrowed or 0)
            entry.interest = (
                Decimal(loan.amount_borrowed or 0)
                * Decimal(loan.interest_rate or 0)
                / Decimal('100')
                if p != 'loan disbursed'
                else Decimal('0.00')
            )
            entry.cumulative_interest = Decimal('0.00')

            entry.principal_balance = principal_balance
            entry.interest_balance = interest_balance
            entry.cumulative_interest_balance = cumulative_interest_balance
            entry.running_balance = (
                principal_balance
                + interest_balance
                + cumulative_interest_balance
            )

            db.session.add(entry)
            continue

        # 🔁 RESET ALLOCATIONS
        entry.principal = Decimal('0.00')
        entry.interest = Decimal('0.00')
        entry.cumulative_interest = Decimal('0.00')

        # 🔴 CUMULATIVE INTEREST ENTRY
        if p == 'cumulative interest':
            ci_amount = Decimal(entry.payment or 0)
            cumulative_interest_balance += ci_amount
            entry.cumulative_interest = ci_amount

        # 🟢 LOAN REPAYMENT ENTRY
        elif p == 'loan repayment':
            payment = Decimal(entry.payment or 0)

            # 1️⃣ Pay cumulative interest
            ci_payment = min(payment, cumulative_interest_balance)
            cumulative_interest_balance -= ci_payment
            payment -= ci_payment
            entry.cumulative_interest = ci_payment

            # 2️⃣ Pay interest
            interest_payment = min(payment, interest_balance)
            interest_balance -= interest_payment
            payment -= interest_payment
            entry.interest = interest_payment

            # 3️⃣ Pay principal
            principal_payment = min(payment, principal_balance)
            principal_balance -= principal_payment
            payment -= principal_payment
            entry.principal = principal_payment

            total_paid += ci_payment + interest_payment + principal_payment

        # 🔢 CLAMP & UPDATE BALANCES
        principal_balance = max(principal_balance, Decimal('0.00'))
        interest_balance = max(interest_balance, Decimal('0.00'))
        cumulative_interest_balance = max(cumulative_interest_balance, Decimal('0.00'))

        entry.principal_balance = principal_balance
        entry.interest_balance = interest_balance
        entry.cumulative_interest_balance = cumulative_interest_balance
        entry.running_balance = (
            principal_balance
            + interest_balance
            + cumulative_interest_balance
        )

        db.session.add(entry)

    # 🔹 UPDATE LOAN SNAPSHOT
    loan.amount_paid = total_paid
    loan.remaining_balance = (
        principal_balance
        + interest_balance
        + cumulative_interest_balance
    )
    loan.status = 'Paid' if loan.remaining_balance <= 0 else 'Partially Paid'

    db.session.commit()

@loan_bp.route('/loan/<int:loan_id>')
@login_required
@roles_required(
    'Admin', 'Cashier', 'Loans Supervisor',
    'Branch_Manager', 'Accountant', 'Loans_Officer'
)
def loan_details(loan_id):
    from utils.branch_filter import filter_by_active_branch
    from sqlalchemy import func
    from datetime import datetime
    from extensions import db

    tab = request.args.get('tab', 'payments')

    query = Loan.query.filter_by(company_id=current_user.company_id)
    query = filter_by_active_branch(query, model=Loan)

    loan = query.filter_by(id=loan_id).first_or_404()

    ledger_entries = (
        LedgerEntry.query
        .filter_by(loan_id=loan.id)
        .order_by(LedgerEntry.date.asc(), LedgerEntry.id.asc())
        .all()
    )

    # 🔐 SINGLE SOURCE OF TRUTH
    last_entry = (
        LedgerEntry.query
        .filter_by(loan_id=loan.id)
        .order_by(LedgerEntry.date.desc(), LedgerEntry.id.desc())
        .first()
    )

    total_paid = (
        db.session.query(func.coalesce(func.sum(LedgerEntry.payment), 0))
        .filter(
            LedgerEntry.loan_id == loan.id,
            LedgerEntry.payment > 0
        )
        .scalar()
    )

    outstanding_balance = (
        last_entry.running_balance
        if last_entry
        else loan.total_due
    )

    return render_template(
        'loans/loan_details.html',
        loan=loan,
        ledger_entries=ledger_entries,
        total_paid=total_paid,
        outstanding_balance=outstanding_balance,
        tab=tab,
        now=datetime.utcnow
    )

@csrf.exempt
@loan_bp.route('/loan/<int:loan_id>/add_cumulative_interest', methods=['POST'])
@login_required
def add_cumulative_interest(loan_id):
    loan = Loan.query.get_or_404(loan_id)

    amount = Decimal(request.form['amount'])
    date_applied = datetime.strptime(
        request.form['date_applied'], '%Y-%m-%d'
    ).date()

    loan.cumulative_interest += amount

    entry = LedgerEntry(
        loan_id=loan.id,
        date=date_applied,
        particulars='Cumulative Interest',
        payment=amount,
        cumulative_interest=amount
    )

    db.session.add(entry)
    db.session.commit()

    recalc_repayment_balances(loan.id)

    flash('Cumulative interest added.', 'success')
    return redirect(url_for('loan.loan_details', loan_id=loan.id))

@csrf.exempt
@loan_bp.route('/ledger/<int:entry_id>/delete', methods=['POST'])
@login_required
def delete_ledger_entry(entry_id):
    entry = LedgerEntry.query.get_or_404(entry_id)
    loan_id = entry.loan_id

    immutable = ('loan application', 'loan approved', 'loan disbursed')

    if entry.particulars.lower() in immutable:
        flash('Cannot delete this entry.', 'warning')
        return redirect(url_for('loan.loan_details', loan_id=loan_id))

    # Delete corresponding cashbook entry, if any
    cb_entry = CashbookEntry.query.filter_by(
        date=entry.date,
        particulars=entry.particulars,
        company_id=entry.loan.company_id if entry.loan else None,
        branch_id=entry.loan.branch_id if entry.loan else None
    ).first()

    if cb_entry:
        db.session.delete(cb_entry)

    # Delete the ledger entry
    db.session.delete(entry)
    db.session.commit()

    # 🔁 Recalculate balances after delete
    recalc_repayment_balances(loan_id)

    flash('Ledger entry deleted successfully.', 'success')
    return redirect(url_for('loan.loan_details', loan_id=loan_id))

@csrf.exempt
@loan_bp.route('/ledger/<int:entry_id>/edit', methods=['POST'])
@login_required
def edit_ledger_entry(entry_id):
    entry = LedgerEntry.query.get_or_404(entry_id)
    loan_id = entry.loan_id

    immutable = ('loan application', 'loan approved', 'loan disbursed')
    if entry.particulars.lower() in immutable:
        flash('Cannot edit this entry.', 'warning')
        return redirect(url_for('loan.loan_details', loan_id=loan_id))

    # 🔹 Validate payment
    try:
        payment = Decimal(request.form['payment'])
        if payment < 0:
            raise ValueError
    except Exception:
        flash('Invalid amount.', 'danger')
        return redirect(url_for('loan.loan_details', loan_id=loan_id))

    # 🔹 Validate date
    try:
        entry.date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
    except Exception:
        flash('Invalid date.', 'danger')
        return redirect(url_for('loan.loan_details', loan_id=loan_id))

    # 🔹 Update ledger
    entry.payment = payment
    db.session.commit()

    # 🔹 Recalculate ledger balances
    recalc_repayment_balances(loan_id)

    flash('Ledger entry updated successfully.', 'success')
    return redirect(url_for('loan.loan_details', loan_id=loan_id))

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

        # ✅ Ensure borrower_id is converted to integer
        try:
           borrower_id = int(borrower_id)
        except (TypeError, ValueError):
            flash("Invalid borrower selected.", "danger")
            return redirect(url_for('loan.add_loan'))

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

@loan_bp.route('/loans/revision', methods=['GET'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans Supervisor')
def loan_revision_hub():

    loan_id = request.args.get('loan_id', type=int)
    selected_loan = Loan.query.get(loan_id) if loan_id else None
    # Fully defined list of revision groups
    revision_groups = [
        {
            'title': 'Modifications',
            'items': [
                {'name': 'Modify loan application', 'url': '#'},
                {'name': 'Push loan due dates forward', 'url': '#'},
                {'name': 'Change loan installment interval', 'url': '#'},
                {'name': 'Recreate loan schedule', 'url': '#'},
                {'name': 'Pull loan due dates backwards', 'url': '#'},
                {'name': 'Modify dues manually', 'url': '#'}
            ]
        },
        {
            'title': 'Deletions',
            'items': [
                {'name': 'Delete approval', 'url': '#'},
                {'name': 'Delete disbursement', 'url': '#'},
                {'name': 'Delete fees', 'url': '#'},
                {'name': 'Delete all repayments of a day', 'url': '#'},
                {'name': 'Delete penalties', 'url': '#'}
            ]
        },
        {
            'title': 'Loan Refinance',
            'items': [
                {'name': 'Loan restructuring and refinance (topup)', 'url': '#'}
            ]
        },
        {
            'title': 'Loan Provision',
            'items': [
                {'name': 'Writeoff loan', 'url': '#'},
                {'name': 'Bulk writeoff', 'url': '#'},
                {'name': 'Provision for a single', 'url': '#'}
            ]
        },
        {
            'title': 'Loan Reschedule',
            'items': [
                {'name': 'Reschedule loan', 'url': '#'}
            ]
        },
        {
            'title': 'Other Tools',
            'items': [
                {'name': 'Transfer portfolio to another loan/credit officer', 'url': '#'},
                {'name': 'Move loan to another product', 'url': '#'}
            ]
        }
    ]

    # Debug sanity check (optional)
    print(type(revision_groups))
    for group in revision_groups:
        print(group['title'], type(group['items']))

    return render_template('loans/loan_revision.html', revision_groups=revision_groups)

@loan_bp.route('/loans/revision/search', methods=['GET'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans Supervisor')
def search_loan_for_revision():
    query = request.args.get('query', '')
    loans = Loan.query.filter(
        (Loan.id.like(f'%{query}%')) |
        (Loan.borrower_name.ilike(f'%{query}%'))
    ).all()
    return render_template('loans/search_results.html', loans=loans, query=query)

from math import ceil

@loan_bp.route('/loans-in-arrears')
@login_required
def loans_in_arrears():
    """
    Display loans that are past due date and not yet fully paid.
    Uses same logic as loan_details page (Overdue = due_date < today and remaining_balance > 0).
    """
    branch_id = session.get('active_branch_id')
    today = datetime.utcnow().date()

    # Base query
    query = Loan.query.filter(
        Loan.company_id == current_user.company_id,
        Loan.remaining_balance > 0,
        Loan.due_date < datetime.combine(today, datetime.min.time())
    )

    # Apply branch restriction (if not superuser)
    if branch_id and not current_user.is_superuser:
        query = query.filter_by(branch_id=branch_id)

    # Fetch results
    loans = query.order_by(Loan.due_date.asc()).all()

    enriched_loans = []
    total_amount_borrowed = Decimal('0')
    total_balance = Decimal('0')
    total_penalty = Decimal('0')
    total_arrears = Decimal('0')

    for loan in loans:
        # Calculate penalty (same logic as your arrears route)
        penalty = Decimal(loan.cumulative_interest or 0)
        remaining_balance = Decimal(loan.remaining_balance or 0)
        total_arrear = remaining_balance + penalty

        # Days overdue
        days_overdue = (today - loan.due_date.date()).days if loan.due_date else 0

        # Last repayment date
        last_repayment = (
            db.session.query(LoanRepayment.date_paid)
            .filter_by(loan_id=loan.id)
            .order_by(LoanRepayment.date_paid.desc())
            .first()
        )
        last_repayment_date = last_repayment[0] if last_repayment else None

        enriched_loans.append({
            'loan_code': loan.loan_id,
            'name': loan.borrower_name,
            'phone': loan.borrower.phone if loan.borrower else 'N/A',
            'amount_borrowed': loan.amount_borrowed,
            'disbursement_date': loan.date,
            'balance': remaining_balance,
            'penalty': penalty,
            'total_arrears': total_arrear,
            'days': days_overdue,
            'last_repayment': last_repayment_date,
            'due_date': loan.due_date,
        })

        total_amount_borrowed += Decimal(loan.amount_borrowed or 0)
        total_balance += remaining_balance
        total_penalty += penalty
        total_arrears += total_arrear

    # 🔽 Sort by fewest overdue days first
    enriched_loans.sort(key=lambda x: x['days'])

    totals = {
        'amount': total_amount_borrowed,
        'original_balance': total_balance,
        'penalty': total_penalty,
        'total_arrears': total_arrears
    }

    return render_template(
        'loans/loans_in_arrears.html',
        loans=enriched_loans,
        totals=totals,
        today=today
    )

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
    loan.remaining_balance = loan.total_due

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
    loan = Loan.query.get_or_404(loan_id)

    # 🔹 GET AND VALIDATE AMOUNT
    try:
        amount = Decimal(request.form['amount_paid'])
        if amount <= 0:
            flash('Repayment amount must be greater than zero.', 'warning')
            return redirect(url_for('loan.loan_details', loan_id=loan.id))
    except Exception:
        flash('Invalid repayment amount.', 'danger')
        return redirect(url_for('loan.loan_details', loan_id=loan.id))

    # 🔹 GET REPAYMENT DATE
    pay_date = datetime.strptime(request.form['repayment_date'], '%Y-%m-%d').date()

    # ✅ SINGLE SOURCE OF TRUTH (STORE GROSS AMOUNT)
    entry = LedgerEntry(
        loan_id=loan.id,
        date=pay_date,
        particulars='Loan Repayment',
        payment=amount,                # ⭐ Store the gross payment
        principal=Decimal('0.00'),
        interest=Decimal('0.00'),
        cumulative_interest=Decimal('0.00')
    )

    db.session.add(entry)
    db.session.commit()

    # 🔹 Create voucher from ledger entry
    from routes.voucher_routes import create_voucher_from_ledger
    create_voucher_from_ledger(entry)

    # 🔁 Recalculate EVERYTHING from ledger
    recalc_repayment_balances(loan.id)


    flash('Repayment recorded successfully.', 'success')
    return redirect(url_for('loan.loan_details', loan_id=loan.id))

@loan_bp.route('/loan/<int:loan_id>/ledger')
@login_required
def loan_ledger(loan_id):
    loan = Loan.query.filter_by(
        id=loan_id,
        company_id=current_user.company_id
    ).first_or_404()

    ledger_entries = (
        LedgerEntry.query
        .filter_by(loan_id=loan.id)
        .order_by(LedgerEntry.date.asc(), LedgerEntry.id.asc())
        .all()
    )

    return render_template(
        'loans/ledger.html',
        loan=loan,
        ledger_entries=ledger_entries
    )

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
    company = loan.company

    # Prepare data
    context = {
        'loan': loan,
        'ledger_entries': loan.ledger_entries,
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
