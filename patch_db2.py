import re

db_path = r"app\database\database.py"
with open(db_path, 'r', encoding='utf-8') as f:
    content = f.read()

user_methods = """
def get_user_by_email(email: str):
    if USE_SUPABASE:
        try:
            sb = _get_supabase()
            res = sb.table('users').select('*').eq('email', email).execute()
            return res.data[0] if res.data else None
        except Exception as e:
            print(f"[Supabase] get_user_by_email error: {e}")
            pass
            
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
        row = cursor.fetchone()
        if row:
            return {
                'id': row[0],
                'email': row[1],
                'hashed_password': row[2],
                'role': row[3],
                'is_active': bool(row[4]),
                'created_at': row[5]
            }
        return None
    except Exception as e:
        print(f"[SQLite] get_user_by_email error: {e}")
        return None
    finally:
        conn.close()

def create_user(user_id: str, email: str, hashed_password: str, role: str = 'ADMIN'):
    if USE_SUPABASE:
        try:
            sb = _get_supabase()
            sb.table('users').insert({
                'id': user_id,
                'email': email,
                'hashed_password': hashed_password,
                'role': role
            }).execute()
            return
        except Exception as e:
            print(f"[Supabase] create_user error: {e}")
            pass
            
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (id, email, hashed_password, role) VALUES (?, ?, ?, ?)",
            (user_id, email, hashed_password, role)
        )
        conn.commit()
    except Exception as e:
        print(f"[SQLite] create_user error: {e}")
    finally:
        conn.close()
"""

# Append to the bottom of the file
content += "\n" + user_methods

with open(db_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("database.py patched again.")
