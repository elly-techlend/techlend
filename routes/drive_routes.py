from flask import current_app
import os
import psycopg2
from flask import Blueprint, redirect, request, session, url_for, flash
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import google.oauth2.credentials
from models import db, Company
from flask_login import current_user, login_required
from functools import wraps
import json  # ✅ needed for parsing env variable

drive_bp = Blueprint("drive", __name__)

# ✅ No need for credentials.json anymore
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# -------------------- Role Check -------------------- #
def company_admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_company_admin:
            flash("Access denied.", "danger")
            return redirect(url_for("dashboard.index"))
        return f(*args, **kwargs)
    return wrapper


# -------------------- Authorize -------------------- #
@drive_bp.route("/drive/authorize")
@login_required
@company_admin_required
def authorize():
    company = Company.query.get(current_user.company_id)
    if not company:
        flash("Company not found.", "danger")
        return redirect(url_for("dashboard.index"))

    # ✅ Load Google credentials from environment
    GOOGLE_CREDENTIALS = json.loads(os.environ["GOOGLE_CREDENTIALS"])

    flow = Flow.from_client_config(
        GOOGLE_CREDENTIALS,
        scopes=SCOPES,
        redirect_uri=url_for("drive.callback", _external=True),
    )

    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )
    session["state"] = state
    return redirect(auth_url)

# -------------------- Test Environment Variable -------------------- #
@drive_bp.route("/test-env")
def test_env():
    from flask import jsonify
    return jsonify({"GOOGLE_CREDENTIALS": os.environ.get("GOOGLE_CREDENTIALS")})

# -------------------- Callback -------------------- #
@drive_bp.route("/drive/callback")
@login_required
@company_admin_required
def callback():
    if "state" not in session:
        flash("Session expired. Please try linking again.", "danger")
        return redirect(url_for("dashboard.index"))

    # ✅ Load Google credentials from environment
    GOOGLE_CREDENTIALS = json.loads(os.environ["GOOGLE_CREDENTIALS"])

    flow = Flow.from_client_config(
        GOOGLE_CREDENTIALS,
        scopes=SCOPES,
        redirect_uri=url_for("drive.callback", _external=True),
    )
    flow.fetch_token(authorization_response=request.url)

    if session["state"] != request.args.get("state"):
        return "State mismatch", 400

    credentials = flow.credentials
    client_id = flow.client_config["client_id"]
    client_secret = flow.client_config["client_secret"]

    company = Company.query.get(current_user.company_id)
    if not company:
        flash("Company not found.", "danger")
        return redirect(url_for("dashboard.index"))

    # ✅ Save tokens & secrets in DB
    company.drive_token = credentials.token
    company.drive_refresh_token = credentials.refresh_token
    company.drive_token_uri = credentials.token_uri
    company.drive_client_id = client_id
    company.drive_client_secret = client_secret
    db.session.commit()

    session.pop("state", None)
    flash("Google Drive linked successfully!", "success")
    return redirect(url_for("dashboard.index"))


