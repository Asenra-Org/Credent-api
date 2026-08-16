import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.ase52 import AgentExecution
from app.core.exceptions import TenantIsolationError

logger = logging.getLogger(__name__)

STALE_RUNNING_THRESHOLD_SECONDS = 3600

class SecurityEscalationException(Exception):
    pass

class InvalidStateTransitionError(Exception):
    pass

def reconcile_orphaned_execution_record(session: Session, exec_record_id: str, tenant_id: str, max_attempts: int = 4) -> None:
    """
    Atomically reconciles an orphaned RUNNING execution record.
    Transitions: RUNNING -> UNKNOWN_EXTERNAL_OUTCOME -> (RETRY_AUTHORIZED or FAILED).
    Enforces maximum attempt boundary (Max 4 attempts, attempt 5 strictly blocked).
    """
    exec_record = session.get(AgentExecution, exec_record_id)
    if not exec_record:
        return
        
    if exec_record.tenant_id != tenant_id:
        raise SecurityEscalationException("Cross-tenant execution reconciliation attempt rejected")
        
    if exec_record.status != 'RUNNING':
        logger.info(f"Execution {exec_record.id} status is {exec_record.status}, no reconciliation needed.")
        return
        
    # Temporary transition to highlight orphaned state
    exec_record.status = 'UNKNOWN_EXTERNAL_OUTCOME'
    
    if exec_record.attempt_number >= max_attempts:
        logger.warning(f"Worker process lost mid-execution (SIGKILL / unhandled crash) reached max attempt limit ({exec_record.attempt_number}/{max_attempts}). Transitioning to FAILED.")
        exec_record.status = 'FAILED'
        exec_record.error_message = f"Exceeded maximum attempt limit ({max_attempts}) after worker loss"
    else:
        exec_record.status = 'RETRY_AUTHORIZED'
        logger.info(f"Execution {exec_record.id} successfully reconciled to RETRY_AUTHORIZED (Next attempt: {exec_record.attempt_number + 1}).")
