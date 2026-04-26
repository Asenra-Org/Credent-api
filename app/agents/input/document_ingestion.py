# =============================================================================
# CREDENT — Document Ingestion Agent (PDF & OCR Extraction)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
import tabula
import os
import json
import re
from PyPDF2 import PdfReader
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List, Optional, Any

CRORE = 10_000_000

def normalize_to_inr(value):
    if value is None:
        return None

    # If already numeric
    if isinstance(value, (int, float)):
        val_int = int(value)
    # SMEs/Mid-caps are rarely > 500 Cr revenue. 
    # If it's a small number, multiply by Crore.
    if 0 < val_int < 500: 
        return val_int * CRORE
        return val_int

    value_str = str(value).replace(",", "").strip().lower()

    # Extract numeric part
    match = re.search(r'[\d.]+', value_str)
    if not match:
        return None

    try:
        number = float(match.group())
    except ValueError:
        return None

    # PRIORITY 1: Explicit Units
    if "crore" in value_str or "cr" in value_str:
        normalized = int(number * CRORE)
    elif "lakh" in value_str:
        normalized = int(number * 100_000)
    elif "m" in value_str and "crore" not in value_str: # Millions (usually from intl docs)
        normalized = int(number * 10_000_00) # (10 Lakhs)
    elif "k" in value_str and "lakh" not in value_str:
        normalized = int(number * 1_000)
    
    # PRIORITY 2: Raw Large Numbers
    elif number > 1_000_000:
        normalized = int(number)
        
    # PRIORITY 3: Implicit Scaling for Small Numbers
    elif 0 < number < 500:
        normalized = int(number * CRORE)
        
    else:
        # Ambiguous numbers remain as-is to prevent 10x/100x explosions
        normalized = int(number)

    print(f"[NORMALIZE] Input: {value} | Number: {number} | Output: {normalized}")
    return normalized

# UPGRADED SCHEMA: Focus on hard financials to prevent "Tone-based" hallucinations
class RiskExtraction(BaseModel):
    company_name: str = Field(description="The name of the company applying for credit")
    sector: str = Field(description="The industry sector (e.g., Manufacturing, Fintech) inferred from the text")
    
    # NEW: Quantifiable Credit Data (Crucial to prevent ESG-only approvals)
    total_revenue: Optional[Any] = Field(None, description="Annual revenue (turnover)")
    total_debt: Optional[Any] = Field(None, description="Total short/long term borrowings")
    shareholder_equity: Optional[Any] = Field(None, description="Net worth / Share capital + reserves")
    current_assets: Optional[Any] = Field(None, description="Total current assets")
    current_liabilities: Optional[Any] = Field(None, description="Total current liabilities")
    
    base_score: int = Field(description="An estimated starting credit score (0-100)")
    qualitative_notes: str = Field(description="Summary of operational capacity or CIBIL/GSTR notes")
    financial_commitments: List[str] = Field(description="Existing loans, guarantees, or credit lines")
    legal_risks: List[str] = Field(description="Ongoing litigation, defaults, or notices")
    sanction_details: List[str] = Field(description="Details of limits sanctioned by other banks")

# Default fallback when all extraction fails
DEFAULT_EXTRACTION = {
    "company_name": "Unknown Entity",
    "sector": "Unknown",
    "total_revenue": None,
    "total_debt": None,
    "shareholder_equity": None,
    "current_assets": None,
    "current_liabilities": None,
    "base_score": 65,
    "qualitative_notes": "Document could not be fully processed. Manual review required.",
    "financial_commitments": [],
    "legal_risks": ["Unable to extract — manual review required"],
    "sanction_details": []
}

