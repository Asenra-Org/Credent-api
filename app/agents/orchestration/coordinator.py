# =============================================================================
# CREDENT — Multi-Agent Coordinator (Orchestration & Audit Trail)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================

import os
import logging
import asyncio
from datetime import datetime

from app.core.llm import ChatGroqWithFallback as ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from app.agents.input.document_ingestion import DocumentIngestionAgent
from app.agents.analysis.financial_health import FinancialHealthAgent
from app.agents.analysis.management_quality import ManagementQualityAgent
from app.agents.analysis.sector_context import SectorContextAgent
from app.agents.analysis.integrity_verification import IntegrityVerificationAgent
from app.agents.orchestration.cam_generator import CAMGeneratorAgent
from app.database.database import get_policy, create_case, update_case_step, update_case_result, mark_case_failed, get_case
from app.agents.orchestration.case_state import LoanCaseState, PIPELINE_STEPS, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED
from app.core.decision_config import DECISION_PATH_TEMPERATURE
from app.core.execution_state import AnalysisStatus, DECISION_ANALYSIS_INCOMPLETE, classify_exception

logger = logging.getLogger(__name__)

# Timeout threshold for downstream asynchronous agent executions.
# Sarvam-105b can take 3-5 min per LLM call; with 4 agents running in
# parallel the old 120 s budget was routinely exhausted before any of them
# could finish, producing "ANALYSIS INCOMPLETE" on every run.
# 900 s (15 min) gives the full parallel batch room to complete.
AGENT_TIMEOUT_SECONDS = 900.0

# Timeout threshold for explanation generation
EXPLANATION_TIMEOUT_SECONDS = 120.0

# Safe Default Policy Configuration (Fallback)
DEFAULT_POLICY = {
    "current_ratio_safe": 1.2,
    "current_ratio_min": 1.0,
    "dscr_safe": 1.25,
    "dscr_min": 1.0,
    "de_high": 2.0,
    "auto_approve_cutoff": 60.0,
    "auto_reject_cutoff": 40.0,
    "penalty_weights": {
        "integrity_mismatch": 15.0,
        "promoter_flags": 10.0
    }
}

# Backward compatibility threshold fallbacks
THRESHOLD_CURRENT_RATIO_SAFE = DEFAULT_POLICY["current_ratio_safe"]
THRESHOLD_CURRENT_RATIO_MIN = DEFAULT_POLICY["current_ratio_min"]
THRESHOLD_DSCR_SAFE = DEFAULT_POLICY["dscr_safe"]
THRESHOLD_DSCR_MIN = DEFAULT_POLICY["dscr_min"]
THRESHOLD_DE_HIGH = DEFAULT_POLICY["de_high"]

# Recommendation templates
REC_CURRENT_RATIO = "Assess the working capital cycle and evaluate short-term liabilities."
REC_DSCR = "Request debt amortization schedules and project future free cash flows."
REC_DE = "Review the company's capital structure and assess debt repayment capabilities."


def _pipeline_step_index(step: str) -> int:
    """
    [W7] Return the ordinal index of `step` in PIPELINE_STEPS.
    Used to determine resume position for crash recovery in run_appraisal_with_state().
    Returns -1 for any unrecognised transient step name (e.g. 'ingestion_running').
    """
    try:
        return PIPELINE_STEPS.index(step)
    except ValueError:
        return -1


