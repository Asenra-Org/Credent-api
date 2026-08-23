from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, EmailStr
from typing import Optional
import uuid
import secrets
import hashlib
import datetime

from app.database.auth_db import get_auth_connection
from app.security.dependencies import get_current_user_and_session, get_current_tenant, require_role
from app.security.auth_service import hash_password

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

# --- Models ---

class CreateOrgRequest(BaseModel):
    name: str

class InviteUserRequest(BaseModel):
    email: EmailStr
    role: str  # CREDIT_ANALYST, UNDERWRITING_MANAGER, VIEWER, ORG_ADMIN

class UpdateUserStatusRequest(BaseModel):
    is_active: bool

class UpdateUserRoleRequest(BaseModel):
    role: str

# --- Endpoints ---

@router.get("/me")
def get_my_profile(current_user: dict = Depends(get_current_user_and_session)):
    """Get current authenticated user's profile, role, and organization."""
    user_id = current_user["user_id"]
    tenant_id = current_user["tenant_id"]
    
    conn = get_auth_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT email, is_active, created_at FROM users WHERE id = ?", (user_id,))
        user_row = cursor.fetchone()
        if not user_row:
            raise HTTPException(status_code=404, detail="User not found")
        
        cursor.execute("SELECT role, is_active FROM tenant_memberships WHERE user_id = ? AND tenant_id = ?", (user_id, tenant_id))
        membership = cursor.fetchone()
        
        cursor.execute("SELECT id, name FROM organizations WHERE id = ?", (tenant_id,))
        org_row = cursor.fetchone()
        
        return {
            "user_id": user_id,
            "email": user_row[0],
            "is_active": bool(user_row[1]),
            "created_at": user_row[2],
            "role": membership[0] if membership else None,
            "organization": {
                "id": org_row[0] if org_row else tenant_id,
                "name": org_row[1] if org_row else "Unknown"
            }
        }
    finally:
        conn.close()


@router.post("/organizations", status_code=status.HTTP_201_CREATED)
def create_organization(
    req: CreateOrgRequest,
    role: str = Depends(require_role(["SUPER_ADMIN"])),
    current_user: dict = Depends(get_current_user_and_session)
):
    """Create a new organization. SUPER_ADMIN only."""
    org_id = str(uuid.uuid4())
    conn = get_auth_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO organizations (id, name) VALUES (?, ?)",
            (org_id, req.name)
        )
        conn.commit()
        return {"id": org_id, "name": req.name, "message": "Organization created"}
    finally:
        conn.close()


@router.get("/organizations")
def list_organizations(
    role: str = Depends(require_role(["SUPER_ADMIN"])),
    current_user: dict = Depends(get_current_user_and_session)
):
    """List all organizations. SUPER_ADMIN only."""
    conn = get_auth_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, is_active, created_at FROM organizations ORDER BY created_at DESC")
        rows = cursor.fetchall()
        return [{"id": r[0], "name": r[1], "is_active": bool(r[2]), "created_at": r[3]} for r in rows]
    finally:
        conn.close()


