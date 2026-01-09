from flask import Blueprint, render_template, redirect, url_for, flash, request,session
from extensions import db
from flask_login import login_required, current_user
from models import Branch
from utils.decorators import roles_required
from forms import CashbookEntryForm
from datetime import date
from models import CashbookEntry
from datetime import datetime, timedelta
import pandas as pd
from sqlalchemy import extract
from extensions import csrf
from decimal import Decimal, InvalidOperation
from helpers.cashbook_helpers import recalculate_balances

cashbook_bp = Blueprint('cashbook', __name__, url_prefix='/cashbook')

@cashbook_bp.route('/debug/session')
@login_required
def debug_session():
    return {
        'active_branch_id': session.get('active_branch_id'),
        'user_branch_id': current_user.branch_id,
        'company_id': current_user.company_id,
        'is_superuser': current_user.is_superuser,
    }

def recalculate_balances(company_id, branch_id=None):
    query = CashbookEntry.query.filter_by(company_id=company_id)
    if branch_id:
        query = query.filter_by(branch_id=branch_id)

    entries = query.order_by(CashbookEntry.date, CashbookEntry.id).all()

    running_balance = Decimal('0.00')
    for entry in entries:
        credit = entry.credit or Decimal('0.00')
        debit = entry.debit or Decimal('0.00')
        running_balance += credit - debit
        entry.balance = running_balance

    db.session.commit()

def ledger_to_cashbook(entry):
    """
    Convert a LedgerEntry into a CashbookEntry.
    Rules:
        - Loan repayment (cash in) => Credit
        - Loan disbursement (cash out) => Debit
    """
    debit = Decimal('0.00')
    credit = Decimal('0.00')
    particulars = entry.particulars

    # Loan repayment = cash in
    if 'repayment' in particulars.lower():
        credit = entry.payment
    # Loan disbursement = cash out
    elif 'disbursed' in particulars.lower():
        debit = entry.principal + entry.interest + entry.cumulative_interest

    # Avoid duplicates
    existing = CashbookEntry.query.filter_by(
        particulars=particulars,
        date=entry.date,
        branch_id=entry.loan.branch_id if entry.loan else None,
        company_id=entry.loan.company_id if entry.loan else None
    ).first()
    if existing:
        return existing

    cb_entry = CashbookEntry(
        date=entry.date,
        particulars=particulars,
        debit=debit,
        credit=credit,
        balance=Decimal('0.00'),  # recalculated
        company_id=entry.loan.company_id if entry.loan else None,
        branch_id=entry.loan.branch_id if entry.loan else None,
        created_by=entry.loan.user_id if entry.loan else 1
    )

    db.session.add(cb_entry)
    db.session.commit()

    # Recalculate balances
    recalculate_balances(cb_entry.company_id, cb_entry.branch_id)

    return cb_entry

@cashbook_bp.route('/cashbook/new', methods=['GET', 'POST'])
@login_required
def new_cashbook_entry():
    from app import db
    from models import CashbookEntry
    form = CashbookEntryForm()
    if form.validate_on_submit():
        last_entry = CashbookEntry.query \
            .filter_by(company_id=current_user.company_id) \
            .order_by(CashbookEntry.date.desc(), CashbookEntry.id.desc()) \
            .first()

        last_balance = Decimal(last_entry.balance) if last_entry else Decimal('0.0')

        debit = Decimal(form.debit.data or '0')
        credit = Decimal(form.credit.data or '0')

        new_balance = last_balance + credit - debit

        entry = CashbookEntry(
            date=form.date.data,
            particulars=form.particulars.data,
            debit=debit,
            credit=credit,
            balance=new_balance,
            company_id=current_user.company_id,
            branch_id=current_user.branch_id,
            created_by=current_user.id
        )

        db.session.add(entry)
        db.session.commit()
        flash('Cashbook entry added successfully.', 'success')
        return redirect(url_for('cashbook.view_cashbook'))

    return render_template('cashbook/new_entry.html', form=form)

