# scripts/backfill_all_loans.py

from app import db
from models import Loan, LoanRepayment, LedgerEntry
from sqlalchemy import func

def backfill_ledger():
    loans = Loan.query.all()
    for loan in loans:
        print(f"Backfilling Loan ID: {loan.loan_id}, Borrower: {loan.borrower.name}")
        
        # Check if ledger entries already exist
        existing_entries = LedgerEntry.query.filter_by(loan_id=loan.id).count()
        if existing_entries > 0:
            print("Ledger already exists, skipping...")
            continue

        # 1. Insert initial ledger entries
        entries = [
            LedgerEntry(
                loan_id=loan.id,
                date=loan.date,
                principal=loan.amount_borrowed,
                interest=loan.interest_rate * loan.amount_borrowed / 100,
                cumulative_interest=0,
                payment=0.0,
                particulars="Loan Application",
                running_balance=loan.amount_borrowed + (loan.interest_rate * loan.amount_borrowed / 100)
            ),
            LedgerEntry(
                loan_id=loan.id,
                date=loan.date,
                principal=loan.amount_borrowed,
                interest=loan.interest_rate * loan.amount_borrowed / 100,
                cumulative_interest=0,
                payment=0.0,
                particulars="Loan Approved",
                running_balance=loan.amount_borrowed + (loan.interest_rate * loan.amount_borrowed / 100)
            ),
            LedgerEntry(
                loan_id=loan.id,
                date=loan.date,
                principal=loan.amount_borrowed,
                interest=0.0,
                cumulative_interest=0,
                payment=0.0,
                particulars="Loan Disbursed",
                running_balance=loan.amount_borrowed + (loan.interest_rate * loan.amount_borrowed / 100)
            )
        ]
        db.session.add_all(entries)
        db.session.flush()  # flush to get IDs

        # 2. Insert repayments
        repayments = LoanRepayment.query.filter_by(loan_id=loan.id).order_by(LoanRepayment.date_paid, LoanRepayment.id).all()
        running_balance = loan.amount_borrowed + (loan.interest_rate * loan.amount_borrowed / 100)

        for rep in repayments:
            principal_paid = min(rep.amount_paid, running_balance)  # allocate to principal if needed
            interest_paid = rep.amount_paid - principal_paid
            running_balance -= rep.amount_paid
            
            entry = LedgerEntry(
                loan_id=loan.id,
                date=rep.date_paid,
                principal=principal_paid,
                interest=interest_paid,
                cumulative_interest=0,
                payment=rep.amount_paid,
                particulars="Loan repayment",
                running_balance=running_balance
            )
            db.session.add(entry)

        db.session.commit()
        print(f"Backfill completed for Loan ID: {loan.loan_id}")

if __name__ == "__main__":
    backfill_ledger()
