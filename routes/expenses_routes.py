# routes/expenses_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from models import Expense
from extensions import db
from flask_login import login_required, current_user
from datetime import datetime
from models import CashbookEntry
from routes.cashbook_routes import add_cashbook_entry

expenses_bp = Blueprint('expenses', __name__, url_prefix='/expenses')

@expenses_bp.route('/')
@login_required
def all_expenses():
    branch_id = session.get('active_branch_id')
    query = Expense.query.filter_by(company_id=current_user.company_id)

    if branch_id:
        query = query.filter_by(branch_id=branch_id)

    expenses = query.order_by(Expense.date.desc()).all()
    return render_template('expenses/all_expenses.html', expenses=expenses)

@expenses_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add_expense():
    if request.method == 'POST':
        date = datetime.strptime(request.form['date'], '%Y-%m-%d')
        description = request.form['description']
        amount = float(request.form['amount'])
        category = request.form.get('category', '')
        branch_id = session.get('active_branch_id')

        expense = Expense(
            date=date,
            description=description,
            amount=amount,
            category=category,
            company_id=current_user.company_id,
            branch_id=branch_id,
            created_by_id=current_user.id
        )

        db.session.add(expense)
        db.session.commit()

        # Add to cashbook as a debit
        add_cashbook_entry(
            date=date,
            particulars=f"Expense: {description}",
            debit=amount,
            credit=0,
            company_id=current_user.company_id,
            branch_id=branch_id,
            created_by=current_user.id
        )

        flash("Expense recorded successfully.", "success")
        return redirect(url_for('expenses.all_expenses'))

    return render_template('expenses/add_expense.html', current_date=datetime.today().strftime('%Y-%m-%d'))

from sqlalchemy import extract, func

@expenses_bp.route('/charts')
@login_required
def expense_charts():
    branch_id = session.get('active_branch_id')

    # Monthly Expense Totals
    query = db.session.query(
        extract('month', Expense.date).label('month'),
        func.sum(Expense.amount).label('total')
    ).filter_by(company_id=current_user.company_id)

    if branch_id:
        query = query.filter(Expense.branch_id == branch_id)

    query = query.group_by(extract('month', Expense.date)).order_by(extract('month', Expense.date))

    monthly_expenses = query.all()

    # Prepare data for chart
    months = []
    totals = []

    for row in monthly_expenses:
        month_name = datetime(2025, int(row.month), 1).strftime('%B')
        months.append(month_name)
        totals.append(float(row.total))

    return render_template('expenses/charts.html', months=months, totals=totals)
