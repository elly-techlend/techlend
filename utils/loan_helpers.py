from datetime import datetime, timedelta
from models import Loan, LedgerEntry, LoanRepayment
from extensions import db
from utils.utils import sum_paid
from utils.logging import log_action 
from sqlalchemy import func

def apply_cumulative_interest_for_overdue_loans(company_id, branch_id=None):
    today = datetime.today().date()
    
    overdue_loans = Loan.query.filter(
        Loan.company_id == company_id,
        Loan.approval_status == 'approved',
        Loan.due_date < today - timedelta(days=3),
        Loan.remaining_balance > 0
    )
    if branch_id:
        overdue_loans = overdue_loans.filter_by(branch_id=branch_id)

    for loan in overdue_loans.all():
        existing_penalty = LedgerEntry.query.filter_by(
            loan_id=loan.id,
            date=today,
            particulars="Cumulative Interest"
        ).first()

        existing_repayment = LoanRepayment.query.filter_by(
            loan_id=loan.id,
            date_paid=today,
            is_system_generated=True
        ).first()

        if existing_penalty or existing_repayment:
            continue

        total_paid = loan.amount_paid
        remaining_balance = loan.total_due - total_paid

        penalty_amount = loan.interest_rate / 100 * (loan.amount_borrowed if total_paid == 0 else remaining_balance)
        if penalty_amount <= 0:
            continue

        loan.total_due += penalty_amount
        loan.remaining_balance += penalty_amount

        ledger = LedgerEntry(
            loan_id=loan.id,
            date=today,
            particulars="Cumulative Interest",
            principal=0,
            interest=0,
            cumulative_interest=penalty_amount,
            principal_balance=loan.amount_borrowed - sum_paid(loan.id, 'principal_paid'),
            interest_balance=loan.interest_rate / 100 * loan.amount_borrowed - sum_paid(loan.id, 'interest_paid'),
            cumulative_interest_balance=penalty_amount,
            running_balance=loan.remaining_balance
        )
        db.session.add(ledger)

        repayment = LoanRepayment(
            loan_id=loan.id,
            branch_id=loan.branch_id,
            amount_paid=0,
            principal_paid=0,
            interest_paid=0,
            cumulative_interest_paid=penalty_amount,
            date_paid=today,
            balance_after=loan.remaining_balance,
            is_system_generated=True
        )
        db.session.add(repayment)

        log_action(f"Cumulative interest of {penalty_amount:.2f} auto-applied to Loan ID {loan.id}")

    db.session.commit()
