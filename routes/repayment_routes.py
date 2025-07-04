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

    # Filter by period (day, month, year)
    period = request.args.get('period')
    date_str = request.args.get('date')

    if date_str:
        try:
            filter_date = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            flash('Invalid date format. Please use YYYY-MM-DD.', 'danger')
            return render_template('repayments/view_repayments.html', repayments=[], total_collected=0)

        if period == 'day':
            repayments_query = repayments_query.filter(
                func.date(LoanRepayment.date_paid) == filter_date.date()
            )
        elif period == 'month':
            start_date = filter_date.replace(day=1)
            # Get the first day of next month safely
            month_days = calendar.monthrange(start_date.year, start_date.month)[1]
            next_month = start_date + timedelta(days=month_days)
            next_month = next_month.replace(day=1)
            repayments_query = repayments_query.filter(
                LoanRepayment.date_paid >= start_date,
                LoanRepayment.date_paid < next_month
            )
        elif period == 'year':
            start_date = datetime(filter_date.year, 1, 1)
            end_date = datetime(filter_date.year, 12, 31, 23, 59, 59)
            repayments_query = repayments_query.filter(
                LoanRepayment.date_paid >= start_date,
                LoanRepayment.date_paid <= end_date
            )

    repayments = repayments_query.order_by(LoanRepayment.date_paid.desc()).all()
    total_collected = sum(r.amount_paid for r in repayments)

    return render_template(
        'repayments/view_repayments.html',
        repayments=repayments,
        total_collected=total_collected
    )

@repayment_bp.route('/repayments/export/pdf')
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans_Officer', 'Loans Supervisor', 'Cashier')
def export_pdf():
    branch_id = session.get('active_branch_id')

    # Query repayments with borrower name
    repayments_query = LoanRepayment.query.options(joinedload(LoanRepayment.loan).joinedload(Loan.borrower)) \
        .filter(Loan.company_id == current_user.company_id)

    if branch_id:
        repayments_query = repayments_query.filter(Loan.branch_id == branch_id)

    repayments = repayments_query.all()
    total_collected = sum(r.amount_paid for r in repayments)

    # Render HTML template
    html = render_template('repayments/pdf_template.html', repayments=repayments, total_collected=total_collected)

    # Convert to PDF
    pdf = BytesIO()
    pisa.CreatePDF(BytesIO(html.encode('utf-8')), dest=pdf)

    response = make_response(pdf.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=repayments_{datetime.now().date()}.pdf'

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
