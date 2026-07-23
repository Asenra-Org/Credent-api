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

from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from app.agents.input.document_ingestion import DocumentIngestionAgent
from app.agents.analysis.financial_health import FinancialHealthAgent
from app.agents.analysis.management_quality import ManagementQualityAgent
from app.agents.analysis.sector_context import SectorContextAgent
from app.agents.analysis.integrity_verification import IntegrityVerificationAgent
from app.agents.orchestration.cam_generator import CAMGeneratorAgent

logger = logging.getLogger(__name__)

# Timeout threshold for downstream asynchronous agent executions
AGENT_TIMEOUT_SECONDS = 15.0

# Timeout threshold for explanation generation
EXPLANATION_TIMEOUT_SECONDS = 10.0

# Business Thresholds for credit health evaluations
THRESHOLD_CURRENT_RATIO_SAFE = 1.2
THRESHOLD_CURRENT_RATIO_MIN = 1.0
THRESHOLD_DSCR_SAFE = 1.25
THRESHOLD_DSCR_MIN = 1.0
THRESHOLD_DE_HIGH = 2.0

# Recommendation templates
REC_CURRENT_RATIO = "Assess the working capital cycle and evaluate short-term liabilities."
REC_DSCR = "Request debt amortization schedules and project future free cash flows."
REC_DE = "Review the company's capital structure and assess debt repayment capabilities."


class AgentCoordinator:
    """
    Central orchestrator that:
    1. Receives a credit appraisal request
    2. Dispatches tasks to specialized agents
    3. Collects and synthesizes their outputs
    4. Builds an evidence trail for explainability
    5. Triggers the output layer (CAM, scoring, pricing)
    """

    def __init__(self):
        # Set up agent instances
        self.ingestion_agent = DocumentIngestionAgent()
        self.financial_agent = FinancialHealthAgent()
        self.management_agent = ManagementQualityAgent()
        self.sector_agent = SectorContextAgent()
        self.integrity_agent = IntegrityVerificationAgent()
        self.cam_agent = CAMGeneratorAgent()

        # Initialize LLM for narrative explanation generation
        api_key = os.getenv("GROQ_API_KEY")
        self.llm = ChatGroq(
            model="llama-3.1-8b-instant",
            temperature=0.2,
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

        logger.info("Credit appraisal started for document: %s", os.path.basename(file_path))

        # Stage 2: Sequential Ingestion (Fail-Fast)
        try:
            ingestion_result = await self.ingestion_agent.ingest_pdf(file_path)
            if not ingestion_result or ingestion_result.get("error"):
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
            "status": "success",
            "company_name": company_name,
            "management_score": 0.0,
            "risk_level": "Undetermined",
            "promoter_analysis": [
                {
                    "name": p,
                    "experience_years": 0,
                    "risk_flags": [],
                    "verdict": "Undetermined"
                } for p in (promoter_ids if promoter_ids else [company_name])
            ],
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
            evidence_trail = await self.build_evidence_trail(individual_outputs)
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

        try:
            cam_result = await self.cam_agent.generate_cam(
                extracted_financials,
                integrity_result,
                web_research,
                score
            )
        except Exception as cam_exc:
            logger.warning("Decision engine failed: %s. Triggering fallback decision mapping.", str(cam_exc))
            decision = "MANUAL REVIEW"
            cam_result = {
                "five_cs": {k: "Manual review required due to system error." for k in ["character", "capacity", "capital", "collateral", "conditions"]},
                "decision": decision,
                "recommended_loan_amount": "Withheld",
                "recommended_interest_rate": "Withheld",
                "decision_rationale": "Underwriting could not be completed because CAM generation failed."
            }

        appraisal_id = f"APPRAISAL_{int(datetime.now().timestamp())}"

        logger.info("Appraisal completed successfully. ID: %s", appraisal_id)

        return {
            "status": "success",
            "appraisal_id": appraisal_id,
            "individual_agent_outputs": individual_outputs,
            "combined_decision": cam_result,
            "evidence_trail": evidence_trail,
            "explanation": explanation
        }


    async def build_evidence_trail(self, agent_outputs: dict) -> list[dict]:
        """Assemble evidence from all agent outputs into a structured trail."""
        evidence_list = []
        seen_descriptions = set()

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
                        if cr_val < THRESHOLD_CURRENT_RATIO_SAFE:
                            severity = "HIGH" if cr_val < THRESHOLD_CURRENT_RATIO_MIN else "MEDIUM"
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
                        if dscr_val < THRESHOLD_DSCR_SAFE:
                            severity = "HIGH" if dscr_val < THRESHOLD_DSCR_MIN else "MEDIUM"
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
                        if de_val > THRESHOLD_DE_HIGH:
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

