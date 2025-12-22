from extensions import db
from flask_login import UserMixin
from datetime import datetime, date
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import event, UniqueConstraint
from sqlalchemy.orm import validates, relationship
import random
import string
from slugify import slugify
from sqlalchemy.event import listens_for
from itsdangerous import URLSafeTimedSerializer
from flask import current_app
from decimal import Decimal

user_roles = db.Table('user_roles',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id'), primary_key=True),
    db.Column('role_id', db.Integer, db.ForeignKey('roles.id'), primary_key=True)
)

def generate_reset_token(email):
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    return serializer.dumps(email, salt='password-reset-salt')

def verify_reset_token(token, expiration=3600):
    serializer = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        email = serializer.loads(token, salt='password-reset-salt', max_age=expiration)
    except Exception:
        return None
    return email

def set_password(self, password):
    self.password_hash = generate_password_hash(password)

class Role(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

    def __repr__(self):
        return f"<Role {self.name}>"

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False, index=True)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(200), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    is_superuser = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    theme = db.Column(db.String(10), default='light')
    timezone = db.Column(db.String(50), default='')
    language = db.Column(db.String(50), default='')

    full_name = db.Column(db.String(150), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=True)

    company = db.relationship('Company', back_populates='users')  # <== No backref here
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'), nullable=True)
    branch = db.relationship('Branch', foreign_keys=[branch_id], backref='users')

    default_branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'))
    default_branch = relationship("Branch", foreign_keys=[default_branch_id])

    roles = db.relationship('Role', secondary=user_roles, backref=db.backref('users', lazy='dynamic'))

    @property
    def role_names(self):
        return [role.name.lower() for role in self.roles]

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    @property
    def is_admin(self):
        return 'admin' in [role.name.lower() for role in self.roles]

    @property
    def is_company_admin(self):
        return self.is_admin and self.company_id is not None

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        return str(self.id)

    def has_role(self, role_names):
        if isinstance(role_names, list):
            return any(role.name.lower() in [r.lower() for r in role_names] for role in self.roles)
        return any(role.name.lower() == role_names.lower() for role in self.roles)

    def __repr__(self):
        return f"<User {self.username}>"

