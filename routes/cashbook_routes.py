from flask import Blueprint, render_template, redirect, url_for, flash, request,session
from extensions import db
from flask_login import login_required, current_user
from models import Branch
from utils.decorators import roles_required
from forms import CashbookEntryForm
from datetime import date
from models import CashbookEntry
from datetime import datetime
import pandas as pd

cashbook_bp = Blueprint('cashbook', __name__)

@cashbook_bp.route('/cashbook/new', methods=['GET', 'POST'])
@login_required
def new_cashbook_entry():
    from app import db  # Import here inside the function
    from models import CashbookEntry
    form = CashbookEntryForm()
    if form.validate_on_submit():
        # Get last balance for current company
        last_entry = CashbookEntry.query \
            .filter_by(company_id=current_user.company_id) \
            .order_by(CashbookEntry.date.desc(), CashbookEntry.id.desc()) \
            .first()

        last_balance = last_entry.balance if last_entry else 0.0

        debit = float(form.debit.data or 0)
        credit = float(form.credit.data or 0)
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

@cashbook_bp.route('/cashbook')
@login_required
@roles_required('Superuser', 'Admin', 'Accountant', 'Branch_Manager', 'Loans_Supervisor', 'Cashier')
def view_cashbook():
    from sqlalchemy import extract
    from datetime import datetime

    page = request.args.get('page', 1, type=int)
    month = page  # Page = month
    year = request.args.get('year', type=int) or datetime.now().year
    branch_id = session.get('active_branch_id')

    query = CashbookEntry.query.filter_by(company_id=current_user.company_id)

    if not current_user.is_superuser and branch_id:
        query = query.filter_by(branch_id=branch_id)

    query = query.filter(
        extract('year', CashbookEntry.date) == year,
        extract('month', CashbookEntry.date) == month
    )

    entries = query.order_by(CashbookEntry.date.desc(), CashbookEntry.id.desc()).all()

    # Compute running balance (in reverse so balance accumulates from bottom to top)
    running_balance = 0.0
    for entry in reversed(entries):
        running_balance = running_balance + (entry.credit or 0) - (entry.debit or 0)
        entry.balance = running_balance

    total_debit = sum(e.debit or 0 for e in entries)
    total_credit = sum(e.credit or 0 for e in entries)
    final_balance = running_balance

    years = db.session.query(extract('year', CashbookEntry.date)).distinct().all()
    months = [(i, datetime(1900, i, 1).strftime('%B')) for i in range(1, 13)]

    return render_template(
        'cashbook/view_cashbook.html',
        entries=entries,
        total_debit=total_debit,
        total_credit=total_credit,
        final_balance=final_balance,
        selected_year=year,
        selected_month=month,
        years=[y[0] for y in years],
        months=months,
        current_page=page,
        total_pages=12  # Fixed 12 pages (months)
    )

def add_cashbook_entry(date, particulars, debit, credit, company_id, branch_id=None, created_by=None):
    from models import CashbookEntry
    from extensions import db

    # Get the last balance for this company and branch
    last_entry = CashbookEntry.query.filter_by(
        company_id=company_id,
        branch_id=branch_id
    ).order_by(CashbookEntry.id.desc()).first()

    last_balance = last_entry.balance if last_entry else 0.0

    # Calculate new balance
    new_balance = last_balance + credit - debit

    # Create the entry
    entry = CashbookEntry(
        date=date,
        particulars=particulars,
        debit=debit,
        credit=credit,
        balance=new_balance,
        company_id=company_id,
        branch_id=branch_id,
        created_by=current_user.id 
    )
    if created_by is None:
        raise ValueError("created_by must be provided to add_cashbook_entry()")

    db.session.add(entry)
    db.session.commit()

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
