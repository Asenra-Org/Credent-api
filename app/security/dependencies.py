from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import datetime

from app.security.auth_service import verify_access_token
from app.database.auth_db import get_auth_connection

security = HTTPBearer()

def get_current_user_and_session(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = verify_access_token(token)
    except Exception as e:
        # verify_access_token already raises HTTPException
        raise e
        
    user_id = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    session_id = payload.get("session_id")
    
    if not user_id or not tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
        
    # Verify user still active and not locked
    conn = get_auth_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT is_active, is_locked FROM users WHERE id = ?", (user_id,))
        user_row = cursor.fetchone()
        if not user_row or not user_row[0] or user_row[1]:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User account disabled or locked")
            
        # Verify session not revoked
        cursor.execute("SELECT is_revoked FROM sessions WHERE id = ?", (session_id,))
        session_row = cursor.fetchone()
        if not session_row or session_row[0]:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session has been revoked")
    finally:
        conn.close()
        
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "session_id": session_id
    }

def get_current_tenant(current_user: dict = Depends(get_current_user_and_session)):
    """Returns the authenticated tenant_id, explicitly enforcing server-side extraction."""
    return current_user["tenant_id"]

def require_role(allowed_roles: list[str]):
    """
    Returns a dependency that verifies the user has one of the allowed roles
    for their active tenant membership. 
    """
    def role_checker(current_user: dict = Depends(get_current_user_and_session)):
        user_id = current_user["user_id"]
        tenant_id = current_user["tenant_id"]
        
        conn = get_auth_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT role, is_active FROM tenant_memberships 
                WHERE user_id = ? AND tenant_id = ?
            """, (user_id, tenant_id))
            row = cursor.fetchone()
            
            if not row:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No membership found for this tenant")
                
            role, is_active = row
            if not is_active:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant membership is inactive")
                
            if role not in allowed_roles:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Insufficient permissions. Required one of: {allowed_roles}")
                
            return role
        finally:
            conn.close()
            
    return role_checker