class Company(db.Model):
    __tablename__ = 'companies'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), unique=True, nullable=False, index=True)
    slug = db.Column(db.String(160), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    logo_url = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    expenses = db.relationship('Expense', back_populates='company')

    drive_token = db.Column(db.String)
    drive_refresh_token = db.Column(db.String)
    drive_token_uri = db.Column(db.String)
    drive_client_id = db.Column(db.String)
    drive_client_secret = db.Column(db.String)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    users = db.relationship('User', back_populates='company', lazy=True)  # <== Match the User model
    loans = db.relationship('Loan', back_populates='company', cascade='all, delete', lazy=True)

    other_incomes = db.relationship('OtherIncome', back_populates='company', cascade='all, delete-orphan')

    def __repr__(self):
        return f"<Company {self.name}>"

# Add event listener for creating slug
@listens_for(Company, 'before_insert')
def create_slug(mapper, connection, target):
    if not target.slug:
        target.slug = slugify(target.name)

class Branch(db.Model):
    __tablename__ = 'branches'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(100), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    phone_number = db.Column(db.String(20), nullable=True)
    expenses = db.relationship('Expense', back_populates='branch')

    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = db.Column(db.DateTime, nullable=True)  # Soft delete timestamp

    # Foreign key to the Company
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id', name='fk_branches_company_id'), nullable=False)
    
    # Relationship to the Company
    company = db.relationship('Company',backref=db.backref('branches', cascade='all, delete', lazy='dynamic'))

    # Unique constraint for branch name within a company
    __table_args__ = (
        UniqueConstraint('name', 'company_id', name='uq_branches_name_company'),
    )

    def __repr__(self):
        return f"<Branch {self.name} - Company ID {self.company_id}>"

class Borrower(db.Model):
    __tablename__ = 'borrowers'

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'), nullable=True)
    borrower_id = db.Column(db.String(20), unique=True, nullable=False)  # e.g., BRW-XXXXXX
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    occupation = db.Column(db.String(120))
    gender = db.Column(db.String(10))
    title = db.Column(db.String(10))  # e.g., Mr, Mrs, Dr, etc.
    date_of_birth = db.Column(db.Date)
    registration_date = db.Column(db.Date, default=date.today)
    place_of_birth = db.Column(db.String(100))
    email = db.Column(db.String(120))
    address = db.Column(db.String(200))
    marital_status = db.Column(db.String(20))
    spouse_name = db.Column(db.String(100))
    number_of_children = db.Column(db.Integer)
    education = db.Column(db.String(50))
    next_of_kin = db.Column(db.String(100))
    photo = db.Column(db.String(200))
    company_id = db.Column(db.Integer)  # For multi-tenancy

    documents = db.relationship('BorrowerDocument', back_populates='borrower', cascade='all, delete-orphan', lazy='dynamic')

    # Relationships
    loans = db.relationship('Loan', back_populates='borrower', lazy='dynamic', cascade='all, delete-orphan')
    savings_accounts = db.relationship('SavingAccount', back_populates='borrower', lazy='dynamic', cascade='all, delete-orphan')

    @property
    def total_paid(self):
        from models import LoanRepayment
        total = 0
        for loan in self.loans:
            repayments = LoanRepayment.query.filter_by(loan_id=loan.id).all()
            total += sum(r.amount_paid for r in repayments)
        return total

    @property
    def open_balance(self):
        return sum(loan.remaining_balance for loan in self.loans)

class BorrowerDocument(db.Model):
    __tablename__ = 'borrower_documents'

    id = db.Column(db.Integer, primary_key=True)
    borrower_id = db.Column(db.Integer, db.ForeignKey('borrowers.id'), nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    description = db.Column(db.String(200))  # optional: e.g., "Agreement", "ID Card"

    borrower = db.relationship('Borrower', back_populates='documents')

class Loan(db.Model):
    __tablename__ = 'loans'

    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'), nullable=True)
    loan_id = db.Column(db.String(20), unique=True, nullable=True)
    borrower_id = db.Column(db.Integer, db.ForeignKey('borrowers.id'), nullable=False)
    borrower = db.relationship('Borrower', back_populates='loans')

    is_archived = db.Column(db.Boolean, default=False)
    borrower_name = db.Column(db.String(150), nullable=False)
    phone_number = db.Column(db.String(20))
    
    amount_borrowed = db.Column(db.Numeric(12, 2), nullable=False)
    processing_fee = db.Column(db.Numeric(12, 2), default=Decimal('0.00'))
    interest_rate = db.Column(db.Float, default=20.0)  # Keep this as Float unless you're calculating interest with Decimal
    total_due = db.Column(db.Numeric(12, 2), nullable=False)
    amount_paid = db.Column(db.Numeric(12, 2), default=Decimal('0.00'))
    remaining_balance = db.Column(db.Numeric(12, 2), nullable=False)

    loan_duration_value = db.Column(db.Integer)
    loan_duration_unit = db.Column(db.String(10))  # 'days', 'weeks', etc.
    collateral = db.Column(db.String(255))

    cumulative_interest = db.Column(db.Numeric(12, 2), default=Decimal('0.00'))
    date = db.Column(db.DateTime, default=db.func.current_timestamp())
    due_date = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(50), default='Pending')
    approval_status = db.Column(db.String(20), default='pending')

    repayments = db.relationship('LoanRepayment', backref='loan', cascade='all, delete-orphan', passive_deletes=True)
    ledger_entries = db.relationship('LedgerEntry', back_populates='loan', cascade='all, delete-orphan', order_by='LedgerEntry.date')

    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    company = db.relationship('Company', back_populates='loans')

    @property
    def total_interest(self):
        return Decimal(self.amount_borrowed) * Decimal(self.interest_rate) / Decimal(100)

    def __repr__(self):
        return f"<Loan {self.loan_id} for {self.borrower_name}>"

# Using event listeners to auto-generate the loan_id before insert
@event.listens_for(Loan, 'before_insert')
def receive_before_insert(mapper, connection, target):
    if target.loan_id is None:
        # Get the company name (for the company initial part)
        company_name = target.company.name
        # Generate the loan_id
        target.loan_id = Loan.generate_loan_id(company_name)

class LoanRepayment(db.Model):
    __tablename__ = 'loan_repayments'
    id = db.Column(db.Integer, primary_key=True)
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'), nullable=True)
    loan_id = db.Column(db.Integer, db.ForeignKey('loans.id', ondelete='CASCADE'), nullable=False)
    
    amount_paid = db.Column(db.Numeric(12, 2), nullable=False)
    principal_paid = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal('0.00'))
    interest_paid = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal('0.00'))

    cumulative_interest = db.Column(db.Numeric(12, 2), default=Decimal('0.00'))  # ✅ Add this line    
    date_paid = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    balance_after = db.Column(db.Numeric(12, 2), nullable=True)

    def __repr__(self):
        return f"<Repayment of {self.amount_paid} for Loan ID {self.loan_id}>"

