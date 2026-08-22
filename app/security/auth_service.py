import os
import secrets
import hashlib
import bcrypt
import jwt
import pyotp
import uuid
import datetime
from fastapi import HTTPException, status
from app.database.database import get_sqlite_connection

JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-development-key-only")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 1

BOOTSTRAP_TOKEN = os.getenv("BOOTSTRAP_TOKEN", "default-dev-bootstrap-do-not-use")

# 1. PASSWORD SECURITY

def hash_password(password: str) -> str:
    """Hashes a password using bcrypt."""
    if not password:
        raise ValueError("Password cannot be empty")
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def verify_password(password: str, password_hash: str) -> bool:
    """Verifies a password against a bcrypt hash."""
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception:
        return False

# 2. TOKENS & SESSIONS

def generate_access_token(user_id: str, tenant_id: str, session_id: str) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    expire = now + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "type": "access",
        "iat": now,
        "exp": expire
    }
    encoded_jwt = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded_jwt

def generate_mfa_challenge_token(user_id: str, tenant_id: str) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    expire = now + datetime.timedelta(minutes=5) # short lived
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "type": "mfa_challenge",
        "iat": now,
        "exp": expire
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_mfa_challenge_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "mfa_challenge":
            raise jwt.InvalidTokenError("Invalid token type")
        return payload
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired MFA challenge token")

def verify_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise jwt.InvalidTokenError("Invalid token type")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

def generate_refresh_token() -> str:
    return secrets.token_urlsafe(64)

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()

def create_session(user_id: str, ip_address: str = None, user_agent: str = None) -> tuple[str, str]:
    """Creates a new session and returns (session_id, raw_refresh_token)."""
    session_id = str(uuid.uuid4())
    raw_token = generate_refresh_token()
    token_hash = hash_token(raw_token)
    
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sessions (id, user_id, refresh_token_hash, expires_at, ip_address, user_agent)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, user_id, token_hash, expires_at.strftime('%Y-%m-%d %H:%M:%S'), ip_address, user_agent))
        conn.commit()
    finally:
        conn.close()
        
    return session_id, raw_token

def revoke_session(session_id: str):
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("""
            UPDATE sessions
            SET is_revoked = 1, revoked_at = ?
            WHERE id = ?
        """, (now, session_id))
        conn.commit()
    finally:
        conn.close()

# 3. ACCOUNT LOCKOUT

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES_1 = 30
LOCKOUT_MINUTES_2 = 60
LOCKOUT_MINUTES_3 = 1440 # 24 hours

def _get_lockout_duration(failed_count: int) -> int:
    if failed_count < MAX_FAILED_ATTEMPTS:
        return 0
    elif failed_count < MAX_FAILED_ATTEMPTS + 5:
        return LOCKOUT_MINUTES_1
    elif failed_count < MAX_FAILED_ATTEMPTS + 10:
        return LOCKOUT_MINUTES_2
    else:
        return LOCKOUT_MINUTES_3

def handle_failed_login(email: str):
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, failed_login_count FROM users WHERE email = ? COLLATE NOCASE", (email,))
        row = cursor.fetchone()
        if row:
            user_id = row[0]
            failed_count = row[1] + 1
            lockout_mins = _get_lockout_duration(failed_count)
            
            if lockout_mins > 0:
                lockout_until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=lockout_mins)
                cursor.execute("""
                    UPDATE users 
                    SET failed_login_count = ?, lockout_until = ?, is_locked = 1
                    WHERE id = ?
                """, (failed_count, lockout_until.strftime('%Y-%m-%d %H:%M:%S'), user_id))
            else:
                cursor.execute("""
                    UPDATE users 
                    SET failed_login_count = ?
                    WHERE id = ?
                """, (failed_count, user_id))
            conn.commit()
    finally:
        conn.close()

def handle_successful_login(user_id: str):
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users 
            SET failed_login_count = 0, is_locked = 0, lockout_until = NULL
            WHERE id = ?
        """, (user_id,))
        conn.commit()
    finally:
        conn.close()

# 4. SECURE BOOTSTRAP

def bootstrap_system(provided_token: str, initial_password: str) -> dict:
    """
    Initializes the system. Can only be called once.
    """
    if not provided_token or not secrets.compare_digest(provided_token, BOOTSTRAP_TOKEN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid bootstrap token")
        
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        
        # Check if already bootstrapped
        cursor.execute("SELECT is_bootstrapped FROM system_state WHERE id = 1")
        row = cursor.fetchone()
        if not row or row[0] == 1:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="System already bootstrapped")
            
        # Begin transaction implicitly in sqlite3
        user_id = str(uuid.uuid4())
        tenant_id = str(uuid.uuid4())
        
        password_hash = hash_password(initial_password)
        
        # Create SuperAdmin
        cursor.execute("""
            INSERT INTO users (id, email, password_hash)
            VALUES (?, ?, ?)
        """, (user_id, "admin@credent.local", password_hash))
        
        # Create Organization
        cursor.execute("INSERT INTO organizations (id, name) VALUES (?, ?)", (tenant_id, "CRESEM Platform"))

        # Create First Tenant Membership
        cursor.execute("""
            INSERT INTO tenant_memberships (user_id, tenant_id, role)
            VALUES (?, ?, ?)
        """, (user_id, tenant_id, "SUPER_ADMIN"))
        
        # Set bootstrapped to true
        cursor.execute("UPDATE system_state SET is_bootstrapped = 1 WHERE id = 1")
        
        conn.commit()
        return {"user_id": user_id, "tenant_id": tenant_id, "email": "admin@credent.local"}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

# 5. MFA (MULTI-FACTOR AUTHENTICATION)

def enroll_mfa(user_id: str, email: str) -> str:
    """
    Generates a new TOTP secret for the user, stores it, and returns the provisioning URI.
    Note: This does NOT enable MFA. Verification is required to activate it.
    """
    secret = pyotp.random_base32()
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET mfa_secret = ? WHERE id = ?", (secret, user_id))
        conn.commit()
    finally:
        conn.close()
        
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name="Credent")

def verify_and_enable_mfa(user_id: str, code: str) -> bool:
    """
    Verifies a TOTP code and activates MFA if successful.
    """
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT mfa_secret FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if not row or not row[0]:
            return False
            
        secret = row[0]
        totp = pyotp.TOTP(secret)
        
        if totp.verify(code):
            cursor.execute("UPDATE users SET mfa_enabled = 1, failed_login_count = 0 WHERE id = ?", (user_id,))
            conn.commit()
            return True
        return False
    finally:
        conn.close()

def verify_mfa_login(user_id: str, code: str) -> bool:
    """
    Verifies a TOTP code during login.
    """
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT mfa_secret, mfa_enabled FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if not row or not row[1] or not row[0]: # Not enabled or no secret
            return False
            
        secret = row[0]
        totp = pyotp.TOTP(secret)
        
        if totp.verify(code):
            return True
        return False
    finally:
        conn.close()

def handle_failed_mfa(user_id: str):
    """
    Re-uses lockout logic for MFA failures to prevent brute force.
    """
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            handle_failed_login(row[0])
    finally:
        conn.close()

def disable_mfa(user_id: str):
    """
    Disables MFA and revokes all active sessions for security.
    """
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET mfa_enabled = 0, mfa_secret = NULL WHERE id = ?", (user_id,))
        
        # Revoke all sessions since security context changed drastically
        now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("UPDATE sessions SET is_revoked = 1, revoked_at = ? WHERE user_id = ?", (now, user_id))
        conn.commit()
    finally:
        conn.close()
