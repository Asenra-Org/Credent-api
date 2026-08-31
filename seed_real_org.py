import uuid
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database.auth_db import get_auth_connection
from app.security.auth_service import hash_password

def seed_data():
    conn = get_auth_connection()
    cursor = conn.cursor()

    # 1. Create Credent HQ for SUPER_ADMIN
    hq_org_id = str(uuid.uuid4())
    hq_org_name = "Credent HQ"
    cursor.execute("INSERT INTO organizations (id, name) VALUES (?, ?)", (hq_org_id, hq_org_name))

    # 2. Create Real Inspired Org
    bank_org_id = str(uuid.uuid4())
    bank_org_name = "Axis Bank - SME Lending"
    cursor.execute("INSERT INTO organizations (id, name) VALUES (?, ?)", (bank_org_id, bank_org_name))

    # Define users to create
    users = [
        # SUPER_ADMIN
        {"email": "karan.patil@asenra.in", "role": "SUPER_ADMIN", "org_id": hq_org_id},
        
        # Real Inspired Org Roles
        {"email": "regional.head@axis.com", "role": "ORG_ADMIN", "org_id": bank_org_id},
        {"email": "credit.manager@axis.com", "role": "UNDERWRITING_MANAGER", "org_id": bank_org_id},
        {"email": "credit.analyst@axis.com", "role": "CREDIT_ANALYST", "org_id": bank_org_id},
        {"email": "risk.auditor@axis.com", "role": "VIEWER", "org_id": bank_org_id}
    ]

    pwd = "TestPassword123!"
    hashed = hash_password(pwd)

    print(f"Creating Organizations: '{hq_org_name}' & '{bank_org_name}'")
    
    for u in users:
        cursor.execute("SELECT id FROM users WHERE email = ?", (u["email"],))
        existing_user = cursor.fetchone()
        
        if existing_user:
            uid = existing_user[0]
            print(f"User {u['email']} already exists with id {uid}. Updating role and tenant...")
            # update password
            cursor.execute("UPDATE users SET password_hash = ?, is_active = 1 WHERE id = ?", (hashed, uid))
            # Delete old memberships and add new one
            cursor.execute("DELETE FROM tenant_memberships WHERE user_id = ?", (uid,))
            cursor.execute("INSERT INTO tenant_memberships (user_id, tenant_id, role, is_active) VALUES (?, ?, ?, 1)", (uid, u["org_id"], u["role"]))
        else:
            uid = str(uuid.uuid4())
            cursor.execute("INSERT INTO users (id, email, password_hash, is_active) VALUES (?, ?, ?, 1)", (uid, u["email"], hashed))
            cursor.execute("INSERT INTO tenant_memberships (user_id, tenant_id, role, is_active) VALUES (?, ?, ?, 1)", (uid, u["org_id"], u["role"]))
            
        print(f"Seeded {u['role']}: {u['email']} (Password: {pwd})")

    conn.commit()
    conn.close()
    print("Seed successful.")

if __name__ == "__main__":
    seed_data()
