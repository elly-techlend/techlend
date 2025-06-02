from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from utils.decorators import superuser_required, roles_required, admin_or_superuser_required
from models import Loan, SavingAccount, SavingTransaction, Borrower
from utils.logging import log_company_action, log_system_action, log_action
from extensions import db
from flask import session
from utils import get_company_filter
from utils.branch_filter import filter_by_active_branch
from routes.cashbook_routes import add_cashbook_entry
from extensions import csrf
from datetime import datetime

savings_blueprint = Blueprint('savings', __name__)

# View all savings accounts
@savings_blueprint.route('/')
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans_Supervisor')
def view_savings():
    branch_id = session.get('active_branch_id')

    # Filter savings accounts by company (and branch if applicable)
    accounts_query = SavingAccount.query.filter_by(company_id=current_user.company_id)

    if branch_id:
        accounts_query = accounts_query.filter_by(branch_id=branch_id)

    accounts = accounts_query.all()

    return render_template('savings/view_savings.html', accounts=accounts)

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
@savings_blueprint.route('/<int:saving_id>/transactions', methods=['GET'])
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans_Supervisor')
def view_transactions(saving_id):
    branch_id = session.get('active_branch_id')  # Get active branch from session

    # Filter saving account by company and optionally by branch
    saving_query = get_company_filter(SavingAccount).filter_by(id=saving_id)
    if branch_id:
        saving_query = saving_query.filter_by(branch_id=branch_id)

    saving = saving_query.first_or_404()

    # Fetch related transactions ordered by most recent first
    transactions = SavingTransaction.query.filter_by(account_id=saving.id).order_by(SavingTransaction.date.desc()).all()

    total_deposits = sum(t.amount for t in transactions if t.transaction_type == 'deposit')
    total_withdrawals = sum(t.amount for t in transactions if t.transaction_type == 'withdrawal')
    balance = total_deposits - total_withdrawals

    return render_template(
        'savings/transactions.html',
        saving=saving,
        transactions=transactions,
        balance=balance  # ✅ Pass this to template
    )

    return render_template('savings/transactions.html', saving=saving, transactions=transactions)

# Deposit into a savings account
@csrf.exempt
@savings_blueprint.route('/<int:saving_id>/deposit', methods=['POST'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans_Supervisor')
def deposit(saving_id):
    branch_id = session.get('active_branch_id')  # Get active branch from session

    # Filter savings by company (and branch if applicable)
    saving_query = SavingAccount.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        saving_query = saving_query.filter_by(branch_id=branch_id)

    saving = saving_query.filter_by(id=saving_id).first_or_404()

    try:
        amount = float(request.form['amount'])
        if amount <= 0:
            flash('Deposit amount must be greater than zero.', 'warning')
            return redirect(url_for('savings.view_transactions', saving_id=saving.id))
    except (ValueError, KeyError):
        flash('Invalid deposit amount.', 'danger')
        return redirect(url_for('savings.view_transactions', saving_id=saving.id))

    # Update balance on saving account
    saving.balance += amount

    transaction = SavingTransaction(
        account_id=saving.id,
        amount=amount,
        transaction_type='deposit',
        date=datetime.utcnow()
    )
    db.session.add(transaction)
    db.session.commit()

    # Add Ledger Entry for Deposit
    ledger_entry = LedgerEntry(
        loan_id=None,  # Not related to a loan
        date=datetime.utcnow().date(),
        particulars=f"Savings deposit by {borrower_name}",
        principal=0.0,
        interest=0.0,
        penalty=0.0,
        principal_balance=0.0,
        interest_balance=0.0,
        penalty_balance=0.0,
        running_balance=amount
    )
    db.session.add(ledger_entry)
    db.session.commit()

    # Get borrower name for logging
    borrower = Borrower.query.filter_by(id=saving.borrower_id).first()
    borrower_name = borrower.name if borrower else "Unknown borrower"

    # Add to Cashbook
    add_cashbook_entry(
        date=datetime.utcnow().date(),
        particulars=f"Deposit by {borrower_name}",
        debit=amount,
        credit=0,
        branch_id=session.get('active_branch_id'),
        company_id=current_user.company_id,
        created_by=current_user.id
    )

    log_action(f"{current_user.full_name} made a deposit of {amount} to saving account of {borrower_name}")
    flash('Deposit successful.', 'success')
    return redirect(url_for('savings.view_transactions', saving_id=saving.id))

# Withdraw from a savings account
@csrf.exempt
@savings_blueprint.route('/<int:saving_id>/withdraw', methods=['POST'])
@login_required
@roles_required('Admin', 'Branch_Manager', 'Loans_Supervisor')
def withdraw(saving_id):
    branch_id = session.get('active_branch_id')  # Get active branch from session

    # Filter savings by company (and branch if applicable)
    saving_query = SavingAccount.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        saving_query = saving_query.filter_by(branch_id=branch_id)

    saving = saving_query.filter_by(id=saving_id).first_or_404()

    try:
        amount = float(request.form['amount'])
        if amount <= 0:
            flash('Withdrawal amount must be greater than zero.', 'warning')
            return redirect(url_for('savings.view_transactions', saving_id=saving.id))
    except (ValueError, KeyError):
        flash('Invalid withdrawal amount.', 'danger')
        return redirect(url_for('savings.view_transactions', saving_id=saving.id))

    # Use balance on saving account directly (recommended)
    current_balance = saving.balance

    if amount > current_balance:
        flash('Insufficient balance for withdrawal.', 'danger')
        return redirect(url_for('savings.view_transactions', saving_id=saving.id))

    # Update balance on saving account
    saving.balance -= amount

    transaction = SavingTransaction(
        account_id=saving.id,
        amount=amount,
        transaction_type='withdrawal',
        date=datetime.utcnow()
    )
    db.session.add(transaction)
    db.session.commit()

    # Add Ledger Entry for Withdrawal
    ledger_entry = LedgerEntry(
        loan_id=None,  # Not related to a loan
        date=datetime.utcnow().date(),
        particulars=f"Savings withdrawal by {borrower_name}",
        principal=0.0,
        interest=0.0,
        penalty=0.0,
        principal_balance=0.0,
        interest_balance=0.0,
        penalty_balance=0.0,
        running_balance=-amount
    )
    db.session.add(ledger_entry)
    db.session.commit()

    # Get borrower name for logging
    borrower = Borrower.query.filter_by(id=saving.borrower_id).first()
    borrower_name = borrower.name if borrower else "Unknown borrower"

    # Add to Cashbook
    add_cashbook_entry(
        date=datetime.utcnow().date(),
        particulars=f"Withdrawal by {borrower_name}",
        debit=amount,
        credit=0,
        branch_id=branch_id,
        company_id=current_user.company_id,
        created_by=current_user.id
    )

    log_action(f"{current_user.full_name} made a withdrawal of {amount} from saving account of {borrower_name}")

    flash('Withdrawal successful.', 'success')
    return redirect(url_for('savings.view_transactions', saving_id=saving.id))
