# utils/branch_filter.py
from flask_login import current_user
from flask import session
from models import Borrower  # import others as needed

def filter_by_active_branch(query, model=None, borrower_join=False):
    branch_id = session.get('active_branch_id')
    
    # If no branch is selected, return nothing (you can change this to raise an error or fallback)
    if not branch_id:
        return query.filter(False)  # returns empty result safely

    # Allow filtering for both superuser and admin
    if borrower_join:
        return query.join(Borrower).filter(Borrower.branch_id == branch_id)
    elif model and hasattr(model, 'branch_id'):
        return query.filter(model.branch_id == branch_id)

    return query  # fallback, unfiltered