class DocumentIngestionAgent:
    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            print("[WARN] GROQ_API_KEY not set. AI extraction will use fallback defaults.")
        self.llm = ChatGroq(
            model="llama-3.1-8b-instant",
            temperature=0,
            api_key=api_key or "dummy"
        )
        try:
            self.structured_llm = self.llm.with_structured_output(RiskExtraction, method="json_mode")
        except Exception as e:
            print(f"[WARN] Structured output init failed: {e}")
            self.structured_llm = None
        
    async def ingest_pdf(self, file_path: str) -> dict:
        """HYBRID EXTRACTION: Tries standard text first, falls back to OCR for messy/scanned PDFs."""
        raw_text = ""
        
        # Validate file exists
        if not os.path.exists(file_path):
            return {"text": "", "tables_count": 0, "error": f"File not found: {file_path}"}
        
        # Attempt 1: Standard Digital Extraction
        try:
            reader = PdfReader(file_path)
            pages_text = []
            for page in reader.pages:
                try:
                    text = page.extract_text()
                    if text:
                        pages_text.append(text)
                except Exception as page_err:
                    print(f"[PDF] Page extraction error: {page_err}")
                    continue
            raw_text = "\n".join(pages_text)
        except Exception as e:
            print(f"[PDF] PyPDF2 Failed: {e}")

        # Attempt 2: OCR Fallback for "Messy/Scanned" Indian PDFs
        if len(raw_text.strip()) < 100:
            print("[PDF] Detected scanned document. Initiating Tesseract OCR...")
            try:
                from pdf2image import convert_from_path
                import pytesseract
                images = convert_from_path(file_path)
                ocr_text = []
                for i, img in enumerate(images):
                    try:
                        text = pytesseract.image_to_string(img)
                        ocr_text.append(text)
                    except Exception as ocr_page_err:
                        print(f"[OCR] Page {i} failed: {ocr_page_err}")
                        continue
                raw_text = "\n".join(ocr_text)
            except ImportError:
                print("[OCR] pdf2image or pytesseract not installed. Skipping OCR.")
            except Exception as e:
                print(f"[OCR] Failed (Ensure Poppler/Tesseract are installed): {e}")

        # Final check: if we still have no text, return early with error
        if len(raw_text.strip()) < 10:
            return {"text": "", "tables_count": 0, "error": "No readable text could be extracted from the PDF."}

        # Attempt to read tables (non-critical)
        tables_count = 0
        try:
            tables = tabula.read_pdf(file_path, pages='all', multiple_tables=True)
            tables_count = len(tables) if tables else 0
        except Exception:
            tables_count = 0

        return {"text": raw_text, "tables_count": tables_count}

    def _extract_json_from_text(self, text: str) -> dict:
        """Try to extract JSON from raw LLM text response."""
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            return json.loads(json_match.group())
        raise ValueError("No JSON found in response")

    async def parse_financial_statement(self, raw_text: str) -> dict:
        """Parse financial statement text using AI with multi-tier fallback."""
        if not raw_text or len(raw_text.strip()) < 10:
            print("[PARSE] No text to parse, returning defaults.")
            return DEFAULT_EXTRACTION.copy()

        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are a Senior Indian Credit Risk Officer. Extract all requested details from the raw document text. 
            
            INDIAN CONTEXT SENSITIVITY: 
            Pay special attention to and extract any mentions of:
            - GSTR-2A / GSTR-3B reconciliations
            - CIBIL Commercial scores or CMR rankings
            - SMA-0, SMA-1, SMA-2, or NPA classifications
            - EPFO/ESIC defaults

            CRITICAL: Focus on HARD FINANCIAL DATA. 
            - Identify P&L items: total_revenue, total_debt.
            - Identify Balance Sheet items: shareholder_equity, total assets/liabilities.
            - UNIT CONVERSION (MANDATORY): Return financial values as FOUND (e.g. '62 Cr', '120000000').
              * 1 Crore = 10,000,000
              * If document says "62 Crores", you MUST return "620000000" OR "62 Cr".
              * NEVER return raw numbers in Millions (e.g. 620) as they can be misinterpreted as 620 Crores.
              * If document mentions "38 Crores", return "380000000".
            - If the document is purely a "Transparency", "ESG" or "Policy" document WITHOUT financial tables, return null for those fields.
            
            DO NOT be swayed by "Good Tone" or "Governance Policies" if Revenue/Debt data is missing. 
            A credit score of 0-100 MUST reflect presence of creditworthy financial data.
            
            JSON schema:
            {{
                "company_name": "string",
                "sector": "string",
                "total_revenue": float or null,
                "total_debt": float or null,
                "shareholder_equity": float or null,
                "current_assets": float or null,
                "current_liabilities": float or null,
                "base_score": 85,
                "qualitative_notes": "string",
                "financial_commitments": ["string"],
                "legal_risks": ["string"],
                "sanction_details": ["string"]
            }}"""),
            ("user", "{text}")
        ])

        # Limit text to avoid token overflow
        truncated_text = raw_text[:30000]

        # Attempt 1: Structured output
        if self.structured_llm:
            try:
                chain = prompt | self.structured_llm
                result = await chain.ainvoke({"text": truncated_text})
                parsed = result.model_dump()
                
                # NORMALIZE FINANCIALS
                fin_fields = ["total_revenue", "total_debt", "shareholder_equity", "current_assets", "current_liabilities"]
                for field in fin_fields:
                    parsed[field] = normalize_to_inr(parsed.get(field))
                    
                return parsed
            except Exception as e:
                print(f"[PARSE] Structured output failed: {e}")

        # Attempt 2: Raw LLM + manual JSON parse
        try:
            chain = prompt | self.llm
            raw_result = await chain.ainvoke({"text": truncated_text})
            raw_response = raw_result.content if hasattr(raw_result, 'content') else str(raw_result)
            parsed = self._extract_json_from_text(raw_response)
            
            # Fill defaults for any missing keys
            for key, default_val in DEFAULT_EXTRACTION.items():
                parsed.setdefault(key, default_val)
                
            # NORMALIZE FINANCIALS
            fin_fields = ["total_revenue", "total_debt", "shareholder_equity", "current_assets", "current_liabilities"]
            for field in fin_fields:
                parsed[field] = normalize_to_inr(parsed.get(field))
            
            # Clamp base_score to 0-100
            try:
                parsed["base_score"] = max(0, min(100, int(parsed["base_score"])))
            except (ValueError, TypeError):
                parsed["base_score"] = 65

            return parsed
        except Exception as e2:
            print(f"[PARSE] Raw fallback failed: {e2}")

        # Attempt 3: Return defaults
        print("[PARSE] All AI extraction failed. Returning defaults.")
        return DEFAULT_EXTRACTION.copy()