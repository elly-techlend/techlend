import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from flask import url_for

def send_reset_email(user, token):
    from_email = os.environ.get("FROM_EMAIL")
    sendgrid_api_key = os.environ.get("SENDGRID_API_KEY")

    if not from_email:
        raise ValueError("FROM_EMAIL is not set in environment variables")
    if not sendgrid_api_key:
        raise ValueError("SENDGRID_API_KEY is not set in environment variables")

    reset_link = url_for('auth.reset_password_token', token=token, _external=True)
    subject = "Password Reset Request - TechLend"
    html_content = f"""
    <p>Hello {getattr(user, 'username', 'User')},</p>
    <p>You requested to reset your password. Click the link below to proceed:</p>
    <p><a href="{reset_link}">Reset Password</a></p>
    <p>If you didn't request this, you can ignore this email.</p>
    <br>
    <p>Thanks,<br>TechLend Team</p>
    """

    message = Mail(
        from_email=from_email,
        to_emails=user.email,
        subject=subject,
        html_content=html_content
    )

    try:
        sg = SendGridAPIClient(sendgrid_api_key)
        sg.send(message)
        print(f"[✓] Password reset email sent to {user.email}")
    except Exception as e:
        print(f"[✗] Failed to send email: {str(e)}")
