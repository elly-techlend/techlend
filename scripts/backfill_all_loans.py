#!/usr/bin/env python3

import sys
import os
from datetime import datetime

# Make sure Python can find the app package
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from models import Loan, LoanRepayment, LedgerEntry

app = create_app()  # create the Flask app instance
from decimal import Decimal
from app import create_app, db
from models import Loan, LedgerEntry

def backfill_single_loan(loan):
    print(f"Backfilling Loan ID: {loan.loan_id}, Borrower: {loan.borrower_name}")

    # Clear existing ledger entries
    LedgerEntry.query.filter_by(loan_id=loan.id).delete()
    db.session.commit()

    running_balance = loan.total_due

    # Loan Application
    db.session.add(LedgerEntry(
        loan_id=loan.id,
        date=loan.date.date(),
        particulars="Loan Application",
        principal=loan.amount_borrowed,
        interest=loan.total_interest,
        cumulative_interest=Decimal('0.00'),
        payment=Decimal('0.00'),
        running_balance=running_balance
    ))

    # Loan Approved
    db.session.add(LedgerEntry(
        loan_id=loan.id,
        date=loan.date.date(),
        particulars="Loan Approved",
        principal=loan.amount_borrowed,
        interest=loan.total_interest,
        cumulative_interest=Decimal('0.00'),
        payment=Decimal('0.00'),
        running_balance=running_balance
    ))

    # Loan Disbursed
    db.session.add(LedgerEntry(
        loan_id=loan.id,
        date=loan.date.date(),
        particulars="Loan Disbursed",
        principal=loan.amount_borrowed,
        interest=Decimal('0.00'),
        cumulative_interest=Decimal('0.00'),
        payment=Decimal('0.00'),  # Payment is zero here!
        running_balance=running_balance
    ))

    # Loan Repayments
    repayments = sorted(loan.repayments, key=lambda r: r.date_paid)
    for r in repayments:
        payment_remaining = r.amount_paid
        principal_payment = min(payment_remaining, running_balance)
        running_balance -= principal_payment

        db.session.add(LedgerEntry(
            loan_id=loan.id,
            date=r.date_paid.date(),
            particulars="Loan repayment",
            principal=principal_payment,
            interest=Decimal('0.00'),  # Update if interest allocation needed
            cumulative_interest=Decimal('0.00'),
            payment=payment_remaining,
            running_balance=running_balance
        ))

    db.session.commit()


def backfill_all_loans():
    with app.app_context():
        # Only loans for company_id = 5
        loans = Loan.query.filter_by(company_id=5).all()
        for loan in loans:
            backfill_single_loan(loan)


if __name__ == "__main__":
    from app import create_app
    app = create_app()
    with app.app_context():
        backfill_all_loans()
        print("Backfill completed for company_id = 5")
