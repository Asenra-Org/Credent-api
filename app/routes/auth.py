from fastapi import APIRouter, HTTPException, status, Depends, Request, Response
from pydantic import BaseModel, EmailStr
import datetime

from app.security.auth_service import (
    verify_password,
    handle_failed_login,
    handle_successful_login,
    create_session,
    revoke_session,
    generate_access_token,
    bootstrap_system,
    hash_token,
    enroll_mfa,
    verify_and_enable_mfa,
    verify_mfa_login,
    handle_failed_mfa,
    disable_mfa,
    generate_mfa_challenge_token,
    verify_mfa_challenge_token
)
from app.database.auth_db import get_auth_connection
from app.security.dependencies import get_current_user_and_session
from app.security.rate_limit_dependency import rate_limit

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class BootstrapRequest(BaseModel):
    initial_password: str

class MFACodeRequest(BaseModel):
    code: str

class MFALoginRequest(BaseModel):
    challenge_token: str
    code: str

@router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
def bootstrap(req: BootstrapRequest, request: Request):
    token = request.headers.get("X-Bootstrap-Token")
    if not token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing bootstrap token")
    if len(req.initial_password) < 12:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password too short")
    result = bootstrap_system(token, req.initial_password)
    return {"message": "System bootstrapped successfully", "data": result}

@router.post("/login", dependencies=[Depends(rate_limit("auth"))])
def login(req: LoginRequest, request: Request, response: Response):
    conn = get_auth_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, password_hash, is_active, is_locked, lockout_until, mfa_enabled
            FROM users
            WHERE email = ? COLLATE NOCASE
        """, (req.email,))
        user_row = cursor.fetchone()

        generic_fail = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials or account locked")
        if not user_row:
            handle_failed_login(req.email)
            raise generic_fail

        user_id, pwd_hash, is_active, is_locked, lockout_until, mfa_enabled = user_row
        if not is_active:
            raise generic_fail

        if is_locked:
            if lockout_until:
                lockout_time = datetime.datetime.strptime(lockout_until, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc)
                if datetime.datetime.now(datetime.timezone.utc) < lockout_time:
                    raise generic_fail
                else:
                    cursor.execute("UPDATE users SET is_locked = 0, failed_login_count = 0 WHERE id = ?", (user_id,))
                    conn.commit()
            else:
                raise generic_fail

        if not verify_password(req.password, pwd_hash):
            handle_failed_login(req.email)
            raise generic_fail

        cursor.execute("SELECT tenant_id FROM tenant_memberships WHERE user_id = ? AND is_active = 1 LIMIT 1", (user_id,))
        tenant_row = cursor.fetchone()
        if not tenant_row:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active tenant membership")
        tenant_id = tenant_row[0]

        if mfa_enabled:
            # Issue MFA Challenge
            challenge_token = generate_mfa_challenge_token(user_id, tenant_id)
            return {"mfa_required": True, "challenge_token": challenge_token}

        handle_successful_login(user_id)
        ip_addr = request.client.host if request.client else None
        user_agent = request.headers.get("User-Agent")

        session_id, raw_refresh = create_session(user_id, ip_addr, user_agent)
        access_token = generate_access_token(user_id, tenant_id, session_id)

        response.set_cookie(key="refresh_token", value=raw_refresh, httponly=True, secure=True, samesite="Strict", max_age=86400)
        return {"access_token": access_token, "token_type": "bearer"}
    finally:
        conn.close()

@router.post("/mfa/verify-login", dependencies=[Depends(rate_limit("auth"))])
def mfa_verify_login(req: MFALoginRequest, request: Request, response: Response):
    payload = verify_mfa_challenge_token(req.challenge_token)
    user_id = payload["sub"]
    tenant_id = payload["tenant_id"]

    # Check lock status again just in case
    conn = get_auth_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT is_locked, lockout_until FROM users WHERE id = ?", (user_id,))
        user_row = cursor.fetchone()
        if user_row and user_row[0]:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account locked")

        if not verify_mfa_login(user_id, req.code):
            handle_failed_mfa(user_id)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MFA code")

        handle_successful_login(user_id)

        ip_addr = request.client.host if request.client else None
        user_agent = request.headers.get("User-Agent")

        session_id, raw_refresh = create_session(user_id, ip_addr, user_agent)
        access_token = generate_access_token(user_id, tenant_id, session_id)

        response.set_cookie(key="refresh_token", value=raw_refresh, httponly=True, secure=True, samesite="Strict", max_age=86400)
        return {"access_token": access_token, "token_type": "bearer"}
    finally:
        conn.close()

@router.post("/mfa/enroll")
def mfa_enroll(current_user: dict = Depends(get_current_user_and_session)):
    user_id = current_user["user_id"]

    conn = get_auth_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT email, mfa_enabled FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if row[1]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA is already enabled")
        email = row[0]
    finally:
        conn.close()

    uri = enroll_mfa(user_id, email)
    return {"message": "MFA Secret Generated", "provisioning_uri": uri}

@router.post("/mfa/activate")
def mfa_activate(req: MFACodeRequest, current_user: dict = Depends(get_current_user_and_session)):
    user_id = current_user["user_id"]
    if verify_and_enable_mfa(user_id, req.code):
        return {"message": "MFA activated successfully"}

    handle_failed_mfa(user_id)
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid MFA code")

@router.post("/mfa/disable")
def disable_mfa_endpoint(current_user: dict = Depends(get_current_user_and_session)):
    # Requires an active session. In a stricter system, might require re-auth.
    user_id = current_user["user_id"]
    disable_mfa(user_id)
    return {"message": "MFA disabled and all sessions revoked. Please log in again."}



@router.post("/refresh")
def refresh(request: Request, response: Response):
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")

    token_hash = hash_token(refresh_token)

    conn = get_auth_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, expires_at, is_revoked
            FROM sessions
            WHERE refresh_token_hash = ?
        """, (token_hash,))
        session_row = cursor.fetchone()

        if not session_row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

        session_id, user_id, expires_at, is_revoked = session_row

        if is_revoked:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked")

        exp_time = datetime.datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc)
        if datetime.datetime.now(datetime.timezone.utc) > exp_time:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

        # Verify user still active
        cursor.execute("SELECT is_active, is_locked FROM users WHERE id = ?", (user_id,))
        user_row = cursor.fetchone()
        if not user_row or not user_row[0] or user_row[1]:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User account disabled or locked")

        cursor.execute("SELECT tenant_id FROM tenant_memberships WHERE user_id = ? AND is_active = 1 LIMIT 1", (user_id,))
        tenant_row = cursor.fetchone()
        if not tenant_row:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active tenant membership")
        tenant_id = tenant_row[0]

        # Issue new access token
        access_token = generate_access_token(user_id, tenant_id, session_id)

        # Optional: rotate refresh token could happen here, but we'll stick to a 24h lived one for now.
        return {"access_token": access_token, "token_type": "bearer"}
    finally:
        conn.close()

@router.post("/logout")
def logout(request: Request, response: Response):
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        token_hash = hash_token(refresh_token)
        conn = get_auth_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sessions WHERE refresh_token_hash = ?", (token_hash,))
            row = cursor.fetchone()
            if row:
                revoke_session(row[0])
        finally:
            conn.close()

    response.delete_cookie("refresh_token")
    return {"message": "Logged out successfully"}
