from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_required, current_user
from extensions import db
from models import OtherIncome
from datetime import datetime
from utils.decorators import roles_required
from extensions import csrf

other_income_bp = Blueprint('other_income', __name__,)

@other_income_bp.route('/other_income')
@login_required
@roles_required('Superuser', 'Admin')
def view_other_income():
    branch_id = session.get("active_branch_id")
    
    query = OtherIncome.query.filter_by(company_id=current_user.company_id, is_active=True)

    # Always filter by branch if branch_id is available (regardless of user role)
    if branch_id:
        query = query.filter_by(branch_id=branch_id)

    incomes = query.order_by(OtherIncome.income_date.desc()).all()

    return render_template('other_income/view_other_income.html', incomes=incomes)

@csrf.exempt
@other_income_bp.route('/other-income/add', methods=['GET', 'POST'])
@roles_required('Superuser', 'Admin')
@login_required
def add_income():
    from datetime import datetime
    if request.method == 'POST':
        description = request.form.get('description')
        amount = request.form.get('amount')
        income_date = request.form.get('income_date')  # optional, else use today

        # Validate inputs
        if not description or not amount:
            flash('Description and Amount are required.', 'error')
            return redirect(url_for('other_income.add_income'))

        try:
            amount = float(amount)
        except ValueError:
            flash('Invalid amount format.', 'error')
            return redirect(url_for('other_income.add_income'))

        if income_date:
            try:
                income_date = datetime.strptime(income_date, '%Y-%m-%d').date()
            except ValueError:
                flash('Invalid date format, use YYYY-MM-DD.', 'error')
                return redirect(url_for('other_income.add_income'))
        else:
            income_date = datetime.utcnow().date()

        new_income = OtherIncome(
            description=description,
            amount=amount,
            income_date=income_date,
            company_id=current_user.company_id,
            branch_id=session.get("active_branch_id"),  # ✅ FIXED LINE
            created_by_id=current_user.id,
        )

        db.session.add(new_income)
        db.session.commit()
        flash('Other income added successfully.', 'success')
        return redirect(url_for('other_income.view_other_income'))

    return render_template('other_income/add_income.html', datetime=datetime)