class AgentCoordinator:
    """
    Central orchestrator that:
    1. Receives a credit appraisal request
    2. Dispatches tasks to specialized agents
    3. Collects and synthesizes their outputs
    4. Builds an evidence trail for explainability
    5. Triggers the output layer (CAM, scoring, pricing)
    """

    def __init__(
        self,
        ingestion_agent=None,
        financial_agent=None,
        management_agent=None,
        sector_agent=None,
        integrity_agent=None,
        cam_agent=None
    ):
        # Set up agent instances with explicit non-None dependency injection
        self.ingestion_agent = ingestion_agent if ingestion_agent is not None else DocumentIngestionAgent()
        self.financial_agent = financial_agent if financial_agent is not None else FinancialHealthAgent()
        self.management_agent = management_agent if management_agent is not None else ManagementQualityAgent()
        self.sector_agent = sector_agent if sector_agent is not None else SectorContextAgent()
        self.integrity_agent = integrity_agent if integrity_agent is not None else IntegrityVerificationAgent()
        self.cam_agent = cam_agent if cam_agent is not None else CAMGeneratorAgent()

        # Initialize LLM for narrative explanation generation
        api_key = os.getenv("GROQ_API_KEY")
        self.llm = ChatGroq(
            model=os.getenv("PRIMARY_LLM_MODEL", "openai/gpt-oss-20b"),
            # [P0-3] Decision path: greedy decoding.
            temperature=DECISION_PATH_TEMPERATURE,
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            api_key=api_key or "dummy",
        )

    async def run_appraisal(self, application_data: dict) -> dict:
        """
        Execute the full credit appraisal pipeline.
        Returns a comprehensive assessment with evidence trail.
        """
        # Stage 1: Input Validation
        if not isinstance(application_data, dict):
            logger.error("Mandatory validation failed: application_data is not a dictionary.")
            raise ValueError("application_data must be a dictionary.")

        file_path = application_data.get("file_path")
        if not file_path or not isinstance(file_path, str):
            logger.error("Mandatory validation failed: file_path is missing or not a string.")
            raise ValueError("file_path must be a non-empty string.")

        if not os.path.exists(file_path):
            logger.error("Mandatory validation failed: file_path does not exist on disk.")
            raise ValueError(f"File not found: {file_path}")

        # Stage 1.5: Policy Retrieval
        institution_id = application_data.get("institution_id", "DEFAULT")
        try:
            policy = get_policy(institution_id) or DEFAULT_POLICY
        except Exception as pol_err:
            logger.warning("Failed to load policy for %s: %s. Using default policy.", institution_id, pol_err)
            policy = DEFAULT_POLICY

        # Stage 2: Sequential Ingestion (Fail-Fast)
        try:
            ingestion_result = await self.ingestion_agent.ingest_pdf(file_path)
            if not ingestion_result or (isinstance(ingestion_result, dict) and isinstance(ingestion_result.get("error"), str) and ingestion_result.get("error")):
                err_msg = ingestion_result.get("error", "Unknown ingestion error.")
                logger.error("Mandatory ingestion step failed: %s", err_msg)
                raise ValueError(f"Ingestion failed: {err_msg}")

            raw_text = ingestion_result.get("text", "")
            if not raw_text or len(raw_text.strip()) < 10:
                logger.error("Mandatory ingestion step failed: Empty or insufficient extracted text.")
                raise ValueError("Insufficient text extracted from PDF.")

            extracted_financials = await self.ingestion_agent.parse_financial_statement(raw_text)
            logger.info("Mandatory Ingestion and statement parsing completed.")
        except Exception as exc:
            logger.error("Mandatory ingestion phase failed with exception: %s", str(exc), exc_info=True)
            raise ValueError(f"Ingestion flow aborted: {exc}")

        # Extract downstream routing variables
        company_name = extracted_financials.get("company_name", "Unknown Company")
        sector_name = extracted_financials.get("sector", "Unknown Sector")
        promoter_ids = application_data.get("promoter_ids", [])
        gst_data = application_data.get("gst_data", [])
        bank_data = application_data.get("bank_data", [])

        # Stage 3: Launch Downstream Agents Concurrently
        # Sub-task coroutine to execute SectorContext outlook and circular checks sequentially within its task boundary
        async def get_sector_data(sec: str) -> dict:
            outlook_data = await self.sector_agent.get_sector_outlook(sec)
            rbi_data = await self.sector_agent.check_rbi_policies(sec)
            res = dict(outlook_data) if isinstance(outlook_data, dict) else {}
            res["rbi_policy_impact"] = rbi_data if isinstance(rbi_data, list) else []
            return res

        tasks = [
            asyncio.create_task(self.financial_agent.analyze(extracted_financials)),
            asyncio.create_task(self.management_agent.analyze({"company_name": company_name, "promoter_ids": promoter_ids})),
            asyncio.create_task(get_sector_data(sector_name)),
            asyncio.create_task(self.integrity_agent.cross_validate(gst_data, bank_data))
        ]

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=AGENT_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            logger.warning("Downstream agent orchestration timed out after %s seconds. Resolving with default fallbacks.", AGENT_TIMEOUT_SECONDS)
            results = [asyncio.TimeoutError("Task timed out")] * len(tasks)

        logger.info("Orchestration completed. Processing results.")

        # Stage 4: Collect and Normalize Results with Fallback Mappings
        financial_fallback = {
            "status": "success",
            "company_name": company_name,
            "financial_health_score": 50.0,
            "risk_level": "Medium",
            "ratios": {},
            "cash_flow_assessment": {"status": "Stable", "operating_cash_flow": None, "free_cash_flow": None, "trend": "Stable"},
            "analysis_notes": ["Financial analysis defaulted due to agent failure."],
            "recommendation": "Manual review required."
        }

        management_fallback = {
            "status": "error",
            "company_name": company_name,
            "management_score": 0.0,
            "risk_level": "Undetermined",
            "requires_manual_review": True,
            "fallback_reason": "llm_failure",
            "is_knockout": False,
            "promoter_analysis": [],
            "governance_assessment": {
                "board_independence": "Undetermined",
                "regulatory_compliance": "Undetermined",
                "risk_level": "Undetermined"
            }
        }

        sector_fallback = {
            "status": "success",
            "sector": sector_name,
            "outlook": "Stable",
            "risk_factors": ["Sector analysis defaulted due to agent failure."],
            "rbi_policy_impact": []
        }

        integrity_fallback = {
            "status": "success",
            "flags": [],
            "warnings": ["Integrity cross-validation defaulted due to agent failure."]
        }

        # Resolve financial health results
        fin_res = results[0]
        if isinstance(fin_res, Exception):
            logger.warning("FinancialHealthAgent failed: %s. Activating fallback.", str(fin_res))
            financial_result = financial_fallback
        else:
            financial_result = fin_res
        logger.debug("Financial health analysis completed.")

        # Resolve promoter quality results
        mgt_res = results[1]
        if isinstance(mgt_res, Exception):
            logger.warning("ManagementQualityAgent failed: %s. Activating fallback.", str(mgt_res))
            management_result = management_fallback
        else:
            management_result = mgt_res
        logger.debug("Management quality check completed.")

        # Resolve sector context results
        sec_res = results[2]
        if isinstance(sec_res, Exception):
            logger.warning("SectorContextAgent failed: %s. Activating fallback.", str(sec_res))
            sector_result = sector_fallback
        else:
            sector_result = sec_res
        logger.debug("Sector context analysis completed.")

        # Resolve integrity validation results
        int_res = results[3]
        if isinstance(int_res, Exception):
            logger.warning("IntegrityVerificationAgent failed: %s. Activating fallback.", str(int_res))
            integrity_result = integrity_fallback
        else:
            integrity_result = int_res
        logger.debug("Integrity cross-validation completed.")

        individual_outputs = {
            "ingestion": extracted_financials,
            "financial_health": financial_result,
            "management_quality": management_result,
            "sector_context": sector_result,
            "integrity_check": integrity_result
        }

        # Stage 5: Evidence Trail Generation (Graceful Degradation)
        try:
            evidence_trail = await self.build_evidence_trail(individual_outputs, policy)
            logger.debug("Evidence trail generated with %s entries.", len(evidence_trail))
        except Exception as exc:
            logger.error("Evidence trail generation failed: %s. Degrading gracefully with empty trail.", str(exc), exc_info=True)
            evidence_trail = [
                {
                    "category": "System",
                    "source_agent": "AgentCoordinator",
                    "severity": "WARNING",
                    "title": "Evidence Trail Degraded",
                    "description": f"The system failed to build the audit evidence trail dynamically due to: {type(exc).__name__}",
                    "recommendation": "Review logs to diagnose coordinator parsing issues.",
                    "confidence": "High"
                }
            ]

        # Stage 6: Explanation Summary
        explanation = await self.generate_explanation(evidence_trail)

        # Stage 7: Assemble Final Response (Including Decision Synthesis)
        score = int(financial_result.get("financial_health_score", 50.0))
        web_research = {
            "company_news": [],
            "sector_headwinds": sector_result.get("risk_factors", []),
            "litigation_signals": []
        }

        management_score = management_result.get("management_score", 0.0)
        is_knockout = management_result.get("is_knockout", False)
        requires_manual_review = management_result.get("requires_manual_review", False)
        fallback_reason = management_result.get("fallback_reason", "system_error")

        forced_decision = None
        forced_rationale = None

        if requires_manual_review:
            forced_decision = "MANUAL REVIEW"
            if fallback_reason == "missing_promoter":
                forced_rationale = "Management assessment could not be completed because promoter information was unavailable. The case has been escalated for manual review."
            else:
                forced_rationale = "Management assessment could not be completed and manual review is required."
        elif is_knockout:
            forced_decision = "REJECT"
            forced_rationale = "Management Hard Gate: Knockout condition detected."
        elif management_score < 50:
            forced_decision = "REJECT"
            forced_rationale = f"Management Hard Gate: Score ({management_score}) is below the minimum threshold of 50."

        try:
            ingestion_citations = extracted_financials.get("citations", {})
            cam_result = await self.cam_agent.generate_cam(
                extracted_financials,
                integrity_result,
                web_research,
                score,
                ingestion_citations=ingestion_citations
            )

            # Apply Hard Gate Override
            if forced_decision:
                cam_result["decision"] = forced_decision
                original_rationale = cam_result.get("decision_rationale", "")

                if requires_manual_review:
                    if fallback_reason == "missing_promoter":
                        five_c_text = "Manual review required because promoter information is unavailable or could not be extracted."
                    else:
                        five_c_text = "Manual review required because management assessment could not be completed."

                    if isinstance(cam_result.get("five_cs"), dict):
                        for k in cam_result["five_cs"]:
                            if isinstance(cam_result["five_cs"][k], dict):
                                cam_result["five_cs"][k]["text"] = five_c_text
                            else:
                                cam_result["five_cs"][k] = five_c_text

                    if "System encountered an error" in original_rationale:
                        cam_result["decision_rationale"] = forced_rationale
                    else:
                        cam_result["decision_rationale"] = f"{forced_rationale}\n\nOriginal Synthesis: {original_rationale}"
                else:
                    cam_result["decision_rationale"] = f"{forced_rationale}\n\nOriginal Synthesis: {original_rationale}"

                if forced_decision == "REJECT":
                    cam_result["recommended_loan_amount"] = "0"
                    cam_result["recommended_interest_rate"] = "N/A"
                elif forced_decision == "MANUAL REVIEW":
                    cam_result["recommended_loan_amount"] = "Withheld"
                    cam_result["recommended_interest_rate"] = "TBD"

        except Exception as cam_exc:
            logger.warning("Decision engine failed: %s. Triggering fallback decision mapping.", str(cam_exc))
            decision = "MANUAL REVIEW"

            if requires_manual_review and fallback_reason == "missing_promoter":
                five_c_text = "Manual review required because promoter information is unavailable or could not be extracted."
                rationale_text = "Management assessment could not be completed because promoter information was unavailable. The case has been escalated for manual review."
            elif requires_manual_review:
                five_c_text = "Manual review required because management assessment could not be completed."
                rationale_text = "Management assessment could not be completed and manual review is required."
            else:
                five_c_text = "Manual review required due to system error."
                rationale_text = "Underwriting could not be completed because CAM generation failed due to timeout. Triggering fallback decision."

            cam_result = {
                "five_cs": {k: five_c_text for k in ["character", "capacity", "capital", "collateral", "conditions"]},
                "decision": decision,
                "recommended_loan_amount": "Withheld",
                "recommended_interest_rate": "Withheld",
                "decision_rationale": rationale_text
            }

        appraisal_id = f"APPRAISAL_{int(datetime.now().timestamp())}"

        logger.info("Appraisal completed successfully. ID: %s", appraisal_id)

        return {
            "status": "success",
            "appraisal_id": appraisal_id,
            "individual_agent_outputs": individual_outputs,
            "combined_decision": cam_result,
            "evidence_trail": evidence_trail,
            "explanation": explanation,
            "evidence_citations": {
                "revenue": ingestion_citations.get("revenue"),
                "debt": ingestion_citations.get("debt"),
                "equity": ingestion_citations.get("equity"),
                "dscr": self._build_dscr_citation(extracted_financials, financial_result),
                "current_ratio": self._build_current_ratio_citation(extracted_financials, financial_result),
            }
        }


    async def run_appraisal_with_state(self, application_data: dict, case_id: str = None) -> dict:
        """
        [ASE-54] Stateful entry point for credit appraisal with persistent case tracking.

        - Creates a new LoanCaseState and persists it to DB on each major step.
        - Dynamically skips agents if their required data is absent.
        - On crash/restart, pass the existing case_id to resume from the last saved step.
        - Old run_appraisal() is untouched; this is an additive entry point.
        """
        import uuid

        # --- Resolve or create a case_id ---
        case_needs_creation = False
        if case_id:
            db_record = get_case(case_id)
            if db_record and db_record["status"] == STATUS_COMPLETED:
                logger.info("[ASE-54] Case %s already COMPLETED. Returning persisted result.", case_id)
                return db_record["result_data"]
            if db_record:
                logger.info("[ASE-54] Resuming case %s from step '%s'.", case_id, db_record["current_step"])
                state = LoanCaseState.from_db_record(db_record)
            else:
                logger.warning("[ASE-54] case_id %s not found in DB. Will create it.", case_id)
                case_needs_creation = True
        else:
            case_id = f"CASE_{uuid.uuid4().hex[:12].upper()}"
            case_needs_creation = True

        if case_needs_creation:
            institution_id = application_data.get("institution_id", "DEFAULT")
            create_case(case_id, application_data, institution_id)
            state = LoanCaseState(case_id=case_id, institution_id=institution_id)
            logger.info("[ASE-54] Created new case %s.", case_id)

        try:
            # ---- [W7] Crash recovery: compute resume position ----
            _resume_idx = _pipeline_step_index(state.current_step)
            _ingestion_done = (
                _resume_idx >= _pipeline_step_index("ingestion_complete")
                and bool(state.extracted_data)
            )
            _agents_done = (
                _resume_idx >= _pipeline_step_index("agents_dispatched")
                and bool(state.financial_result or state.sector_result or state.integrity_result)
            )
            _evidence_done = (
                _resume_idx >= _pipeline_step_index("evidence_built")
                and bool(state.evidence_trail)
            )
            if _ingestion_done:
                logger.info(
                    "[ASE-54] Case %s: Crash recovery — resuming from persisted step '%s'.",
                    case_id, state.current_step
                )

            # ---- STEP: Validate input ----
            if not isinstance(application_data, dict):
                raise ValueError("application_data must be a dictionary.")
            file_path = application_data.get("file_path")
            # [W7] On resume the temp file is already deleted — only enforce existence for fresh starts
            if not _ingestion_done:
                if not file_path or not os.path.exists(file_path):
                    raise ValueError(f"File not found or missing: {file_path}")

            # ---- STEP: Load policy (always reload — cheap and stateless) ----
            institution_id = application_data.get("institution_id", "DEFAULT")
            if _resume_idx < _pipeline_step_index("policy_loaded"):
                update_case_step(state.case_id, "policy_loading")
            try:
                policy = get_policy(institution_id) or DEFAULT_POLICY
            except Exception:
                policy = DEFAULT_POLICY
            if _resume_idx < _pipeline_step_index("policy_loaded"):
                state.step_complete("policy_loaded")
                update_case_step(state.case_id, "policy_loaded")

            # ---- STEP: Ingestion (mandatory, fail-fast) ----
            if not _ingestion_done:
                update_case_step(state.case_id, "ingestion_running")
                ingestion_result = await self.ingestion_agent.ingest_pdf(file_path)
                if not ingestion_result or ingestion_result.get("error"):
                    raise ValueError(f"Ingestion failed: {ingestion_result.get('error', 'unknown')}")

                raw_text = ingestion_result.get("text", "")
                if not raw_text or len(raw_text.strip()) < 10:
                    raise ValueError("Insufficient text extracted from PDF.")

                extracted_financials = await self.ingestion_agent.parse_financial_statement(raw_text)
                state.extracted_data = extracted_financials
                state.step_complete("ingestion_complete")
                update_case_step(state.case_id, "ingestion_complete")
                # [W7] Early snapshot: persist extracted_data immediately so crash after ingestion is recoverable
                update_case_result(state.case_id, {"extracted_data": extracted_financials}, status=STATUS_RUNNING)
            else:
                # Resume: reuse persisted ingestion output — no re-parse of the (now-deleted) temp file
                extracted_financials = state.extracted_data
                logger.info("[ASE-54] Case %s: Resuming — using persisted ingestion data.", case_id)

            # ---- DYNAMIC ROUTING: Detect available data ----
            state.detect_available_data(extracted_financials)
            company_name = extracted_financials.get("company_name", "Unknown Company")
            sector_name = extracted_financials.get("sector", "Unknown Sector")
            promoter_ids = application_data.get("promoter_ids", [])
            gst_data = application_data.get("gst_data", [])
            bank_data = application_data.get("bank_data", [])

            # [W7] GST/bank routing flag — skip IntegrityVerificationAgent when no transaction data provided
            state.has_gst_bank_data = bool(gst_data or bank_data)

            if not state.has_financials:
                logger.info("[ASE-54] Case %s: No P&L data detected — skipping FinancialHealthAgent.", case_id)
            if not state.has_promoters:
                logger.info("[ASE-54] Case %s: No promoter data detected — skipping ManagementAgent, routing to MANUAL REVIEW.", case_id)
            if not state.has_gst_bank_data:
                logger.info("[ASE-54] Case %s: No GST/bank data — skipping IntegrityVerificationAgent.", case_id)

            # ---- STEP: Dispatch agents (dynamic) ----
            if not _agents_done:
                update_case_step(state.case_id, "agents_dispatched")

                async def get_sector_data(sec: str) -> dict:
                    outlook_data = await self.sector_agent.get_sector_outlook(sec)
                    rbi_data = await self.sector_agent.check_rbi_policies(sec)
                    res = dict(outlook_data) if isinstance(outlook_data, dict) else {}
                    res["rbi_policy_impact"] = rbi_data if isinstance(rbi_data, list) else []
                    return res

                # Build task list — skip agents based on routing flags
                financial_fallback = {
                    "status": "skipped", "company_name": company_name,
                    "financial_health_score": 50.0, "risk_level": "Medium",
                    "ratios": {}, "cash_flow_assessment": {"status": "Stable"},
                    "analysis_notes": ["Financial analysis skipped — no P&L data detected."],
                    "recommendation": "Manual review required."
                }
                management_fallback = {
                    "status": "error", "company_name": company_name,
                    "management_score": 0.0, "risk_level": "Undetermined",
                    "requires_manual_review": True, "fallback_reason": "missing_promoter",
                    "is_knockout": False, "promoter_analysis": [],
                    "governance_assessment": {"board_independence": "Undetermined", "regulatory_compliance": "Undetermined", "risk_level": "Undetermined"}
                }
                sector_fallback = {
                    "status": "success", "sector": sector_name, "outlook": "Stable",
                    "risk_factors": ["Sector analysis unavailable."], "rbi_policy_impact": []
                }
                # [W7] Separate skipped sentinel vs agent-failure fallback
                integrity_skipped = {
                    "status": "skipped", "flags": [],
                    "warnings": ["Integrity cross-validation skipped — no GST or bank data provided."]
                }
                integrity_fallback = {
                    "status": "success", "flags": [],
                    "warnings": ["Integrity cross-validation defaulted due to agent failure."]
                }

                tasks = []
                task_map = []  # Track which result maps to which agent

                if state.has_financials:
                    tasks.append(asyncio.create_task(self.financial_agent.analyze(extracted_financials)))
                    task_map.append("financial")
                if state.has_promoters:
                    tasks.append(asyncio.create_task(self.management_agent.analyze({"company_name": company_name, "promoter_ids": promoter_ids})))
                    task_map.append("management")
                tasks.append(asyncio.create_task(get_sector_data(sector_name)))
                task_map.append("sector")
                # [W7] Only dispatch IntegrityVerificationAgent when GST or bank data is present
                if state.has_gst_bank_data:
                    tasks.append(asyncio.create_task(self.integrity_agent.cross_validate(gst_data, bank_data)))
                    task_map.append("integrity")

                try:
                    results = await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True),
                        timeout=AGENT_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    logger.warning("[ASE-54] Agent orchestration timed out. Using fallbacks.")
                    results = [asyncio.TimeoutError("timed out")] * len(tasks)

                # Map results back
                result_map = dict(zip(task_map, results))

                fin_res = result_map.get("financial")
                state.financial_result = fin_res if (fin_res and not isinstance(fin_res, Exception)) else financial_fallback

                mgt_res = result_map.get("management")
                state.management_result = mgt_res if (mgt_res and not isinstance(mgt_res, Exception)) else management_fallback

                sec_res = result_map.get("sector")
                state.sector_result = sec_res if (sec_res and not isinstance(sec_res, Exception)) else sector_fallback

                # [W7] Use skipped sentinel if not dispatched; use fallback on dispatch failure
                int_res = result_map.get("integrity")
                if not state.has_gst_bank_data:
                    state.integrity_result = integrity_skipped
                else:
                    state.integrity_result = int_res if (int_res and not isinstance(int_res, Exception)) else integrity_fallback

                # [W7] Persist intermediate snapshot — agents checkpoint for crash recovery
                update_case_result(state.case_id, state.to_snapshot(), status=STATUS_RUNNING)
            else:
                # Resume: all agent results already persisted in DB — restore and skip re-dispatch
                logger.info("[ASE-54] Case %s: Resuming — using persisted agent results.", case_id)

            # ---- STEP: Evidence + CAM (reuse existing logic) ----
            individual_outputs = {
                "ingestion": extracted_financials,
                "financial_health": state.financial_result,
                "management_quality": state.management_result,
                "sector_context": state.sector_result,
                "integrity_check": state.integrity_result
            }

            if not _evidence_done:
                try:
                    evidence_trail = await self.build_evidence_trail(individual_outputs, policy)
                except Exception as exc:
                    logger.error("[ASE-54] Evidence trail failed: %s", exc)
                    evidence_trail = []
                state.evidence_trail = evidence_trail
                state.step_complete("evidence_built")
                update_case_step(state.case_id, "evidence_built")
            else:
                # Resume: use persisted evidence trail
                evidence_trail = state.evidence_trail
                logger.info("[ASE-54] Case %s: Resuming — using persisted evidence trail.", case_id)

            explanation = await self.generate_explanation(evidence_trail)

            score = int(state.financial_result.get("financial_health_score", 50.0))
            web_research = {
                "company_news": [],
                "sector_headwinds": state.sector_result.get("risk_factors", []),
                "litigation_signals": []
            }

            management_score = state.management_result.get("management_score", 0.0)
            is_knockout = state.management_result.get("is_knockout", False)
            requires_manual_review = state.management_result.get("requires_manual_review", False)
            fallback_reason = state.management_result.get("fallback_reason", "system_error")

            forced_decision = None
            forced_rationale = None

            # [ASE-63] Respect manager overrides from a resumed HITL pause
            if state.manager_decision:
                forced_decision = state.manager_decision
                forced_rationale = state.manager_rationale or "Manager override applied."
                logger.info("[ASE-63] Resuming with manager override: %s", forced_decision)
            else:
                if requires_manual_review:
                    forced_decision = "MANUAL REVIEW"
                    forced_rationale = (
                        "Management assessment could not be completed because promoter information was unavailable."
                        if fallback_reason == "missing_promoter"
                        else "Management assessment could not be completed and manual review is required."
                    )
                elif is_knockout:
                    forced_decision = "REJECT"
                    forced_rationale = "Management Hard Gate: Knockout condition detected."
                elif management_score < 50:
                    forced_decision = "REJECT"
                    forced_rationale = f"Management Hard Gate: Score ({management_score}) below threshold of 50."

                # [ASE-63] True HITL Pause if critical risk is detected
                # Only pause for:
                # 1. requires_manual_review
                # 2. CRITICAL evidence severity
                has_critical_evidence = any(item.get("severity") == "CRITICAL" for item in evidence_trail)

                if requires_manual_review or has_critical_evidence:
                    pause_reason = "HUMAN_APPROVAL_REQUIRED"
                    if has_critical_evidence and not requires_manual_review:
                        pause_reason = "CRITICAL_RISK_DETECTED"

                    logger.warning(f"[ASE-63] Critical risk detected ({pause_reason}). Forcing MANUAL REVIEW override.")
                    
                    forced_decision = "MANUAL REVIEW"
                    forced_rationale = f"Pipeline flagged for HITL review. {pause_reason}" 

            try:
                ingestion_citations = extracted_financials.get("citations", {})
                cam_result = await self.cam_agent.generate_cam(extracted_financials, state.integrity_result, web_research, score, ingestion_citations=ingestion_citations)
                if forced_decision:
                    cam_result["decision"] = forced_decision
                    cam_result["decision_rationale"] = forced_rationale
                    if forced_decision == "REJECT":
                        cam_result["recommended_loan_amount"] = "0"
                        cam_result["recommended_interest_rate"] = "N/A"
                    elif forced_decision == "MANUAL REVIEW":
                        cam_result["recommended_loan_amount"] = "Withheld"
                        cam_result["recommended_interest_rate"] = "TBD"
            except Exception as cam_exc:
                logger.warning("[ASE-54] CAM generation failed: %s", cam_exc)
                # [P0-4] A CAM generation failure is a system fault, not an
                # underwriting conclusion. Emitting MANUAL REVIEW here made a
                # broken pipeline indistinguishable from a credit officer
                # deciding a case needs human review.
                _code, _retryable = classify_exception(cam_exc)
                cam_result = {
                    "document_control": {"status": "ERROR"},
                    "five_cs": {k: "NOT PROVIDED" for k in ["character", "capacity", "capital", "collateral", "conditions"]},
                    "decision": DECISION_ANALYSIS_INCOMPLETE,
                    "decision_allowed": False,
                    "analysis_status": AnalysisStatus.FAILED.value,
                    "error_code": _code,
                    "retryable": _retryable,
                    "recommended_loan_amount": "UNAVAILABLE",
                    "recommended_interest_rate": "UNAVAILABLE",
                    "decision_rationale": (
                        "Credit recommendation unavailable: the Credit Appraisal Memo could not be generated. This is a system failure, not an underwriting conclusion."
                    ),
                }

            state.step_complete("cam_complete")

            final_result = {
                "status": "success",
                "case_id": state.case_id,
                "appraisal_id": f"APPRAISAL_{int(datetime.now().timestamp())}",
                "individual_agent_outputs": individual_outputs,
                "combined_decision": cam_result,
                "evidence_trail": evidence_trail,
                "explanation": explanation,
                "routing_flags": {
                    "has_financials": state.has_financials,
                    "has_promoters": state.has_promoters,
                    "has_gst_bank_data": state.has_gst_bank_data,  # [W7]
                },
                "evidence_citations": {
                    "revenue": ingestion_citations.get("revenue"),
                    "debt": ingestion_citations.get("debt"),
                    "equity": ingestion_citations.get("equity"),
                    "dscr": self._build_dscr_citation(extracted_financials, state.financial_result),
                    "current_ratio": self._build_current_ratio_citation(extracted_financials, state.financial_result),
                }
            }
            state.final_result = final_result
            state.mark_complete()
            update_case_result(state.case_id, final_result, status=STATUS_COMPLETED)
            logger.info("[ASE-54] Case %s completed successfully.", state.case_id)
            return final_result

        except Exception as exc:
            logger.error("[ASE-54] Case %s failed at step '%s': %s", state.case_id, state.current_step, exc, exc_info=True)
            state.step_failed(str(exc))
            mark_case_failed(state.case_id, str(exc))
            raise


    async def build_evidence_trail(self, agent_outputs: dict, policy: dict = None) -> list[dict]:
        """Assemble evidence from all agent outputs into a structured trail."""
        evidence_list = []
        seen_descriptions = set()

        if not policy:
            policy = DEFAULT_POLICY

        cr_safe = policy.get("current_ratio_safe", THRESHOLD_CURRENT_RATIO_SAFE)
        cr_min = policy.get("current_ratio_min", THRESHOLD_CURRENT_RATIO_MIN)
        dscr_safe = policy.get("dscr_safe", THRESHOLD_DSCR_SAFE)
        dscr_min = policy.get("dscr_min", THRESHOLD_DSCR_MIN)
        de_high = policy.get("de_high", THRESHOLD_DE_HIGH)

        def add_evidence(
            category: str,
            source: str,
            severity: str,
            title: str,
            description: str,
            recommendation: str,
            confidence: str = "High",
        ):
            # De-duplicate identical descriptions to prevent noise
            if not description:
                return
            desc_key = (source, description.strip().lower())
            if desc_key in seen_descriptions:
                return
            seen_descriptions.add(desc_key)

            evidence_list.append({
                "category": category,
                "source_agent": source,
                "severity": severity,
                "title": title,
                "description": description.strip(),
                "recommendation": recommendation.strip(),
                "confidence": confidence,
            })

        # Safe helper to handle dict get
        if not isinstance(agent_outputs, dict):
            logger.warning("agent_outputs is not a dictionary. Returning empty evidence trail.")
            return []

        # 1. Ingestion Agent
        ingestion = agent_outputs.get("ingestion", {})
        if isinstance(ingestion, dict):
            # Parse legal risks
            legal_risks = ingestion.get("legal_risks", [])
            if isinstance(legal_risks, list):
                for risk in legal_risks:
                    if risk and isinstance(risk, str) and risk.strip() and "unable to extract" not in risk.lower():
                        add_evidence(
                            category="Legal/Compliance",
                            source="DocumentIngestionAgent",
                            severity="MEDIUM",
                            title="Legal Risk Ingested",
                            description=risk,
                            recommendation="Review litigation details and verify active legal liabilities.",
                            confidence="High",
                        )

            # Parse sanction details
            sanctions = ingestion.get("sanction_details", [])
            if isinstance(sanctions, list):
                for sanction in sanctions:
                    if sanction and isinstance(sanction, str) and sanction.strip():
                        add_evidence(
                            category="Legal/Compliance",
                            source="DocumentIngestionAgent",
                            severity="INFO",
                            title="Existing Sanction / Limit Details",
                            description=sanction,
                            recommendation="Verify limits sanctioned by other financial institutions.",
                            confidence="High",
                        )

        # 2. Financial Health Agent
        financial = agent_outputs.get("financial_health", {})
        if isinstance(financial, dict):
            # Parse ratios
            ratios = financial.get("ratios", {})
            if isinstance(ratios, dict):
                current_ratio = ratios.get("current_ratio")
                if current_ratio is not None:
                    try:
                        cr_val = float(current_ratio)
                        if cr_val < cr_safe:
                            severity = "HIGH" if cr_val < cr_min else "MEDIUM"
                            add_evidence(
                                category="Financial Ratios",
                                source="FinancialHealthAgent",
                                severity=severity,
                                title="Low Current Ratio",
                                description=f"The current ratio is evaluated at {cr_val:.2f}, indicating tight liquidity.",
                                recommendation=REC_CURRENT_RATIO,
                                confidence="High",
                            )
                    except (ValueError, TypeError) as exc:
                        logger.debug("Failed to parse current_ratio field for evidence trail: %s", type(exc).__name__)

                dscr = ratios.get("dscr")
                if dscr is not None:
                    try:
                        dscr_val = float(dscr)
                        if dscr_val < dscr_safe:
                            severity = "HIGH" if dscr_val < dscr_min else "MEDIUM"
                            add_evidence(
                                category="Financial Ratios",
                                source="FinancialHealthAgent",
                                severity=severity,
                                title="Weak Debt Service Coverage Ratio (DSCR)",
                                description=f"Calculated DSCR is {dscr_val:.2f}, representing a potential risk in meeting debt service obligations.",
                                recommendation=REC_DSCR,
                                confidence="High",
                            )
                    except (ValueError, TypeError) as exc:
                        logger.debug("Failed to parse dscr field for evidence trail: %s", type(exc).__name__)

                debt_to_equity = ratios.get("debt_to_equity")
                if debt_to_equity is not None:
                    try:
                        de_val = float(debt_to_equity)
                        if de_val > de_high:
                            add_evidence(
                                category="Financial Ratios",
                                source="FinancialHealthAgent",
                                severity="HIGH",
                                title="High Leverage (Debt-to-Equity)",
                                description=f"Debt-to-Equity ratio is high at {de_val:.2f}.",
                                recommendation=REC_DE,
                                confidence="High",
                            )
                    except (ValueError, TypeError) as exc:
                        logger.debug("Failed to parse debt_to_equity field for evidence trail: %s", type(exc).__name__)

            # Parse analysis notes
            analysis_notes = financial.get("analysis_notes", [])
            if isinstance(analysis_notes, list):
                for note in analysis_notes:
                    if note and isinstance(note, str) and note.strip():
                        # Determine if warning
                        is_warning = any(x in note.lower() for x in ["zero", "could not be calculated", "insufficient"])
                        severity = "MEDIUM" if is_warning else "INFO"
                        add_evidence(
                            category="Financial Ratios",
                            source="FinancialHealthAgent",
                            severity=severity,
                            title="Financial Analysis Metric Note",
                            description=note,
                            recommendation="Check the underlying values and perform manual calculation if necessary.",
                            confidence="High",
                        )

            # Cash flow assessment
            cash_flow = financial.get("cash_flow_assessment", {})
            if isinstance(cash_flow, dict):
                cf_status = cash_flow.get("status")
                if cf_status == "Weak":
                    add_evidence(
                        category="Financial Ratios",
                        source="FinancialHealthAgent",
                        severity="HIGH",
                        title="Weak Cash Flow Status",
                        description="The cash flow assessment shows weak dynamics, with potential negative or declining trends.",
                        recommendation="Audit historical bank statements and evaluate cash flow trend metrics.",
                        confidence="High",
                    )

        # 3. Promoter Agent (Management Quality)
        management = agent_outputs.get("management_quality", {})
        if isinstance(management, dict):
            promoter_analysis = management.get("promoter_analysis", [])
            if isinstance(promoter_analysis, list):
                for promoter in promoter_analysis:
                    if isinstance(promoter, dict):
                        p_name = promoter.get("name", "Unknown Promoter")
                        risk_flags = promoter.get("risk_flags", [])
                        if isinstance(risk_flags, list):
                            for flag in risk_flags:
                                if flag and isinstance(flag, str) and flag.strip():
                                    severity = "CRITICAL" if "default" in flag.lower() or "regulatory" in flag.lower() else "HIGH"
                                    add_evidence(
                                        category="Promoter Risk",
                                        source="ManagementQualityAgent",
                                        severity=severity,
                                        title="Promoter Background Warning Flag",
                                        description=f"Promoter {p_name}: {flag}",
                                        recommendation="Perform extensive background reference checks and CIBIL queries.",
                                        confidence="High",
                                    )

        # 4. Sector Context Agent
        sector_context = agent_outputs.get("sector_context", {})
        if isinstance(sector_context, dict):
            outlook = sector_context.get("outlook")
            if outlook == "Negative":
                add_evidence(
                    category="Sector/Macro Risk",
                    source="SectorContextAgent",
                    severity="MEDIUM",
                    title="Negative Sector Outlook",
                    description=f"The macro industry sector {sector_context.get('sector', 'Unknown')} outlook is Negative.",
                    recommendation="Limit exposure limits and implement stricter loan covenants.",
                    confidence="Medium",
                )

            # Risk factors
            risk_factors = sector_context.get("risk_factors", [])
            if isinstance(risk_factors, list):
                for factor in risk_factors:
                    if factor and isinstance(factor, str) and factor.strip() and "unable to retrieve" not in factor.lower():
                        add_evidence(
                            category="Sector/Macro Risk",
                            source="SectorContextAgent",
                            severity="INFO",
                            title="Sector Headwind Identified",
                            description=factor,
                            recommendation="Monitor industry developments and compare against borrower operational metrics.",
                            confidence="Medium",
                        )

            # RBI policy impact
            rbi_policies = sector_context.get("rbi_policy_impact", [])
            if isinstance(rbi_policies, list):
                for policy in rbi_policies:
                    if isinstance(policy, dict):
                        impact = policy.get("impact")
                        ref = policy.get("circular_ref", "N/A")
                        summary = policy.get("summary", "")
                        if impact == "Unfavorable":
                            add_evidence(
                                category="Sector/Macro Risk",
                                source="SectorContextAgent",
                                severity="HIGH",
                                title=f"Unfavorable RBI Circular Mapped: {ref}",
                                description=summary or f"RBI Circular Ref {ref} has an unfavorable impact on this sector.",
                                recommendation="Review circular compliance and adjust exposure criteria accordingly.",
                                confidence="High",
                            )

        # 5. Integrity Agent
        integrity = agent_outputs.get("integrity_check", {})
        if isinstance(integrity, dict):
            flags = integrity.get("flags", [])
            if isinstance(flags, list):
                for flag in flags:
                    if isinstance(flag, dict):
                        flag_name = flag.get("flag", "Data Integrity Flag")
                        severity = flag.get("severity", "MEDIUM")
                        details = flag.get("details", "")
                        add_evidence(
                            category="Data Integrity",
                            source="IntegrityVerificationAgent",
                            severity=severity,
                            title=flag_name,
                            description=details,
                            recommendation="Request original transactional records, verified GST filings, or reconciled bank files.",
                            confidence="High",
                        )

            warnings = integrity.get("warnings", [])
            if isinstance(warnings, list):
                for warning in warnings:
                    if warning and isinstance(warning, str) and warning.strip() and "skipped" not in warning.lower():
                        add_evidence(
                            category="Data Integrity",
                            source="IntegrityVerificationAgent",
                            severity="MEDIUM",
                            title="Integrity Agent Check Warning",
                            description=warning,
                            recommendation="Investigate missing data parameters or incomplete statements.",
                            confidence="High",
                        )

        return evidence_list


    async def generate_explanation(self, evidence_trail: list[dict]) -> str:
        """Generate human-readable explanation of the decision."""
        if not evidence_trail:
            return "No credit appraisal evidence was provided. Manual review is required to evaluate the application."

        # Format the evidence trail into a readable context string
        context_lines = []
        for item in evidence_trail:
            category = item.get("category", "General")
            source = item.get("source_agent", "Unknown")
            severity = item.get("severity", "INFO")
            title = item.get("title", "Metric Note")
            desc = item.get("description", "")
            context_lines.append(
                f"- [{category}] [{severity}] {title} (Source: {source}): {desc}"
            )
        evidence_text = "\n".join(context_lines)

        prompt = ChatPromptTemplate.from_messages([
            ("system", (
                "You are a Senior Chief Credit Officer at a commercial bank.\n"
                "Synthesize a clear, professional, and explainable natural language credit "
                "appraisal rationale based on the provided audit evidence trail.\n"
                "Your explanation MUST address:\n"
                "- Score and Decision Derivation: Summarize the findings that impact the credit decision.\n"
                "- Positive Factors: Highlight strong financial metrics, clean background checks, and stable conditions.\n"
                "- Negative Factors: Highlight warnings, low liquidity ratios, circular trading flags, or promoter defaults.\n"
                "Keep the language objective, concise, and audit-ready."
            )),
            ("user", "Audit Evidence Trail:\n{evidence_text}")
        ])

        try:
            # Enforce execution timeout (10.0 seconds) to prevent hanging
            if self.llm:
                chain = prompt | self.llm
                response = await asyncio.wait_for(
                    chain.ainvoke({"evidence_text": evidence_text}),
                    timeout=EXPLANATION_TIMEOUT_SECONDS
                )
                raw_text = response.content if hasattr(response, "content") else str(response)
                cleaned_text = raw_text.strip()
                if cleaned_text:
                    return cleaned_text
        except asyncio.TimeoutError:
            logger.warning("LLM explanation generation timed out. Falling back to deterministic summary.")
        except Exception as e:
            logger.error(f"Error calling LLM for explanation: {e}", exc_info=True)

        # Fallback Tier 2: Deterministic local summary parsing
        try:
            positives = []
            negatives = []
            for item in evidence_trail:
                severity = item.get("severity", "INFO")
                title = item.get("title", "Note")
                desc = item.get("description", "")
                message = f"{title}: {desc}"
                if severity in ["HIGH", "CRITICAL"]:
                    negatives.append(message)
                elif severity == "MEDIUM":
                    negatives.append(message)
                else:
                    positives.append(message)

            summary_parts = ["Appraisal Summary (System Fallback):"]
            if positives:
                summary_parts.append("\nPositive Factors:")
                summary_parts.extend(f"- {p}" for p in positives[:5])
            if negatives:
                summary_parts.append("\nNegative/Warning Factors:")
                summary_parts.extend(f"- {n}" for n in negatives[:5])

            return "\n".join(summary_parts)
        except Exception as fallback_err:
            logger.error(f"Deterministic explanation fallback failed: {fallback_err}", exc_info=True)

        # Fallback Tier 3: Static default response
        return (
            "Credit appraisal successfully compiled. Key metrics (ratios, cash flow, integrity) "
            "have been logged to the audit evidence trail. Please review the detailed evidence "
            "trail below to determine final loan eligibility."
        )

    def _build_dscr_citation(self, extracted_financials: dict, financial_result: dict) -> dict:
        """[W8] Build a calculated citation for DSCR."""
        if not isinstance(financial_result, dict):
            return None
        ratios = financial_result.get("ratios", {})
        if "dscr" not in ratios or ratios["dscr"] is None:
            return None

        citations = extracted_financials.get("citations", {}) if isinstance(extracted_financials, dict) else {}
        inputs = []

        revenue_cit = citations.get("revenue") if isinstance(citations, dict) else None
        if revenue_cit and isinstance(revenue_cit, dict) and revenue_cit.get("page"):
            inputs.append(f"revenue (page {revenue_cit['page']})")

        debt_cit = citations.get("debt") if isinstance(citations, dict) else None
        if debt_cit and isinstance(debt_cit, dict) and debt_cit.get("page"):
            inputs.append(f"debt (page {debt_cit['page']})")

        return {
            "formula": "DSCR = Net Operating Income / Debt Service",
            "inputs": inputs,
            "confidence": "CALCULATED",
            "note": "This metric was calculated by the system, not extracted from the document."
        }

    def _build_current_ratio_citation(self, extracted_financials: dict, financial_result: dict) -> dict:
        """[W8] Build a calculated citation for Current Ratio."""
        if not isinstance(financial_result, dict):
            return None
        ratios = financial_result.get("ratios", {})
        if "current_ratio" not in ratios or ratios["current_ratio"] is None:
            return None

        citations = extracted_financials.get("citations", {}) if isinstance(extracted_financials, dict) else {}
        inputs = []

        # Current Ratio = Current Assets / Current Liabilities
        # These come from the ingestion extraction, not from separate citation keys,
        # so we trace back to the revenue citation as the best available document reference.
        revenue_cit = citations.get("revenue") if isinstance(citations, dict) else None
        if revenue_cit and isinstance(revenue_cit, dict) and revenue_cit.get("page"):
            inputs.append(f"current_assets (document: {revenue_cit.get('document', 'source document')})")
            inputs.append(f"current_liabilities (document: {revenue_cit.get('document', 'source document')})")

        return {
            "formula": "Current Ratio = Current Assets / Current Liabilities",
            "inputs": inputs,
            "confidence": "CALCULATED",
            "note": "This metric was calculated by the system, not extracted from the document."
        }


