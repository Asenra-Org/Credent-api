import uuid
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database.database import get_sqlite_connection
from app.security.auth_service import hash_password

def seed_data():
    conn = get_sqlite_connection()
    cursor = conn.cursor()

    # Create Organization
    org_id = str(uuid.uuid4())
    org_name = "HDFC Bank"
    cursor.execute("INSERT INTO organizations (id, name) VALUES (?, ?)", (org_id, org_name))

    # Define users to create
    users = [
        {"email": "admin@hdfc.com", "role": "ORG_ADMIN"},
        {"email": "maker@hdfc.com", "role": "CREDIT_ANALYST"},
        {"email": "checker@hdfc.com", "role": "UNDERWRITING_MANAGER"}
    ]

    pwd = "TestPassword123!"
    hashed = hash_password(pwd)

    print(f"Creating Organization: {org_name}")
    for u in users:
        uid = str(uuid.uuid4())
        cursor.execute("INSERT INTO users (id, email, password_hash, is_active) VALUES (?, ?, ?, 1)", (uid, u["email"], hashed))
        cursor.execute("INSERT INTO tenant_memberships (user_id, tenant_id, role, is_active) VALUES (?, ?, ?, 1)", (uid, org_id, u["role"]))
        print(f"Created {u['role']}: {u['email']}")

    conn.commit()
    conn.close()
    print("Seed successful.")

if __name__ == "__main__":
    seed_data()
