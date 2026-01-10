from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import login_required, current_user
from utils.decorators import roles_required
from extensions import db
from models import Voucher, Loan, Borrower, Branch, Company, LedgerEntry, LoanRepayment, User
from num2words import num2words
from datetime import datetime, timedelta, date
from io import BytesIO
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from extensions import csrf

voucher_bp = Blueprint('voucher_bp', __name__, url_prefix='/vouchers')

# -----------------------------
# List all vouchers
# -----------------------------
@voucher_bp.route('/')
@login_required
def vouchers():
    company_id = current_user.company_id
    branch_id = current_user.branch_id

    vouchers = Voucher.query.filter_by(company_id=company_id, branch_id=branch_id)\
                            .order_by(Voucher.date.desc()).all()

    return render_template('vouchers/vouchers.html', vouchers=vouchers)

def create_voucher_from_ledger(entry: LedgerEntry):
    """
    Generate a Voucher from a LedgerEntry for automatic receipts.
    Works with the new LedgerEntry model (no credit/debit fields).
    """
    # Only process repayments with a positive payment
    if not entry.loan_id or not entry.payment or entry.payment <= 0:
        return

    voucher = Voucher(
        voucher_type='Receipt',
        description='Loan Repayment',
        amount=float(entry.payment),          # Use payment, not credit
        date=entry.date,
        borrower_id=entry.loan.borrower_id,
        loan_id=entry.loan_id,
        company_id=entry.loan.company_id,
        branch_id=entry.loan.branch_id,
        created_by=entry.loan.created_by
    )
    db.session.add(voucher)
    db.session.commit()

# -----------------------------
# View voucher details (JSON)
# -----------------------------
@voucher_bp.route('/view/<int:voucher_id>')
@login_required
def view_voucher(voucher_id):
    voucher = Voucher.query.get_or_404(voucher_id)

    # Ledger-safe allocations
    ledger_entry = LedgerEntry.query.filter_by(voucher_id=voucher.id).first()
    principal = ledger_entry.principal if ledger_entry else 0
    interest = ledger_entry.interest if ledger_entry else 0
    penalty = getattr(ledger_entry, 'penalty', 0) if ledger_entry else 0

    amount_in_words = num2words(voucher.amount, to='currency', lang='en').capitalize()

    return jsonify({
        'voucher_number': voucher.voucher_number,
        'voucher_type': voucher.voucher_type,
        'description': voucher.description,
        'amount': f"{voucher.amount:,.2f}",
        'amount_words': amount_in_words,
        'date': voucher.date.strftime('%Y-%m-%d'),
        'borrower_name': voucher.borrower.name if voucher.borrower else 'N/A',
        'loan_id': voucher.loan.loan_id if voucher.loan else 'N/A',
        'served_by': voucher.creator.full_name if voucher.creator else 'System',
        'allocations': {
            'principal': f"{principal:,.2f}",
            'interest': f"{interest:,.2f}",
            'penalty': f"{penalty:,.2f}",
            'total': f"{voucher.amount:,.2f}"
        }
    })

@csrf.exempt
@voucher_bp.route('/clear_old', methods=['POST'])
@login_required
@roles_required('Admin', 'Branch_Manager')
def clear_old_vouchers():
    """
    Deletes vouchers older than X days to keep the database clean.
    """
    # Define how old vouchers should be before deletion
    days_threshold = 30  # for example, 90 days
    cutoff_date = datetime.utcnow() - timedelta(days=days_threshold)

    # Filter vouchers for current company and branch
    vouchers_to_delete = Voucher.query.filter(
        Voucher.company_id == current_user.company_id,
        Voucher.branch_id == current_user.branch_id,
        Voucher.date < cutoff_date
    ).all()

    count = len(vouchers_to_delete)

    if count == 0:
        flash('No old vouchers to delete.', 'info')
        return redirect(url_for('voucher_bp.vouchers'))

    # Delete the old vouchers
    for v in vouchers_to_delete:
        db.session.delete(v)
    db.session.commit()

    flash(f'Successfully deleted {count} vouchers older than {days_threshold} days.', 'success')
    return redirect(url_for('voucher_bp.vouchers'))

# -----------------------------
# Render receipt template
# -----------------------------
@voucher_bp.route('/<int:voucher_id>/receipt')
@login_required
def view_receipt(voucher_id):
    voucher = Voucher.query.get_or_404(voucher_id)

    # Fetch related objects safely
    company = Company.query.get(voucher.company_id)
    branch = Branch.query.get(voucher.branch_id) if voucher.branch_id else None
    borrower = Borrower.query.get(voucher.borrower_id) if voucher.borrower_id else None
    loan = Loan.query.get(voucher.loan_id) if voucher.loan_id else None
    user = voucher.creator  # should be User object

    # Try to fetch the repayment linked to this voucher (optional)
    repayment = None
    if loan:
        repayment = LoanRepayment.query.filter_by(
            loan_id=loan.id,
            amount_paid=voucher.amount
        ).order_by(LoanRepayment.date_paid.desc()).first()

    principal = repayment.principal_paid if repayment else 0
    interest = repayment.interest_paid if repayment else 0
    cumulative_interest = repayment.cumulative_interest if repayment else 0
    penalty = getattr(repayment, 'penalty', 0) if repayment else 0

    # Amount in words
    amount_words = num2words(voucher.amount, to='currency', lang='en')\
                        .replace('euro', 'shillings').title()

    return render_template(
        'vouchers/receipt_template.html',
        voucher=voucher,
        company=company,
        branch=branch,
        borrower=borrower,
        loan=loan,
        user=user,
        amount_words=amount_words,
        principal_paid=principal,
        interest_paid=interest,
        cumulative_interest=cumulative_interest,
        penalty_paid=penalty
    )

