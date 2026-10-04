import sqlite3

conn = sqlite3.connect('app/database/credent.db')
c = conn.cursor()

# Get users with their roles
c.execute("""
    SELECT u.email, tm.role, tm.is_active
    FROM users u
    JOIN tenant_memberships tm ON u.id = tm.user_id
    ORDER BY tm.role
""")
rows = c.fetchall()
print(f"{'Email':<45} {'Role':<25} {'Active'}")
print("-" * 80)
for r in rows:
    print(f"{r[0]:<45} {r[1]:<25} {bool(r[2])}")

conn.close()
