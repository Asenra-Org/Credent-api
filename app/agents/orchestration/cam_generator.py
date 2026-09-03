# =============================================================================
# CREDENT - Institutional CAM Generator Agent
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# =============================================================================
import os
import json
import httpx
import re
from app.core.llm import ChatGroqWithFallback as ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any
from app.core.decision_config import DECISION_PATH_TEMPERATURE

class Citation(BaseModel):
    id: int = Field(default=0, description="Unique integer ID for the citation")
    snippet: Optional[str] = Field(default=None, description="Exact excerpt")
    page: Optional[int] = Field(default=None, description="Page number")
    document: Optional[str] = Field(default=None, description="Document type")
    location: Optional[str] = Field(default=None, description="Exact field label")

# ---------------------------------------------------------
# NEW INSTITUTIONAL CAM SCHEMA
# ---------------------------------------------------------

class DocumentControl(BaseModel):
    borrower_name: str = Field(default="NOT PROVIDED")
    case_id: str = Field(default="CRESEM-XXXX")
    appraisal_date: str = Field(default="NOT PROVIDED")
    status: str = Field(default="PENDING")
    version: str = Field(default="v1.0")

class ExecutiveSummary(BaseModel):
    industry: str = Field(default="NOT PROVIDED")
    facility_requested: str = Field(default="NOT PROVIDED")
    revenue: str = Field(default="NOT PROVIDED")
    ebitda: str = Field(default="NOT PROVIDED")
    pat: str = Field(default="NOT PROVIDED")
    net_worth: str = Field(default="NOT PROVIDED")
    total_debt: str = Field(default="NOT PROVIDED")
    dscr: str = Field(default="NOT COMPUTABLE")
    current_ratio: str = Field(default="NOT COMPUTABLE")
    strengths: List[str] = Field(default_factory=list)
    key_concerns: List[str] = Field(default_factory=list)
    critical_conditions: List[str] = Field(default_factory=list)

class BorrowerProfile(BaseModel):
    legal_name: str = Field(default="NOT PROVIDED")
    incorporation_date: str = Field(default="NOT PROVIDED")
    registered_location: str = Field(default="NOT PROVIDED")
    business_activity: str = Field(default="NOT PROVIDED")
    years_in_operation: str = Field(default="NOT PROVIDED")
    existing_lenders: str = Field(default="NOT PROVIDED")

class Facility(BaseModel):
    facility_type: str = Field(default="NOT PROVIDED")
    requested_amount: str = Field(default="NOT PROVIDED")
    tenor: str = Field(default="NOT PROVIDED")
    repayment_structure: str = Field(default="NOT PROVIDED")
    security: str = Field(default="NOT PROVIDED")

class Management(BaseModel):
    promoter_background: str = Field(default="NOT PROVIDED")
    management_capability: str = Field(default="NOT PROVIDED")
    governance_indicators: str = Field(default="NOT PROVIDED")
    related_party_concerns: str = Field(default="NOT PROVIDED")

class Business(BaseModel):
    business_model: str = Field(default="NOT PROVIDED")
    revenue_drivers: str = Field(default="NOT PROVIDED")
    competitive_position: str = Field(default="NOT PROVIDED")
    industry_characteristics: str = Field(default="NOT PROVIDED")

class FinancialMetric(BaseModel):
    metric: str = Field(default="NOT PROVIDED")
    value: str = Field(default="NOT PROVIDED")
    trend: str = Field(default="N/A")

class FinancialAnalysis(BaseModel):
    performance: List[FinancialMetric] = Field(default_factory=list)
    balance_sheet: List[FinancialMetric] = Field(default_factory=list)
    cash_flow: List[FinancialMetric] = Field(default_factory=list)

class Ratio(BaseModel):
    name: str = Field(default="NOT PROVIDED")
    value: str = Field(default="NOT COMPUTABLE")
    interpretation: str = Field(default="NOT PROVIDED")
    source: str = Field(default="Derived")

class Ratios(BaseModel):
    key_ratios: List[Ratio] = Field(default_factory=list)

class CrossDocVerification(BaseModel):
    metric: str = Field(default="NOT PROVIDED")
    source_a: str = Field(default="NOT PROVIDED")
    source_b: str = Field(default="NOT PROVIDED")
    consistency: str = Field(default="NOT PROVIDED", description="MATCH or VARIANCE")
    observation: str = Field(default="NOT PROVIDED")

