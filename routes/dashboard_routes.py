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

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

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
    """Home page — serves the welcome dashboard with loans and tools."""
    
    # Get active branch if set
    active_branch_id = session.get('active_branch_id')

    # Filter loans by company (and branch if active)
    loans = Loan.query.filter_by(company_id=current_user.company_id)\
                      .filter_by(branch_id=active_branch_id) if active_branch_id else \
            Loan.query.filter_by(company_id=current_user.company_id)
    loans = loans.all()

    # Placeholder selected loan (optional)
    selected_loan = None

    # Revision groups (tool cards)
    revision_groups = [
        {
            "title": "Forex Rates",
            "items": [
                {"name": "USD", "endpoint": "#"},
                {"name": "GBP", "endpoint": "#"},
                {"name": "KES", "endpoint": "#"},
                {"name": "TZS", "endpoint": "#"},
                {"name": "RWF", "endpoint": "#"}
            ]
        },
        {
            "title": "Reports and Payments",
            "items": [
                {"name": "Daily Report", "endpoint": "#"},
                {"name": "Weekly Report", "endpoint": "#"},
                {"name": "Monthly Report", "endpoint": "#"},
                {"name": "Make Payment Receipt", "endpoint": "voucher_bp.vouchers"},
                {"name": "Generate Invoice", "endpoint": "#"}
            ]
        },
        {
            "title": "Other Tools",
            "items": [
                {"name": "Cash Calculator", "endpoint": "#"},
                {"name": "Send Demand Notice", "endpoint": "#"},
                {"name": "Export Data", "endpoint": "#"},
                {"name": "Collateral for sale", "endpoint": "#"},
                {"name": "Last activity", "endpoint": "#"}
            ]
        }
    ]

    # Last update timestamp
    last_update = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return render_template(
        'dashboard/home.html',
        current_user=current_user,
        revision_groups=revision_groups,
        selected_loan=selected_loan,
        last_update=last_update,
        loans=loans  # Optional: pass loans if needed elsewhere
    )

@dashboard_bp.route('/dashboard')
@login_required
def dashboard():
    active_branch_id = session.get('active_branch_id')
    today = date.today()
    now = datetime.now()
    current_year = now.year
    current_month = now.month

    # Start/end of today for SQLAlchemy comparisons
    today_start = datetime.combine(today, datetime.min.time())
    today_end = datetime.combine(today, datetime.max.time())

    # Filter loans for this company (and branch if set)
    loan_query = Loan.query.filter_by(company_id=current_user.company_id)
    if active_branch_id:
        loan_query = loan_query.filter_by(branch_id=active_branch_id)
    loans = loan_query.all()

    if not loans:
        return render_template('dashboard/dashboard.html', message="No loans found for your company.")

    # --- Borrowers ---
    borrowers_today = len({l.borrower_name for l in loans if l.date.date() == today})
    borrowers_month = len({l.borrower_name for l in loans if l.date.year == current_year and l.date.month == current_month})
    borrowers_year = len({l.borrower_name for l in loans if l.date.year == current_year})
    total_borrowers = len({l.borrower_name for l in loans})

    # --- Disbursed ---
    disbursed_today = sum(l.amount_borrowed for l in loans if l.date.date() == today)
    disbursed_month = sum(l.amount_borrowed for l in loans if l.date.year == current_year and l.date.month == current_month)
    disbursed_year = sum(l.amount_borrowed for l in loans if l.date.year == current_year)
    total_disbursed = sum(l.amount_borrowed for l in loans)

    # --- Repaid ---
    repayments_query = LoanRepayment.query.join(Loan).filter(Loan.company_id == current_user.company_id)
    if active_branch_id:
        repayments_query = repayments_query.filter(Loan.branch_id == active_branch_id)
    repayments = repayments_query.all()

    repaid_today = sum(r.amount_paid for r in repayments if r.date_paid.date() == today)
    collections_month = sum(r.amount_paid for r in repayments if r.date_paid.year == current_year and r.date_paid.month == current_month)
    collections_year = sum(r.amount_paid for r in repayments if r.date_paid.year == current_year)
    total_repaid = sum(r.amount_paid for r in repayments)

    # --- Overdue Loans ---
    overdue_loans_query = Loan.query.filter(
        Loan.company_id == current_user.company_id,
        Loan.remaining_balance > 0,
        Loan.due_date < today_end  # <-- datetime compatible
    )
    if active_branch_id:
        overdue_loans_query = overdue_loans_query.filter(Loan.branch_id == active_branch_id)
    overdue_loans = overdue_loans_query.order_by(Loan.due_date.asc()).limit(5).all()

    overdue_today = len([l for l in loans if l.remaining_balance > 0 and l.due_date <= today_end and l.due_date >= today_start])
    overdue_month = len([l for l in loans if l.remaining_balance > 0 and l.due_date.year == current_year and l.due_date.month == current_month])
    overdue_year = len([l for l in loans if l.remaining_balance > 0 and l.due_date.year == current_year])

    # Total overdue loans (all, not limited)
    total_overdue = len([l for l in loans if l.remaining_balance > 0])

    # --- Monthly chart data ---
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
        monthly_data[month_key]["total_interest"] += float(loan.total_due - loan.amount_borrowed if hasattr(loan, "total_due") else loan.amount_borrowed * Decimal('0.2'))

    sorted_months = sorted(monthly_data.keys())
    months = [datetime.strptime(m, "%Y-%m").strftime("%b %Y") for m in sorted_months]
    loans_disbursed = [monthly_data[m]["total_disbursed"] for m in sorted_months]
    loans_repaid = [monthly_data[m]["total_paid"] for m in sorted_months]
    remaining_balances = [monthly_data[m]["total_remaining"] for m in sorted_months]
    interest_earned = [monthly_data[m]["total_interest"] for m in sorted_months]

    # --- Last update ---
    latest_loan = max(loans, key=lambda l: l.date, default=None)
    if latest_loan:
        local_tz = pytz.timezone("Africa/Kampala")
        local_date = latest_loan.date.astimezone(local_tz)
        last_update = local_date.strftime("%d %b %Y, %I:%M %p")
    else:
        last_update = "No data"

    return render_template(
        'dashboard/dashboard.html',
        total_borrowers=total_borrowers,
        borrowers_today=borrowers_today,
        borrowers_month=borrowers_month,
        borrowers_year=borrowers_year,
        total_disbursed=total_disbursed,
        disbursed_today=disbursed_today,
        disbursed_month=disbursed_month,
        disbursed_year=disbursed_year,
        total_repaid=total_repaid,
        repaid_today=repaid_today,
        collections_month=collections_month,
        collections_year=collections_year,
        overdue_loans=overdue_loans,
        overdue_today=overdue_today,
        overdue_month=overdue_month,
        overdue_year=overdue_year,
        months=months,
        loans_disbursed=loans_disbursed,
        loans_repaid=loans_repaid,
        remaining_balances=remaining_balances,
        interest_earned=interest_earned,
        last_update=last_update,
        user=current_user,
        company=current_user.company
    )