@cashbook_bp.route('/cashbook', methods=['GET'])
@login_required
@roles_required('Superuser', 'Admin', 'Accountant', 'Branch_Manager', 'Loans_Supervisor', 'Cashier')
def view_cashbook():
    page = request.args.get('page', 1, type=int)
    per_page = 50
    branch_id = session.get('active_branch_id')
    today = datetime.today()

    query = CashbookEntry.query.filter_by(company_id=current_user.company_id)
    if not current_user.is_superuser and branch_id:
        query = query.filter_by(branch_id=branch_id)

    # Get filter type (all, today, weekly, monthly, yearly, etc)
    filter_option = request.args.get('filter', default=None)

    selected_day = None
    selected_month = None
    selected_year = None

    if filter_option == 'today':
        query = query.filter(CashbookEntry.date == today.date())
    elif filter_option == 'weekly':
        start_week = today - timedelta(days=today.weekday())  # Monday
        end_week = start_week + timedelta(days=6)             # Sunday
        query = query.filter(CashbookEntry.date.between(start_week.date(), end_week.date()))
    elif filter_option == 'monthly':
        selected_month = request.args.get('month', today.month, type=int)
        selected_year = request.args.get('year', today.year, type=int)
        query = query.filter(
            extract('month', CashbookEntry.date) == selected_month,
            extract('year', CashbookEntry.date) == selected_year
        )
    elif filter_option == 'yearly':
        selected_year = request.args.get('year', today.year, type=int)
        query = query.filter(extract('year', CashbookEntry.date) == selected_year)
    else:
        # Fallback: manual day/month/year filters if provided in URL
        selected_day = request.args.get('day', type=int)
        selected_month = request.args.get('month', type=int)
        selected_year = request.args.get('year', type=int)

        if selected_day:
            query = query.filter(extract('day', CashbookEntry.date) == selected_day)
        if selected_month:
            query = query.filter(extract('month', CashbookEntry.date) == selected_month)
        if selected_year:
            query = query.filter(extract('year', CashbookEntry.date) == selected_year)

    total_entries = query.count()
    paginated_entries = query.order_by(CashbookEntry.date.desc(), CashbookEntry.id.desc())\
                              .paginate(page=page, per_page=per_page, error_out=False)

    # Compute running balance
    all_entries = query.all()
    total_debit = sum(e.debit or Decimal('0.00') for e in all_entries)
    total_credit = sum(e.credit or Decimal('0.00') for e in all_entries)
    final_balance = all_entries[-1].balance if all_entries else Decimal('0.00')

    # Month and year dropdown data
    months = [(i, datetime(2000, i, 1).strftime('%B')) for i in range(1, 13)]
    current_year = datetime.now().year
    years = list(range(current_year - 10, current_year + 1))

    return render_template(
        'cashbook/view_cashbook.html',
        entries=paginated_entries.items,
        total_debit=total_debit,
        total_credit=total_credit,
        final_balance=final_balance,
        current_page=page,
        total_pages=paginated_entries.pages,
        selected_day=selected_day,
        selected_month=selected_month,
        selected_year=selected_year,
        months=months,
        years=years,
        filter=filter_option
    )

def add_cashbook_entry(
    date,
    particulars,
    debit,
    credit,
    company_id,
    branch_id=None,
    created_by=None
):
    from models import CashbookEntry
    from decimal import Decimal
    from extensions import db
    from routes.cashbook_routes import recalculate_balances

    if created_by is None:
        raise ValueError("created_by must be provided")

    entry = CashbookEntry(
        date=date,
        particulars=particulars,
        debit=Decimal(str(debit)),
        credit=Decimal(str(credit)),
        balance=Decimal('0.00'),  # recalculated
        company_id=company_id,
        branch_id=branch_id,
        created_by=created_by
    )

    db.session.add(entry)
    db.session.commit()  # ✅ THIS WAS MISSING

    # 🔁 ALWAYS recalc after insert
    recalculate_balances(company_id)

    return entry

@csrf.exempt
@cashbook_bp.route('/cashbook/edit/<int:entry_id>', methods=['GET', 'POST'])
@login_required
def edit_cashbook_entry(entry_id):
    entry = CashbookEntry.query.get_or_404(entry_id)
    form = CashbookEntryForm(obj=entry)

    if form.validate_on_submit():
        entry.date = form.date.data
        entry.particulars = form.particulars.data
        entry.debit = Decimal(form.debit.data or 0)
        entry.credit = Decimal(form.credit.data or 0)

        db.session.commit()
        recalculate_balances(entry.company_id)
        flash('Cashbook entry updated.', 'success')
        return redirect(url_for('cashbook.view_cashbook'))

    return render_template('cashbook/edit_entry.html', form=form, entry=entry)

@csrf.exempt
@cashbook_bp.route('/cashbook/delete/<int:entry_id>', methods=['POST'])
@login_required
def delete_cashbook_entry(entry_id):
    entry = CashbookEntry.query.get_or_404(entry_id)
    db.session.delete(entry)
    db.session.commit()
    recalculate_balances(entry.company_id)
    flash('Cashbook entry deleted.', 'warning')
    return redirect(url_for('cashbook.view_cashbook'))

from flask import send_file
import io

@cashbook_bp.route('/cashbook/export/<format>')
@login_required
def export_cashbook(format):
    query = CashbookEntry.query.filter_by(company_id=current_user.company_id)

    if current_user.branch_id and not current_user.has_role(['admin', 'accountant']):
        query = query.filter_by(branch_id=current_user.branch_id)

    entries = query.order_by(CashbookEntry.date, CashbookEntry.id).all()

    data = [{
        'Date': e.date.strftime('%Y-%m-%d'),
        'Particulars': e.particulars,
        'Debit': e.debit,
        'Credit': e.credit,
        'Balance': e.balance
    } for e in entries]

    df = pd.DataFrame(data)

    if format == 'excel':
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Cashbook')
        output.seek(0)
        return send_file(output, download_name='cashbook.xlsx', as_attachment=True)

    elif format == 'pdf':
        from xhtml2pdf import pisa
        from flask import render_template_string

        html = render_template_string("""
        <html><body><h3>Cashbook Export</h3><table border="1" cellpadding="5">
        <tr><th>Date</th><th>Particulars</th><th>Debit</th><th>Credit</th><th>Balance</th></tr>
        {% for e in entries %}
        <tr><td>{{ e['Date'] }}</td><td>{{ e['Particulars'] }}</td><td>{{ e['Debit'] }}</td>
        <td>{{ e['Credit'] }}</td><td>{{ e['Balance'] }}</td></tr>
        {% endfor %}
        </table></body></html>
        """, entries=data)

        pdf = io.BytesIO()
        pisa.CreatePDF(html, dest=pdf)
        pdf.seek(0)
        return send_file(pdf, download_name='cashbook.pdf', as_attachment=True)

    flash('Invalid format selected.', 'danger')
    return redirect(url_for('cashbook.view_cashbook'))
