from flask import Blueprint, render_template, flash, redirect, url_for, session, request
from flask_login import login_required, current_user
from extensions import db
from models import Collateral, Borrower, Loan
from utils.decorators import roles_required
from extensions import csrf

collateral_bp = Blueprint('collateral', __name__, url_prefix='/collaterals')


@collateral_bp.route('/')
@login_required
@roles_required('Superuser', 'Admin', 'Accountant', 'Branch_Manager', 'Loans_Supervisor')
def view_collateral():
    branch_id = session.get('active_branch_id')
    search_query = request.args.get('search', '').strip()

    # Base query (company scope)
    query = Collateral.query.join(Borrower).filter(
        Borrower.company_id == current_user.company_id
    )

    # Branch scope
    if not current_user.is_superuser and branch_id:
        query = query.filter(Borrower.branch_id == branch_id)

    # Search filter
    if search_query:
        query = query.filter(Borrower.name.ilike(f"%{search_query}%"))

    collaterals = query.all()

    # 🔑 ACTIVE LOAN MAP (single source of truth)
    collateral_status = {}
    for c in collaterals:
        active_loan = Loan.query.filter_by(
            borrower_id=c.borrower_id,  # check borrower's loans
            status='active'
        ).first()
        collateral_status[c.id] = active_loan is not None

    return render_template(
        'collateral/collateral_register.html',
        collaterals=collaterals,
        collateral_status=collateral_status,
        search_query=search_query
    )

@csrf.exempt
@collateral_bp.route('/delete/<int:collateral_id>', methods=['POST'])
@login_required
def delete_collateral(collateral_id):
    collateral = Collateral.query.get_or_404(collateral_id)

    # 🔒 BLOCK DELETE IF BORROWER HAS ACTIVE LOAN
    active_loan = Loan.query.filter_by(
        borrower_id=collateral.borrower_id,
        status='active'
    ).first()

    if active_loan:
        flash(
            'This collateral belongs to a borrower with an active loan and cannot be deleted.',
            'danger'
        )
        return redirect(url_for('collateral.view_collateral'))

    db.session.delete(collateral)
    db.session.commit()

    flash('Collateral deleted successfully.', 'success')
    return redirect(url_for('collateral.view_collateral'))

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

