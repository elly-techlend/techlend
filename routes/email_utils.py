# email_utils.py
import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

def send_reset_email(to_email, token):
    sendgrid_api_key = os.environ.get('SENDGRID_API_KEY')
    from_email = os.environ.get('FROM_EMAIL')

    if not sendgrid_api_key:
        raise ValueError("SENDGRID_API_KEY is not set in environment variables")
    if not from_email:
        raise ValueError("FROM_EMAIL is not set in environment variables")

    reset_url = f"https://your-domain.com/reset-password/{token}"

    message = Mail(
        from_email=from_email,
        to_emails=to_email,
        subject='Password Reset Request - TechLend',
        html_content=f"""
            <p>Hello,</p>
            <p>You requested a password reset. Click the link below to reset your password:</p>
            <p><a href="{reset_url}">Reset Password</a></p>
            <p>If you didn't request this, please ignore this email.</p>
        """
    )
    try:
        sg = SendGridAPIClient(sendgrid_api_key)
        response = sg.send(message)
        return response.status_code
    except Exception as e:
        print(f"SendGrid Error: {e}")
        return None
