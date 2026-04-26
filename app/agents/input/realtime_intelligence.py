# =============================================================================
# CREDENT — Realtime Intelligence Agent (Web Research & Synthesis)
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================
import os
import json
import re
from langchain_groq import ChatGroq
from langchain_community.tools import DuckDuckGoSearchResults
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from typing import List

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
            model="llama-3.1-8b-instant",
            temperature=0.2, 
            api_key=api_key or "dummy"
        )
        try:
            self.structured_llm = self.llm.with_structured_output(ResearchReport, method="json_mode")
        except Exception as e:
            print(f"[WARN] Structured output init failed: {e}")
            self.structured_llm = None
        
        # Initialize the free web search tool
        try:
            self.search = DuckDuckGoSearchResults()
        except Exception as e:
            print(f"[WARN] DuckDuckGo search init failed: {e}")
            self.search = None

    def _extract_json_from_text(self, text: str) -> dict:
        """Try to extract JSON from raw LLM text response."""
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            return json.loads(json_match.group())
        raise ValueError("No JSON found in response")

    async def conduct_research(self, company_name: str, sector: str) -> dict:
        """Crawl the web for company news and sector headwinds with full error handling."""
        
        # Validate inputs
        if not company_name or not company_name.strip():
            print("[RESEARCH] No company name provided, returning defaults.")
            return DEFAULT_RESEARCH.copy()
        
        if not sector or not sector.strip():
            sector = "General Business"

        # Step 1: Fetch web search results (non-critical if it fails)
        company_results = ""
        sector_results = ""
        
        if self.search:
            company_query = f"{company_name} news litigation fraud defaults promoter"
            sector_query = f"{sector} sector India RBI regulations headwinds challenges"

            try:
                company_results = self.search.invoke(company_query)
                if not isinstance(company_results, str):
                    company_results = str(company_results)
            except Exception as e:
                print(f"[RESEARCH] Company web search failed: {e}")
                company_results = "Web search unavailable."

            try:
                sector_results = self.search.invoke(sector_query)
                if not isinstance(sector_results, str):
                    sector_results = str(sector_results)
            except Exception as e:
                print(f"[RESEARCH] Sector web search failed: {e}")
                sector_results = "Web search unavailable."
        else:
            company_results = "Web search tool not available."
            sector_results = "Web search tool not available."

        # Step 2: Feed results to LLM for synthesis
        prompt = ChatPromptTemplate.from_messages([
            ("system", """You are an elite credit research analyst. Synthesize the raw web search results into a clean, structured risk report. Filter out irrelevant marketing noise. 
            
            You MUST output ONLY valid JSON that EXACTLY matches this schema:
            {{
                "company_news": ["list of strings", "recent red flags"],
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
        return DEFAULT_RESEARCH.copy()