class LedgerEntry(db.Model):
    __tablename__ = 'ledger_entries'

    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.Integer, db.ForeignKey('loans.id', ondelete='CASCADE'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    particulars = db.Column(db.String(255), nullable=False)

    principal = db.Column(db.Numeric(12, 2), default=Decimal('0.00'))
    interest = db.Column(db.Numeric(12, 2), default=Decimal('0.00'))
    cumulative_interest = db.Column(db.Numeric(12, 2), default=Decimal('0.00'))  # NEW

    amount = db.Column(db.Numeric(12, 2), default=Decimal('0.00'))
    
    principal_balance = db.Column(db.Numeric(12, 2), default=Decimal('0.00'))
    interest_balance = db.Column(db.Numeric(12, 2), default=Decimal('0.00'))
    cumulative_interest_balance = db.Column(db.Numeric(12, 2), default=Decimal('0.00'))  # NEW
    
    running_balance = db.Column(db.Numeric(12, 2), default=Decimal('0.00'))

    loan = db.relationship('Loan', back_populates='ledger_entries')

class SavingAccount(db.Model):
    __tablename__ = 'saving_accounts'

    id = db.Column(db.Integer, primary_key=True)

    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    borrower_id = db.Column(db.Integer, db.ForeignKey('borrowers.id'), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'), nullable=False)

    account_number = db.Column(db.String(30), unique=True, nullable=False)
    balance = db.Column(db.Numeric(12, 2), default=Decimal('0.00'))
    date_opened = db.Column(db.DateTime, default=datetime.utcnow)

    borrower = db.relationship('Borrower', back_populates='savings_accounts')
    company = db.relationship('Company', backref='saving_accounts')
    branch = db.relationship('Branch')

    def __repr__(self):
        return f"<SavingAccount {self.account_number} for {self.borrower.name}>"

class SavingTransaction(db.Model):
    __tablename__ = 'saving_transactions'
    id = db.Column(db.Integer, primary_key=True)

    account_id = db.Column(db.Integer, db.ForeignKey('saving_accounts.id'), nullable=False)
    transaction_type = db.Column(db.String(10), nullable=False)  # 'deposit' or 'withdrawal'
    amount = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal('0.00'))
    date = db.Column(db.DateTime, default=datetime.utcnow)

    account = db.relationship('SavingAccount', backref='transactions')

    def __repr__(self):
        return f"<{self.transaction_type.title()} of {self.amount} on {self.date.strftime('%Y-%m-%d')} to Account {self.account_id}>"

class ArchivedLoan(db.Model):
    __tablename__ = 'archived_loans'
    id = db.Column(db.Integer, primary_key=True)
    original_loan_id = db.Column(db.Integer, nullable=False)
    borrower_id = db.Column(db.Integer, db.ForeignKey('borrowers.id'))
    borrower_name = db.Column(db.String(255))
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'))
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'))
    amount = db.Column(db.Numeric(12, 2))
    interest = db.Column(db.Float)
    duration = db.Column(db.Integer)
    status = db.Column(db.String(50))
    created_at = db.Column(db.DateTime)
    archived_at = db.Column(db.DateTime, default=datetime.utcnow)
    archived_by = db.Column(db.String(255))  # full name of staff

    def __repr__(self):
        return f'<ArchivedLoan {self.original_loan_id}>'

class CashbookEntry(db.Model):
    __tablename__ = 'cashbook_entries'

    id = db.Column(db.Integer, primary_key=True)
    
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'), nullable=True)
    date = db.Column(db.Date, default=date.today, nullable=False)

    particulars = db.Column(db.String(255), nullable=False)
    debit = db.Column(db.Numeric(12, 2), default=Decimal('0.00'))
    credit = db.Column(db.Numeric(12, 2), default=Decimal('0.00'))
    balance = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal('0.00'))

    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    company = db.relationship('Company', backref='cashbook_entries')
    branch = db.relationship('Branch', backref='cashbook_entries')
    user = db.relationship('User', backref='cashbook_entries')

    def __repr__(self):
        return f"<CashbookEntry {self.date} | {self.particulars} | Dr: {self.debit} | Cr: {self.credit}>"

