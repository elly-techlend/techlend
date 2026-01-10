from flask import Blueprint, render_template, request, session
from flask_login import login_required, current_user
from extensions import db
from sqlalchemy import extract
from decimal import Decimal
from models import (
    CashbookEntry, LedgerEntry, Loan, OtherIncome, Expense,
    BankTransfer, SavingTransaction, SavingAccount
)
from datetime import datetime, timedelta

cashbook_bp = Blueprint('cashbook', __name__, url_prefix='/cashbook')


def recalc_balances(entries):
    """Given a list of cashbook entries, compute running balances."""
    running_balance = Decimal('0.00')
    for entry in entries:
        debit = entry.debit or Decimal('0.00')
        credit = entry.credit or Decimal('0.00')
        running_balance += credit - debit
        entry.balance = running_balance
    return running_balance


def refresh_cashbook(company_id, branch_id=None):
    """Populate Cashbook from all sources: Ledger, Bank, Savings, Expenses, Other Income."""
    # 1️⃣ Clear existing cashbook entries for this branch/company
    query = CashbookEntry.query.filter_by(company_id=company_id)
    if branch_id:
        query = query.filter_by(branch_id=branch_id)
    query.delete()
    db.session.commit()

    # 2️⃣ Loan repayments & disbursements from Ledger
    ledger_entries = LedgerEntry.query.join(Loan, LedgerEntry.loan_id == Loan.id)\
        .filter(Loan.company_id == company_id)
    if branch_id:
        ledger_entries = ledger_entries.filter(Loan.branch_id == branch_id)
    ledger_entries = ledger_entries.order_by(LedgerEntry.date.asc(), LedgerEntry.id.asc()).all()

    for entry in ledger_entries:
        debit = Decimal('0.00')
        credit = Decimal('0.00')
        particulars = entry.particulars or ''

        if 'repayment' in particulars.lower():
            credit = entry.payment or Decimal('0.00')
        elif 'disbursed' in particulars.lower() or 'loan disbursed' in particulars.lower():
            debit = entry.principal or Decimal('0.00')

        cb_entry = CashbookEntry(
            date=entry.date,
            particulars=particulars,
            debit=debit,
            credit=credit,
            balance=Decimal('0.00'),
            company_id=company_id,
            branch_id=entry.loan.branch_id if entry.loan else branch_id,
            created_by=entry.loan.user_id if entry.loan else None
        )
        db.session.add(cb_entry)

    # 3️⃣ Other Income → Credit
    other_incomes = OtherIncome.query.filter_by(company_id=company_id, is_active=True)
    if branch_id:
        other_incomes = other_incomes.filter_by(branch_id=branch_id)
    for income in other_incomes.all():
        db.session.add(CashbookEntry(
            date=income.income_date,
            particulars=f"Other Income: {income.description}",
            debit=Decimal('0.00'),
            credit=income.amount,
            balance=Decimal('0.00'),
            company_id=company_id,
            branch_id=income.branch_id,
            created_by=income.created_by_id
        ))

    # 4️⃣ Expenses → Debit
    expenses = Expense.query.filter_by(company_id=company_id)
    if branch_id:
        expenses = expenses.filter_by(branch_id=branch_id)
    for exp in expenses.all():
        db.session.add(CashbookEntry(
            date=exp.date,
            particulars=f"Expense: {exp.description}",
            debit=exp.amount,
            credit=Decimal('0.00'),
            balance=Decimal('0.00'),
            company_id=company_id,
            branch_id=exp.branch_id,
            created_by=exp.created_by_id
        ))

    # 5️⃣ Bank transfers
    bank_transfers = BankTransfer.query.filter_by(company_id=company_id, is_active=True)
    if branch_id:
        bank_transfers = bank_transfers.filter_by(branch_id=branch_id)
    for bt in bank_transfers.all():
        debit = bt.amount if bt.transfer_type == 'deposit' else Decimal('0.00')
        credit = bt.amount if bt.transfer_type == 'withdrawal' else Decimal('0.00')
        db.session.add(CashbookEntry(
            date=bt.transfer_date,
            particulars=f"Bank {bt.transfer_type.capitalize()} - Ref: {bt.reference or 'N/A'}",
            debit=debit,
            credit=credit,
            balance=Decimal('0.00'),
            company_id=company_id,
            branch_id=bt.branch_id,
            created_by=bt.created_by_id
        ))

    # 6️⃣ Savings transactions
    savings_tx = SavingTransaction.query.join(SavingAccount, SavingTransaction.account_id == SavingAccount.id)\
        .filter(SavingAccount.company_id == company_id)
    if branch_id:
        savings_tx = savings_tx.filter(SavingAccount.branch_id == branch_id)
    for tx in savings_tx.all():
        debit = tx.amount if tx.transaction_type == 'withdrawal' else Decimal('0.00')
        credit = tx.amount if tx.transaction_type == 'deposit' else Decimal('0.00')
        db.session.add(CashbookEntry(
            date=datetime.utcnow().date(),
            particulars=f"Savings {tx.transaction_type} by {tx.account.borrower.name}",
            debit=debit,
            credit=credit,
            balance=Decimal('0.00'),
            company_id=company_id,
            branch_id=tx.account.branch_id,
            created_by=current_user.id
        ))

    db.session.commit()

    # 🔹 Recalculate running balances
    all_entries = CashbookEntry.query.filter_by(company_id=company_id)
    if branch_id:
        all_entries = all_entries.filter_by(branch_id=branch_id)
    all_entries = all_entries.order_by(CashbookEntry.date.asc(), CashbookEntry.id.asc()).all()
    recalc_balances(all_entries)


