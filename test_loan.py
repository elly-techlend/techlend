from app import create_app, db
from models import Loan, LoanRepayment, LedgerEntry
from decimal import Decimal

# --- Setup Flask application context ---
app = create_app()
app.app_context().push()

def recalc_repayment_balances(loan_id):
    """
    Fully recalculate repayments, per-row outstanding balances,
    and rebuild repayment ledger entries in chronological order.
    """
    loan = Loan.query.get(loan_id)
    if not loan:
        print(f"Loan ID {loan_id} not found for recalculation.")
        return

    # Clear repayment ledger entries first
    LedgerEntry.query.filter_by(loan_id=loan.id, particulars='Loan repayment').delete()
    db.session.commit()

    repayments = LoanRepayment.query.filter_by(loan_id=loan.id).order_by(LoanRepayment.date_paid.asc()).all()
    if not repayments:
        print(f"No repayments found for Loan ID {loan.loan_id}.")
        return

    total_due = Decimal(loan.total_due)
    total_principal_paid = Decimal('0.00')
    total_interest_paid = Decimal('0.00')
    total_cumulative_interest_paid = Decimal('0.00')
    total_paid_so_far = Decimal('0.00')

    total_interest_due = Decimal(loan.amount_borrowed) * Decimal(loan.interest_rate) / Decimal('100')
    cumulative_interest_due = loan.cumulative_interest or Decimal('0.00')

    for rep in repayments:
        amount_remaining = Decimal(rep.amount_paid or 0)

        # 1️ Pay cumulative interest first
        cumulative_payment = min(amount_remaining, cumulative_interest_due)
        amount_remaining -= cumulative_payment
        cumulative_interest_due -= cumulative_payment

        # 2️ Pay normal interest
        interest_payment = min(amount_remaining, max(Decimal('0.00'), total_interest_due - total_interest_paid))
        amount_remaining -= interest_payment
        total_interest_paid += interest_payment

        # 3️ Remaining goes to principal
        principal_payment = amount_remaining
        total_principal_paid += principal_payment

        total_paid_so_far += rep.amount_paid or Decimal('0.00')

        # Set balances
        rep.cumulative_interest = cumulative_payment
        rep.interest_paid = interest_payment
        rep.principal_paid = principal_payment
        rep.balance_after = max(Decimal('0.00'), total_due - total_paid_so_far)
        db.session.add(rep)

        # Create ledger entry
        ledger_entry = LedgerEntry(
            loan_id=loan.id,
            date=rep.date_paid,
            particulars='Loan repayment',
            principal=principal_payment,
            interest=interest_payment,
            cumulative_interest=cumulative_payment,
            principal_balance=max(Decimal('0.00'), loan.amount_borrowed - total_principal_paid),
            interest_balance=max(Decimal('0.00'), total_interest_due - total_interest_paid),
            cumulative_interest_balance=max(Decimal('0.00'), cumulative_interest_due),
            running_balance=rep.balance_after
        )
        db.session.add(ledger_entry)

    loan.amount_paid = total_paid_so_far
    loan.remaining_balance = max(Decimal('0.00'), total_due - total_paid_so_far)
    loan.status = 'Paid' if loan.remaining_balance <= 0 else 'Partially Paid'

    db.session.commit()
    print(f"Backfill completed for Loan ID {loan.loan_id}. Remaining balance: {loan.remaining_balance}")

def test_backfill_one_loan_verbose(loan_id_str):
    """
    Backfill one loan using string loan_id (loan.loan_id).
    """
    # Use filter_by for string loan_id
    loan = Loan.query.filter_by(loan_id=loan_id_str).first()
    if not loan:
        print(f"Loan {loan_id_str} not found!")
        return

    print(f"Backfilling Loan ID: {loan.loan_id}, Borrower: {loan.borrower_name}")
    recalc_repayment_balances(loan.id)

# --- Run test for one loan ---
if __name__ == "__main__":
    test_backfill_one_loan_verbose("C3-T00056")
