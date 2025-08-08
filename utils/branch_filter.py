# utils/branch_filter.py
from flask_login import current_user
from flask import session
from models import Borrower, Branch

def filter_by_active_branch(query, model=None, borrower_join=False):
    # If superuser, no filtering needed — they can see all data
    if 'superuser' in getattr(current_user, 'roles', []):
        return query

    branch_id = session.get('active_branch_id')
    if not branch_id:
        # No active branch selected — return empty result safely
        return query.filter(False)

    # Verify the branch is valid, belongs to current user's company, and not deleted
    branch = Branch.query.filter_by(id=branch_id, company_id=current_user.company_id).filter(Branch.deleted_at.is_(None)).first()
    if not branch:
        # Invalid branch in session — return empty result safely
        return query.filter(False)

    if borrower_join:
        # Join Borrower and filter by borrower's branch
        return query.join(Borrower).filter(Borrower.branch_id == branch.id)
    elif model and hasattr(model, 'branch_id'):
        # Filter by model's branch_id
        return query.filter(model.branch_id == branch.id)

    # Fallback: return unfiltered query if conditions don't apply
    return query
