from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from utils.decorators import roles_required
from forms import LoginForm, VoucherForm
from extensions import db
from utils.logging import log_company_action, log_system_action, log_action
from models import Loan, Borrower, Branch, LoanRepayment, Collateral, LedgerEntry, Company, Voucher
from utils.branch_filter import filter_by_active_branch
from flask import session, send_file
from datetime import datetime, timedelta, date
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from io import BytesIO
from flask import make_response, send_file
from num2words import num2words
from reportlab.lib.units import mm

voucher_bp = Blueprint('voucher_bp', __name__, url_prefix='/vouchers')

@voucher_bp.route('/vouchers', methods=['GET'])
@login_required
def vouchers():
    """
    Display auto-filled vouchers only.
    """
    company_id = current_user.company_id
    branch_id = current_user.branch_id

    vouchers = Voucher.query.filter_by(company_id=company_id, branch_id=branch_id)\
                            .order_by(Voucher.date.desc()).all()

    return render_template('vouchers/vouchers.html', vouchers=vouchers)

@voucher_bp.route('/vouchers/view/<int:voucher_id>')
@login_required
def view_voucher(voucher_id):
    voucher = Voucher.query.get_or_404(voucher_id)
    company = voucher.company
    branch = voucher.branch
    borrower = voucher.borrower
    loan = voucher.loan

    # Fetch related repayment allocation if it exists
    repayment = LoanRepayment.query.filter_by(loan_id=loan.id, amount_paid=voucher.amount).order_by(LoanRepayment.date_paid.desc()).first() if loan else None

    # Convert amount to words
    amount_in_words = num2words(voucher.amount, to='currency', lang='en').capitalize()

    return jsonify({
        'company_name': company.name if company else 'N/A',
        'branch_name': branch.name if branch else 'N/A',
        'branch_contact': branch.phone_number if branch else '',
        'receipt_number': voucher.voucher_number,
        'borrower_name': borrower.name if borrower else 'N/A',
        'loan_id': loan.id if loan else 'N/A',
        'amount': f"{voucher.amount:,.2f}",
        'amount_words': amount_in_words,
        'date': voucher.date.strftime('%Y-%m-%d'),
        'served_by': voucher.creator.full_name if voucher.creator else 'System',
        'allocations': {
            'principal': f"{repayment.principal_component:,.2f}" if repayment and repayment.principal_component else '0.00',
            'interest': f"{repayment.interest_component:,.2f}" if repayment and repayment.interest_component else '0.00',
            'penalty': f"{repayment.penalty_component:,.2f}" if repayment and repayment.penalty_component else '0.00',
            'total': f"{voucher.amount:,.2f}"
        }
    })

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

@voucher_bp.route('/<int:voucher_id>/receipt')
@login_required
def view_receipt(voucher_id):
    voucher = Voucher.query.get_or_404(voucher_id)
    company = Company.query.get(voucher.company_id)
    branch = Branch.query.get(voucher.branch_id) if voucher.branch_id else None
    borrower = Borrower.query.get(voucher.borrower_id) if voucher.borrower_id else None
    user = voucher.creator

    # Fetch the actual loan object
    loan = Loan.query.get(voucher.loan_id) if voucher.loan_id else None

    # Repayment breakdown
    repayment = LoanRepayment.query.filter_by(loan_id=voucher.loan_id).order_by(LoanRepayment.date_paid.desc()).first()
    cumulative_interest = repayment.cumulative_interest if repayment else 0
    interest_paid = repayment.interest_paid if repayment else 0
    principal_paid = repayment.principal_paid if repayment else 0

    # Amount in words
    amount_words = num2words(voucher.amount, to='currency', lang='en').replace('euro', 'shillings').title()

    return render_template(
        'vouchers/receipt_template.html',
        voucher=voucher,
        company=company,
        branch=branch,
        borrower=borrower,
        user=user,
        loan=loan,
        amount_words=amount_words,
        cumulative_interest=cumulative_interest,
        interest_paid=interest_paid,
        principal_paid=principal_paid
    )

