"""
Reset a user's password directly in the local database.
Run: python reset_password.py
"""
import sqlite3
import bcrypt

DB_PATH = 'app/database/credent.db'
EMAIL = 'karan.patil@asenra.in'
NEW_PASSWORD = 'Asenra@2025'

# Hash the new password
salt = bcrypt.gensalt(rounds=12)
hashed = bcrypt.hashpw(NEW_PASSWORD.encode('utf-8'), salt).decode('utf-8')

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("UPDATE users SET password_hash = ? WHERE email = ?", (hashed, EMAIL))
conn.commit()
print(f"Updated {c.rowcount} row(s). Password reset for: {EMAIL}")
print(f"New password: {NEW_PASSWORD}")
conn.close()