@dashboard_bp.route('dashboard/loan_data')
@login_required
def loan_data():
    today = date.today()
    now = datetime.now()
    current_year = now.year
    current_month = now.month

    today_start = datetime.combine(today, datetime.min.time())
    today_end = datetime.combine(today, datetime.max.time())

    branch_id = session.get('active_branch_id')

    # Filter loans
    loan_query = Loan.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        loan_query = loan_query.filter_by(branch_id=branch_id)
    loans = loan_query.all()

    if not loans:
        return jsonify({})  # empty fallback

    # Borrowers
    borrowers_today = len({l.borrower_name for l in loans if l.date.date() == today})
    borrowers_month = len({l.borrower_name for l in loans if l.date.year == current_year and l.date.month == current_month})
    borrowers_year = len({l.borrower_name for l in loans if l.date.year == current_year})

    # Disbursed
    disbursed_today = sum(l.amount_borrowed for l in loans if l.date.date() == today)
    disbursed_month = sum(l.amount_borrowed for l in loans if l.date.year == current_year and l.date.month == current_month)
    disbursed_year = sum(l.amount_borrowed for l in loans if l.date.year == current_year)
    total_borrowed = sum(l.amount_borrowed for l in loans)

    # Repaid
    repayments_query = LoanRepayment.query.join(Loan).filter(Loan.company_id == current_user.company_id)
    if branch_id:
        repayments_query = repayments_query.filter(Loan.branch_id == branch_id)
    repayments = repayments_query.all()

    repaid_today = sum(r.amount_paid for r in repayments if r.date_paid.date() == today)
    collections_month = sum(r.amount_paid for r in repayments if r.date_paid.year == current_year and r.date_paid.month == current_month)
    collections_year = sum(r.amount_paid for r in repayments if r.date_paid.year == current_year)
    total_paid = sum(r.amount_paid for r in repayments)

    # Overdue
    overdue_today = len([l for l in loans if l.remaining_balance > 0 and l.due_date <= today_end and l.due_date >= today_start])
    overdue_month = len([l for l in loans if l.remaining_balance > 0 and l.due_date.year == current_year and l.due_date.month == current_month])
    overdue_year = len([l for l in loans if l.remaining_balance > 0 and l.due_date.year == current_year])
    total_remaining = sum(l.remaining_balance for l in loans)

    # Monthly chart data
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
        monthly_data[month_key]["total_interest"] += float(loan.total_due - loan.amount_borrowed if hasattr(loan, "total_due") else loan.amount_borrowed * Decimal('0.2'))

    sorted_months = sorted(monthly_data.keys())
    months = [datetime.strptime(m, "%Y-%m").strftime("%b %Y") for m in sorted_months]
    loans_disbursed = [monthly_data[m]["total_disbursed"] for m in sorted_months]
    loans_repaid = [monthly_data[m]["total_paid"] for m in sorted_months]
    remaining_balances = [monthly_data[m]["total_remaining"] for m in sorted_months]
    interest_earned = [monthly_data[m]["total_interest"] for m in sorted_months]

    return jsonify({
        "borrowers_today": borrowers_today,
        "borrowers_month": borrowers_month,
        "borrowers_year": borrowers_year,
        "disbursed_today": float(disbursed_today),
        "disbursed_month": float(disbursed_month),
        "disbursed_year": float(disbursed_year),
        "repaid_today": float(repaid_today),
        "collections_month": float(collections_month),
        "collections_year": float(collections_year),
        "overdue_today": overdue_today,
        "overdue_month": overdue_month,
        "overdue_year": overdue_year,
        "total_borrowed": float(total_borrowed),
        "total_paid": float(total_paid),
        "total_remaining": float(total_remaining),
        "months": months,
        "loans_disbursed": loans_disbursed,
        "loans_repaid": loans_repaid,
        "remaining_balances": remaining_balances,
        "interest_earned": interest_earned
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