# -------------------- Upload Backup -------------------- #
@drive_bp.route("/drive/upload")
@login_required
@company_admin_required
def upload_backup():
    company_id = current_user.company_id

    # ✅ Get DB URL from Flask config or env
    db_url = current_app.config.get("SQLALCHEMY_DATABASE_URI") or os.getenv("DATABASE_URL")
    if not db_url:
        flash("Database URL not defined in config or environment.", "danger")
        return redirect(url_for("dashboard.index"))

    # 🔧 psycopg2 needs postgresql://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    try:
        conn = psycopg2.connect(db_url)
    except Exception as e:
        flash(f"Database connection error: {str(e)}", "danger")
        return redirect(url_for("dashboard.index"))

    # -------------------- Backup SQL -------------------- #
    join_tables_sql = {
        # 1️⃣ Roles (needed for users)
        "roles": """
            SELECT 'INSERT INTO roles (id, name) VALUES ('
                || COALESCE(id::text,'NULL') || ',''' 
                || COALESCE(name,'') || ''');'
            FROM roles;
        """,

        # 2️⃣ Branches (depends on companies)
        "branches": """
            SELECT 'INSERT INTO branches (id, company_id, name, location, address, phone_number, is_active, created_at, updated_at, deleted_at) VALUES ('
                || COALESCE(b.id::text,'NULL') || ',' 
                || COALESCE(b.company_id::text,'NULL') || ',''' 
                || COALESCE(b.name,'') || ''',''' 
                || COALESCE(b.location,'') || ''',''' 
                || COALESCE(b.address,'') || ''',''' 
                || COALESCE(b.phone_number,'') || ''',''' 
                || COALESCE(b.is_active::text,'NULL') || ',''' 
                || COALESCE(b.created_at::text,'') || ',''' 
                || COALESCE(b.updated_at::text,'') || ',''' 
                || COALESCE(b.deleted_at::text,'') || ''');'
            FROM branches b
            WHERE company_id = %s;
        """,

        # 3️⃣ Users (depends on branches)
        "users": """
            SELECT 'INSERT INTO users (id, username, email, password_hash, is_active, is_superuser, created_at, updated_at, theme, timezone, language, full_name, company_id, branch_id, default_branch_id) VALUES ('
                || COALESCE(id::text,'NULL') || ',''' 
                || COALESCE(username,'') || ''',''' 
                || COALESCE(email,'') || ''',''' 
                || COALESCE(password_hash,'') || ''',' 
                || COALESCE(is_active::text,'NULL') || ',' 
                || COALESCE(is_superuser::text,'NULL') || ',''' 
                || COALESCE(created_at::text,'') || ',''' 
                || COALESCE(updated_at::text,'') || ',''' 
                || COALESCE(theme,'') || ''',''' 
                || COALESCE(timezone,'') || ''',''' 
                || COALESCE(language,'') || ''',''' 
                || COALESCE(full_name,'') || ''',' 
                || COALESCE(company_id::text,'NULL') || ',' 
                || COALESCE(branch_id::text,'NULL') || ',' 
                || COALESCE(default_branch_id::text,'NULL') || ');'
            FROM users
            WHERE company_id = %s;
        """,

        # 4️⃣ User Roles (depends on users, roles)
        "user_roles": """
            SELECT 'INSERT INTO user_roles (user_id, role_id) VALUES ('
                || COALESCE(user_id::text,'NULL') || ',' 
                || COALESCE(role_id::text,'NULL') || ');'
            FROM user_roles;
        """,

        # 5️⃣ Borrowers (depends on branches)
        "borrowers": """
            SELECT 'INSERT INTO borrowers (id, branch_id, borrower_id, created_at, name, phone, occupation, gender, title, date_of_birth, registration_date, place_of_birth, email, address, marital_status, spouse_name, number_of_children, education, next_of_kin, photo, company_id) VALUES ('
                || COALESCE(br.id::text,'NULL') || ',' 
                || COALESCE(br.branch_id::text,'NULL') || ',''' 
                || COALESCE(br.borrower_id,'') || ''',''' 
                || COALESCE(br.created_at::text,'') || ''',''' 
                || COALESCE(br.name,'') || ''',''' 
                || COALESCE(br.phone,'') || ''',''' 
                || COALESCE(br.occupation,'') || ''',''' 
                || COALESCE(br.gender,'') || ''',''' 
                || COALESCE(br.title,'') || ''',''' 
                || COALESCE(br.date_of_birth::text,'') || ''',''' 
                || COALESCE(br.registration_date::text,'') || ''',''' 
                || COALESCE(br.place_of_birth,'') || ''',''' 
                || COALESCE(br.email,'') || ''',''' 
                || COALESCE(br.address,'') || ''',''' 
                || COALESCE(br.marital_status,'') || ''',''' 
                || COALESCE(br.spouse_name,'') || ',' 
                || COALESCE(br.number_of_children::text,'NULL') || ',''' 
                || COALESCE(br.education,'') || ''',''' 
                || COALESCE(br.next_of_kin,'') || ''',''' 
                || COALESCE(br.photo,'') || ''',' 
                || COALESCE(br.company_id::text,'NULL') || ');'
            FROM borrowers br
            WHERE company_id = %s;
        """,

        # 6️⃣ Loans (depends on borrowers, branches, users, companies)
        "loans": """
            SELECT 'INSERT INTO loans (id, branch_id, loan_id, borrower_id, is_archived, borrower_name, phone_number, amount_borrowed, processing_fee, interest_rate, total_due, amount_paid, remaining_balance, collateral, date, due_date, status, created_by, company_id, approval_status, loan_duration_value, loan_duration_unit, cumulative_interest) VALUES ('
                || COALESCE(l.id::text,'NULL') || ',' 
                || COALESCE(l.branch_id::text,'NULL') || ',''' 
                || COALESCE(l.loan_id,'') || ''',' 
                || COALESCE(l.borrower_id::text,'NULL') || ',' 
                || COALESCE(l.is_archived::text,'NULL') || ',''' 
                || COALESCE(l.borrower_name,'') || ''',''' 
                || COALESCE(l.phone_number,'') || ''',' 
                || COALESCE(l.amount_borrowed::text,'NULL') || ',' 
                || COALESCE(l.processing_fee::text,'NULL') || ',' 
                || COALESCE(l.interest_rate::text,'NULL') || ',' 
                || COALESCE(l.total_due::text,'NULL') || ',' 
                || COALESCE(l.amount_paid::text,'NULL') || ',' 
                || COALESCE(l.remaining_balance::text,'NULL') || ',''' 
                || COALESCE(l.collateral,'') || ''',''' 
                || COALESCE(l.date::text,'') || ''',''' 
                || COALESCE(l.due_date::text,'') || ''',''' 
                || COALESCE(l.status,'') || ''',' 
                || COALESCE(l.created_by::text,'NULL') || ',' 
                || COALESCE(l.company_id::text,'NULL') || ',''' 
                || COALESCE(l.approval_status,'') || ''',' 
                || COALESCE(l.loan_duration_value::text,'NULL') || ',''' 
                || COALESCE(l.loan_duration_unit,'') || ''',' 
                || COALESCE(l.cumulative_interest::text,'NULL') || ');'
            FROM loans l
            WHERE company_id = %s;
        """,

        # 7️⃣ Archived Loans
        "archived_loans": """
            SELECT 'INSERT INTO archived_loans (id, original_loan_id, borrower_id, borrower_name, company_id, branch_id, amount, interest, duration, status, created_at, archived_at, archived_by) VALUES ('
                || COALESCE(id::text,'NULL') || ',' 
                || COALESCE(original_loan_id::text,'NULL') || ',' 
                || COALESCE(borrower_id::text,'NULL') || ',''' 
                || COALESCE(borrower_name,'') || ''',' 
                || COALESCE(company_id::text,'NULL') || ',' 
                || COALESCE(branch_id::text,'NULL') || ',' 
                || COALESCE(amount::text,'NULL') || ',' 
                || COALESCE(interest::text,'NULL') || ',' 
                || COALESCE(duration::text,'NULL') || ',''' 
                || COALESCE(status,'') || ''',''' 
                || COALESCE(created_at::text,'') || ''',''' 
                || COALESCE(archived_at::text,'') || ''',''' 
                || COALESCE(archived_by,'') || ''');'
            FROM archived_loans
            WHERE company_id = %s;
        """,

        # 8️⃣ Loan Repayments
        "loan_repayments": """
            SELECT 'INSERT INTO loan_repayments (id, branch_id, loan_id, amount_paid, date_paid, balance_after, principal_paid, interest_paid, cumulative_interest) VALUES ('
                || COALESCE(lr.id::text,'NULL') || ',' 
                || COALESCE(lr.branch_id::text,'NULL') || ',' 
                || COALESCE(lr.loan_id::text,'NULL') || ',' 
                || COALESCE(lr.amount_paid::text,'NULL') || ',''' 
                || COALESCE(lr.date_paid::text,'') || ''',' 
                || COALESCE(lr.balance_after::text,'NULL') || ',' 
                || COALESCE(lr.principal_paid::text,'NULL') || ',' 
                || COALESCE(lr.interest_paid::text,'NULL') || ',' 
                || COALESCE(lr.cumulative_interest::text,'NULL') || ');'
            FROM loan_repayments lr
            JOIN loans l ON lr.loan_id = l.id
            WHERE l.company_id = %s;
        """,

        # 9️⃣ Collaterals
        "collaterals": """
            SELECT 'INSERT INTO collaterals (id, borrower_id, item_name, model, serial_number, status, condition, created_at) VALUES ('
                || COALESCE(c.id::text,'NULL') || ',' 
                || COALESCE(c.borrower_id::text,'NULL') || ',''' 
                || COALESCE(c.item_name,'') || ''',''' 
                || COALESCE(c.model,'') || ''',''' 
                || COALESCE(c.serial_number,'') || ''',''' 
                || COALESCE(c.status,'') || ''',''' 
                || COALESCE(c.condition,'') || ''',''' 
                || COALESCE(c.created_at::text,'') || ''');'
            FROM collaterals c
            JOIN borrowers b ON c.borrower_id = b.id
            WHERE b.company_id = %s;
        """,

        #  🔟 Saving Accounts
        "saving_accounts": """
            SELECT 'INSERT INTO saving_accounts (id, company_id, borrower_id, branch_id, account_number, balance, date_opened) VALUES ('
                || COALESCE(sa.id::text,'NULL') || ',' 
                || COALESCE(sa.company_id::text,'NULL') || ',' 
                || COALESCE(sa.borrower_id::text,'NULL') || ',' 
                || COALESCE(sa.branch_id::text,'NULL') || ',''' 
                || COALESCE(sa.account_number,'') || ''',' 
                || COALESCE(sa.balance::text,'NULL') || ',''' 
                || COALESCE(sa.date_opened::text,'') || ''');'
            FROM saving_accounts sa
            WHERE company_id = %s;
        """,

        # 1️⃣1️⃣ Saving Transactions
        "saving_transactions": """
            SELECT 'INSERT INTO saving_transactions (id, account_id, transaction_type, amount, date) VALUES ('
                || COALESCE(st.id::text,'NULL') || ',' 
                || COALESCE(st.account_id::text,'NULL') || ',''' 
                || COALESCE(st.transaction_type,'') || ''',' 
                || COALESCE(st.amount::text,'NULL') || ',''' 
                || COALESCE(st.date::text,'') || ''');'
            FROM saving_transactions st
            JOIN saving_accounts sa ON st.account_id = sa.id
            WHERE sa.company_id = %s;
        """,

        # 1️⃣2️⃣ Expenses
        "expenses": """
            SELECT 'INSERT INTO expenses (id, description, amount, date, category, company_id, branch_id, created_by_id, created_at, updated_at) VALUES ('
                || COALESCE(id::text,'NULL') || ',''' 
                || COALESCE(description,'') || ''',' 
                || COALESCE(amount::text,'NULL') || ',''' 
                || COALESCE(date::text,'') || ''',''' 
                || COALESCE(category,'') || ''',' 
                || COALESCE(company_id::text,'NULL') || ',' 
                || COALESCE(branch_id::text,'NULL') || ',' 
                || COALESCE(created_by_id::text,'NULL') || ',''' 
                || COALESCE(created_at::text,'') || ''',''' 
                || COALESCE(updated_at::text,'') || ''');'
            FROM expenses
            WHERE company_id = %s;
        """,

        # 1️⃣3️⃣ Other Income
        "other_income": """
            SELECT 'INSERT INTO other_income (id, description, amount, income_date, company_id, branch_id, created_by_id, created_at, updated_at, is_active) VALUES ('
                || COALESCE(id::text,'NULL') || ',''' 
                || COALESCE(description,'') || ''',' 
                || COALESCE(amount::text,'NULL') || ',''' 
                || COALESCE(income_date::text,'') || ''',' 
                || COALESCE(company_id::text,'NULL') || ',' 
                || COALESCE(branch_id::text,'NULL') || ',' 
                || COALESCE(created_by_id::text,'NULL') || ',''' 
                || COALESCE(created_at::text,'') || ''',''' 
                || COALESCE(updated_at::text,'') || ''',' 
                || COALESCE(is_active::text,'NULL') || ');'
            FROM other_income
            WHERE company_id = %s;
        """,

        # 1️⃣4️⃣ Bank Transfers
        "bank_transfers": """
            SELECT 'INSERT INTO bank_transfers (id, transfer_type, amount, reference, transfer_date, company_id, branch_id, created_by_id, is_active, created_at, updated_at) VALUES ('
                || COALESCE(id::text,'NULL') || ',''' 
                || COALESCE(transfer_type,'') || ''',' 
                || COALESCE(amount::text,'NULL') || ',''' 
                || COALESCE(reference,'') || ''',''' 
                || COALESCE(transfer_date::text,'') || ''',' 
                || COALESCE(company_id::text,'NULL') || ',' 
                || COALESCE(branch_id::text,'NULL') || ',' 
                || COALESCE(created_by_id::text,'NULL') || ',' 
                || COALESCE(is_active::text,'NULL') || ',''' 
                || COALESCE(created_at::text,'') || ''',''' 
                || COALESCE(updated_at::text,'') || ''');'
            FROM bank_transfers
            WHERE company_id = %s;
        """,

        # 1️⃣5️⃣ Cashbook Entries
        "cashbook_entries": """
            SELECT 'INSERT INTO cashbook_entries (id, company_id, branch_id, date, particulars, debit, credit, balance, created_by) VALUES ('
                || COALESCE(id::text,'NULL') || ',' 
                || COALESCE(company_id::text,'NULL') || ',' 
                || COALESCE(branch_id::text,'NULL') || ',''' 
                || COALESCE(date::text,'') || ''',''' 
                || COALESCE(particulars,'') || ''',' 
                || COALESCE(debit::text,'NULL') || ',' 
                || COALESCE(credit::text,'NULL') || ',' 
                || COALESCE(balance::text,'NULL') || ',' 
                || COALESCE(created_by::text,'NULL') || ');'
            FROM cashbook_entries
            WHERE company_id = %s;
        """,

        # 1️⃣6️⃣ Ledger Entries
        "ledger_entries": """
            SELECT 'INSERT INTO ledger_entries (id, loan_id, date, particulars, principal, interest, principal_balance, interest_balance, running_balance, cumulative_interest, cumulative_interest_balance) VALUES ('
                || COALESCE(id::text,'NULL') || ',' 
                || COALESCE(loan_id::text,'NULL') || ',''' 
                || COALESCE(date::text,'') || ''',''' 
                || COALESCE(particulars,'') || ''',' 
                || COALESCE(principal::text,'NULL') || ',' 
                || COALESCE(interest::text,'NULL') || ',' 
                || COALESCE(principal_balance::text,'NULL') || ',' 
                || COALESCE(interest_balance::text,'NULL') || ',' 
                || COALESCE(running_balance::text,'NULL') || ',' 
                || COALESCE(cumulative_interest::text,'NULL') || ',' 
                || COALESCE(cumulative_interest_balance::text,'NULL') || ');'
            FROM ledger_entries;
        """,

        # 1️⃣7️⃣ Cashflow Snapshots
        "cashflow_snapshots": """
            SELECT 'INSERT INTO cashflow_snapshots (id, month, year, cash_in, cash_out, net_flow, company_id, branch_id, created_at) VALUES ('
                || COALESCE(id::text,'NULL') || ',''' 
                || COALESCE(month,'') || ''',' 
                || COALESCE(year::text,'NULL') || ',' 
                || COALESCE(cash_in::text,'NULL') || ',' 
                || COALESCE(cash_out::text,'NULL') || ',' 
                || COALESCE(net_flow::text,'NULL') || ',' 
                || COALESCE(company_id::text,'NULL') || ',' 
                || COALESCE(branch_id::text,'NULL') || ',''' 
                || COALESCE(created_at::text,'') || ''');'
            FROM cashflow_snapshots
            WHERE company_id = %s;
        """,

        # 1️⃣8️⃣ Company Logs
        "company_logs": """
            SELECT 'INSERT INTO company_logs (id, company_id, branch_id, message, created_at) VALUES ('
                || COALESCE(id::text,'NULL') || ',' 
                || COALESCE(company_id::text,'NULL') || ',' 
                || COALESCE(branch_id::text,'NULL') || ',''' 
                || COALESCE(message,'') || ''',''' 
                || COALESCE(created_at::text,'') || ''');'
            FROM company_logs
            WHERE company_id = %s;
        """,
    }

    backup_file_path = f"/tmp/backup_company_{company_id}.sql"

    try:
        with conn.cursor() as cur, open(backup_file_path, "w", encoding="utf-8") as f:
            for table, sql in join_tables_sql.items():
                cur.execute(sql, (company_id,))
                rows = cur.fetchall()
                for row in rows:
                    if row is None:
                        continue
                    value = row[0] if row[0] is not None else "NULL"
                    f.write(str(value) + "\n")

        # ------------------ Upload to Google Drive ------------------ #
        company = Company.query.get(company_id)
        if not company or not company.drive_token:
            flash("Google Drive not linked for this company.", "danger")
            return redirect(url_for("dashboard.index"))

        creds = google.oauth2.credentials.Credentials(
            token=company.drive_token,
            refresh_token=company.drive_refresh_token,
            token_uri=company.drive_token_uri,
            client_id=company.drive_client_id,
            client_secret=company.drive_client_secret,
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )

        service = build("drive", "v3", credentials=creds)

        file_metadata = {"name": f"backup_company_{company_id}.sql"}
        media = MediaFileUpload(backup_file_path, mimetype="application/sql")
        file = service.files().create(
            body=file_metadata, media_body=media, fields="id"
        ).execute()

        # Optionally, remove local temp file
        os.remove(backup_file_path)

        flash(f"Backup uploaded to Google Drive successfully! File ID: {file.get('id')}", "success")
        return redirect(url_for("dashboard.index"))

    except Exception as e:
        flash(f"Error during backup: {str(e)}", "danger")
        return redirect(url_for("dashboard.index"))

    finally:
        conn.close()
