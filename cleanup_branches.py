from app import create_app, db
from models import Branch, Borrower, Loan, LoanRepayment, BankTransfer, CashbookEntry, Expense, LedgerEntry, Collateral, OtherIncome
from datetime import datetime

def hard_delete_branch_and_related(branch_id):
    """ Permanently delete a branch and all related records """
    # 1. Delete dependent records in the correct order

    # Loan repayments (must delete before loans)
    LoanRepayment.query.join(Loan).filter(Loan.branch_id == branch_id).delete(synchronize_session=False)

    # Collateral linked to loans in this branch
    Collateral.query.join(Loan).filter(Loan.branch_id == branch_id).delete(synchronize_session=False)

    # Loans
    Loan.query.filter_by(branch_id=branch_id).delete(synchronize_session=False)

    # Borrowers
    Borrower.query.filter_by(branch_id=branch_id).delete(synchronize_session=False)

    # Cashbook entries
    CashbookEntry.query.filter_by(branch_id=branch_id).delete(synchronize_session=False)

    # Ledger entries
    LedgerEntry.query.filter_by(branch_id=branch_id).delete(synchronize_session=False)

    # Bank transfers
    BankTransfer.query.filter_by(branch_id=branch_id).delete(synchronize_session=False)

    # Expenses
    Expense.query.filter_by(branch_id=branch_id).delete(synchronize_session=False)

    # Other incomes
    OtherIncome.query.filter_by(branch_id=branch_id).delete(synchronize_session=False)

    # 2. Delete the branch itself
    Branch.query.filter_by(id=branch_id).delete(synchronize_session=False)

    # Commit changes
    db.session.commit()
    print(f"Branch ID {branch_id} and all related data permanently deleted.")


def list_and_cleanup_soft_deleted_branches():
    app = create_app()
    with app.app_context():
        # Query soft deleted branches
        soft_deleted_branches = Branch.query.filter(Branch.deleted_at.isnot(None)).all()

        if not soft_deleted_branches:
            print("No soft deleted branches found.")
            return

        print(f"Found {len(soft_deleted_branches)} soft deleted branches:")
        for branch in soft_deleted_branches:
            print(f"ID: {branch.id} | Name: {branch.name} | Deleted At: {branch.deleted_at}")

        confirm = input("Do you want to permanently delete these branches and all related data? (yes/no): ").strip().lower()
        if confirm == 'yes':
            for branch in soft_deleted_branches:
                hard_delete_branch_and_related(branch.id)
            print("All selected branches have been permanently deleted.")
        else:
            print("No branches were deleted.")


if __name__ == "__main__":
    list_and_cleanup_soft_deleted_branches()