class BankingAnalysis(BaseModel):
    average_credits: str = Field(default="NOT PROVIDED")
    average_debits: str = Field(default="NOT PROVIDED")
    emi_servicing: str = Field(default="NOT PROVIDED")
    cheque_returns: str = Field(default="NOT PROVIDED")
    analytical_notes: str = Field(default="NOT PROVIDED")

class TaxAnalysis(BaseModel):
    gst_turnover: str = Field(default="NOT PROVIDED")
    itr_revenue: str = Field(default="NOT PROVIDED")
    filing_consistency: str = Field(default="NOT PROVIDED")

class Collateral(BaseModel):
    security_type: str = Field(default="NOT PROVIDED")
    valuation: str = Field(default="NOT PROVIDED")
    ltv: str = Field(default="NOT PROVIDED")

class FiveCItem(BaseModel):
    evidence: str = Field(default="NOT PROVIDED")
    assessment: str = Field(default="NOT PROVIDED")
    risk_implication: str = Field(default="NOT PROVIDED")

class FiveCs(BaseModel):
    character: FiveCItem = Field(default_factory=lambda: FiveCItem())
    capacity: FiveCItem = Field(default_factory=lambda: FiveCItem())
    capital: FiveCItem = Field(default_factory=lambda: FiveCItem())
    collateral: FiveCItem = Field(default_factory=lambda: FiveCItem())
    conditions: FiveCItem = Field(default_factory=lambda: FiveCItem())

class RiskItem(BaseModel):
    area: str = Field(default="NOT PROVIDED")
    level: str = Field(default="MEDIUM", description="HIGH, MEDIUM, LOW")
    evidence: str = Field(default="NOT PROVIDED")
    mitigation: str = Field(default="NOT PROVIDED")

class RiskAssessment(BaseModel):
    risks: List[RiskItem] = Field(default_factory=list)

class Indicator(BaseModel):
    finding: str = Field(default="NOT PROVIDED")
    evidence: str = Field(default="NOT PROVIDED")
    severity: str = Field(default="MEDIUM")
    implication: str = Field(default="NOT PROVIDED")

class InformationGap(BaseModel):
    requirement: str = Field(default="NOT PROVIDED")
    reason: str = Field(default="NOT PROVIDED")
    priority: str = Field(default="MEDIUM", description="HIGH, MEDIUM, LOW")

class Recommendation(BaseModel):
    decision: str = Field(default="MANUAL REVIEW", description="APPROVE, APPROVE WITH CONDITIONS, MANUAL REVIEW, REJECT, REWORK")
    rationale: str = Field(default="NOT PROVIDED")
    conditions: List[str] = Field(default_factory=list)

class EvidenceItem(BaseModel):
    finding: str = Field(default="NOT PROVIDED")
    value: str = Field(default="NOT PROVIDED")
    source_document: str = Field(default="NOT PROVIDED")
    page: str = Field(default="NOT PROVIDED")
    status: str = Field(default="UNVERIFIED", description="VERIFIED, UNVERIFIED, MISSING, CONFLICTING, DERIVED")

class CAMDocument(BaseModel):
    document_control: DocumentControl = Field(default_factory=lambda: DocumentControl())
    executive_summary: ExecutiveSummary = Field(default_factory=lambda: ExecutiveSummary())
    borrower_profile: BorrowerProfile = Field(default_factory=lambda: BorrowerProfile())
    facility: Facility = Field(default_factory=lambda: Facility())
    management: Management = Field(default_factory=lambda: Management())
    business: Business = Field(default_factory=lambda: Business())
    financial_analysis: FinancialAnalysis = Field(default_factory=lambda: FinancialAnalysis())
    ratios: Ratios = Field(default_factory=lambda: Ratios())
    cross_document_verification: List[CrossDocVerification] = Field(default_factory=list)
    banking_analysis: BankingAnalysis = Field(default_factory=lambda: BankingAnalysis())
    tax_analysis: TaxAnalysis = Field(default_factory=lambda: TaxAnalysis())
    collateral: Collateral = Field(default_factory=lambda: Collateral())
    five_cs: FiveCs = Field(default_factory=lambda: FiveCs())
    risk_assessment: RiskAssessment = Field(default_factory=lambda: RiskAssessment())
    positive_indicators: List[Indicator] = Field(default_factory=list)
    red_flags: List[Indicator] = Field(default_factory=list)
    information_gaps: List[InformationGap] = Field(default_factory=list)
    recommendation: Recommendation = Field(default_factory=lambda: Recommendation(decision="MANUAL REVIEW", rationale=""))
    evidence_register: List[EvidenceItem] = Field(default_factory=list)

