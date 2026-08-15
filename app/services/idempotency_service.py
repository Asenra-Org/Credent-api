import json
import hashlib
import re
import logging
from typing import Tuple, Optional, Any, Dict
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.models.ase52 import IdempotencyRecord
from app.core.exceptions import TenantIsolationError

logger = logging.getLogger(__name__)

class IdempotencyConflictError(Exception):
    pass

class IdempotencyInProgressError(Exception):
    pass

class IdempotencyRecoveryPersistenceError(Exception):
    pass

class MalformedIdempotencyKeyError(Exception):
    pass

class PayloadTooLargeError(Exception):
    pass

class RepositoryConflictError(Exception):
    pass

MAX_IDEMPOTENT_PAYLOAD_BYTES = 1048576  # 1MB
MAX_IDEMPOTENCY_KEY_LENGTH = 128
STALE_RECORD_TIMEOUT_SECONDS = 300

_SAFE_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.\:\/]{1,255}$")

def _canonicalize_obj(obj: Any) -> Any:
    """Recursively canonicalizes nested dicts/lists for deterministic JSON serialization."""
    if isinstance(obj, dict):
        return {k: _canonicalize_obj(v) for k, v in sorted(obj.items())}
    elif isinstance(obj, list):
        return [_canonicalize_obj(v) for v in obj]
    return obj

def compute_request_fingerprint(method: str, path: str, body_bytes: bytes) -> str:
    """Calculates a deterministic SHA-256 fingerprint for a request."""
    if len(body_bytes) > MAX_IDEMPOTENT_PAYLOAD_BYTES:
        raise PayloadTooLargeError(f"Request payload size ({len(body_bytes)} bytes) exceeds maximum allowable limit of {MAX_IDEMPOTENT_PAYLOAD_BYTES} bytes.")
    
    # We ignore headers/auth tokens for the fingerprint.
    base_str = f"{method}|{path}|{body_bytes.decode('utf-8', errors='replace')}"
    return hashlib.sha256(base_str.encode('utf-8')).hexdigest()

def validate_idempotency_key(idempotency_key: str) -> None:
    if not idempotency_key:
        raise MalformedIdempotencyKeyError("Idempotency-Key header cannot be empty.")
    if len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise MalformedIdempotencyKeyError(f"Idempotency-Key length ({len(idempotency_key)}) exceeds maximum allowed limit of {MAX_IDEMPOTENCY_KEY_LENGTH} characters.")
    if not _SAFE_KEY_PATTERN.match(idempotency_key):
        raise MalformedIdempotencyKeyError("Idempotency-Key contains invalid characters. Must contain only alphanumeric, '_', '-', '.', ':', '/'.")

class IdempotencyService:
    """Domain service managing atomic idempotency record creation, collision checking, and response replay."""
    
    def __init__(self, session: Session):
        self.session = session
        
    def _handle_null_response_record(self, existing: IdempotencyRecord, tenant_id: str, clean_key: str, request_hash: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError(f"Idempotency key '{clean_key}' was previously used with a different request payload.")
            
        raise IdempotencyInProgressError(f"A request with idempotency key '{clean_key}' is currently in progress.")

    def process_idempotent_request(self, tenant_id: str, idempotency_key: str, request_hash: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Checks for existing idempotency records or registers a new pending record.
        Returns: Tuple[is_replayed: bool, cached_response: Optional[Dict[str, Any]]]
        """
        if not tenant_id:
            raise TenantIsolationError("Tenant identity context is required for idempotency verification.")
            
        validate_idempotency_key(idempotency_key)
        
        existing = self.session.query(IdempotencyRecord).filter_by(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key
        ).first()
        
        if existing:
            if existing.request_hash != request_hash:
                raise IdempotencyConflictError(f"Idempotency key '{idempotency_key}' collision with different request payload.")
                
            if existing.response_payload is None:
                return self._handle_null_response_record(existing, tenant_id, idempotency_key, request_hash)
                
            try:
                cached = json.loads(existing.response_payload)
                return True, cached
            except Exception:
                raise Exception("Stored idempotency response payload was corrupted.")
                
        # Register new pending record
        # Note: We rely on unique constraint to catch concurrent inserts during flush/commit
        try:
            new_record = IdempotencyRecord(
                id=f"idemp_{hashlib.md5((tenant_id+idempotency_key).encode()).hexdigest()}",
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response_payload=None,
                created_at=datetime.now(timezone.utc)
            )
            self.session.add(new_record)
            self.session.flush()
            return False, None
        except Exception as e:
            # If IntegrityError occurs, it means a concurrent request already registered it.
            logger.error(f"Failed to flush new idempotency record: {e}")
            self.session.rollback()
            raise IdempotencyInProgressError(f"A request with idempotency key '{idempotency_key}' is currently in progress.")

    def store_response(self, tenant_id: str, idempotency_key: str, status_code: int, response_body: Dict[str, Any], headers: Dict[str, str] = None):
        """Stores completed response status, headers, and body in the idempotency record."""
        record = self.session.query(IdempotencyRecord).filter_by(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key
        ).first()
        
        if record:
            record.response_payload = json.dumps(response_body)
            self.session.add(record)
            self.session.flush()
