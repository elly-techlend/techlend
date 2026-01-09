from models import CashbookEntry, Loan, SavingAccount
from extensions import db
from decimal import Decimal

def recalculate_balances(company_id, branch_id=None):
    """Recalculate running balances for cashbook entries"""
    entries = CashbookEntry.query.filter_by(company_id=company_id)
    if branch_id:
        entries = entries.filter_by(branch_id=branch_id)

    running_balance = Decimal('0.00')
    for entry in entries.order_by(CashbookEntry.date.asc(), CashbookEntry.id.asc()):
        debit = entry.debit or Decimal('0.00')
        credit = entry.credit or Decimal('0.00')
        running_balance += credit - debit
        entry.balance = running_balance
        db.session.add(entry)
    db.session.commit()

def ledger_to_cashbook(entry, created_by):
    """
    Convert a LedgerEntry (loan or saving) into a CashbookEntry.
    Automatically includes borrower name and correct debit/credit.
    """
    if entry.loan_id:
        # Loan entry
        loan = entry.loan
        borrower_name = loan.borrower_name if loan.borrower_name else "Unknown borrower"
        company_id = loan.company_id
        branch_id = loan.branch_id
        created_by = loan.created_by or 1
    else:
        # Savings entry
        saving = SavingAccount.query.filter_by(id=entry.saving_account_id).first() if hasattr(entry, 'saving_account_id') else None
        borrower_name = saving.borrower.name if saving else "Unknown borrower"
        company_id = saving.company_id if saving else None
        branch_id = saving.branch_id if saving else None
        created_by = 1  # fallback

    # Determine debit/credit
    particulars_lower = (entry.particulars or "").lower()
    if particulars_lower.startswith("loan repayment") or particulars_lower.startswith("savings withdrawal"):
        debit = Decimal(entry.payment or 0)
        credit = Decimal('0.00')
    else:
        debit = Decimal('0.00')
        credit = Decimal(entry.payment or 0)

    # Include borrower name in particulars
    cb_particulars = f"{entry.particulars} by {borrower_name}" if borrower_name else entry.particulars

    # Check if CashbookEntry already exists to avoid duplicates
    existing = CashbookEntry.query.filter_by(
        date=entry.date,
        particulars=cb_particulars,
        company_id=company_id,
        branch_id=branch_id
    ).first()
    if existing:
        return existing  # Skip creating duplicate

    cb_entry = CashbookEntry(
        date=entry.date,
        particulars=cb_particulars,
        debit=debit,
        credit=credit,
        balance=Decimal('0.00'),  # will recalc later
        company_id=company_id,
        branch_id=branch_id,
        created_by=created_by
    )

    db.session.add(cb_entry)
    db.session.commit()

    return cb_entry