# -----------------------------
# Download PDF receipt
# -----------------------------
@voucher_bp.route('/<int:voucher_id>/download_pdf')
@login_required
def download_voucher_pdf(voucher_id):
    # Fetch voucher
    voucher = Voucher.query.get_or_404(voucher_id)

    # Fetch related objects
    company = Company.query.get(voucher.company_id)
    branch = Branch.query.get(voucher.branch_id) if voucher.branch_id else None
    borrower = Borrower.query.get(voucher.borrower_id) if voucher.borrower_id else None
    user = User.query.get(voucher.created_by) if voucher.created_by else None
    loan = Loan.query.get(voucher.loan_id) if voucher.loan_id else None

    # Fetch repayment allocation if it exists
    repayment = None
    if voucher.loan_id:
        repayment = LoanRepayment.query.filter_by(
            loan_id=voucher.loan_id,
            amount_paid=voucher.amount
        ).order_by(LoanRepayment.date_paid.desc()).first()

    # Safe amounts
    principal_paid = repayment.principal_paid if repayment and hasattr(repayment, 'principal_paid') else 0
    interest_paid = repayment.interest_paid if repayment and hasattr(repayment, 'interest_paid') else 0
    cumulative_interest = repayment.cumulative_interest if repayment and hasattr(repayment, 'cumulative_interest') else 0

    # Amount in words
    amount_words = num2words(voucher.amount, to='currency', lang='en').replace('euro','shillings').title()

    # Create PDF buffer
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=10*mm,
        leftMargin=10*mm,
        topMargin=15*mm,
        bottomMargin=15*mm
    )

    # Styles
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='TitleCenter', parent=styles['Title'], alignment=1, fontSize=18, spaceAfter=10))
    styles.add(ParagraphStyle(name='HeadingCenter', parent=styles['Heading2'], alignment=1, fontSize=14, spaceAfter=10))
    styles.add(ParagraphStyle(name='NormalLeft', parent=styles['Normal'], alignment=0, fontSize=11, spaceAfter=6))
    styles.add(ParagraphStyle(name='ItalicLeft', parent=styles['Italic'], alignment=0, fontSize=10, spaceAfter=6))

    elements = []

    # Header
    elements.append(Paragraph(company.name if company else 'Company', styles['TitleCenter']))
    elements.append(Paragraph(
        f"Branch: {branch.name if branch else ''} | Contact: {branch.phone_number if branch else ''}", 
        styles['HeadingCenter']
    ))
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(f"Payment Receipt (Voucher #: {voucher.voucher_number})", styles['HeadingCenter']))
    elements.append(Spacer(1, 10))

    # Payment acknowledgment
    elements.append(Paragraph(f"Received from: {borrower.name if borrower else 'N/A'}", styles['NormalLeft']))
    elements.append(Paragraph(f"Loan ID: {loan.loan_id if loan else 'N/A'}", styles['NormalLeft']))
    elements.append(Paragraph(f"Date: {voucher.date.strftime('%Y-%m-%d')}", styles['NormalLeft']))
    elements.append(Paragraph(f"Amount Paid: {voucher.amount:,.2f} ({amount_words})", styles['NormalLeft']))
    elements.append(Spacer(1, 10))

    # Allocation table
    data = [
        ['Allocation', 'Amount'],
        ['Principal', f"{principal_paid:,.2f}"],
        ['Interest', f"{interest_paid:,.2f}"],
        ['Cumulative Interest', f"{cumulative_interest:,.2f}"],
        ['Total', f"{voucher.amount:,.2f}"]
    ]

    page_width, _ = A4
    usable_width = page_width - 20*mm  # left + right margins

    table = Table(data, colWidths=[usable_width*0.7, usable_width*0.3], hAlign='CENTER')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.grey),
        ('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        ('FONTSIZE', (0,0), (-1,-1), 11),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 6),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 24))

    # Footer note
    elements.append(Paragraph("Note: This receipt is not valid without the company stamp/seal.", styles['ItalicLeft']))
    elements.append(Spacer(1, 36))

    # Signature blocks
    signature_table = Table([
        ['Client Signature: ____________________', f"Served By: {user.full_name if user else 'System'} ____________________"]
    ], colWidths=[usable_width/2, usable_width/2], hAlign='CENTER')
    signature_table.setStyle(TableStyle([
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('FONTSIZE', (0,0), (-1,-1), 11),
        ('TOPPADDING',(0,0),(-1,-1),20)
    ]))
    elements.append(signature_table)

    # Build PDF
    doc.build(elements)
    buffer.seek(0)

    return send_file(
        buffer, 
        as_attachment=True, 
        download_name=f"Receipt_{voucher.voucher_number}.pdf", 
        mimetype='application/pdf'
    )

@voucher_bp.route('/vouchers/json', methods=['GET'])
@login_required
def vouchers_json():
    company_id = current_user.company_id
    branch_id = current_user.branch_id

    vouchers = Voucher.query.filter_by(company_id=company_id, branch_id=branch_id)\
                            .order_by(Voucher.date.desc()).all()

    vouchers_list = []
    for v in vouchers:
        vouchers_list.append({
            'voucher_number': v.voucher_number,
            'voucher_type': v.voucher_type,
            'description': v.description,
            'amount': str(v.amount),
            'date': v.date.strftime('%Y-%m-%d'),
            'borrower': v.borrower.name if v.borrower_id else '',
        })

    return {'vouchers': vouchers_list}

