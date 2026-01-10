# routes/expenses_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from models import Expense
from extensions import db
from flask_login import login_required, current_user
from datetime import datetime
from models import CashbookEntry
from extensions import csrf
from decimal import Decimal, InvalidOperation
from routes.cashbook_routes import add_cashbook_entry, refresh_cashbook
from utils.decorators import roles_required

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

@csrf.exempt
@expenses_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add_expense():
    branch_id = session.get('active_branch_id')

    if request.method == 'POST':
        # 1️⃣ Get form values
        try:
            date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
            description = request.form['description']
            amount = Decimal(request.form['amount'])
            category = request.form.get('category', '')
        except Exception as e:
            flash(f"Invalid input: {e}", "danger")
            return redirect(url_for('expenses.add_expense'))

        # 2️⃣ Save expense
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

        # 3️⃣ Add to cashbook
        add_cashbook_entry(
            date=date,
            particulars=f"Expense: {description}",
            debit=amount,
            credit=Decimal('0.00'),
            company_id=current_user.company_id,
            branch_id=branch_id,
            created_by=current_user.id
        )

        db.session.commit()
        flash("Expense recorded successfully.", "success")
        return redirect(url_for('expenses.all_expenses'))

    # GET → show the form
    return render_template(
        'expenses/add_expense.html',
        current_date=datetime.today().strftime('%Y-%m-%d')
    )

@csrf.exempt
@expenses_bp.route('/<int:expense_id>/edit', methods=['POST'])
@login_required
@roles_required('Admin', 'Branch_Manager')
def edit_expense(expense_id):
    branch_id = session.get('active_branch_id')

    expense = Expense.query.filter_by(
        id=expense_id,
        company_id=current_user.company_id,
        branch_id=branch_id
    ).first_or_404()

    expense.description = request.form['description']
    expense.category = request.form.get('category')
    expense.amount = float(request.form['amount'])

    expense.date = datetime.strptime(
        request.form['date'], '%Y-%m-%d'
    )

    db.session.commit()

    # 🔄 Ledger-safe rebuild
    refresh_cashbook(current_user.company_id, branch_id)

    flash('Expense updated successfully.', 'success')
    return redirect(url_for('expenses.all_expenses'))

@csrf.exempt
@expenses_bp.route('/<int:expense_id>/delete', methods=['POST'])
@login_required
@roles_required('Admin', 'Branch_Manager')
def delete_expense(expense_id):
    branch_id = session.get('active_branch_id')

    expense = Expense.query.filter_by(
        id=expense_id,
        company_id=current_user.company_id,
        branch_id=branch_id
    ).first_or_404()

    db.session.delete(expense)
    db.session.commit()

    # 🔄 Ledger-safe rebuild
    refresh_cashbook(current_user.company_id, branch_id)

    flash('Expense deleted successfully.', 'success')
    return redirect(url_for('expenses.all_expenses'))

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
