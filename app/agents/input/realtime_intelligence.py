# =============================================================================
# CREDENT — Realtime Intelligence Agent (Web Research & Synthesis)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
import os
import json
import re
from app.core.llm import ChatGroqWithFallback as ChatGroq
from duckduckgo_search import DDGS
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List
from app.core.decision_config import DECISION_PATH_TEMPERATURE

# Default fallback response
DEFAULT_RESEARCH = {
    "company_news": ["No relevant news found or web search unavailable."],
    "sector_headwinds": ["Unable to retrieve sector data. Manual research recommended."],
    "litigation_signals": ["No litigation data found or web search unavailable."]
}

# Output schema
class ResearchReport(BaseModel):
    company_news: List[str] = Field(default_factory=list, description="Recent news, red flags, or updates about the company or its promoters")
    sector_headwinds: List[str] = Field(default_factory=list, description="Current challenges, RBI regulations, or headwinds in the specified sector")
    litigation_signals: List[str] = Field(default_factory=list, description="Any mentions of lawsuits, defaults, or regulatory actions found online")

class RealtimeIntelligenceAgent:
    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            print("[WARN] GROQ_API_KEY not set. Research agent will use defaults.")
        
        self.llm = ChatGroq(
            model=os.getenv("PRIMARY_LLM_MODEL", "openai/gpt-oss-20b"),
            # [P0-3] Decision path: greedy decoding.
            temperature=DECISION_PATH_TEMPERATURE,
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
            api_key=api_key or "dummy"
        )
        # Bypassed structured output to prevent Sarvam looping
        self.structured_llm = None
        
        self.search = True

    def _extract_json_from_text(self, text: str) -> dict:
        from json_repair import repair_json
        import json
        import re
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try: return json.loads(json_match.group())
            except: 
                try: return json.loads(repair_json(json_match.group()))
                except: pass
        try: return json.loads(repair_json(text))
        except: raise ValueError("No JSON found in response")

    async def conduct_research(self, company_name: str, sector: str) -> dict:
        """Crawl the web for company news and sector headwinds with full error handling."""
        
        # Validate inputs
        if not company_name or not company_name.strip():
            print("[RESEARCH] No company name provided, returning defaults.")
            # [P1-5] Structured failure marker so downstream validation can tell
            # "no adverse findings" apart from "research never ran".
            degraded = DEFAULT_RESEARCH.copy()
            degraded["agent_status"] = "DEGRADED"
            degraded["error_code"] = "INVALID_OUTPUT"
            degraded["research_degraded"] = True
            degraded["degradation_reason"] = "No company name supplied for research."
            degraded["retryable"] = True
            return degraded
        
        if not sector or not sector.strip():
            sector = "General Business"

        # Step 1: Fetch web search results
        company_results = ""
        sector_results = ""
        
        company_query = f"{company_name} news litigation fraud defaults promoter"
        sector_query = f"{sector} sector India RBI regulations headwinds challenges"

        try:
            with DDGS() as ddgs:
                c_res = list(ddgs.text(company_query, max_results=5))
                company_results = json.dumps(c_res)
        except Exception as e:
            print(f"[RESEARCH] Company web search failed: {e}")
            company_results = "Web search unavailable."

        try:
            with DDGS() as ddgs:
                s_res = list(ddgs.text(sector_query, max_results=5))
                sector_results = json.dumps(s_res)
        except Exception as e:
            print(f"[RESEARCH] Sector web search failed: {e}")
            sector_results = "Web search unavailable."

        # Step 2: Feed results to LLM for synthesis
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are an elite credit risk and OSINT analyst. Your job is to extract ONLY critical, objective risk signals from raw web search results.
            
            CRITICAL RULES:
            1. STRICTLY EXCLUDE all marketing fluff, company bios, promotional text, and self-descriptions (e.g., "India's largest...", "most loved app", "leading provider").
            2. ONLY include objective news events, financial red flags, leadership changes, or operational updates.
            3. If a search result is just a company's own website description, ignore it completely.
            
            You MUST output ONLY valid JSON that EXACTLY matches this schema:
            {{
                "company_news": ["list of strings", "objective news and red flags ONLY. No marketing."],
                "sector_headwinds": ["list of strings", "regulatory challenges"],
                "litigation_signals": ["list of strings", "lawsuits or defaults"]
            }}"""),
            ("user", "Company Search Results:\n{company_results}\n\nSector Search Results:\n{sector_results}")
        ])

        invoke_params = {
            "company_results": str(company_results)[:4000],
            "sector_results": str(sector_results)[:4000]
        }

        # Attempt 1: Structured output  
        if self.structured_llm:
            try:
                chain = prompt | self.structured_llm
                result = await chain.ainvoke(invoke_params)
                return result.model_dump()
            except Exception as e:
                print(f"[RESEARCH] Structured output failed: {e}")

        # Attempt 2: Raw LLM + JSON parse
        try:
            chain = prompt | self.llm
            raw_result = await chain.ainvoke(invoke_params)
            raw_text = raw_result.content if hasattr(raw_result, 'content') else str(raw_result)
            parsed = self._extract_json_from_text(raw_text)
            
            # Ensure all keys exist with defaults
            for key, default_val in DEFAULT_RESEARCH.items():
                parsed.setdefault(key, default_val)
                # Ensure values are lists of strings
                if not isinstance(parsed[key], list):
                    parsed[key] = [str(parsed[key])]
            
            return parsed
        except Exception as e2:
            print(f"[RESEARCH] Raw fallback failed: {e2}")

        # Attempt 3: Return defaults
        print("[RESEARCH] All research methods failed. Returning defaults.")
        # [P1-5] Structured failure marker so downstream validation can tell
        # "no adverse findings" apart from "research never ran".
        degraded = DEFAULT_RESEARCH.copy()
        degraded["agent_status"] = "DEGRADED"
        degraded["error_code"] = "EXTERNAL_RESEARCH_UNAVAILABLE"
        degraded["research_degraded"] = True
        degraded["degradation_reason"] = "External research unavailable."
        degraded["retryable"] = True
        return degraded

