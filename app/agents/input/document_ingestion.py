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
from pypdf import PdfReader
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List, Optional, Any

CRORE = 10_000_000

# ---------------------------------------------------------------------------
# Pre-compiled Regex Patterns
# Pre-compiling at module load time is a performance best-practice: the regex
# engine parses the pattern once rather than on every function call.
# ---------------------------------------------------------------------------

# Matches any character that is NOT a printable ASCII character (0x20–0x7E),
# a standard newline (\n), a carriage return (\r), or a horizontal tab (\t).
# This strips OCR/PDF artefacts such as null bytes, form-feeds, and Unicode
# control characters that inflate token counts without adding semantic value.
_RE_CONTROL_CHARS: re.Pattern = re.compile(
    r'[^\x20-\x7E\n\r\t]'
)

# Matches three or more consecutive blank lines (lines containing only
# optional whitespace). Reduced to a single blank line to preserve
# paragraph separation without wasting LLM context tokens.
_RE_EXCESS_NEWLINES: re.Pattern = re.compile(
    r'(\s*\n){3,}'
)

# Matches documents that contain at least one recognisable Indian or
# international financial term. The pattern uses a non-capturing alternation
# and is case-insensitive at the call site via re.IGNORECASE.
# Keeping all terms in a single compiled pattern is O(n) in document length.
_RE_FINANCIAL_TERMS: re.Pattern = re.compile(
    r'\b(?:'
    r'revenue|turnover|profit|loss|ebitda|ebit|pat|pbt'
    r'|balance\s+sheet|income\s+statement|cash\s+flow'
    r'|total\s+assets|total\s+liabilities|net\s+worth'
    r'|shareholder[s]?\s+equity|retained\s+earnings'
    r'|borrowings?|loan|credit\s+limit|overdraft|npa'
    r'|working\s+capital|current\s+ratio|debt\s+to\s+equity'
    r'|gstr|cibil|cmr|sma|epfo|esic'
    r'|crore|lakh|rupees?|inr|₹'
    r'|annual\s+report|financial\s+statement|audit(?:ed|or)?'
    r'|depreciation|amortis[a-z]+|provisions?'
    r')\b',
    re.IGNORECASE,
)


def _remove_duplicate_headers(pages_text: List[str]) -> List[str]:
    """Remove lines that appear verbatim as the first line of three or more
    separate pages — a strong signal that the line is a repeating page header
    (e.g., company name, report title, confidentiality notice) rather than
    substantive content.

    Strategy:
    - Collect the *first non-empty line* from each page.
    - Any candidate that appears on >= 3 pages is considered a header.
    - Strip every exact occurrence of a detected header from *all* pages.

    Args:
        pages_text: List of per-page text strings as returned by PyPDF2 or
                    pytesseract — one element per page, before joining.

    Returns:
        A new list with the same structure but with duplicate headers removed.
    """
    if not pages_text:
        return pages_text

    # --- Step 1: Identify candidate headers ---
    # Map: first_line -> count of pages it leads
    header_counts: dict = {}
    for page in pages_text:
        lines = page.splitlines()
        # Skip leading blank lines to find the true first content line.
        for line in lines:
            stripped = line.strip()
            if stripped:
                header_counts[stripped] = header_counts.get(stripped, 0) + 1
                break  # Only inspect the first non-empty line per page.

    # A line must appear at the top of at least 3 pages to qualify as a header.
    # Threshold of 3 prevents over-aggressive removal on short (2-page) docs.
    HEADER_MIN_OCCURRENCES = 3
    duplicate_headers = {
        line for line, count in header_counts.items()
        if count >= HEADER_MIN_OCCURRENCES
    }

    if not duplicate_headers:
        return pages_text  # Nothing to remove — fast path.

    print(
        f"[CLEAN] Removing {len(duplicate_headers)} duplicate page header(s): "
        + str(duplicate_headers)
    )

    # --- Step 2: Strip identified headers from every page ---
    cleaned_pages = []
    for page in pages_text:
        filtered_lines = [
            line for line in page.splitlines()
            if line.strip() not in duplicate_headers
        ]
        cleaned_pages.append("\n".join(filtered_lines))

    return cleaned_pages

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
from typing import Optional
from pydantic import BaseModel, Field

