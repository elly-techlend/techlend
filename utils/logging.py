from datetime import datetime
from flask_login import current_user
from extensions import db
from models import CompanyLog, SystemLog


def log_company_action(company_id, message, branch_id=None):
    log = CompanyLog(
        company_id=company_id,
        branch_id=branch_id,  # this is the key addition
        message=str(message),
        created_at=datetime.utcnow()
    )
    db.session.add(log)
    db.session.commit()

def log_system_action(message):
    log = SystemLog(
        message=str(message),
        created_at=datetime.utcnow()
    )
    db.session.add(log)
    db.session.commit()


def log_action(message, user=None):
    """
    Logs an action to both company and system logs.
    Defaults to current_user if no user is provided.
    """
    user = user or current_user

    if not user or not getattr(user, 'is_authenticated', False) or not message:
        return

    company_id = user.company_id
    branch_id = getattr(user, 'branch_id', None)

    print(f"Logging for company: {company_id}, branch: {branch_id}")

    log_company_action(company_id, str(message), branch_id=branch_id)
    log_system_action(str(message))
