from flask import Blueprint, render_template, request, send_file, make_response, session, flash
from flask_login import login_required, current_user
from utils.decorators import superuser_required, roles_required, admin_or_superuser_required
from datetime import datetime, timedelta
from utils.logging import log_company_action, log_system_action, log_action
from xhtml2pdf import pisa
import io
from models import Borrower, Loan, LoanRepayment
from extensions import db
from flask import session
from sqlalchemy.orm import joinedload
from io import BytesIO
import calendar
from sqlalchemy import func

# Create the blueprint
repayment_bp = Blueprint('repayments', __name__)

# Replace @app.route with @repayment_bp.route
@repayment_bp.route('/repayments', methods=['GET'])
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans_Officer', 'Loans Supervisor', 'Cashier')
def all_repayments():
    branch_id = session.get('active_branch_id')

    repayments_query = LoanRepayment.query\
        .join(Loan)\
        .join(Borrower, Borrower.id == Loan.borrower_id)\
        .filter(Loan.company_id == current_user.company_id)

    if branch_id:
        repayments_query = repayments_query.filter(Loan.branch_id == branch_id)

    filter_type = request.args.get('filter')  # <-- read 'filter' parameter from template
    today = datetime.today().date()

    if filter_type == 'today':
        repayments_query = repayments_query.filter(func.date(LoanRepayment.date_paid) == today)
    elif filter_type == 'weekly':
        start_week = today - timedelta(days=today.weekday())  # Monday
        end_week = start_week + timedelta(days=7)
        repayments_query = repayments_query.filter(
            LoanRepayment.date_paid >= start_week,
            LoanRepayment.date_paid < end_week
        )
    elif filter_type == 'monthly':
        month = int(request.args.get('month', today.month))
        year = int(request.args.get('year', today.year))
        start_date = datetime(year, month, 1)
        month_days = calendar.monthrange(year, month)[1]
        end_date = start_date + timedelta(days=month_days)
        end_date = end_date.replace(day=1)  # first day of next month
        repayments_query = repayments_query.filter(
            LoanRepayment.date_paid >= start_date,
            LoanRepayment.date_paid < end_date
        )
    elif filter_type == 'yearly':
        year = int(request.args.get('year', today.year))
        start_date = datetime(year, 1, 1)
        end_date = datetime(year, 12, 31, 23, 59, 59)
        repayments_query = repayments_query.filter(
            LoanRepayment.date_paid >= start_date,
            LoanRepayment.date_paid <= end_date
        )

    repayments = repayments_query.order_by(LoanRepayment.date_paid.desc()).all()
    total_collected = sum(r.amount_paid for r in repayments)

    return render_template(
        'repayments/view_repayments.html',
        repayments=repayments,
        total_collected=total_collected,
        filter=filter_type,
        months=[(i, calendar.month_name[i]) for i in range(1, 13)],
        years=list(range(2020, today.year + 1)),
        selected_month=int(request.args.get('month', today.month)),
        selected_year=int(request.args.get('year', today.year))
    )

@repayment_bp.route('/repayments/export/pdf')
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans_Officer', 'Loans Supervisor', 'Cashier')
def export_pdf():
    branch_id = session.get('active_branch_id')
    filter_type = request.args.get('filter_type', 'all')  # default to all
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    # Base query
    repayments_query = LoanRepayment.query.join(Loan).join(Borrower)\
        .options(joinedload(LoanRepayment.loan).joinedload(Loan.borrower))\
        .filter(Loan.company_id == current_user.company_id)

    # Filter by branch
    if branch_id:
        repayments_query = repayments_query.filter(Loan.branch_id == branch_id)

    # Date filtering
    today = datetime.today().date()
    if filter_type == 'today':
        repayments_query = repayments_query.filter(func.date(LoanRepayment.date_paid) == today)
        filter_label = f"Today ({today.strftime('%Y-%m-%d')})"
    elif filter_type == 'month':
        repayments_query = repayments_query.filter(
            extract('year', LoanRepayment.date_paid) == today.year,
            extract('month', LoanRepayment.date_paid) == today.month
        )
        filter_label = f"This Month ({today.strftime('%B %Y')})"
    elif filter_type == 'year':
        repayments_query = repayments_query.filter(
            extract('year', LoanRepayment.date_paid) == today.year
        )
        filter_label = f"This Year ({today.year})"
    elif filter_type == 'custom' and start_date and end_date:
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d').date()
            end = datetime.strptime(end_date, '%Y-%m-%d').date()
            repayments_query = repayments_query.filter(
                func.date(LoanRepayment.date_paid).between(start, end)
            )
            filter_label = f"Custom Range ({start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')})"
        except ValueError:
            filter_label = "All"
    else:
        filter_label = "All"

    repayments = repayments_query.all()
    total_collected = sum(r.amount_paid for r in repayments)

    # Render HTML
    html = render_template(
        'repayments/pdf_template.html',
        repayments=repayments,
        total_collected=total_collected,
        filter_label=filter_label
    )

    # Convert to PDF
    pdf = BytesIO()
    pisa.CreatePDF(BytesIO(html.encode('utf-8')), dest=pdf)

    response = make_response(pdf.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=repayments_{today}.pdf'

    return response

@repayment_bp.route('/repayments/charts')
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans_Officer', 'Loans Supervisor', 'Cashier')
def repayment_charts():
    branch_id = session.get('active_branch_id')  # 👈 Get active branch from session

    # Filter loans by company (and branch if applicable)
    loan_query = Loan.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        loan_query = loan_query.filter_by(branch_id=branch_id)

    filter_option = request.args.get('filter', 'all')
    query = LoanRepayment.query

    if not current_user.is_superuser:
        query = query.join(Loan).filter(Loan.company_id == current_user.company_id)

    today = datetime.today().date()
    if filter_option == 'today':
        query = query.filter(db.func.date(LoanRepayment.date_paid) == today)
    elif filter_option == 'week':
        start_of_week = today - timedelta(days=today.weekday())
        query = query.filter(db.func.date(LoanRepayment.date_paid) >= start_of_week)
    elif filter_option == 'month':
        start_of_month = today.replace(day=1)
        query = query.filter(db.func.date(LoanRepayment.date_paid) >= start_of_month)

    repayments = query.all()
    total_amount = sum(r.amount_paid for r in repayments)

    return render_template(
        'repayments/repayment_charts.html',
        repayments=repayments,
        total_amount=total_amount,
        filter_option=filter_option
    )
