from decimal import Decimal
from app import create_app, db
from models import Loan, LedgerEntry, LoanRepayment
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy import create_engine

# ------------------ CONFIG ------------------
app = create_app()

# Backup DB connection
BACKUP_DB_URL = "postgresql+psycopg://techlend_db_user:***@dpg-d553v9muk2gs73bmhe3g-a.singapore-postgres.render.com/techlend_db_ost1"
backup_engine = create_engine(BACKUP_DB_URL)
BackupSession = scoped_session(sessionmaker(bind=backup_engine))
backup_session = BackupSession()

LOAN_ID_TO_MIGRATE = "C3-T00061"

def migrate_loan():
    with app.app_context():
        # Fetch loan from backup
        backup_loan = backup_session.query(Loan).filter_by(loan_id=LOAN_ID_TO_MIGRATE).first()
        if not backup_loan:
            print("Loan not found in backup DB!")
            return

        # Check if loan already exists in primary DB
        existing = Loan.query.filter_by(loan_id=LOAN_ID_TO_MIGRATE).first()
        if existing:
            print("Loan already exists in primary DB!")
            return

        # Copy loan to primary DB
        loan = Loan(
            loan_id=backup_loan.loan_id,
            borrower_id=backup_loan.borrower_id,
            borrower_name=backup_loan.borrower_name,
            phone_number=backup_loan.phone_number,
            amount_borrowed=backup_loan.amount_borrowed,
            processing_fee=backup_loan.processing_fee,
            interest_rate=backup_loan.interest_rate,
            total_due=backup_loan.total_due,
            amount_paid=backup_loan.amount_paid,
            remaining_balance=backup_loan.remaining_balance,
            loan_duration_value=backup_loan.loan_duration_value,
            loan_duration_unit=backup_loan.loan_duration_unit,
            collateral=backup_loan.collateral,
            date=backup_loan.date,
            due_date=backup_loan.due_date,
            status=backup_loan.status,
            approval_status=backup_loan.approval_status,
            company_id=backup_loan.company_id,
            created_by=backup_loan.created_by,
            branch_id=backup_loan.branch_id
        )
        db.session.add(loan)
        db.session.flush()  # get loan.id for ledger

        # Copy Ledger Entries
        backup_ledgers = backup_session.query(LedgerEntry).filter_by(loan_id=backup_loan.id).all()
        for l in backup_ledgers:
            db.session.add(LedgerEntry(
                loan_id=loan.id,
                date=l.date,
                particulars=l.particulars,
                principal=l.principal,
                interest=l.interest,
                cumulative_interest=l.cumulative_interest,
                payment=l.payment,
                running_balance=l.running_balance,
                principal_balance=getattr(l, 'principal_balance', l.principal),
                interest_balance=getattr(l, 'interest_balance', l.interest),
                penalty_balance=getattr(l, 'penalty_balance', 0)
            ))

        # Copy Repayments if model exists
        if hasattr(backup_loan, "repayments"):
            for r in backup_loan.repayments:
                db.session.add(LoanRepayment(
                    loan_id=loan.id,
                    amount_paid=r.amount_paid,
                    date_paid=r.date_paid,
                    balance_after=r.balance_after
                ))

        db.session.commit()
        print(f"Loan {loan.loan_id} successfully migrated to primary DB!")

if __name__ == "__main__":
    migrate_loan()
