from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response
from flask_login import login_required, current_user
from extensions import db
from flask import session
from models import BankTransfer, Branch
from datetime import datetime
from models import CashbookEntry
from weasyprint import HTML
from io import BytesIO
from sqlalchemy import and_
from utils.decorators import roles_required
from extensions import csrf
from decimal import Decimal
from routes.cashbook_routes import add_cashbook_entry

bank_bp = Blueprint('bank', __name__, template_folder='../templates/bank')

# ------------------ BANK DEPOSIT ------------------
@csrf.exempt
@bank_bp.route('/deposit', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans_Officer', 'Loans_Supervisor')
def bank_deposit():
    branch_id = session.get("active_branch_id")

    if request.method == 'POST':
        # PROCESS DEPOSIT
        amount = request.form.get('amount')
        reference = request.form.get('reference')
        transfer_date_str = request.form.get('transfer_date')

        if not amount:
            flash("Amount is required.", "error")
            return redirect(url_for('bank.bank_deposit'))

        try:
            amount = Decimal(amount)
        except:
            flash("Invalid amount format.", "error")
            return redirect(url_for('bank.bank_deposit'))

        try:
            transfer_date = datetime.strptime(transfer_date_str, '%Y-%m-%d').date()
        except:
            flash("Invalid or missing transfer date.", "error")
            return redirect(url_for('bank.bank_deposit'))

        # Save Bank Transfer
        transfer = BankTransfer(
            transfer_type='deposit',
            amount=amount,
            reference=reference,
            transfer_date=transfer_date,
            company_id=current_user.company_id,
            branch_id=branch_id,
            created_by_id=current_user.id
        )
        db.session.add(transfer)

        # Cashbook entry
        add_cashbook_entry(
            date=transfer_date,
            particulars=f"Bank deposit - Ref: {reference or 'N/A'}",
            debit=amount,
            credit=Decimal('0.00'),
            company_id=current_user.company_id,
            branch_id=branch_id,
            created_by=current_user.id
        )

        db.session.commit()
        flash("Bank deposit recorded successfully.", "success")
        return redirect(url_for('bank.view_transfers'))

    # GET → render form
    return render_template('bank/deposit.html', datetime=datetime)

# ------------------ BANK WITHDRAW ------------------
@csrf.exempt
@bank_bp.route('/withdraw', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'Accountant', 'Branch_Manager', 'Loans_Supervisor')
def bank_withdraw():
    branch_id = session.get("active_branch_id")

    if request.method == 'POST':
        # PROCESS WITHDRAW
        amount = request.form.get('amount')
        reference = request.form.get('reference')
        transfer_date_str = request.form.get('transfer_date')

        if not amount:
            flash("Amount is required.", "error")
            return redirect(url_for('bank.bank_withdraw'))

        try:
            amount = Decimal(amount)
        except:
            flash("Invalid amount format.", "error")
            return redirect(url_for('bank.bank_withdraw'))

        try:
            transfer_date = datetime.strptime(transfer_date_str, '%Y-%m-%d').date()
        except:
            flash("Invalid or missing transfer date.", "error")
            return redirect(url_for('bank.bank_withdraw'))

        # Save Bank Transfer
        transfer = BankTransfer(
            transfer_type='withdrawal',
            amount=amount,
            reference=reference,
            transfer_date=transfer_date,
            company_id=current_user.company_id,
            branch_id=branch_id,
            created_by_id=current_user.id
        )
        db.session.add(transfer)

        # Cashbook entry
        add_cashbook_entry(
            date=transfer_date,
            particulars=f"Bank withdrawal - Ref: {reference or 'N/A'}",
            debit=Decimal('0.00'),
            credit=amount,
            company_id=current_user.company_id,
            branch_id=branch_id,
            created_by=current_user.id
        )

        db.session.commit()
        flash("Bank withdrawal recorded successfully.", "success")
        return redirect(url_for('bank.view_transfers'))

    # GET → render form
    return render_template('bank/withdraw.html', datetime=datetime)

@bank_bp.route('/bank-transfers')
@login_required
@roles_required('Admin', 'Accountant')
def view_transfers():
    from datetime import datetime

    query = BankTransfer.query.filter_by(company_id=current_user.company_id, is_active=True)

    # Enforce branch filter for everyone except superuser
    if 'superuser' not in current_user.roles:
        branch_id = session.get("active_branch_id")
        query = query.filter_by(branch_id=branch_id)
        branches = []  # Don't show filter
    else:
        # Allow superuser to use branch filter if provided
        branch_id = request.args.get('branch_id')
        if branch_id:
            query = query.filter_by(branch_id=branch_id)
        branches = Branch.query.filter_by(company_id=current_user.company_id).all()

    # Other filters
    transfer_type = request.args.get('type')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    if transfer_type in ['deposit', 'withdraw']:
        query = query.filter_by(transfer_type=transfer_type)

    if start_date:
        try:
            start = datetime.strptime(start_date, '%Y-%m-%d').date()
            query = query.filter(BankTransfer.transfer_date >= start)
        except ValueError:
            flash('Invalid start date format.', 'error')

    if end_date:
        try:
            end = datetime.strptime(end_date, '%Y-%m-%d').date()
            query = query.filter(BankTransfer.transfer_date <= end)
        except ValueError:
            flash('Invalid end date format.', 'error')

    transfers = query.order_by(BankTransfer.transfer_date.desc()).all()

    return render_template(
        'bank/view_transfers.html',
        transfers=transfers,
        datetime=datetime,
        branches=branches
    )

@csrf.exempt
@bank_bp.route('/transfer/<int:transfer_id>/delete', methods=['POST'])
@login_required
@roles_required('Admin', 'Branch_Manager')
def delete_transfer(transfer_id):
    branch_id = session.get('active_branch_id')

    transfer = BankTransfer.query.filter_by(
        id=transfer_id,
        company_id=current_user.company_id,
        branch_id=branch_id
    ).first_or_404()

    db.session.delete(transfer)
    db.session.commit()

    # 🔄 Rebuild cashbook safely
    refresh_cashbook(current_user.company_id, branch_id)

    flash('Bank transfer deleted successfully.', 'success')
    return redirect(url_for('bank.view_transfers'))

@bank_bp.route('/transfer/<int:transfer_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('Admin', 'Branch_Manager')
def edit_transfer(transfer_id):
    branch_id = session.get('active_branch_id')

    transfer = BankTransfer.query.filter_by(
        id=transfer_id,
        company_id=current_user.company_id,
        branch_id=branch_id
    ).first_or_404()

    if request.method == 'POST':
        transfer.amount = Decimal(request.form['amount'])
        transfer.reference = request.form.get('reference')
        transfer.transfer_date = datetime.strptime(
            request.form['transfer_date'], '%Y-%m-%d'
        )

        db.session.commit()

        # 🔄 Re-sync cashbook
        refresh_cashbook(current_user.company_id, branch_id)

        flash('Bank transfer updated successfully.', 'success')
        return redirect(url_for('bank.view_transfers'))

    return render_template('bank/edit_transfer.html', transfer=transfer)

@bank_bp.route('/bank-transfers/export-pdf')
@login_required
def export_transfers_pdf():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    transfer_type = request.args.get('type')

    filters = [
        BankTransfer.company_id == current_user.company_id,
        BankTransfer.branch_id == session.get("active_branch_id")
    ]

    if start_date:
        filters.append(BankTransfer.transfer_date >= datetime.strptime(start_date, '%Y-%m-%d').date())
    if end_date:
        filters.append(BankTransfer.transfer_date <= datetime.strptime(end_date, '%Y-%m-%d').date())
    if transfer_type:
        filters.append(BankTransfer.transfer_type == transfer_type)

    transfers = BankTransfer.query.filter(and_(*filters)).order_by(BankTransfer.transfer_date.desc()).all()

    # Render HTML to a string
    html_out = render_template('bank/transfer_pdf_template.html', transfers=transfers)

    # Generate PDF
    pdf_io = BytesIO()
    HTML(string=html_out).write_pdf(pdf_io)
    pdf_io.seek(0)

    # Prepare response
    response = make_response(pdf_io.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'inline; filename=bank_transfers.pdf'
    return response
