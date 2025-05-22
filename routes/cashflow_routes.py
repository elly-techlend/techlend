from flask import Blueprint, render_template, request, session
from flask_login import login_required, current_user
from models import CashbookEntry, OtherIncome
from datetime import datetime
from utils.decorators import roles_required

cashflow_bp = Blueprint('cashflow', __name__)

@cashflow_bp.route('/cash-flow')
@login_required
@roles_required('Admin')
def view_cash_flow():
    branch_id = session.get('active_branch_id')  # Get active branch from session
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    start = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
    end = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None

    # Base queries filtered by company
    query_cashbook = CashbookEntry.query.filter(CashbookEntry.company_id == current_user.company_id)
    query_income = OtherIncome.query.filter(
        OtherIncome.company_id == current_user.company_id,
        OtherIncome.is_active == True
    )

    # Branch filter applied ONLY if user is NOT superuser and branch_id is set in session
    if not current_user.is_superuser and branch_id:
        query_cashbook = query_cashbook.filter(CashbookEntry.branch_id == branch_id)
        query_income = query_income.filter(OtherIncome.branch_id == branch_id)

    # Date filters
    if start:
        query_cashbook = query_cashbook.filter(CashbookEntry.date >= start)
        query_income = query_income.filter(OtherIncome.date_received >= start)
    if end:
        query_cashbook = query_cashbook.filter(CashbookEntry.date <= end)
        query_income = query_income.filter(OtherIncome.date_received <= end)

    cashbook_entries = query_cashbook.all()
    other_income_entries = query_income.all()

    total_in = sum(entry.credit for entry in cashbook_entries)
    total_out = sum(entry.debit for entry in cashbook_entries)
    net_flow = total_in - total_out

    return render_template(
        'cashflow/view_cashflow.html',
        cashbook=cashbook_entries,
        income=other_income_entries,
        total_in=total_in,
        total_out=total_out,
        net_flow=net_flow,
        branch_id=branch_id
    )
