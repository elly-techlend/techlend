from flask import Blueprint, render_template, jsonify
from flask_login import login_required, current_user
from extensions import db
from models import Company, Branch, Loan, LoanRepayment
from datetime import datetime, date
from collections import defaultdict
from flask import session
import pytz
from extensions import csrf
from decimal import Decimal, InvalidOperation

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.before_app_request
def set_active_branch_context():
    if current_user.is_authenticated and not current_user.is_superuser:
        # Get the active branch from session or user's assigned branch
        active_branch_id = session.get('active_branch_id', current_user.branch_id)

        # Validate branch is not deleted
        branch = Branch.query.filter_by(
            id=active_branch_id,
            company_id=current_user.company_id
        ).filter(Branch.deleted_at.is_(None)).first()

        if branch:
            session['active_branch_id'] = branch.id
            session['active_branch_name'] = branch.name  # Add branch name to session
        else:
            # Fallback to user's assigned branch if available
            fallback_branch = Branch.query.filter_by(
                id=current_user.branch_id,
                company_id=current_user.company_id
            ).filter(Branch.deleted_at.is_(None)).first()

            if fallback_branch:
                session['active_branch_id'] = fallback_branch.id
                session['active_branch_name'] = fallback_branch.name  # Add fallback branch name
            else:
                # No valid branch, clear the session keys
                session.pop('active_branch_id', None)
                session.pop('active_branch_name', None)

@dashboard_bp.route('/switch-branch/<int:branch_id>', methods=['POST'])
@login_required
def switch_branch(branch_id):
    branch_query = Branch.query.filter_by(id=branch_id).filter(Branch.deleted_at.is_(None))

    if 'superuser' in current_user.roles:
        # Superuser can switch to any non-deleted branch
        branch = branch_query.first_or_404()

    elif 'admin' in current_user.roles:
        # Admin can only switch to branches in their own company
        branch = branch_query.filter_by(company_id=current_user.company_id).first_or_404()

    else:
        flash('You do not have permission to switch branches.', 'danger')
        return redirect(request.referrer or url_for('dashboard.index'))

    session['active_branch_id'] = branch.id
    session['active_branch_name'] = branch.name  # Store the branch name for display
    flash(f"Switched to branch: {branch.name}", 'success')

    return redirect(request.referrer or url_for('dashboard.index'))

@dashboard_bp.route('/')
@login_required
def index():
    active_branch_id = session.get('active_branch_id')

    # Filter loans for this company (and branch if set)
    loan_query = Loan.query.filter_by(company_id=current_user.company_id)
    if active_branch_id:
        loan_query = loan_query.filter_by(branch_id=active_branch_id)
    loans = loan_query.all()

    if not loans:
        return render_template('home.html', message="No loans found for your company.")

    # Basic counts
    total_borrowers = len(set(l.borrower_name for l in loans))
    total_loans = len(loans)

    # Totals
    total_disbursed = sum(l.amount_borrowed for l in loans)
    total_repaid = sum(l.amount_paid for l in loans)
    total_balance = sum(l.remaining_balance for l in loans)
    total_interest = sum(
        l.total_due - l.amount_borrowed if hasattr(l, "total_due") else l.amount_borrowed * Decimal("0.2")
        for l in loans
    )

    # Date filtering
    now = datetime.now()
    current_year = now.year
    current_month = now.month

    # Borrowers
    borrowers_year = len({l.borrower_name for l in loans if l.date.year == current_year})
    borrowers_month = len({l.borrower_name for l in loans if l.date.year == current_year and l.date.month == current_month})

    # Disbursed
    disbursed_year = sum(l.amount_borrowed for l in loans if l.date.year == current_year)
    disbursed_month = sum(l.amount_borrowed for l in loans if l.date.year == current_year and l.date.month == current_month)

    # Repaid — use repayment dates if you have LoanRepayment model
    repayments_query = LoanRepayment.query.join(Loan).filter(Loan.company_id == current_user.company_id)
    if active_branch_id:
        repayments_query = repayments_query.filter(Loan.branch_id == active_branch_id)
    repayments = repayments_query.all()

    collections_year = sum(r.amount_paid for r in repayments if r.date_paid.year == current_year)
    collections_month = sum(r.amount_paid for r in repayments if r.date_paid.year == current_year and r.date_paid.month == current_month)

    # Overdue loans
    overdue_loans_query = Loan.query.filter(
        Loan.company_id == current_user.company_id,
        Loan.due_date < date.today(),
        Loan.remaining_balance > 0
    )
    if active_branch_id:
        overdue_loans_query = overdue_loans_query.filter(Loan.branch_id == active_branch_id)
    overdue_loans = overdue_loans_query.order_by(Loan.due_date.asc()).limit(5).all()

    # Monthly chart data
    monthly_data = defaultdict(lambda: {
        "total_disbursed": 0,
        "total_paid": 0,
        "total_remaining": 0,
        "total_interest": 0
    })

    for loan in loans:
        month_key = loan.date.strftime('%Y-%m')
        monthly_data[month_key]["total_disbursed"] += loan.amount_borrowed
        monthly_data[month_key]["total_paid"] += loan.amount_paid
        monthly_data[month_key]["total_remaining"] += loan.remaining_balance
        monthly_data[month_key]["total_interest"] += (
            loan.total_due - loan.amount_borrowed if hasattr(loan, "total_due") else loan.amount_borrowed * 0.2
        )

    sorted_months = sorted(monthly_data.keys())
    months = [datetime.strptime(m, "%Y-%m").strftime("%b %Y") for m in sorted_months]
    loans_disbursed = [monthly_data[m]["total_disbursed"] for m in sorted_months]
    loans_repaid = [monthly_data[m]["total_paid"] for m in sorted_months]
    remaining_balances = [monthly_data[m]["total_remaining"] for m in sorted_months]
    interest_earned = [monthly_data[m]["total_interest"] for m in sorted_months]

    # Last update
    latest_loan = max(loans, key=lambda l: l.date, default=None)
    if latest_loan:
        local_tz = pytz.timezone("Africa/Kampala")
        local_date = latest_loan.date.astimezone(local_tz)
        last_update = local_date.strftime("%d %b %Y, %I:%M %p")
    else:
        last_update = "No data"

    return render_template(
        'home.html',
        total_borrowers=total_borrowers,
        borrowers_year=borrowers_year,
        borrowers_month=borrowers_month,
        total_disbursed=total_disbursed,
        disbursed_year=disbursed_year,
        disbursed_month=disbursed_month,
        total_repaid=total_repaid,
        collections_year=collections_year,
        collections_month=collections_month,
        total_balance=total_balance,
        total_interest=total_interest,
        total_loans=total_loans,
        months=months,
        loans_disbursed=loans_disbursed,
        loans_repaid=loans_repaid,
        remaining_balances=remaining_balances,
        interest_earned=interest_earned,
        overdue_loans=overdue_loans,
        last_update=last_update,
        user=current_user,
        company=current_user.company
    )