class CAMGeneratorAgent:
    def __init__(self):
        # We use llama-3.1-8b-instant, which is fast but has strict output length.
        # However, it should handle 2-3k tokens of JSON well if prompted correctly.
        self.llm = ChatGroq(
            model=os.getenv("PRIMARY_LLM_MODEL", "openai/gpt-oss-20b"),
            temperature=DECISION_PATH_TEMPERATURE,  # [P0-3] decision path 
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            api_key=os.getenv("GROQ_API_KEY")
        )
        # Force structured_llm to None to bypass LangChain's strict length-checking parser.
        # This routes generation to the fallback path which uses `json-repair`, 
        # allowing us to salvage truncated JSONs when Sarvam hits output limits.
        self.structured_llm = None

    def _build_prompt(self):
        schema_json = json.dumps(CAMDocument.model_json_schema(), indent=2).replace("{", "{{").replace("}", "}}")
        return ChatPromptTemplate.from_messages([
            ("system", f"""You are the Senior Chief Credit Officer at an institutional bank.
            Your task is to synthesize the provided evidence into a PROFESSIONAL, BANKING-GRADE Credit Appraisal Memorandum (CAM).
            
            CRITICAL DIRECTIVES:
            1. DO NOT INVENT DATA. If a value is missing, use "NOT PROVIDED", "NOT COMPUTABLE", or "MISSING".
            2. DISTINGUISH FACT FROM INTERPRETATION. State facts strictly based on the extracted PDF data, then separately state your credit interpretation.
            3. EVIDENCE TRACEABILITY: Add 1 or 2 items to the evidence_register to prove key numbers. ALL VALUES MUST BE STRINGS WITH QUOTES (e.g. "1.2", "Page 2"). DO NOT output raw integers or floats.
            4. If the Composite Risk Score < 60, the decision MUST be REJECT.
            5. If there are severe missing gaps, decision MUST be MANUAL REVIEW or REWORK.
            6. THE FIVE Cs ARE ANALYSIS, NOT EXTRACTION. The five_cs section is your own
               professional credit judgement derived from the financial data supplied, so
               directive 1 does not apply to it. You MUST populate all five (character,
               capacity, capital, collateral, conditions). For each one give:
                 - evidence: the specific figures you reasoned from
                 - assessment: your underwriting conclusion in one or two sentences
                 - risk_implication: what it means for repayment risk
               Derive capacity from revenue against debt servicing, capital from shareholder
               equity and gearing, and conditions from the sector and macro context. Do not
               write "NOT PROVIDED" in five_cs whenever financial figures have been supplied;
               if collateral is genuinely absent, say so and state the risk of unsecured
               exposure rather than leaving it blank.
            7. EXECUTIVE SUMMARY FINANCIALS: The pdf_data contains fields: total_revenue, ebitda, pat, total_debt, shareholder_equity.
               - Map total_revenue → executive_summary.revenue
               - Map ebitda → executive_summary.ebitda  
               - Map pat → executive_summary.pat
               - If these fields exist and are not null in pdf_data, you MUST use them verbatim. Do NOT write "NOT PROVIDED" if the value is present in the input data.
            
            Ensure the output strictly adheres to this EXACT JSON schema without generating any trailing commas or malformed curly braces:
            {schema_json}
            """),
            ("user", """
            === APPRAISAL DATA ===
            1. PDF Extraction: {pdf_data}
            2. Integrity Flags: {integrity_data}
            3. Web Research: {research_data}
            4. Composite Risk Score: {score}
            5. Source Citations: {citations}
            
            Synthesize the data into the CAMDocument schema.
            """)
        ])

    def _extract_json_from_text(self, text: str) -> dict:
        from json_repair import repair_json
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try: return json.loads(json_match.group())
            except: 
                try: return json.loads(repair_json(json_match.group()))
                except: pass
        try: return json.loads(repair_json(text))
        except: raise ValueError("No JSON found")

    async def generate_cam(self, extracted_pdf_data: dict, integrity_flags: dict, web_research: dict, final_score: int, ingestion_citations: dict = None) -> dict:
        prompt = self._build_prompt()
        invoke_params = {
            "pdf_data": json.dumps(extracted_pdf_data)[:3000], # Trucate to prevent massive context overflow breaking small models
            "integrity_data": json.dumps(integrity_flags),
            "research_data": json.dumps(web_research),
            "score": final_score,
            "citations": json.dumps(ingestion_citations or {})
        }

        try:
            sarvam_key = os.getenv("SARVAM_API_KEY")
            if sarvam_key:
                # Direct API call to salvage reasoning_content from Sarvam
                print("[CAM] Using direct Sarvam API call to prevent truncation data loss...")
                messages = prompt.format_messages(**invoke_params)
                payload = {
                    "model": "sarvam-105b",
                    "messages": [{"role": m.type, "content": m.content} for m in messages],
                    "temperature": 0.1,
                    "max_tokens": int(os.getenv("LLM_MAX_TOKENS", 4000))
                }
                async with httpx.AsyncClient(timeout=900.0) as client:
                    resp = await client.post(
                        "https://api.sarvam.ai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {sarvam_key}", "Content-Type": "application/json"},
                        json=payload
                    )
                
                if resp.status_code != 200:
                    print(f"[CAM ERROR] Sarvam API HTTP {resp.status_code}: {resp.text}")
                    resp.raise_for_status()

                resp_data = resp.json()
                choice = resp_data.get("choices", [{}])[0].get("message", {})
                content_str = choice.get("content") or ""
                if not content_str and choice.get("reasoning_content"):
                    reasoning = choice.get("reasoning_content")
                    print(f"[CAM] Content empty. Salvaging from reasoning_content (len={len(reasoning)})...")
                    import re
                    match = re.search(r'(\{[\s\S]+)', reasoning)
                    content_str = match.group(1) if match else reasoning
                
                print(f"[CAM] LLM response received | chars={len(content_str)}")
                data = self._extract_json_from_text(content_str)
            else:
                if self.structured_llm:
                    chain = prompt | self.structured_llm
                    result = await chain.ainvoke(invoke_params)
                    data = result.model_dump()
                else:
                    chain = prompt | self.llm
                    res = await chain.ainvoke(invoke_params)
                    print(f"[CAM] LLM response received | chars={len(res.content or [])}")
                    data = self._extract_json_from_text(res.content)
            
            # Formatting
            data["decision"] = data.get("recommendation", {}).get("decision", "MANUAL REVIEW")
            data["recommended_loan_amount"] = data.get("facility", {}).get("requested_amount", "NOT PROVIDED")
            data["recommended_interest_rate"] = "TBD"
            data["decision_rationale"] = data.get("recommendation", {}).get("rationale", "N/A")
            return data
        except Exception as e:
            print(f"[CAM ERROR] {e}")
            return {
                "document_control": {"borrower_name": extracted_pdf_data.get("company_name", "Unknown"), "status": "ERROR"},
                "executive_summary": {"industry": "UNKNOWN", "revenue": "N/A", "ebitda": "N/A", "pat": "N/A", "strengths": [], "key_concerns": ["SYSTEM ERROR"], "critical_conditions": []},
                "borrower_profile": {"legal_name": extracted_pdf_data.get("company_name", "Unknown"), "business_activity": "N/A"},
                "facility": {"facility_type": "N/A", "requested_amount": "N/A", "tenor": "N/A", "security": "N/A"},
                "management": {"key_personnel": [], "experience": "N/A"},
                "business": {"model": "N/A", "market": "N/A"},
                "financial_analysis": {"performance": [], "balance_sheet": [], "cash_flow": []},
                "ratios": {"key_ratios": []},
                "cross_document_verification": [],
                "banking_analysis": {},
                "tax_analysis": {},
                "collateral": {},
                "five_cs": {"character": "N/A", "capacity": "N/A", "capital": "N/A", "collateral": "N/A", "conditions": "N/A"},
                "risk_assessment": {},
                "positive_indicators": [],
                "red_flags": [],
                "information_gaps": [],
                "recommendation": {"decision": "MANUAL REVIEW", "rationale": "System error during CAM generation."},
                "evidence_register": [],
                "decision": "MANUAL REVIEW",
                "recommended_loan_amount": "Withheld",
                "recommended_interest_rate": "TBD",
                "decision_rationale": f"System encountered an error during synthesis. Escalate for human validation."
            }