@router.post("/organizations/{org_id}/users", status_code=status.HTTP_201_CREATED)
def invite_user(
    org_id: str,
    req: InviteUserRequest,
    role: str = Depends(require_role(["SUPER_ADMIN", "ORG_ADMIN"])),
    current_user: dict = Depends(get_current_user_and_session)
):
    """Invite a user to an organization. Creates user + membership."""
    VALID_ROLES = ["CREDIT_ANALYST", "UNDERWRITING_MANAGER", "VIEWER", "ORG_ADMIN"]
    if req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {VALID_ROLES}")
    
    conn = get_auth_connection()
    try:
        cursor = conn.cursor()
        
        # Verify org exists
        cursor.execute("SELECT id FROM organizations WHERE id = ?", (org_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Organization not found")
        
        # Check if user already exists
        cursor.execute("SELECT id FROM users WHERE email = ? COLLATE NOCASE", (req.email,))
        existing = cursor.fetchone()
        
        if existing:
            user_id = existing[0]
            # Check if already a member of this org
            cursor.execute("SELECT user_id FROM tenant_memberships WHERE user_id = ? AND tenant_id = ?", (user_id, org_id))
            if cursor.fetchone():
                raise HTTPException(status_code=409, detail="User already a member of this organization")
            # Add membership
            cursor.execute(
                "INSERT INTO tenant_memberships (user_id, tenant_id, role) VALUES (?, ?, ?)",
                (user_id, org_id, req.role)
            )
        else:
            # Create new user with a temporary password (they'll need to change it or use passwordless later)
            user_id = str(uuid.uuid4())
            temp_password = secrets.token_urlsafe(16)
            pwd_hash = hash_password(temp_password)
            
            cursor.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
                (user_id, req.email, pwd_hash)
            )
            cursor.execute(
                "INSERT INTO tenant_memberships (user_id, tenant_id, role) VALUES (?, ?, ?)",
                (user_id, org_id, req.role)
            )
        
        # Create invitation record
        invite_id = str(uuid.uuid4())
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.execute(
            "INSERT INTO invitations (id, email, organization_id, role, token_hash, expires_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (invite_id, req.email, org_id, req.role, token_hash, expires_at, current_user["user_id"])
        )
        
        conn.commit()
        return {
            "user_id": user_id,
            "email": req.email,
            "role": req.role,
            "organization_id": org_id,
            "invitation_token": raw_token,
            "message": "User invited successfully"
        }
    finally:
        conn.close()


@router.get("/organizations/{org_id}/users")
def list_org_users(
    org_id: str,
    role: str = Depends(require_role(["SUPER_ADMIN", "ORG_ADMIN"])),
    current_user: dict = Depends(get_current_user_and_session)
):
    """List all users in an organization."""
    conn = get_auth_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT u.id, u.email, u.is_active, u.created_at, tm.role, tm.is_active as membership_active
            FROM users u
            JOIN tenant_memberships tm ON u.id = tm.user_id
            WHERE tm.tenant_id = ?
            ORDER BY u.created_at DESC
        """, (org_id,))
        rows = cursor.fetchall()
        return [{
            "user_id": r[0], "email": r[1], "is_active": bool(r[2]),
            "created_at": r[3], "role": r[4], "membership_active": bool(r[5])
        } for r in rows]
    finally:
        conn.close()


@router.patch("/users/{user_id}/status")
def update_user_status(
    user_id: str,
    req: UpdateUserStatusRequest,
    role: str = Depends(require_role(["SUPER_ADMIN", "ORG_ADMIN"])),
    current_user: dict = Depends(get_current_user_and_session)
):
    """Enable or disable a user."""
    conn = get_auth_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if req.is_active else 0, user_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="User not found")
        conn.commit()
        return {"message": f"User {'activated' if req.is_active else 'deactivated'}"}
    finally:
        conn.close()


@router.patch("/users/{user_id}/role")
def update_user_role(
    user_id: str,
    req: UpdateUserRoleRequest,
    role: str = Depends(require_role(["SUPER_ADMIN", "ORG_ADMIN"])),
    current_user: dict = Depends(get_current_user_and_session)
):
    """Change a user's role within the current tenant."""
    VALID_ROLES = ["CREDIT_ANALYST", "UNDERWRITING_MANAGER", "VIEWER", "ORG_ADMIN"]
    if req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {VALID_ROLES}")
    
    tenant_id = current_user["tenant_id"]
    conn = get_auth_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE tenant_memberships SET role = ? WHERE user_id = ? AND tenant_id = ?",
            (req.role, user_id, tenant_id)
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="User membership not found")
        conn.commit()
        return {"message": f"Role updated to {req.role}"}
    finally:
        conn.close()
