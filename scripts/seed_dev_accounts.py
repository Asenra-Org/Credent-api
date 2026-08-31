"""Idempotent development account seeding.

Replaces the single-shot ``seed_org.py``, which created a fresh organization on
every run and failed on the unique email constraint the second time. The test
suite deletes users, so these accounts need recreating regularly; this script is
safe to run as often as needed.

Development only. It exits without doing anything when APP_ENV=production, so it
cannot be used to plant known credentials in a live system - use the
``/api/v1/auth/bootstrap`` endpoint there instead.

    python scripts/seed_dev_accounts.py
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.core.db_policy import current_environment, PRODUCTION  # noqa: E402
from app.database.auth_db import get_auth_connection  # noqa: E402
from app.database.database import init_db  # noqa: E402
from app.security.auth_service import hash_password  # noqa: E402

DEV_PASSWORD = os.getenv("DEV_SEED_PASSWORD", "TestPassword123!")

PLATFORM_ORG = "CRESEM Platform"
TENANT_ORG = "HDFC Bank"

# (email, role, organization). SUPER_ADMIN is a platform operator and belongs to
# the platform organization, not to a lending tenant.
ACCOUNTS = [
    ("karan.patil@asenra.in", "SUPER_ADMIN", PLATFORM_ORG),
    ("admin@hdfc.com", "ORG_ADMIN", TENANT_ORG),
    ("maker@hdfc.com", "CREDIT_ANALYST", TENANT_ORG),
    ("checker@hdfc.com", "UNDERWRITING_MANAGER", TENANT_ORG),
    ("viewer@hdfc.com", "VIEWER", TENANT_ORG),
]


def _get_or_create_org(cursor, name: str) -> str:
    cursor.execute("SELECT id FROM organizations WHERE name = ? LIMIT 1", (name,))
    row = cursor.fetchone()
    if row:
        return row[0]
    org_id = str(uuid.uuid4())
    cursor.execute("INSERT INTO organizations (id, name) VALUES (?, ?)", (org_id, name))
    return org_id


def _upsert_user(cursor, email: str, password_hash: str) -> str:
    cursor.execute("SELECT id FROM users WHERE email = ? COLLATE NOCASE", (email,))
    row = cursor.fetchone()
    if row:
        user_id = row[0]
        # Reset the credential and clear any lockout left by failed attempts.
        cursor.execute(
            """UPDATE users
               SET password_hash = ?, is_active = 1, is_locked = 0,
                   failed_login_count = 0, lockout_until = NULL
               WHERE id = ?""",
            (password_hash, user_id),
        )
        return user_id
    user_id = str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO users (id, email, password_hash, is_active) VALUES (?, ?, ?, 1)",
        (user_id, email, password_hash),
    )
    return user_id


def _upsert_membership(cursor, user_id: str, tenant_id: str, role: str) -> None:
    cursor.execute(
        "SELECT role FROM tenant_memberships WHERE user_id = ? AND tenant_id = ?",
        (user_id, tenant_id),
    )
    if cursor.fetchone():
        cursor.execute(
            "UPDATE tenant_memberships SET role = ?, is_active = 1 WHERE user_id = ? AND tenant_id = ?",
            (role, user_id, tenant_id),
        )
        return
    cursor.execute(
        "INSERT INTO tenant_memberships (user_id, tenant_id, role, is_active) VALUES (?, ?, ?, 1)",
        (user_id, tenant_id, role),
    )


def seed() -> int:
    if current_environment() == PRODUCTION:
        print("Refusing to seed development accounts with APP_ENV=production.")
        print("Use POST /api/v1/auth/bootstrap to create the first live account.")
        return 1

    init_db()
    password_hash = hash_password(DEV_PASSWORD)

    conn = get_auth_connection()
    try:
        cursor = conn.cursor()
        org_ids = {name: _get_or_create_org(cursor, name) for name in (PLATFORM_ORG, TENANT_ORG)}

        for email, role, org_name in ACCOUNTS:
            user_id = _upsert_user(cursor, email, password_hash)
            _upsert_membership(cursor, user_id, org_ids[org_name], role)
            print(f"  {role:22s} {email:26s} ({org_name})")

        conn.commit()
    finally:
        conn.close()

    print(f"\nSeeded {len(ACCOUNTS)} accounts. Password: {DEV_PASSWORD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(seed())