@dashboard_bp.route('/loan_data')
@login_required
def loan_data():
    current_year = datetime.now().year
    current_month = datetime.now().month

    branch_id = session.get('active_branch_id')  # 👈 Get active branch from session

    # Apply branch filtering if set
    loan_query = Loan.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        loan_query = loan_query.filter_by(branch_id=branch_id)

    loans = loan_query.all()

    if not loans:
        return jsonify({
            "total_borrowed": 0,
            "total_paid": 0,
            "total_remaining": 0,
            "months": [],
            "loans_disbursed": [],
            "loans_repaid": [],
            "remaining_balances": [],
            "interest_earned": [],
            "total_borrowers_this_year": 0,
            "total_borrowers_this_month": 0
        })

    total_borrowed = sum(l.amount_borrowed for l in loans)
    total_paid = sum(l.amount_paid for l in loans)
    total_remaining = sum(l.remaining_balance for l in loans)

    monthly_data = defaultdict(lambda: {
        "total_disbursed": 0,
        "total_paid": 0,
        "total_remaining": 0,
        "total_interest": 0
    })

    for loan in loans:
        month_key = loan.date.strftime('%Y-%m')
        monthly_data[month_key]["total_disbursed"] += float(loan.amount_borrowed)
        monthly_data[month_key]["total_paid"] += float(loan.amount_paid)
        monthly_data[month_key]["total_remaining"] += float(loan.remaining_balance)
        monthly_data[month_key]["total_interest"] += loan.amount_borrowed * Decimal('0.2')

    sorted_months = sorted(monthly_data.keys())
    months = [datetime.strptime(m, "%Y-%m").strftime("%b %Y") for m in sorted_months]
    loans_disbursed = [monthly_data[m]["total_disbursed"] for m in sorted_months]
    loans_repaid = [monthly_data[m]["total_paid"] for m in sorted_months]
    remaining_balances = [monthly_data[m]["total_remaining"] for m in sorted_months]
    interest_earned = [monthly_data[m]["total_interest"] for m in sorted_months]

    total_borrowers_this_year = len(set(
        loan.borrower_name for loan in loans if loan.date.year == current_year
    ))
    total_borrowers_this_month = len(set(
        loan.borrower_name for loan in loans if loan.date.year == current_year and loan.date.month == current_month
    ))

    return jsonify({
        'total_borrowers_this_year': total_borrowers_this_year,
        'total_borrowers_this_month': total_borrowers_this_month,
        "total_borrowed": float(total_borrowed),
        "total_paid": float(total_paid),
        "total_remaining": float(total_remaining),
        "months": months,
        "loans_disbursed": [float(x) for x in loans_disbursed],
        "loans_repaid": [float(x) for x in loans_repaid],
        "remaining_balances": [float(x) for x in remaining_balances],
        "interest_earned": [float(x) for x in interest_earned]
    })

@dashboard_bp.route('/summary_data')
@login_required
def summary_data():
    branch_id = session.get('active_branch_id')  # 👈 Get active branch from session

    # Filter loans by company (and branch if applicable)
    loan_query = Loan.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        loan_query = loan_query.filter_by(branch_id=branch_id)

    loans = loan_query.all()

    if not loans:
        return jsonify({
            "total_borrowers": total_borrowers,
            "total_loans": total_loans,
            "total_borrowed": float(total_borrowed),
            "total_paid": float(total_paid),
            "total_remaining": float(total_remaining),
            "total_interest": float(total_interest),
            "last_updated": last_updated
        })

    total_borrowers = len(set(loan.borrower_name for loan in loans))
    total_loans = len(loans)
    total_borrowed = float(sum(l.amount_borrowed for l in loans))
    total_paid = sum(l.amount_paid for l in loans)
    total_remaining = sum(l.remaining_balance for l in loans)
    total_interest = sum(l.amount_borrowed * 0.2 for l in loans)

    last_loan = max(loans, key=lambda l: l.date)
    last_updated = last_loan.date.strftime("%B %d, %Y")

    return jsonify({
        "total_borrowers": total_borrowers,
        "total_loans": total_loans,
        "total_borrowed": total_borrowed,
        "total_paid": total_paid,
        "total_remaining": total_remaining,
        "total_interest": total_interest,
        "last_updated": last_updated
    })