@voucher_bp.route('/<int:voucher_id>/download_pdf')
@login_required
def download_voucher_pdf(voucher_id):
    voucher = Voucher.query.get_or_404(voucher_id)
    company = Company.query.get(voucher.company_id)
    branch = Branch.query.get(voucher.branch_id) if voucher.branch_id else None
    borrower = Borrower.query.get(voucher.borrower_id) if voucher.borrower_id else None
    loan = Loan.query.get(voucher.loan_id) if voucher.loan_id else None
    user = voucher.creator

    # Repayment breakdown
    repayment = LoanRepayment.query.filter_by(loan_id=voucher.loan_id).order_by(LoanRepayment.date_paid.desc()).first()
    cumulative_interest = repayment.cumulative_interest if repayment else 0
    interest_paid = repayment.interest_paid if repayment else 0
    principal_paid = repayment.principal_paid if repayment else 0

    # Amount in words
    amount_words = num2words(voucher.amount, to='currency', lang='en').replace('euro', 'shillings').title()

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=10*mm,
        leftMargin=10*mm,
        topMargin=15*mm,
        bottomMargin=15*mm
    )
    
    styles = getSampleStyleSheet()
    # Adjust fonts
    styles.add(ParagraphStyle(name='TitleCenter', parent=styles['Title'], alignment=1, fontSize=18, spaceAfter=10))
    styles.add(ParagraphStyle(name='HeadingCenter', parent=styles['Heading2'], alignment=1, fontSize=14, spaceAfter=10))
    styles.add(ParagraphStyle(name='NormalLeft', parent=styles['Normal'], alignment=0, fontSize=11, spaceAfter=6))
    styles.add(ParagraphStyle(name='ItalicLeft', parent=styles['Italic'], alignment=0, fontSize=10, spaceAfter=6))

    elements = []

    # Header
    elements.append(Paragraph(company.name if company else 'Company Name', styles['TitleCenter']))
    elements.append(Paragraph(f"Branch: {branch.name if branch else ''} | Contact: {branch.phone_number if branch else ''}", styles['HeadingCenter']))
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(f"Payment Receipt (Voucher #: {voucher.voucher_number})", styles['HeadingCenter']))
    elements.append(Spacer(1, 10))

    # Payment acknowledgment
    elements.append(Paragraph(f"Received from: {borrower.name if borrower else ''}", styles['NormalLeft']))
    elements.append(Paragraph(f"Loan ID: {loan.loan_id if loan else 'N/A'}", styles['NormalLeft']))
    elements.append(Paragraph(f"Date: {voucher.date.strftime('%Y-%m-%d')}", styles['NormalLeft']))
    elements.append(Paragraph(f"Amount Paid: {voucher.amount:,.2f} ({amount_words})", styles['NormalLeft']))
    elements.append(Spacer(1, 10))

    # Allocation table
    data = [
        ['Allocation', 'Amount'],
        ['Cumulative Interest', f"{cumulative_interest:,.2f}"],
        ['Interest', f"{interest_paid:,.2f}"],
        ['Principal', f"{principal_paid:,.2f}"],
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

    # Footer
    elements.append(Paragraph("Note: This receipt is not valid without the company stamp/seal.", styles['ItalicLeft']))
    elements.append(Spacer(1, 36))

    # Signature blocks
    signature_table = Table([
        ['Client Signature: ____________________', f"Served By: {user.full_name if user else 'N/A'} ____________________"]
    ], colWidths=[usable_width/2, usable_width/2], hAlign='CENTER')
    signature_table.setStyle(TableStyle([
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('FONTSIZE', (0,0), (-1,-1), 11),
        ('TOPPADDING',(0,0),(-1,-1),20)
    ]))
    elements.append(signature_table)

    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"Receipt_{voucher.voucher_number}.pdf", mimetype='application/pdf')
