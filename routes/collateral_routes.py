from flask import Blueprint, render_template, flash, redirect, url_for, session
from flask_login import login_required, current_user
from extensions import db
from models import Collateral, Borrower
from utils.decorators import roles_required

collateral_bp = Blueprint('collateral', __name__, url_prefix='/collaterals')

from flask import request

@collateral_bp.route('/collaterals')
@login_required
@roles_required('Superuser', 'Admin', 'Accountant', 'Branch_Manager', 'Loans_Supervisor')
def view_collateral():
    branch_id = session.get('active_branch_id')
    search_query = request.args.get('search', '').strip()

    # Start with base query
    query = Collateral.query.join(Borrower).filter(Borrower.company_id == current_user.company_id)

    # Apply branch filter if branch_id is active (includes Admins now)
    if not current_user.is_superuser and branch_id:
        query = query.filter(Borrower.branch_id == branch_id)

    # Apply search filter
    if search_query:
        query = query.filter(Borrower.name.ilike(f"%{search_query}%"))

    collaterals = query.all()

    return render_template(
        'collateral/collateral_register.html',
        collaterals=collaterals,
        search_query=search_query
    )

@collateral_bp.route('/new/<int:borrower_id>', methods=['GET', 'POST'])
@login_required
def new_collateral(borrower_id):
    borrower = Borrower.query.get_or_404(borrower_id)
    if request.method == 'POST':
        new_item = Collateral(
            borrower_id=borrower.id,
            item_name=request.form['item_name'],
            model=request.form['model'],
            serial_number=request.form['serial_number'],
            status=request.form['status'],
            condition=request.form['condition']
        )
        db.session.add(new_item)
        db.session.commit()
        flash("Collateral saved.", "success")
        return redirect(url_for('loan.add_loan', borrower_id=borrower.id))
    return render_template('collateral/add_collateral.html', borrower=borrower)
