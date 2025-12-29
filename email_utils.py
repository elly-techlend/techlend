# emails.py
import os
from datetime import datetime
from flask import render_template, current_app, url_for
from flask_mail import Mail, Message

# ---------------- Initialize Flask-Mail (not bound to app yet) ----------------
mail = Mail()

# ---------------- Centralized Send Function ----------------
def send_email(to_email, subject, template=None, from_email=None, **context):
    """
    Sends an email using Gmail SMTP via Flask-Mail.
    
    Parameters:
        to_email (str): Recipient's email address.
        subject (str): Email subject line.
        template (str, optional): Path to Jinja email template (e.g., 'emails/reset_password.html').
        from_email (str, optional): Sender's email. Defaults to GMAIL_EMAIL env variable.
        **context: Any template variables required for rendering.
    """
    # Ensure we are in an app context
    app = current_app._get_current_object()

    from_email = from_email or os.environ.get("GMAIL_EMAIL")
    if not from_email or not os.environ.get("GMAIL_APP_PASSWORD"):
        raise ValueError("GMAIL_EMAIL or GMAIL_APP_PASSWORD is not set in environment variables")

    if template:
        html_content = render_template(template, **context)
    elif "html_content" in context:
        html_content = context["html_content"]
    else:
        raise ValueError("Either template or html_content must be provided")

    msg = Message(
        subject=subject,
        recipients=[to_email],
        html=html_content,
        sender=from_email
    )

    try:
        mail.send(msg)
        print(f"[✓] Email sent to {to_email} with subject '{subject}'")
        return True
    except Exception as e:
        print(f"[✗] Failed to send email to {to_email}: {str(e)}")
        return False

# ---------------- Password Reset Email ----------------
def send_reset_email(to_email, token):
    reset_url = url_for('auth.reset_password_token', token=token, _external=True)
    return send_email(
        to_email,
        subject="Password Reset Request - TechLend",
        template="emails/reset_password.html",
        reset_url=reset_url,
        year=datetime.utcnow().year
    )

# ---------------- Bulk Borrower Email ----------------
def send_bulk_borrower_email(borrowers, subject, message_body):
    success = []
    failed = []

    for borrower in borrowers:
        if not borrower.email:
            failed.append(borrower.name)
            continue

        result = send_email(
            borrower.email,
            subject=subject,
            html_content=message_body,
            year=datetime.utcnow().year
        )

        if result:
            success.append(borrower.name)
        else:
            failed.append(borrower.name)

    return {"success": success, "failed": failed}

# ---------------- Loan Approved Email ----------------
def send_loan_approval_email(user, loan):
    return send_email(
        user.email,
        subject="Loan Approved - TechLend",
        template="emails/loan_approved.html",
        user=user,
        loan=loan,
        year=datetime.utcnow().year
    )

# ---------------- Loan Rejected Email ----------------
def send_loan_rejection_email(user, loan):
    return send_email(
        user.email,
        subject="Loan Rejected - TechLend",
        template="emails/loan_rejected.html",
        user=user,
        loan=loan,
        year=datetime.utcnow().year
    )

# ---------------- Repayment Reminder Email ----------------
def send_repayment_reminder_email(user, loan):
    return send_email(
        user.email,
        subject="Repayment Reminder - TechLend",
        template="emails/repayment_reminder.html",
        user=user,
        loan=loan,
        year=datetime.utcnow().year
    )

# ---------------- Arrears Alert Email ----------------
def send_arrears_alert_email(user, loan):
    return send_email(
        user.email,
        subject="Arrears Alert - TechLend",
        template="emails/arrears_alert.html",
        user=user,
        loan=loan,
        year=datetime.utcnow().year
    )