class Expense(db.Model):
    __tablename__ = 'expenses'

    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    category = db.Column(db.String(100), nullable=True)
    
    # Foreign keys
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    # Relationships
    company = db.relationship('Company', back_populates='expenses')
    branch = db.relationship('Branch', back_populates='expenses')
    created_by = db.relationship('User', backref='expenses_created')

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Collateral(db.Model):
    __tablename__ = 'collaterals'

    id = db.Column(db.Integer, primary_key=True)
    borrower_id = db.Column(db.Integer, db.ForeignKey('borrowers.id'), nullable=False)

    item_name = db.Column(db.String(100), nullable=False)
    model = db.Column(db.String(100))
    serial_number = db.Column(db.String(100))
    status = db.Column(db.String(50))      # e.g., held, returned, lost
    condition = db.Column(db.String(100))  # e.g., good, fair, damaged
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    borrower = db.relationship('Borrower', backref='collaterals')

class OtherIncome(db.Model):
    __tablename__ = 'other_income'

    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(255), nullable=False, index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    income_date = db.Column(db.Date, default=datetime.utcnow, nullable=False)
    
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    company = db.relationship('Company', back_populates='other_incomes')

    branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'), nullable=True)  # <-- Add this
    branch = db.relationship('Branch', backref='other_incomes')                      # <-- Add this

    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_by = db.relationship('User')

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    is_active = db.Column(db.Boolean, default=True)

class BankTransfer(db.Model):
    __tablename__ = 'bank_transfers'

    id = db.Column(db.Integer, primary_key=True)
    transfer_type = db.Column(db.String(50))  # 'deposit' or 'withdraw'
    amount = db.Column(db.Float, nullable=False)
    reference = db.Column(db.String(255))
    transfer_date = db.Column(db.Date, nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    branch = db.relationship('Branch', backref='bank_transfers')

class CashFlowSnapshot(db.Model):
    __tablename__ = 'cashflow_snapshots'
    id = db.Column(db.Integer, primary_key=True)
    month = db.Column(db.String(20), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    cash_in = db.Column(db.Float, default=0.0)
    cash_out = db.Column(db.Float, default=0.0)
    net_flow = db.Column(db.Float, default=0.0)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Voucher(db.Model):
    __tablename__ = 'vouchers'

    id = db.Column(db.Integer, primary_key=True)
    voucher_number = db.Column(db.String(100), unique=True, nullable=True)
    voucher_type = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(255))
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    
    borrower_id = db.Column(db.Integer, db.ForeignKey('borrowers.id'), nullable=True)
    loan_id = db.Column(db.Integer, db.ForeignKey('loans.id'), nullable=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey('branches.id'), nullable=True)
    is_approved = db.Column(db.Boolean, default=False)

    # Relationships
    branch = db.relationship('Branch', backref=db.backref('vouchers', lazy='dynamic'))
    borrower = db.relationship('Borrower', backref=db.backref('vouchers', lazy='dynamic'))
    loan = db.relationship('Loan', backref=db.backref('vouchers', lazy='dynamic'))
    creator = db.relationship('User', backref='vouchers_created', lazy=True)

@listens_for(Voucher, 'before_insert')
def generate_voucher_number(mapper, connect, target):
    year = datetime.utcnow().year
    last_voucher = connect.execute(
        db.text("SELECT voucher_number FROM vouchers WHERE voucher_number LIKE :pattern ORDER BY id DESC LIMIT 1"),
            {'pattern': f'VCH-{year}-%'}
        ).fetchone()

    if last_voucher:
        last_num = int(last_voucher[0].split('-')[-1])
    else:
        last_num = 0

    new_number = f"VCH-{year}-{last_num + 1:04d}"
    target.voucher_number = new_number

class CompanyLog(db.Model):
    __tablename__ = 'company_logs'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    branch_id = db.Column(
        db.Integer,
        db.ForeignKey('branches.id', name='fk_companylog_branch_id'),  # <-- Name added here
        nullable=True
    )
    message = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SystemLog(db.Model):
    __tablename__ = 'system_logs'
    id = db.Column(db.Integer, primary_key=True)
    message = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
