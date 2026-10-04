import sqlite3, json

conn = sqlite3.connect('app/database/credent.db')
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
print('TABLES:', tables)

# check loan_cases table
c.execute("SELECT case_id, status, created_at FROM loan_cases ORDER BY created_at DESC LIMIT 5")
print('LOAN_CASES:', c.fetchall())

# check cases table if exists
if 'cases' in tables:
    c.execute("SELECT id, status, borrower_name, created_at FROM cases ORDER BY created_at DESC LIMIT 5")
    print('CASES:', c.fetchall())
