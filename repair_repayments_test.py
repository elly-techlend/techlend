import psycopg2
from decimal import Decimal

# DB connection
conn = psycopg2.connect(
    dbname="techlend_db",
    user="techlend_db_user",
    password="66iyzNoCC9GAeg4DO8uhMfxXD2MTmOk2",
    host="dpg-d0nonj6uk2gs73aoter0-a.singapore-postgres.render.com",
    port=5432
)
cur = conn.cursor()

# Fetch all loans
cur.execute("""
    SELECT id, amount_borrowed, interest_rate, total_due
    FROM loans;
""")

loans = cur.fetchall()
print(f"\n🔍 Found {len(loans)} loans. Recalculating balances...\n")

for idx, loan in enumerate(loans, 1):
    loan_id = loan[0]
    amount_borrowed = Decimal(loan[1])
    interest_rate = Decimal(loan[2])
    total_due = Decimal(loan[3])

    # Sum repayments for this loan
    cur.execute("""
        SELECT COALESCE(SUM(principal_paid), 0), COALESCE(SUM(interest_paid), 0)
        FROM loan_repayments
        WHERE loan_id = %s;
    """, (loan_id,))
    
    result = cur.fetchone()
    total_principal_paid = Decimal(result[0])
    total_interest_paid = Decimal(result[1])
    total_paid = total_principal_paid + total_interest_paid

    # Calculate new remaining balance
    remaining_balance = max(Decimal(0), total_due - total_paid)

    # Update loan record
    cur.execute("""
        UPDATE loans
        SET remaining_balance = %s
        WHERE id = %s;
    """, (remaining_balance, loan_id))

    print(f"✅ ({idx}/{len(loans)}) Loan ID {loan_id} | Remaining Balance Updated To: {remaining_balance}")

# Commit and close
conn.commit()
cur.close()
conn.close()

print("\n✅ Loan balance repair complete.")
