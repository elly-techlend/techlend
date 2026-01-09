from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_required, current_user
from utils.decorators import superuser_required, roles_required, admin_or_superuser_required
from models import Loan, SavingAccount, SavingTransaction, Borrower, CashbookEntry
from utils.logging import log_company_action, log_system_action, log_action
from extensions import db
from flask import session
from utils import get_company_filter
from utils.branch_filter import filter_by_active_branch
from routes.cashbook_routes import add_cashbook_entry, recalculate_balances
from extensions import csrf
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from calendar import month_name

savings_blueprint = Blueprint('savings', __name__)

# View all savings accounts
from sqlalchemy import desc

from sqlalchemy import desc
from datetime import date, timedelta
from calendar import monthrange

@savings_blueprint.route('/')
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans_Supervisor')
def view_savings():
    branch_id = session.get('active_branch_id')
    filter_type = request.args.get('filter')

    today = date.today()

    query = SavingAccount.query.filter_by(
        company_id=current_user.company_id
    )

    if branch_id:
        query = query.filter_by(branch_id=branch_id)

    # 🔎 FILTER LOGIC
    if filter_type == 'today':
        query = query.filter(
            db.func.date(SavingAccount.date_opened) == today
        )

    elif filter_type == 'weekly':
        start_week = today - timedelta(days=today.weekday())
        query = query.filter(
            SavingAccount.date_opened >= start_week
        )

    elif filter_type == 'monthly':
        start_month = today.replace(day=1)
        end_month = today.replace(
            day=monthrange(today.year, today.month)[1]
        )
        query = query.filter(
            SavingAccount.date_opened.between(start_month, end_month)
        )

    elif filter_type == 'yearly':
        start_year = today.replace(month=1, day=1)
        query = query.filter(
            SavingAccount.date_opened >= start_year
        )

    accounts = (
        query
        .order_by(desc(SavingAccount.date_opened))
        .all()
    )

    return render_template(
        'savings/view_savings.html',
        accounts=accounts,
        filter=filter_type
    )

@savings_blueprint.route('/borrower/<int:borrower_id>')
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans_Supervisor')
def view_by_borrower(borrower_id):
    branch_id = session.get('active_branch_id')  # 👈 Get active branch from session

    # Ensure the borrower belongs to the current user's company
    borrower = Borrower.query.filter_by(id=borrower_id, company_id=current_user.company_id).first_or_404()

    # Optionally filter savings accounts by active branch if branch filtering applies
    savings_accounts_query = borrower.savings_accounts
    if branch_id:
        savings_accounts_query = savings_accounts_query.filter_by(branch_id=branch_id)

    savings_accounts = savings_accounts_query.all()

    return render_template('savings/by_borrower.html', borrower=borrower, savings_accounts=savings_accounts)

