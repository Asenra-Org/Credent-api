import os
import json
import re
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List, Dict, Optional

# 1. Define the Five Cs Structure
class FiveCs(BaseModel):
    character: str = Field(description="Analysis of management integrity, litigation history, and market reputation.")
    capacity: str = Field(description="Analysis of repayment capacity, cash flows, and GST vs Bank consistency.")
    capital: str = Field(description="Analysis of net worth and existing financial commitments.")
    collateral: str = Field(description="Analysis of available security (note if unsecured).")
    conditions: str = Field(description="Analysis of macroeconomic factors and sector headwinds.")

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
            
            GOVERNANCE PROTOCOL:
            - APPROVE: Only if Score >= 75 AND no severe integrity/legal flags AND Current Ratio >= 1.0.
            - MANUAL REVIEW: If Score is 60-74 OR if Current Ratio < 1.0 OR moderate risk clustering (e.g. concentration + liquidity).
            - REJECT: Only if Score < 60 OR severe fraud/defaults found.
            
            Evaluation Criteria:
            - Evaluate based on the Five Cs of Credit.
            - If financials (Revenue/Debt) are missing or null, you MUST default to 'MANUAL REVIEW' regardless of base score.
            - If Current Ratio is < 1.0, you MUST recommend 'MANUAL REVIEW' for liquidity structuring.
            
            CRITICAL INSTRUCTION: You MUST output a JSON object containing ALL of the following keys.
            
            {{
                "five_cs": {{
                    "character": "analysis...",
                    "capacity": "analysis...",
                    "capital": "analysis...",
                    "collateral": "analysis...",
                    "conditions": "analysis..."
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
                "five_cs": {k: "Manual review required due to system error." for k in ["character", "capacity", "capital", "collateral", "conditions"]},
                "decision": "MANUAL REVIEW",
                "recommended_loan_amount": "Withheld",
                "recommended_interest_rate": "TBD",
                "decision_rationale": f"System encountered an error during synthesis. Score remains {final_score}/100. Escalating for human validation."
            }