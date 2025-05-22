# techlend/scripts/migrate_sqlite_data.py

import sqlite3
from extensions import db
from app import create_app
from models import User, Company, Loan
from werkzeug.security import generate_password_hash
from datetime import datetime

OLD_DB_PATH = 'loans.db'

app = create_app()
app.app_context().push()

def migrate_users_and_companies():
    conn = sqlite3.connect(OLD_DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT username, password, role FROM users")
    users = cursor.fetchall()

    for u in users:
        username, password, role = u
        email = f"{username}@example.com"
        company_name = f"{username}_company"

        company = Company.query.filter_by(email=email).first()
        if not company:
            company = Company(name=company_name, email=email)
            db.session.add(company)
            db.session.flush()

        existing_user = User.query.filter_by(email=email).first()
        if not existing_user:
            hashed_pw = password if password.startswith('pbkdf2') else generate_password_hash(password)
            user = User(username=username, email=email, password_hash=hashed_pw, role=role, company_id=company.id)
            db.session.add(user)
        else:
            print(f"User with email {email} already exists, skipping user creation.")

    db.session.commit()
    conn.close()
    print("✅ Users and Companies migrated.")

def migrate_loans():
    conn = sqlite3.connect(OLD_DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM loans")
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    for row in rows:
        data = dict(zip(columns, row))
        user = User.query.first()
        if not user:
            print("❌ No user found. Cannot migrate loans without users.")
            return

        loan = Loan(
            borrower_name=data['borrower_name'],
            phone_number=data['phone_number'],
            amount_borrowed=data['amount_borrowed'],
            processing_fee=data['processing_fee'],
            total_due=data['total_due'],
            amount_paid=data['amount_paid'],
            remaining_balance=data['remaining_balance'],
            date=datetime.strptime(data['date'], '%Y-%m-%d'),
            collateral=data['collateral'],
            status=data['status'],
            company_id=user.company_id,
            created_by=user.id
        )

        db.session.add(loan)

    db.session.commit()
    conn.close()
    print("✅ Loans migrated.")

def migrate_all():
    migrate_users_and_companies()
    migrate_loans()

if __name__ == '__main__':
    migrate_all()
