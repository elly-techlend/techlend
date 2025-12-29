from decimal import Decimal
from app import create_app, db
from models import Loan, LedgerEntry

app = create_app()  # Make sure your Flask app factory is used
with app.app_context():
    print("USING DB:", db.engine.url)

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

    # ----------------------------
    # Loan Repayments (CORRECT)
    # ----------------------------
    repayments = sorted(loan.repayments, key=lambda r: r.date_paid)

    principal_balance = Decimal(loan.amount_borrowed)
    interest_balance = Decimal(loan.total_interest or 0)
    cumulative_interest_balance = Decimal('0.00')

    running_balance = principal_balance + interest_balance + cumulative_interest_balance

    for r in repayments:
        payment = Decimal(r.amount_paid or 0)

        # 1️⃣ Pay cumulative interest
        ci_payment = min(payment, cumulative_interest_balance)
        cumulative_interest_balance -= ci_payment
        payment -= ci_payment

        # 2️⃣ Pay interest
        interest_payment = min(payment, interest_balance)
        interest_balance -= interest_payment
        payment -= interest_payment

        # 3️⃣ Pay principal
        principal_payment = min(payment, principal_balance)
        principal_balance -= principal_payment
        payment -= principal_payment

        running_balance = (
            principal_balance +
            interest_balance +
            cumulative_interest_balance
        )

        db.session.add(LedgerEntry(
            loan_id=loan.id,
            date=r.date_paid.date(),
            particulars="Loan Repayment",
            payment=r.amount_paid,
            principal=principal_payment,
            interest=interest_payment,
            cumulative_interest=ci_payment,
            running_balance=running_balance
        ))

    db.session.commit()


def backfill_all_loans():
    with app.app_context():
         #Only loans for company_id = 3
        loans = Loan.query.filter_by(company_id=5).all()
        for loan in loans:
            backfill_single_loan(loan)

#def backfill_all_loans():
 #   with app.app_context():
  #      loan = Loan.query.filter_by(loan_id="C3-T00054").first()

   #     if not loan:
    #        print("Loan not found!")
     #       return

      #  backfill_single_loan(loan)

if __name__ == "__main__":
    backfill_all_loans()
    print("Backfill completed for company_id = 4")
