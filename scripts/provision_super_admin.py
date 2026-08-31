"""Provision a platform SUPER_ADMIN operator.

Why this exists
---------------
``POST /api/v1/auth/bootstrap`` creates the very first operator and then latches
itself off via ``system_state.is_bootstrapped``. Once that is spent there is no
supported way to create another platform operator, so the only remaining option
has been a hand-written SQL statement against the live database. That is exactly
how a SUPER_ADMIN role ended up attached to a *lending tenant* rather than to the
platform organization, which made an ORG_ADMIN account render the platform
console. This script is the supported path, and it refuses to reproduce that
mistake.

What it guarantees
------------------
* SUPER_ADMIN is only ever granted inside the platform organization. Granting it
  inside a customer tenant is refused, not warned about.
* A user ends up with exactly one active membership. Multiple active memberships
  make login non-deterministic, because the tenant is chosen by an unordered
  ``LIMIT 1``; the script reports them and stops rather than adding another.
* The password is read from the environment or a hidden prompt. It is never
  taken from argv (which lands in shell history), never printed, and never
  logged.

Usage
-----
    # interactive, hidden prompt
    python scripts/provision_super_admin.py karan.patil@asenra.in

    # non-interactive
    SUPER_ADMIN_PASSWORD='...' python scripts/provision_super_admin.py karan.patil@asenra.in

Against production, set the target database as the API does (AUTH_DATABASE_URL)
and pass --i-understand-this-is-production.
"""

import argparse
import getpass
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.core.db_policy import PRODUCTION, current_environment  # noqa: E402
from app.database.auth_db import get_auth_connection  # noqa: E402
from app.database.database import init_db  # noqa: E402
from app.security.auth_service import hash_password  # noqa: E402

PLATFORM_ORG = "CRESEM Platform"
MIN_PASSWORD_LEN = 12


def _read_password() -> str:
    """Environment first, hidden prompt second. Never argv."""
    pwd = os.getenv("SUPER_ADMIN_PASSWORD")
    if pwd:
        return pwd
    pwd = getpass.getpass("Password for the operator account: ")
    confirm = getpass.getpass("Confirm: ")
    if pwd != confirm:
        raise SystemExit("Passwords do not match.")
    return pwd


def _platform_org_id(cursor) -> str:
    cursor.execute("SELECT id FROM organizations WHERE name = ? LIMIT 1", (PLATFORM_ORG,))
    row = cursor.fetchone()
    if row:
        return row[0]
    org_id = str(uuid.uuid4())
    cursor.execute("INSERT INTO organizations (id, name) VALUES (?, ?)", (org_id, PLATFORM_ORG))
    print(f"  created platform organization '{PLATFORM_ORG}'")
    return org_id


def provision(email: str, password: str, confirmed_production: bool) -> int:
    is_production = current_environment() == PRODUCTION
    if is_production and not confirmed_production:
        print("APP_ENV=production. Re-run with --i-understand-this-is-production to proceed.")
        return 1

    if len(password) < MIN_PASSWORD_LEN:
        print(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
        return 1

    email = email.strip()
    init_db()

    conn = get_auth_connection()
    try:
        cursor = conn.cursor()
        org_id = _platform_org_id(cursor)

        cursor.execute("SELECT id FROM users WHERE email = ? COLLATE NOCASE", (email,))
        row = cursor.fetchone()

        if row:
            user_id = row[0]
            # An existing account may already belong to a customer tenant. Adding
            # a second active membership would make which one the token carries
            # depend on unordered row return. Stop and let a human decide.
            cursor.execute(
                """SELECT m.tenant_id, m.role, o.name
                   FROM tenant_memberships m
                   LEFT JOIN organizations o ON o.id = m.tenant_id
                   WHERE m.user_id = ? AND m.is_active = 1""",
                (user_id,),
            )
            others = [r for r in cursor.fetchall() if r[0] != org_id]
            if others:
                print(f"\n{email} already has active membership(s) outside the platform org:")
                for tenant_id, role, org_name in others:
                    print(f"    {role:22s} {org_name or tenant_id}")
                print(
                    "\nRefusing to add a second active membership: login picks the tenant\n"
                    "with an unordered LIMIT 1, so the effective role would be arbitrary.\n"
                    "Deactivate the membership above, or use a dedicated operator address."
                )
                return 1
            cursor.execute(
                """UPDATE users
                   SET password_hash = ?, is_active = 1, is_locked = 0,
                       failed_login_count = 0, lockout_until = NULL
                   WHERE id = ?""",
                (hash_password(password), user_id),
            )
            print(f"  reset credential for existing account {email}")
        else:
            user_id = str(uuid.uuid4())
            cursor.execute(
                "INSERT INTO users (id, email, password_hash, is_active) VALUES (?, ?, ?, 1)",
                (user_id, email, hash_password(password)),
            )
            print(f"  created account {email}")

        cursor.execute(
            "SELECT role FROM tenant_memberships WHERE user_id = ? AND tenant_id = ?",
            (user_id, org_id),
        )
        if cursor.fetchone():
            cursor.execute(
                "UPDATE tenant_memberships SET role = 'SUPER_ADMIN', is_active = 1 "
                "WHERE user_id = ? AND tenant_id = ?",
                (user_id, org_id),
            )
        else:
            cursor.execute(
                "INSERT INTO tenant_memberships (user_id, tenant_id, role, is_active) "
                "VALUES (?, ?, 'SUPER_ADMIN', 1)",
                (user_id, org_id),
            )

        conn.commit()
    finally:
        conn.close()

    print(f"\n  SUPER_ADMIN  {email}  ({PLATFORM_ORG})")
    print("  Sign in normally. The password was not printed or logged.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email", help="Operator email address")
    parser.add_argument(
        "--i-understand-this-is-production",
        action="store_true",
        dest="confirmed_production",
        help="Required when APP_ENV=production",
    )
    args = parser.parse_args()
    return provision(args.email, _read_password(), args.confirmed_production)


if __name__ == "__main__":
    raise SystemExit(main())
