import psycopg2
from decimal import Decimal

# Database credentials
conn = psycopg2.connect(
    dbname="techlend_db",
    user="techlend_db_user",
    password="66iyzNoCC9GAeg4DO8uhMfxXD2MTmOk2",
    host="dpg-d0nonj6uk2gs73aoter0-a.singapore-postgres.render.com",
    port=5432
)
cur = conn.cursor()

# Step 1: Fetch all incorrect repayment records (remove LIMIT)
cur.execute("""
SELECT lr.id, lr.loan_id, lr.amount_paid, l.interest_rate, l.amount_borrowed,
       COALESCE(SUM(lr2.interest_paid), 0) AS total_interest_paid,
       COALESCE(SUM(lr2.principal_paid), 0) AS total_principal_paid
FROM loan_repayments lr
JOIN loans l ON lr.loan_id = l.id
LEFT JOIN loan_repayments lr2 ON lr2.loan_id = lr.loan_id AND lr2.id < lr.id
WHERE lr.principal_paid = 0 AND lr.interest_paid = 0 AND lr.amount_paid > 0
GROUP BY lr.id, lr.loan_id, lr.amount_paid, l.interest_rate, l.amount_borrowed
ORDER BY lr.date_paid;
""")

repayments = cur.fetchall()

print(f"\n🔍 Starting full update for {len(repayments)} repayment records...\n")

for idx, row in enumerate(repayments, 1):
    repayment_id = row[0]
    loan_id = row[1]
    amount_paid = Decimal(row[2])
    interest_rate = Decimal(row[3])
    principal = Decimal(row[4])
    interest_paid_so_far = Decimal(row[5])
    principal_paid_so_far = Decimal(row[6])

    total_interest = principal * (interest_rate / Decimal(100))
    remaining_interest = total_interest - interest_paid_so_far
    remaining_principal = principal - principal_paid_so_far

    # Prevent negative interest or principal payments
    interest_paid_now = max(Decimal(0), min(amount_paid, remaining_interest))
    principal_paid_now = max(Decimal(0), amount_paid - interest_paid_now)

    if principal_paid_now > remaining_principal:
        principal_paid_now = remaining_principal

    # Update the repayment record
    cur.execute("""
        UPDATE loan_repayments
        SET interest_paid = %s,
            principal_paid = %s
        WHERE id = %s;
    """, (interest_paid_now, principal_paid_now, repayment_id))

    print(f"✅ ({idx}/{len(repayments)}) Updated Repayment ID {repayment_id} | Interest: {interest_paid_now} | Principal: {principal_paid_now}")

# Save changes to DB
conn.commit()
cur.close()
conn.close()

print("\n✅ Full update complete for all repayments needing repair.")
