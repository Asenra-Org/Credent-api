# =============================================================================
# CREDENT — CAM Generator Agent (Credit Appraisal Memo & Decisioning)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
import os
import json
import re
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List, Dict, Optional

class Citation(BaseModel):
    id: int = Field(description="Unique integer ID for the citation, matching the bracketed inline marker (e.g. [1]).")
    snippet: Optional[str] = Field(default=None, description="Exact excerpt from the provided text.")
    page: Optional[int] = Field(default=None, description="Page number where the snippet was found, if available.")

class MetricWithCitation(BaseModel):
    text: str = Field(description="The analytical text containing inline citation markers like [1].")
    citations: List[Citation] = Field(default_factory=list, description="List of citations backing up the analysis")

# 1. Define the Five Cs Structure
class FiveCs(BaseModel):
    character: MetricWithCitation = Field(description="Analysis of management integrity, litigation history, and market reputation.")
    capacity: MetricWithCitation = Field(description="Analysis of repayment capacity, cash flows, and GST vs Bank consistency.")
    capital: MetricWithCitation = Field(description="Analysis of net worth and existing financial commitments.")
    collateral: MetricWithCitation = Field(description="Analysis of available security (note if unsecured).")
    conditions: MetricWithCitation = Field(description="Analysis of macroeconomic factors and sector headwinds.")

# 2. Define the Final CAM Structure
class CreditAppraisalMemo(BaseModel):
    five_cs: FiveCs
    decision: str = Field(description="'APPROVE', 'MANUAL REVIEW', or 'REJECT'")
    recommended_loan_amount: str = Field(description="Suggested loan amount (e.g., 'INR 50,00,000') or '0' if rejected. MUST BE PRESENT.")
    recommended_interest_rate: str = Field(description="Suggested interest rate (e.g., '14.5%') or 'N/A' if rejected. MUST BE PRESENT.")
    decision_rationale: str = Field(description="Transparent explanation of why this decision was made. Must explicitly reference data points.")

class CAMGeneratorAgent:
    def __init__(self):
        self.llm = ChatGroq(
            model="llama-3.1-8b-instant",
            temperature=0.1, 
            api_key=os.getenv("GROQ_API_KEY")
        )
        try:
            self.structured_llm = self.llm.with_structured_output(CreditAppraisalMemo, method="json_mode")
        except:
            self.structured_llm = None

    def _build_prompt(self):
        return ChatPromptTemplate.from_messages([
            ("system", """You are the Senior Chief Credit Officer. 
            Synthesize appraisal data into a final Credit Appraisal Memo (CAM).
            
            Decision Priority (Highest to Lowest):
            1. If Score < 60 -> MUST REJECT. No exceptions.
            2. Else if financials are missing -> MANUAL REVIEW.
            3. Else if Current Ratio < 1.0 -> MANUAL REVIEW.
            4. Else evaluate the remaining Five Cs criteria to determine APPROVE or MANUAL REVIEW in accordance with the credit policy.
            
            CRITICAL INSTRUCTION: You MUST output a JSON object containing ALL of the following keys.
            
            {{
                "five_cs": {{
                    "character": {{"text": "analysis with [1]...", "citations": [{{"id": 1, "snippet": "...", "page": 1}}]}},
                    "capacity": {{"text": "analysis...", "citations": []}},
                    "capital": {{"text": "analysis...", "citations": []}},
                    "collateral": {{"text": "analysis...", "citations": []}},
                    "conditions": {{"text": "analysis...", "citations": []}}
                }},
                "decision": "APPROVE, MANUAL REVIEW, or REJECT",
                "recommended_loan_amount": "Amount or 'Withheld pending review'",
                "recommended_interest_rate": "Rate or 'TBD'",
                "decision_rationale": "Detailed explanation"
            }}"""),
            ("user", """
            === APPRAISAL DATA ===
            1. PDF Extraction: {pdf_data}
            2. Integrity Flags: {integrity_data}
            3. Web Research: {research_data}
            4. Composite Risk Score: {score}
            """)
        ])

    def _extract_json_from_text(self, text: str) -> dict:
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try: return json.loads(json_match.group())
            except: pass
        raise ValueError("No JSON found")

    async def generate_cam(self, extracted_pdf_data: dict, integrity_flags: dict, web_research: dict, final_score: int) -> dict:
        prompt = self._build_prompt()
        invoke_params = {
            "pdf_data": json.dumps(extracted_pdf_data),
            "integrity_data": json.dumps(integrity_flags),
            "research_data": json.dumps(web_research),
            "score": final_score
        }

        try:
            if self.structured_llm:
                chain = prompt | self.structured_llm
                result = await chain.ainvoke(invoke_params)
                return result.model_dump()
            else:
                chain = prompt | self.llm
                res = await chain.ainvoke(invoke_params)
                return self._extract_json_from_text(res.content)
        except Exception as e:
            print(f"[CAM ERROR] {e}")
            return {
                "five_cs": {k: {"text": "Manual review required due to system error.", "citations": []} for k in ["character", "capacity", "capital", "collateral", "conditions"]},
                "decision": "MANUAL REVIEW",
                "recommended_loan_amount": "Withheld",
                "recommended_interest_rate": "TBD",
                "decision_rationale": f"System encountered an error during synthesis. Score remains {final_score}/100. Escalating for human validation."
            }