class RiskExtraction(BaseModel):
    company_name: str = Field(description="The name of the company applying for credit")
    sector: str = Field(description="The industry sector (e.g., Manufacturing, Fintech) inferred from the text")
    
    # NEW: Quantifiable Credit Data (Crucial to prevent ESG-only approvals)
    total_revenue: Optional[Any] = Field(None, description="Annual revenue (turnover)")
    total_debt: Optional[float] = Field(None, description="Total short/long term borrowings")
    shareholder_equity: Optional[Any] = Field(None, description="Net worth / Share capital + reserves")
    current_assets: Optional[float] = Field(None, description="Total current assets")
    current_liabilities: Optional[float] = Field(None, description="Total current liabilities")
    
    base_score: int = Field(description="An estimated starting credit score (0-100)")
    qualitative_notes: Optional[str] = Field(None, description="Summary of operational capacity or CIBIL/GSTR notes")
    financial_commitments: List[str] = Field(default_factory=list, description="Existing loans, guarantees, or credit lines")
    legal_risks: List[str] = Field(default_factory=list, description="Ongoing litigation, defaults, or notices")
    sanction_details: List[str] = Field(default_factory=list, description="Details of limits sanctioned by other banks")
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
        """HYBRID EXTRACTION: Tries standard text first, falls back to OCR for messy/scanned PDFs.

        Pipeline (in order):
            1. Digital text extraction via PyPDF2.
            2. OCR fallback via Tesseract when digital extraction yields < 100 chars.
            3. Duplicate page-header removal (operates on the page list before joining).
            4. Text sanitization via ``_clean_text()`` (control chars + blank lines).
            5. Financial terminology validation via ``_contains_financial_terms()``.
            6. Table count extraction via tabula (non-critical, best-effort).
        """
        raw_text = ""
        pages_text: List[str] = []

        # Validate file exists
        if not os.path.exists(file_path):
            return {"text": "", "tables_count": 0, "error": f"File not found: {file_path}"}

        # -------------------------------------------------------------------
        # Attempt 1: Standard Digital Extraction
        # -------------------------------------------------------------------
        try:
            reader = PdfReader(file_path)
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

        # -------------------------------------------------------------------
        # Attempt 2: OCR Fallback for "Messy/Scanned" Indian PDFs
        # -------------------------------------------------------------------
        if len(raw_text.strip()) < 100:
            print("[PDF] Detected scanned document. Initiating Tesseract OCR...")
            try:
                from pdf2image import convert_from_path
                import pytesseract
                images = convert_from_path(file_path)
                ocr_pages: List[str] = []
                for i, img in enumerate(images):
                    try:
                        text = pytesseract.image_to_string(img)
                        ocr_pages.append(text)
                    except Exception as ocr_page_err:
                        print(f"[OCR] Page {i} failed: {ocr_page_err}")
                        continue
                pages_text = ocr_pages
                raw_text = "\n".join(pages_text)
            except ImportError:
                print("[OCR] pdf2image or pytesseract not installed. Skipping OCR.")
            except Exception as e:
                print(f"[OCR] Failed (Ensure Poppler/Tesseract are installed): {e}")

        # Final check: if we still have no text, return early with error
        if len(raw_text.strip()) < 10:
            return {"text": "", "tables_count": 0, "error": "No readable text could be extracted from the PDF."}

        # -------------------------------------------------------------------
        # Step 3: Remove duplicate page headers
        # Must happen on the page *list* before joining so we can inspect
        # per-page leading lines independently.
        # -------------------------------------------------------------------
        pages_text = _remove_duplicate_headers(pages_text)
        raw_text = "\n".join(pages_text)

        # -------------------------------------------------------------------
        # Step 4: Sanitize extracted text
        # Strips control characters and collapses excessive blank lines.
        # -------------------------------------------------------------------
        clean_text = self._clean_text(raw_text)

        # -------------------------------------------------------------------
        # Step 5: Financial terminology validation
        # Rejects documents that contain no financial keywords, preventing
        # wasteful LLM calls on brochures, HR policies, etc.
        # -------------------------------------------------------------------
        if not self._contains_financial_terms(clean_text):
            print("[VALIDATE] Document rejected: no financial terms detected.")
            return {
                "text": "",
                "tables_count": 0,
                "error": (
                    "Document rejected: The document does not contain required "
                    "financial terminology (e.g., revenue, balance sheet, "
                    "borrowings, CIBIL). Please upload a financial statement, "
                    "credit report, or audit document."
                ),
            }

        # -------------------------------------------------------------------
        # Step 6: Table count extraction (non-critical, best-effort)
        # -------------------------------------------------------------------
        tables_count = 0
        try:
            tables = tabula.read_pdf(file_path, pages='all', multiple_tables=True)
            tables_count = len(tables) if tables else 0
        except Exception:
            tables_count = 0

        return {"text": clean_text, "tables_count": tables_count}

    # ---------------------------------------------------------------------------
    # Text Preprocessing Helpers
    # These methods must be called in order inside ingest_pdf():
    #   1. _remove_duplicate_headers()  — operates on the page list (before join)
    #   2. _clean_text()                — operates on the joined string
    #   3. _contains_financial_terms()  — validation gate
    # ---------------------------------------------------------------------------

    def _clean_text(self, text: str) -> str:
        """Sanitize raw PDF/OCR text before it is sent to the LLM.

        Applies two sequential regex passes:

        Pass 1 — Control / non-ASCII character removal
            Strips null bytes, form-feed characters (\x0c), Unicode private-use
            area codepoints, and other non-printable bytes that OCR engines and
            some PDF exporters embed in the text stream.  These characters are
            invisible to human readers but consume LLM context tokens and can
            confuse JSON-mode structured outputs.

        Pass 2 — Excessive blank-line collapse
            PDF-to-text converters often emit a blank line between every
            paragraph *and* between every page.  When pages are joined, sections
            of a document can be separated by 5-10 consecutive blank lines.
            We collapse any run of 3+ newlines down to exactly 2 (one blank
            line), which preserves visual paragraph separation without the token
            waste.

        Args:
            text: The raw string returned by PyPDF2 or pytesseract.

        Returns:
            A clean, token-efficient string ready for LLM consumption.
        """
        if not text:
            return text

        original_len = len(text)

        # Pass 1: Strip non-printable / non-ASCII control characters.
        text = _RE_CONTROL_CHARS.sub('', text)

        # Pass 2: Collapse runs of 3+ newlines to a single blank line.
        text = _RE_EXCESS_NEWLINES.sub('\n\n', text)

        # Strip leading / trailing whitespace from the entire document.
        text = text.strip()

        cleaned_len = len(text)
        reduction_pct = round((1 - cleaned_len / original_len) * 100, 1) if original_len else 0
        print(
            f"[CLEAN] Text sanitized: {original_len} → {cleaned_len} chars "
            f"({reduction_pct}% reduction)"
        )
        return text

    def _contains_financial_terms(self, text: str) -> bool:
        """Validate that the document contains recognisable financial content.

        This is a lightweight pre-LLM gate.  Sending a non-financial document
        (e.g. a scanned brochure, an HR policy, or a blank page) to the LLM
        wastes Groq API tokens and returns a meaningless extraction result.

        Mechanism:
            Performs a single regex search over the document text using the
            pre-compiled ``_RE_FINANCIAL_TERMS`` pattern, which matches a
            curated list of English and Indian financial keywords.
            A *single match* is sufficient to pass — this keeps the gate
            permissive enough to accept partial/messy documents while still
            rejecting clearly non-financial content.

        Args:
            text: The cleaned text string produced by ``_clean_text()``.

        Returns:
            ``True`` if at least one financial keyword is found; ``False``
            otherwise.
        """
        return bool(_RE_FINANCIAL_TERMS.search(text))

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
            - Extract the balance sheet value:
            - total_revenue
             -total_debt
             -shareholder_equity
             -current_assets
             -current_liabilities

            Rules:

            - Return all financial values as numeric floats.
             -Remove commas and currency symbols.
             -Convert crore/lakh values to INR.
             -If a value is missing,return null.
             -Do not return "N/A","-", or empty string.
             
              Identify Balance Sheet items: shareholder_equity, total assets/liabilities.
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