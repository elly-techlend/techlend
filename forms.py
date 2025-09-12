from flask import session
from flask_wtf import FlaskForm
from wtforms import (StringField, TextAreaField, SelectField, DateField, IntegerField, FileField, SubmitField, BooleanField, SelectMultipleField)
from wtforms.validators import DataRequired, Email, Optional, EqualTo, Length
from wtforms import StringField, PasswordField, SubmitField, SelectField, DecimalField, DateField
from models import Company, Borrower
from wtforms import HiddenField
from flask_wtf import FlaskForm
from flask_login import current_user
from wtforms.widgets import ListWidget, CheckboxInput

class CSRFOnlyForm(FlaskForm):
    pass

class LoginForm(FlaskForm):
    username_or_email = StringField('Username or Email', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class CompanyForm(FlaskForm):
    name = StringField('Company Name', validators=[DataRequired()])
    email = StringField('Email', validators=[Email()])
    is_active = BooleanField('Active')
    submit = SubmitField('Update Company')

class AdminRegistrationForm(FlaskForm):
    full_name = StringField('Full Name', validators=[DataRequired(), Length(min=2, max=150)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    company = SelectField('Company', coerce=int, validators=[DataRequired()])
    
    def set_company_choices(self):
        self.company.choices = [(c.id, c.name) for c in Company.query.all()]

class CashbookEntryForm(FlaskForm):
    date = DateField('Date', validators=[DataRequired()])
    particulars = StringField('Particulars', validators=[DataRequired()])
    debit = DecimalField('Debit', validators=[Optional()])
    credit = DecimalField('Credit', validators=[Optional()])
    submit = SubmitField('Add Entry')

class AddBorrowerForm(FlaskForm):
    # personal info
    title = SelectField('Title',choices=[('Mr', 'Mr'), ('Mrs', 'Mrs'), ('Miss', 'Miss'),('Dr', 'Dr'), ('Ps', 'Ps'), ('Hon', 'Hon'),('H.E', 'H.E'), ('Rev', 'Rev')],validators=[Optional()])
    name             = StringField('Full Name',            validators=[DataRequired()])
    gender           = SelectField('Gender', choices=[('Male','Male'),('Female','Female')], validators=[Optional()])
    date_of_birth    = DateField('Date of Birth',          validators=[Optional()])
    registration_date= DateField('Registration Date',      validators=[Optional()])
    place_of_birth   = StringField('Place of Birth',       validators=[Optional()])
    photo            = FileField('Photo',                  validators=[Optional()])

    # contact
    email            = StringField('Email',                validators=[Optional(), Email()])
    phone            = StringField('Phone Number',         validators=[DataRequired()])
    address          = StringField('Address',              validators=[Optional()])

    # family & background
    marital_status   = SelectField('Marital Status',
                          choices=[('Single','Single'),('Married','Married')], validators=[Optional()])
    spouse_name      = StringField('Spouse Name',          validators=[Optional()])
    number_of_children = IntegerField('Number of Children', validators=[Optional()])

    # education & branch & kin
    education_level  = SelectField('Education Level',
                          choices=[('None','None'),('Primary','Primary'),
                                   ('Secondary','Secondary'),('Tertiary','Tertiary')], validators=[Optional()])
    occupation       = StringField('Occupation',            validators=[Optional()])
    branch_id        = HiddenField('Branch',                validators=[DataRequired()])
    next_of_kin      = StringField('Next of Kin',           validators=[Optional()])

    submit           = SubmitField('Add Borrower')

class ChangePasswordForm(FlaskForm):
    old_password = PasswordField('Old Password', validators=[DataRequired()])
    new_password = PasswordField('New Password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm New Password', validators=[
        DataRequired(), EqualTo('new_password', message='Passwords must match.')
    ])
    submit = SubmitField('Update Password')

# ---------------- Multi-checkbox Field ----------------
class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()

# ---------------- Borrower Email Form ----------------
class BorrowerEmailForm(FlaskForm):
    subject = StringField("Subject", validators=[DataRequired()])
    message = TextAreaField("Message", validators=[DataRequired()])
    borrowers = MultiCheckboxField("Select Borrowers", coerce=int)
    submit = SubmitField("Send Email")

    def set_borrowers_choices(self):
        """Populate borrowers dynamically based on current user's company and branch"""
        branch_id = session.get('active_branch_id')
        company_id = current_user.company_id

        borrowers = Borrower.query.filter_by(
            company_id=company_id,
            branch_id=branch_id
        ).all()

        # Update choices for the multi-checkbox field
        self.borrowers.choices = [(b.id, b.name) for b in borrowers]
