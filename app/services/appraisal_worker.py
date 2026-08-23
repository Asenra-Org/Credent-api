# =============================================================================
# CREDENT — Appraisal Worker (ASE-52)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
"""
AppraisalWorker — The core unit of work for async credit appraisal processing.

This module contains the single, self-contained function `run_appraisal_job()`
that is called by BOTH the BackgroundTaskAdapter (local dev) AND the CeleryAdapter
(production). Keeping the logic here — not in the adapter — ensures:
  - DRY: one implementation, two execution contexts
  - Testability: can be unit-tested without any queue infrastructure
  - Retry logic: centralized 3-attempt retry with exponential backoff
  - Crash safety: failed jobs set case to PAUSED (never silently lost)

Retry strategy: 3 attempts, 2-second base delay, exponential backoff (2^n * base).
  Attempt 1: immediate
  Attempt 2: 2s delay
  Attempt 3: 4s delay
  After all 3 fail: case status → PAUSED (manual review required)
"""
import asyncio
import logging
import os
import tempfile
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 2.0


async def run_appraisal_job(
    case_id: str,
    storage_path_handle: str,
    institution_id: str = "DEFAULT",
    attempt: int = 1
) -> dict:
    """
    Execute the full credit appraisal pipeline for a single document.

    This is the canonical worker function — callable from any execution context.

    Args:
        case_id: UUID of the loan case record in the database.
        storage_path_handle: Opaque storage handle returned by StorageService.upload_document().
        institution_id: Institution identifier for policy lookup.
        attempt: Current attempt number (1-indexed). Used for retry backoff.

    Returns:
        The full appraisal result dict on success.

    Side Effects:
        - Updates case status to RUNNING, then COMPLETED or PAUSED in the database.
        - Deletes the document from storage after successful processing (cost optimization).
    """
    from app.database.database import update_case_status, save_appraisal, get_case
    from app.services.storage_service import download_document, delete_document
    from app.agents.input.document_ingestion import DocumentIngestionAgent
    from app.agents.orchestration.coordinator import AgentCoordinator
    from app.agents.security.document_security import DocumentSecurityAgent

    logger.info(f"[Worker] Starting appraisal job | case_id={case_id} attempt={attempt}/{MAX_RETRIES}")

    # Mark case as RUNNING
    update_case_status(case_id, "RUNNING", current_step="worker_started")

    try:
        # 1. Download document from Supabase Storage (or local fallback)
        logger.info(f"[Worker] Downloading document from storage: {storage_path_handle}")
        file_bytes = download_document(storage_path_handle)

        # 2. Write to a temp file for the pipeline (agents expect file paths)
        file_ext = _extract_extension(storage_path_handle)
        with tempfile.NamedTemporaryFile(
            suffix=file_ext,
            delete=False,
            prefix=f"credent_{case_id}_"
        ) as tmp:
            tmp.write(file_bytes)
            temp_file_path = tmp.name

        logger.info(f"[Worker] Temp file written: {temp_file_path}")

        try:
            # 3. Security Gate — reject hostile documents before full pipeline
            security_scan = DocumentSecurityAgent.scan_file(temp_file_path)
            if not security_scan.is_safe:
                flags = ", ".join(security_scan.flags)
                logger.warning(f"[Worker] Security scan failed for case {case_id}: {flags}")
                update_case_status(case_id, "REJECTED", current_step="security_failed")
                return {"status": "REJECTED", "reason": f"Security validation failed: {flags}"}

            # 4. Run the full multi-agent appraisal coordinator
            update_case_status(case_id, "RUNNING", current_step="coordinator_running")
            ingestion_agent = DocumentIngestionAgent()
            coordinator = AgentCoordinator(ingestion_agent=ingestion_agent)
            appraisal_result = await coordinator.run_appraisal_with_state(
                {"file_path": temp_file_path, "institution_id": institution_id},
                case_id=case_id
            )

            # 5. Persist the appraisal results
            if isinstance(appraisal_result, dict):
                _persist_appraisal(appraisal_result, case_id, institution_id, coordinator=coordinator)

            # 6. Mark case COMPLETED
            update_case_status(case_id, "COMPLETED", current_step="done")
            logger.info(f"[Worker] ✓ Appraisal completed for case_id={case_id}")

            # 7. Cleanup: delete from storage after successful processing
            try:
                delete_document(storage_path_handle)
            except Exception as cleanup_err:
                logger.warning(f"[Worker] Non-fatal: Storage cleanup failed: {cleanup_err}")

            return appraisal_result

        finally:
            # Always clean up temp file regardless of success/failure
            try:
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)
            except Exception:
                pass

    except Exception as exc:
        logger.error(f"[Worker] Attempt {attempt} failed for case_id={case_id}: {exc}", exc_info=True)

        if attempt < MAX_RETRIES:
            # Exponential backoff: 2s, 4s, 8s...
            delay = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            logger.info(f"[Worker] Retrying case_id={case_id} in {delay:.1f}s (attempt {attempt + 1}/{MAX_RETRIES})")
            update_case_status(case_id, "RETRYING", current_step=f"retry_{attempt}")

            await asyncio.sleep(delay)
            return await run_appraisal_job(
                case_id=case_id,
                storage_path_handle=storage_path_handle,
                institution_id=institution_id,
                attempt=attempt + 1
            )
        else:
            # All retries exhausted — PAUSE the case for manual review
            logger.error(
                f"[Worker] All {MAX_RETRIES} attempts failed for case_id={case_id}. "
                f"Setting status to PAUSED for manual review. Final error: {exc}"
            )
            update_case_status(
                case_id, "PAUSED",
                current_step="manual_review_required"
            )
            return {
                "status": "PAUSED",
                "case_id": case_id,
                "reason": f"Processing failed after {MAX_RETRIES} attempts. Manual review required.",
                "error": str(exc)
            }


