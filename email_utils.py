import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from flask import url_for, render_template
from datetime import datetime

# ---------------- Centralized Send Function ----------------
def send_email(to_email, subject, html_content, from_email=None):
    """
    Sends an email using SendGrid.

    Parameters:
        to_email (str): Recipient's email address.
        subject (str): Email subject line.
        html_content (str): HTML content of the email.
        from_email (str, optional): Sender's email. Defaults to environment variable FROM_EMAIL.
    """
    from_email = from_email or os.environ.get("FROM_EMAIL")
    sendgrid_api_key = os.environ.get("SENDGRID_API_KEY")

    if not from_email:
        raise ValueError("FROM_EMAIL is not set in environment variables")
    if not sendgrid_api_key:
        raise ValueError("SENDGRID_API_KEY is not set in environment variables")

    message = Mail(
        from_email=from_email,
        to_emails=to_email,
        subject=subject,
        html_content=html_content
    )

    try:
        sg = SendGridAPIClient(sendgrid_api_key)
        sg.send(message)
        print(f"[✓] Email sent to {to_email} with subject '{subject}'")
        return True
    except Exception as e:
        print(f"[✗] Failed to send email to {to_email}: {str(e)}")
        return False

# ---------------- Password Reset Email ----------------
def send_reset_email(user, token):
    reset_link = url_for('auth.reset_password_token', token=token, _external=True)
    subject = "Password Reset Request - TechLend"
    html_content = render_template("emails/reset_password.html", user=user, reset_link=reset_link)
    return send_email(user.email, subject, html_content,year=datetime.now().year)

# ---------------- Loan Approved Email ----------------
def send_loan_approval_email(user, loan):
    subject = f"Loan Approved - TechLend"
    html_content = render_template("emails/loan_approved.html", user=user, loan=loan)
    return send_email(user.email, subject, html_content,year=datetime.now().year)

# ---------------- Loan Rejected Email ----------------
def send_loan_rejection_email(user, loan):
    subject = f"Loan Rejected - TechLend"
    html_content = render_template("emails/loan_rejected.html", user=user, loan=loan)
    return send_email(user.email, subject, html_content,year=datetime.now().year)

# ---------------- Repayment Reminder Email ----------------
def send_repayment_reminder_email(user, loan):
    subject = "Repayment Reminder - TechLend"
    html_content = render_template("emails/repayment_reminder.html", user=user, loan=loan)
    return send_email(user.email, subject, html_content,year=datetime.now().year)

# ---------------- Arrears Alert Email ----------------
def send_arrears_alert_email(user, loan):
    subject = "Arrears Alert - TechLend"
    html_content = render_template("emails/arrears_alert.html", user=user, loan=loan)
    return send_email(user.email, subject, html_content,year=datetime.now().year)

def send_borrower_email(borrower, subject, message_body):
    """
    Send a custom email to a single borrower.
    """
    html_content = render_template(
        "emails/borrower_message.html",
        borrower=borrower,
        message_body=message_body,
        year=datetime.now().year
    )
    return send_email(borrower.email, subject, html_content)


def send_bulk_borrower_email(borrowers, subject, message_body):
    """
    Send a custom email to multiple borrowers.

    Parameters:
        borrowers (list): List of borrower objects (must have .email and .name/.username).
        subject (str): Subject of the email.
        message_body (str): Custom message text/HTML.

    Returns:
        dict: Summary with counts of successes and failures.
    """
    results = {"success": [], "failed": []}

    for borrower in borrowers:
        try:
            html_content = render_template(
                "emails/borrower_message.html",
                borrower=borrower,
                message_body=message_body,
                year=datetime.now().year
            )
            success = send_email(borrower.email, subject, html_content)
            if success:
                results["success"].append(borrower.email)
            else:
                results["failed"].append(borrower.email)
        except Exception as e:
            print(f"[✗] Error sending to {borrower.email}: {str(e)}")
            results["failed"].append(borrower.email)

    print(f"[✓] Bulk email complete — {len(results['success'])} sent, {len(results['failed'])} failed.")
    return results
