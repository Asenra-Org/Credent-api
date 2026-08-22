import re

db_path = r"app\database\database.py"
with open(db_path, 'r', encoding='utf-8') as f:
    content = f.read()

users_table_sql = """
    # [Added] Users table for authentication
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        hashed_password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'ADMIN',
        is_active BOOLEAN NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
"""

content = content.replace("cursor.execute('CREATE INDEX IF NOT EXISTS idx_appraisal_created_at", users_table_sql + "\n    cursor.execute('CREATE INDEX IF NOT EXISTS idx_appraisal_created_at")

with open(db_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("database.py patched.")
