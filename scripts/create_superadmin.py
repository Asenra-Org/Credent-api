import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth.security import get_password_hash
from app.database.database import create_user, get_user_by_email
import uuid

def seed_superadmin():
    email = "admin@asenra.in"
    if get_user_by_email(email):
        print(f"Superadmin {email} already exists.")
        return
        
    hashed_pw = get_password_hash("Credent@2026")
    user_id = str(uuid.uuid4())
    create_user(user_id, email, hashed_pw, role="SUPERADMIN")
    print(f"Superadmin {email} created successfully. Password: Credent@2026")

if __name__ == "__main__":
    seed_superadmin()
