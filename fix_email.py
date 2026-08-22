import sqlite3
conn = sqlite3.connect("app/database/credent.db")
conn.execute("UPDATE users SET email = 'admin@asenra.in' WHERE email = 'admin@credent.local'")
conn.commit()
conn.close()