# Add new savings account
@csrf.exempt
@savings_blueprint.route('/add', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans_Supervisor')
def add_saving():
    branch_id = session.get('active_branch_id') or current_user.branch_id

    # Get borrowers filtered by company and (optionally) branch
    borrowers_query = get_company_filter(Borrower)
    if branch_id:
        borrowers_query = borrowers_query.filter_by(branch_id=branch_id)
    borrowers = borrowers_query.all()

    if request.method == 'POST':
        borrower_id = request.form.get('borrower_id')
        account_number = request.form.get('account_number')
        balance_str = request.form.get('balance')

        # Validate borrower exists in current user's company
        borrower = Borrower.query.filter_by(id=borrower_id, company_id=current_user.company_id).first()
        if not borrower:
            flash('Invalid borrower selected.', 'danger')
            return redirect(url_for('savings.add_saving'))

        # Validate account number
        if not account_number or len(account_number) < 3:
            flash('Please enter a valid account number.', 'danger')
            return redirect(url_for('savings.add_saving'))

        # Validate balance
        try:
            balance = float(balance_str)
            if balance < 0:
                raise ValueError("Balance cannot be negative")
        except (ValueError, TypeError):
            flash('Please enter a valid non-negative balance amount.', 'danger')
            return redirect(url_for('savings.add_saving'))

        # Create and save the saving account
        new_account = SavingAccount(
            borrower_id=borrower_id,
            account_number=account_number,
            balance=balance,
            company_id=current_user.company_id,
            branch_id=branch_id  # ✅ guaranteed to have a value
        )
        db.session.add(new_account)
        db.session.commit()

        log_action(f"{current_user.full_name} added a new saving account for borrower: {borrower.name}")
        flash('Savings account added successfully.', 'success')
        return redirect(url_for('savings.view_savings'))

    return render_template('savings/add_saving.html', borrowers=borrowers)

# View transactions for a specific savings account
@savings_blueprint.route('/<int:saving_id>/transactions')
@login_required
def view_transactions(saving_id):
    saving = SavingAccount.query.get_or_404(saving_id)

    # Base query
    transactions_query = SavingTransaction.query.filter_by(account_id=saving_id)

    # Filters from query params
    filter_type = request.args.get('filter', None)
    month = request.args.get('month', type=int)
    year = request.args.get('year', type=int)

    today = datetime.today().date()

    if filter_type == 'today':
        transactions_query = transactions_query.filter(SavingTransaction.date >= today)
    elif filter_type == 'weekly':
        week_start = today - timedelta(days=today.weekday())  # Monday
        transactions_query = transactions_query.filter(SavingTransaction.date >= week_start)
    elif filter_type == 'monthly' and month and year:
        transactions_query = transactions_query.filter(
            SavingTransaction.date >= datetime(year, month, 1),
            SavingTransaction.date < datetime(year, month % 12 + 1, 1)
        )
    elif filter_type == 'yearly' and year:
        transactions_query = transactions_query.filter(
            SavingTransaction.date >= datetime(year, 1, 1),
            SavingTransaction.date < datetime(year + 1, 1, 1)
        )

    transactions = transactions_query.order_by(SavingTransaction.date).all()

    # Calculate running balance
    running_balance = 0
    for t in transactions:
        if t.transaction_type.lower() == 'deposit':
            running_balance += t.amount
        else:
            running_balance -= t.amount
        t.running_balance = running_balance

    # Current balance
    balance = transactions[-1].running_balance if transactions else 0

    # Prepare months/years for monthly filter
    months = [(i, month_name[i]) for i in range(1, 13)]
    years = list(range(today.year - 5, today.year + 2))
    selected_month = month or today.month
    selected_year = year or today.year

    return render_template(
        'savings/view_transactions.html',
        saving=saving,
        transactions=transactions,
        balance=balance,
        filter=filter_type,
        months=months,
        years=years,
        selected_month=selected_month,
        selected_year=selected_year
    )

# Deposit into a savings account
@csrf.exempt
@savings_blueprint.route('/<int:saving_id>/deposit', methods=['POST'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans_Supervisor', 'Cashier')
def deposit(saving_id):
    branch_id = session.get('active_branch_id')

    saving = (
        SavingAccount.query
        .filter_by(
            id=saving_id,
            company_id=current_user.company_id,
            branch_id=branch_id
        )
        .first_or_404()
    )

    try:
        amount = Decimal(request.form['amount'])
        if amount <= 0:
            raise ValueError
    except Exception:
        flash('Invalid deposit amount.', 'danger')
        return redirect(url_for('savings.view_transactions', saving_id=saving.id))

    # 🔹 Update savings balance
    saving.balance += amount

    # 🔹 Savings transaction
    tx = SavingTransaction(
        account_id=saving.id,
        transaction_type='deposit',
        amount=amount
    )
    db.session.add(tx)

    # 🔹 Cashbook (REAL CASH MOVEMENT)
    borrower_name = saving.borrower.name
    add_cashbook_entry(
        date=datetime.utcnow().date(),
        particulars=f"Savings deposit by {borrower_name}",
        debit=Decimal('0.00'),
        credit=amount,
        company_id=current_user.company_id,
        branch_id=branch_id,
        created_by=current_user.id
    )

    db.session.commit()

    flash('Deposit successful.', 'success')
    return redirect(url_for('savings.view_transactions', saving_id=saving.id))

# Withdraw from a savings account
@csrf.exempt
@savings_blueprint.route('/<int:saving_id>/withdraw', methods=['POST'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans_Supervisor')
def withdraw(saving_id):
    branch_id = session.get('active_branch_id')

    saving = (
        SavingAccount.query
        .filter_by(
            id=saving_id,
            company_id=current_user.company_id,
            branch_id=branch_id
        )
        .first_or_404()
    )

    try:
        amount = Decimal(request.form['amount'])
        if amount <= 0:
            raise ValueError
    except Exception:
        flash('Invalid withdrawal amount.', 'danger')
        return redirect(url_for('savings.view_transactions', saving_id=saving.id))

    if amount > saving.balance:
        flash('Insufficient balance.', 'danger')
        return redirect(url_for('savings.view_transactions', saving_id=saving.id))

    # 🔹 Update savings balance
    saving.balance -= amount

    # 🔹 Savings transaction
    tx = SavingTransaction(
        account_id=saving.id,
        transaction_type='withdrawal',
        amount=amount
    )
    db.session.add(tx)

    # 🔹 Cashbook (REAL CASH MOVEMENT)
    borrower_name = saving.borrower.name
    add_cashbook_entry(
        date=datetime.utcnow().date(),
        particulars=f"Savings withdrawal by {borrower_name}",
        debit=amount,
        credit=Decimal('0.00'),
        company_id=current_user.company_id,
        branch_id=branch_id,
        created_by=current_user.id
    )

    db.session.commit()

    flash('Withdrawal successful.', 'success')
    return redirect(url_for('savings.view_transactions', saving_id=saving.id))

@csrf.exempt
@savings_blueprint.route('/transaction/<int:trans_id>/delete', methods=['POST'])
@login_required
def delete_transaction(trans_id):
    trans = SavingTransaction.query.get_or_404(trans_id)
    db.session.delete(trans)
    db.session.commit()
    flash("Transaction deleted successfully.", "warning")
    return redirect(url_for('savings.view_transactions', saving_id=trans.account_id))

@csrf.exempt
@savings_blueprint.route('/transaction/<int:trans_id>/edit', methods=['POST'])
@login_required
def edit_transaction(trans_id):
    trans = SavingTransaction.query.get_or_404(trans_id)
    try:
        trans.amount = float(request.form['amount'])
        trans.date = request.form['date']  # assuming datetime input is handled in model
        db.session.commit()
        flash("Transaction updated successfully.", "success")
    except Exception as e:
        flash(f"Failed to update transaction: {e}", "danger")
    return redirect(url_for('savings.view_transactions', saving_id=trans.account_id))