@cashbook_bp.route('/', methods=['GET'])
@login_required
def view_cashbook():
    page = request.args.get('page', 1, type=int)
    per_page = 50
    branch_id = session.get('active_branch_id')

    # Always refresh cashbook from all sources
    refresh_cashbook(current_user.company_id, branch_id)

    query = CashbookEntry.query.filter_by(company_id=current_user.company_id)
    if branch_id:
        query = query.filter_by(branch_id=branch_id)

    # Filters
    today = datetime.today()
    filter_option = request.args.get('filter')
    selected_day = request.args.get('day', type=int)
    selected_month = request.args.get('month', type=int)
    selected_year = request.args.get('year', type=int)

    if filter_option == 'today':
        query = query.filter(CashbookEntry.date == today.date())
    elif filter_option == 'weekly':
        start_week = today - timedelta(days=today.weekday())
        end_week = start_week + timedelta(days=6)
        query = query.filter(CashbookEntry.date.between(start_week.date(), end_week.date()))
    elif filter_option == 'monthly' and selected_month and selected_year:
        query = query.filter(
            extract('month', CashbookEntry.date) == selected_month,
            extract('year', CashbookEntry.date) == selected_year
        )
    elif filter_option == 'yearly' and selected_year:
        query = query.filter(extract('year', CashbookEntry.date) == selected_year)
    else:
        # Manual day/month/year filter
        if selected_day:
            query = query.filter(extract('day', CashbookEntry.date) == selected_day)
        if selected_month:
            query = query.filter(extract('month', CashbookEntry.date) == selected_month)
        if selected_year:
            query = query.filter(extract('year', CashbookEntry.date) == selected_year)

    total_entries = query.count()
    paginated = query.order_by(CashbookEntry.date.desc(), CashbookEntry.id.desc())\
                     .paginate(page=page, per_page=per_page, error_out=False)
    entries = paginated.items

    total_debit = sum(e.debit or Decimal('0.00') for e in entries)
    total_credit = sum(e.credit or Decimal('0.00') for e in entries)
    final_balance = entries[-1].balance if entries else Decimal('0.00')

    months = [(i, datetime(2000, i, 1).strftime('%B')) for i in range(1, 13)]
    current_year = datetime.now().year
    years = list(range(current_year - 10, current_year + 1))

    return render_template(
        'cashbook/view_cashbook.html',
        entries=entries,
        total_debit=total_debit,
        total_credit=total_credit,
        final_balance=final_balance,
        current_page=page,
        total_pages=paginated.pages,
        months=months,
        years=years,
        selected_day=selected_day,
        selected_month=selected_month,
        selected_year=selected_year,
        filter=filter_option
    )

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