async def resume_appraisal_job(case_id: str) -> dict:
    """[ASE-63] Resumes a PAUSED case directly, bypassing file download."""
    from app.database.database import update_case_status
    from app.agents.input.document_ingestion import DocumentIngestionAgent
    from app.agents.orchestration.coordinator import AgentCoordinator

    logger.info(f"[Worker] Resuming appraisal job | case_id={case_id}")
    update_case_status(case_id, "RUNNING", current_step="coordinator_resumed")

    try:
        ingestion_agent = DocumentIngestionAgent()
        coordinator = AgentCoordinator(ingestion_agent=ingestion_agent)

        # Pass empty application_data (file_path=None) since coordinator restores from DB
        appraisal_result = await coordinator.run_appraisal_with_state(
            {"file_path": None, "institution_id": "DEFAULT"},
            case_id=case_id
        )

        if isinstance(appraisal_result, dict):
            if appraisal_result.get("status") == "paused":
                logger.info(f"[Worker] Appraisal paused again for case_id={case_id}")
                return appraisal_result
            _persist_appraisal(appraisal_result, case_id, "DEFAULT", coordinator=coordinator)

        update_case_status(case_id, "COMPLETED", current_step="done")
        logger.info(f"[Worker] ✓ Resumed appraisal completed for case_id={case_id}")
        return appraisal_result
    except Exception as exc:
        logger.error(f"[Worker] Resume failed for case_id={case_id}: {exc}", exc_info=True)
        update_case_status(case_id, "FAILED", current_step="failed")
        return {"status": "FAILED", "reason": str(exc)}



def _persist_appraisal(appraisal_result: dict, case_id: str, institution_id: str, coordinator=None) -> None:
    """Extract relevant fields from the coordinator output and persist to database."""
    from app.database.database import save_appraisal
    from app.core.appraisal_safety import apply_safety_gate, persistence_fields

    try:
        individual = appraisal_result.get("individual_agent_outputs", {})
        ingestion_data = individual.get("ingestion", {})
        financial_data = individual.get("financial_health", {})
        sector_data = individual.get("sector_context", {})
        management_data = individual.get("management_quality", {})

        base_score = ingestion_data.get("base_score") or financial_data.get("financial_health_score", 50)
        forensics_penalty = appraisal_result.get("forensics_penalty", {})
        adjusted_score = forensics_penalty.get("adjusted_score", base_score)

        # [P0-4] Identical safety path to the API route: validate every agent,
        # then refuse to persist a valid credit decision when a REQUIRED
        # component failed. The record is still written for auditability, but
        # carries decision_allowed=False and ANALYSIS_INCOMPLETE.
        _exec_summary, _prov_summary, _ledger = apply_safety_gate(
            appraisal_result,
            coordinator=coordinator,
        )

        save_appraisal({
            "company_id": f"COMP_{case_id[:8]}",
            "company_name": ingestion_data.get("company_name", "Unknown Entity"),
            "sector": sector_data.get("sector", "N/A"),
            "revenue": financial_data.get("metrics", {}).get("revenue", 0.0),
            "debt": financial_data.get("metrics", {}).get("total_debt", 0.0),
            "base_score": base_score,
            "adjusted_score": adjusted_score,
            "decision": appraisal_result.get("combined_decision", {}).get("decision", "PENDING"),
            "recommended_loan_amount": appraisal_result.get("combined_decision", {}).get("recommended_loan_amount", "0"),
            "recommended_interest_rate": appraisal_result.get("combined_decision", {}).get("recommended_interest_rate", "N/A"),
            "decision_rationale": appraisal_result.get("combined_decision", {}).get("decision_rationale", ""),
            "cam_report": appraisal_result.get("combined_decision", {}),
            "web_research": {},
            "integrity_flags": individual.get("integrity_check", {}),
            "raw_document_data": ingestion_data,
            "financial_ratios": financial_data.get("ratios", {}),
            "management_score": management_data.get("management_score", 0.0),
            "promoter_analysis": management_data.get("promoter_analysis", []),
            "governance_assessment": management_data.get("governance_assessment", {}),
            "institution_id": institution_id,
            **persistence_fields(_exec_summary, _prov_summary, _ledger),
        })
    except Exception as e:
        logger.error(f"[Worker] Persistence error for case_id={case_id}: {e}", exc_info=True)


def _extract_extension(storage_path_handle: str) -> str:
    """Extract file extension from a storage path handle for temp file naming."""
    path = storage_path_handle.split("://", 1)[-1]
    ext = os.path.splitext(path)[1]
    return ext if ext else ".pdf"
